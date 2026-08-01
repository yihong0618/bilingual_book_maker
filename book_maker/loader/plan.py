"""Coverage-complete translation planning for EPUBs.

Instead of selecting tags to translate (`--translate-tags p`) — which silently
drops any text living in unknown markup — this module *partitions* every text
node in each document into exactly one of two states:

    TRANSLATE(unit)   |   SKIP(reason)

Units are formed at the nearest block-level ancestor of each translatable text
node, where block/inline is resolved from the book's own stylesheets (simple
selectors only) with HTML defaults as fallback.  A block that contains another
text-bearing block is never itself credited with that text, so double
translation is impossible by construction.

Invariant (checked by tests, reported to users):
    total_chars == sum(unit.chars) + sum(skipped[reason])

Runs of short sibling units (poetry) are grouped into stanza-aligned windows
so they can be sent to the model together for context.

Known accepted limitation: a text node consisting solely of a roman numeral
(e.g. ``<em>I</em>``) is classified as a line-number reference and skipped.
"""

import hashlib
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from bs4.element import NavigableString, Tag

try:
    # bs4 wraps rt/rp content in these subclasses; the strict type check
    # below would otherwise drop them from the walk entirely — never
    # translated (good) but also never accounted (bad).
    from bs4.element import RubyParenthesisString, RubyTextString

    TEXT_NODE_TYPES = (NavigableString, RubyTextString, RubyParenthesisString)
except ImportError:  # very old bs4
    TEXT_NODE_TYPES = (NavigableString,)
from ebooklib import ITEM_DOCUMENT

from .helper import is_text_link

# Bump whenever a change alters which units a book partitions into (their
# order, count, or text): resume caches are positional over the unit list, so
# the loader folds this into the resume fingerprint and refuses stale caches.
PLAN_SCHEMA_VERSION = 2

# Every action a plan JSON may carry; anything else is a typo and must fail
# loud — a misspelled "skip" silently treated as translate would quietly
# undo the user's decision.
VALID_PLAN_ACTIONS = frozenset(["translate", "skip", "llm-skip", "force-translate"])

# Sample lines retained per trivially-skipped signature for the classifier.
TRIVIAL_SAMPLE_CAP = 5

# --------------------------------------------------------------------- CSS

# Tags that are block-level by HTML default (for our purpose: an element that
# establishes its own line of text, including table cells and list items).
# NB: dfn is inline per the HTML spec — listing it here would split its
# paragraph into two units mid-sentence.
DEFAULT_BLOCK_TAGS = frozenset("""
    address article aside blockquote body caption dd details div dl dt
    fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr html
    li main nav ol p pre section summary table tbody td tfoot th thead tr ul
    """.split())

BLOCKISH_DISPLAY = frozenset(
    [
        "block",
        "flex",
        "grid",
        "table",
        "table-row",
        "table-cell",
        "list-item",
        "flow-root",
    ]
)

# Containers whose text is never document content. svg (<text>/<title>/<desc>)
# and math (<mtext>) fragments would otherwise merge into the nearest prose
# unit, and "translating" them breaks the markup.
NON_CONTENT_TAGS = frozenset(
    ["script", "style", "head", "title", "template", "svg", "math"]
)

# Ruby annotations (furigana readings) are skipped unconditionally — not via
# the user-editable exclude list: get_text() would otherwise splice readings
# into the base text (漢字かんじ) before it ever reaches the model.
RUBY_ANNOTATION_TAGS = frozenset(["rt", "rp", "rtc"])

DEFAULT_EXCLUDE_TAGS = ("sup", "code")

# Pop-up note semantics: content hidden by CSS/attributes is still shown by
# popup-capable readers when it carries these, so it must be translated.
_NOTE_EPUB_TYPES = frozenset(
    ["footnote", "endnote", "rearnote", "note", "footnotes", "endnotes"]
)
_NOTE_ROLES = frozenset(["doc-footnote", "doc-endnote"])

# Zero-width / soft-hyphen characters: kept in the document, stripped from
# text sent to the model (soft hyphens split words mid-token).
_INVISIBLE_CHARS_RE = re.compile("[\\u00ad\\u200b\\ufeff]")


def _normalize_text(raw):
    """Whitespace-collapse + invisible-character strip for model-bound text."""
    return " ".join(_INVISIBLE_CHARS_RE.sub("", raw).split())


_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_DISPLAY_RE = re.compile(r"(?:^|;)\s*display\s*:\s*([\w-]+)", re.I)
_CSS_SIMPLE_SEL_RE = re.compile(r"([a-zA-Z][\w-]*)?(?:\.([\w-]+))?")


def _flatten_at_rules(css_text):
    """Resolve at-rules before naive rule extraction.

    The flat regex would otherwise pull rules out of ``@media print { ... }``
    as if unconditional — hiding screen-visible text. Non-screen media
    (print/speech/aural) and displayless at-rules (@font-face/@page/
    @keyframes) are dropped; everything else (@media screen/all/amzn-*,
    @supports) is unwrapped so its inner rules apply.
    """
    out = []
    i, n = 0, len(css_text)
    while i < n:
        at = css_text.find("@", i)
        if at == -1:
            out.append(css_text[i:])
            break
        out.append(css_text[i:at])
        brace = css_text.find("{", at)
        semi = css_text.find(";", at)
        if brace == -1 or (semi != -1 and semi < brace):
            # blockless at-rule (@import/@charset/@namespace)
            i = semi + 1 if semi != -1 else n
            continue
        depth, j = 1, brace + 1
        while j < n and depth:
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
            j += 1
        prelude = css_text[at:brace].lower()
        body = css_text[brace + 1 : j - 1]
        if prelude.startswith("@media"):
            if not re.search(r"\b(print|speech|aural)\b", prelude):
                out.append(_flatten_at_rules(body))
        elif prelude.startswith("@supports"):
            out.append(_flatten_at_rules(body))
        i = j
    return " ".join(out)


def parse_css_display(css_text):
    """Extract ``display`` values for simple selectors (tag, .class, tag.class).

    Returns {(tag_or_None, class_or_None): display}.  Anything fancier
    (descendant combinators, ids, pseudo-classes) is ignored — HTML defaults
    then apply, which is the right failure mode for our purpose.
    """
    css_text = re.sub(r"/\*.*?\*/", " ", css_text, flags=re.S)
    css_text = _flatten_at_rules(css_text)
    mapping = {}
    for rule in _CSS_RULE_RE.finditer(css_text):
        selectors, body = rule.groups()
        display = _CSS_DISPLAY_RE.search(body)
        if not display:
            continue
        value = display.group(1).lower()
        for sel in selectors.split(","):
            sel = sel.strip()
            m = _CSS_SIMPLE_SEL_RE.fullmatch(sel)
            if not m or (not m.group(1) and not m.group(2)):
                continue
            tag = m.group(1).lower() if m.group(1) else None
            mapping[(tag, m.group(2))] = value
    return mapping


class DisplayResolver:
    """Resolve whether an element renders block-level.

    Precedence: css(tag.class) > css(.class) > css(tag) > HTML default.
    Later stylesheets win over earlier ones.
    """

    def __init__(self, css_maps):
        self.rules = {}
        for m in css_maps:
            self.rules.update(m)

    def display_of(self, element):
        tag = element.name
        classes = element.get("class") or []
        for cls in classes:
            if (tag, cls) in self.rules:
                return self.rules[(tag, cls)]
        for cls in classes:
            if (None, cls) in self.rules:
                return self.rules[(None, cls)]
        if (tag, None) in self.rules:
            return self.rules[(tag, None)]
        return None

    def is_block(self, element):
        display = self.display_of(element)
        if display is not None:
            return display in BLOCKISH_DISPLAY
        return element.name in DEFAULT_BLOCK_TAGS


# ------------------------------------------------------------- predicates

# Numbers and their usual companions: decimal/thousands separators, ranges,
# percent signs, currency symbols, section marks. A run of these is apparatus
# (a figure, a page range, "42%"), never prose worth spending tokens on.
_NUMERIC_TEXT_RE = re.compile(r"[\d.,:;%()\[\]§$£€¥+±/–—-]+")
_ROMAN_TOKEN_RE = re.compile(r"[IVXLCDM]{1,7}")
_DIGIT_TOKEN_RE = re.compile(r"\d+")


def classify_skip(text):
    """Return a skip reason for a text node, or None if it needs translation.

    Reasons are a closed set so the coverage report can account for every
    skipped character: whitespace / link / numeric / roman-ref / symbol.
    """
    t = text.strip()
    if not t:
        return "whitespace"
    if is_text_link(t):
        return "link"
    tokens = t.split()
    # Match on the whitespace-stripped whole, so "50 %" and "1 234,50 €" are
    # caught as well as "50%" — a lone "%" token carries no digit of its own.
    if _NUMERIC_TEXT_RE.fullmatch("".join(tokens)) and any(c.isdigit() for c in t):
        return "numeric"
    if all(
        _ROMAN_TOKEN_RE.fullmatch(tok) or _DIGIT_TOKEN_RE.fullmatch(tok)
        for tok in tokens
    ):
        return "roman-ref"
    if not any(c.isalnum() for c in t):
        return "symbol"
    return None


_CJK_RE = re.compile("[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")


# Prose-type blocks are exempt from the trivial filter: a standalone "No."
# paragraph is dialogue, not apparatus.
_TRIVIAL_EXEMPT_TAGS = frozenset(
    ["p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption"]
)


def is_trivial_unit(text, tag=None):
    """A unit not worth an API round-trip: manuscript sigla ("(a)", "M",
    "aa", "Kf"), stray initials, list markers.

    Fewer than 3 alphabetic characters — unless the text contains CJK, where
    two characters are a full word (lemo.epub's title is just 檸檬), or the
    unit is a prose-type block (dialogue like "No.", "Sí", "Да").
    """
    if tag in _TRIVIAL_EXEMPT_TAGS:
        return False
    if _CJK_RE.search(text):
        return False
    return sum(c.isalpha() for c in text) < 3


def _signature(element):
    classes = element.get("class") or []
    if classes:
        return f"{element.name}.{classes[0]}"
    return element.name


def _inline_hidden(element):
    """display:none via the style attribute, or the HTML ``hidden`` attribute.

    aria-hidden is deliberately NOT a signal: it hides from assistive tech
    only and is routinely set on perfectly visible content.
    """
    if element.has_attr("hidden"):
        return True
    style = element.get("style")
    if style:
        m = _CSS_DISPLAY_RE.search(style)
        return bool(m and m.group(1).lower() == "none")
    return False


def _ancestor_skip_reason(node, exclude_tags, resolver=None):
    hidden = False
    note = False
    for ancestor in node.parents:
        name = ancestor.name
        if name in NON_CONTENT_TAGS:
            return "non-content"
        if name in RUBY_ANNOTATION_TAGS:
            return "ruby"
        if name in exclude_tags:
            return "excluded-tag"
        epub_type = ancestor.get("epub:type") or ""
        if "pagebreak" in epub_type or "page-list" in epub_type:
            return "pagebreak"
        role = ancestor.get("role") or ""
        if role == "doc-pagebreak":
            return "pagebreak"
        if (
            any(tok in _NOTE_EPUB_TYPES for tok in epub_type.split())
            or role in _NOTE_ROLES
        ):
            note = True
        if not hidden and (
            (resolver is not None and resolver.display_of(ancestor) == "none")
            or _inline_hidden(ancestor)
        ):
            hidden = True
    if hidden and not note:
        # note-semantic content stays: popup-capable readers show CSS-hidden
        # footnote asides regardless of the stylesheet
        return "hidden"
    return None


def _is_single_roman(text):
    """A lone roman-numeral letter (C, I, V, X, L, D, M).

    Ambiguous by nature: it is a line-number marker in Gilgamesh (span.mr)
    but a drop cap in TOC entries ("C" of "Cover") or the pronoun "I".
    partition_soup resolves it with a per-signature vote: skip only when the
    subtree's class never carries translatable text anywhere in the file.
    """
    return len(text) == 1 and text in "IVXLCDM"


def _inline_subtree_root(node, resolver):
    """The outermost inline ancestor of a text node (below its block).

    Skip/translate is decided at this granularity: <span class="line_number">
    <span class="mr">I</span> <span class="mn">5</span></span> reads "I 5" as
    a whole and dies; <a><span>C</span><span>HAPTER </span><span>I</span></a>
    reads "CHAPTER I" as a whole and survives — including its roman numeral,
    which is content there, not apparatus.
    """
    root = node
    for ancestor in node.parents:
        if ancestor.name == "[document]" or resolver.is_block(ancestor):
            break
        root = ancestor
    return root


def _visible_text(root):
    """Subtree text for classification: ruby annotations excluded, invisible
    characters stripped, whitespace collapsed."""
    parts = []
    for node in root.descendants:
        if type(node) not in TEXT_NODE_TYPES:
            continue
        in_annotation = False
        for ancestor in node.parents:
            if ancestor.name in RUBY_ANNOTATION_TAGS:
                in_annotation = True
                break
            if ancestor is root:
                break
        if not in_annotation:
            parts.append(str(node))
    return _normalize_text("".join(parts))


def _separate_brs(root):
    """``one<br/>two`` must not read "onetwo": give each <br> a newline text
    node so unit text keeps the word boundary.  <wbr> is deliberately left
    alone (joining without a space is correct for word-break opportunities),
    as is <br> inside <pre>, where inserted whitespace would render."""
    for br in root.find_all("br"):
        if br.find_parent("pre") is None:
            br.insert_after("\n")


def _classify_subtrees(nodes_iter, resolver, exclude_tags):
    """Shared two-pass classification core for partition/clean-text.

    Yields nothing; returns (entries, subtree_info, sig_has_prose) where
    entries = [(node, chars, ancestor_reason_or_None, subtree_id)],
    subtree_info = {subtree_id: (reason_or_None, combined_text, signature)}.
    Whitespace-only nodes become 0-char "whitespace" entries: they carry no
    accountable text, but the callers keep them in unit text as glue so words
    don't merge across inline tag boundaries (``<b>one</b> <b>two</b>``).
    """
    entries = []
    subtree_info = {}
    sig_has_prose = set()
    for node in nodes_iter:
        stripped = str(node).strip()
        if not stripped:
            entries.append((node, 0, "whitespace", None))
            continue
        chars = len(stripped)
        reason = _ancestor_skip_reason(node, exclude_tags, resolver)
        if reason is not None:
            entries.append((node, chars, reason, None))
            continue
        root = _inline_subtree_root(node, resolver)
        rid = id(root)
        if rid not in subtree_info:
            if isinstance(root, Tag):
                combined = _visible_text(root)
                sig = _signature(root)
            else:
                combined = _normalize_text(stripped)
                sig = _signature(node.parent) if node.parent else ""
            subtree_reason = classify_skip(combined)
            subtree_info[rid] = (subtree_reason, combined, sig)
            if subtree_reason is None:
                sig_has_prose.add(sig)
        entries.append((node, chars, None, rid))
    return entries, subtree_info, sig_has_prose


def _resolve_entry(entry, subtree_info, sig_has_prose):
    """Final verdict for one text node: skip reason string, or None (keep)."""
    _node, _chars, ancestor_reason, rid = entry
    if ancestor_reason is not None:
        return ancestor_reason
    reason, combined, sig = subtree_info[rid]
    if reason is None:
        return None
    if _is_single_roman(combined) and sig in sig_has_prose:
        return None  # drop cap / <em>I</em>: class carries prose elsewhere
    return reason


def _nearest_block(node, resolver, stop=None):
    for ancestor in node.parents:
        if stop is not None and ancestor is stop:
            return None
        if resolver.is_block(ancestor):
            return ancestor
    return None


def _append_glue(parts, node):
    """A skipped node that carried surrounding whitespace still separates words.

    ``he <em>walks</em> [<em>back and forth</em>,]`` — the " [" node is skipped
    as a symbol, and dropping it whole would yield "walksback and forth". Only
    the separation survives, never the skipped characters themselves.
    """
    raw = str(node)
    if raw == raw.strip():
        return
    if parts and parts[-1][-1:].isspace():
        return
    parts.append(" ")


def unit_clean_text(element, resolver, exclude_tags=DEFAULT_EXCLUDE_TAGS):
    """Recompute a unit's translatable text from its element, statelessly.

    Keeps only text nodes that (a) are not skipped and (b) belong to this
    block — i.e. have no *other* block between themselves and `element`.
    """
    _separate_brs(element)
    text_nodes = (
        n
        for n in element.descendants
        if type(n) in TEXT_NODE_TYPES
        and _nearest_block(n, resolver, stop=element) is None
    )
    entries, subtree_info, sig_has_prose = _classify_subtrees(
        text_nodes, resolver, exclude_tags
    )
    parts = []
    for entry in entries:
        node, chars, _, _ = entry
        if _resolve_entry(entry, subtree_info, sig_has_prose) is None or chars == 0:
            parts.append(str(node))
        else:
            _append_glue(parts, node)
    return _normalize_text("".join(parts))


# -------------------------------------------------------------- partition


@dataclass
class Unit:
    element: object
    file_name: str
    signature: str
    text: str
    chars: int
    group_id: int = None
    nodes: list = None  # the exact text nodes this unit owns (same soup)


@dataclass
class FilePlan:
    file_name: str
    units: list = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    total_chars: int = 0
    # signature -> [units, chars, sample texts]: what the trivial filter ate,
    # kept so the LLM classifier can be asked about letter-bearing ones
    trivial: dict = field(default_factory=dict)


def partition_soup(
    soup,
    resolver,
    file_name,
    exclude_tags=DEFAULT_EXCLUDE_TAGS,
    overrides=None,
):
    """Partition every text node of a document into units and skip reasons."""
    fp = FilePlan(file_name=file_name)
    body = soup.body or soup
    _separate_brs(body)
    owners = {}  # id(block) -> [block, [node_texts], chars]
    order = []

    text_nodes = (n for n in body.descendants if type(n) in TEXT_NODE_TYPES)
    entries, subtree_info, sig_has_prose = _classify_subtrees(
        text_nodes, resolver, exclude_tags
    )

    for entry in entries:
        node, chars, _, _ = entry
        fp.total_chars += chars
        reason = _resolve_entry(entry, subtree_info, sig_has_prose)
        if reason is not None:
            if chars == 0:
                # whitespace glue: no accountable text, but it must appear in
                # the unit text so words don't merge across tag boundaries
                key = id(_nearest_block(node, resolver) or body)
                if key in owners:
                    owners[key][1].append(str(node))
            else:
                fp.skipped[reason] += chars
                key = id(_nearest_block(node, resolver) or body)
                if key in owners:
                    _append_glue(owners[key][1], node)
            continue

        block = _nearest_block(node, resolver) or body
        key = id(block)
        if key not in owners:
            owners[key] = [block, [], 0, []]
            order.append(key)
        owners[key][1].append(str(node))
        owners[key][2] += chars
        owners[key][3].append(node)

    for key in order:
        block, parts, chars, nodes = owners[key]
        text = _normalize_text("".join(parts))
        sig = _signature(block)
        forced = bool(overrides) and overrides.get(sig) == "force-translate"
        if not forced and is_trivial_unit(text, tag=block.name):
            fp.skipped["trivial"] += chars
            row = fp.trivial.setdefault(sig, [0, 0, []])
            row[0] += 1
            row[1] += chars
            if text not in row[2] and len(row[2]) < TRIVIAL_SAMPLE_CAP:
                row[2].append(text)
            continue
        fp.units.append(
            Unit(
                element=block,
                file_name=file_name,
                signature=sig,
                text=text,
                chars=chars,
                nodes=nodes,
            )
        )

    if overrides:
        kept = []
        for unit in fp.units:
            action = overrides.get(unit.signature)
            if action in ("skip", "llm-skip"):
                reason = "user-excluded" if action == "skip" else "llm-excluded"
                fp.skipped[reason] += unit.chars
            else:
                kept.append(unit)
        fp.units = kept

    return fp


# ------------------------------------------------------------ poetry runs

POETRY_MIN_RUN = 3
POETRY_MAX_MEDIAN_CHARS = 70


def _run_compatible(prev, unit):
    """Consecutive units continue a run if they are structural siblings.

    Same parent, or (Gilgamesh) parents that are themselves same-signature
    siblings — verse lines living in per-stanza wrapper divs.
    """
    if prev.element.name != unit.element.name:
        return False
    pp, up = prev.element.parent, unit.element.parent
    if pp is up:
        return True
    return _signature(pp) == _signature(up) and pp.parent is up.parent


def assign_poetry_groups(units, group_size=8, next_group_id=0):
    """Mark runs of short sibling units as poetry and window them for context.

    A qualifying run (>= POETRY_MIN_RUN units, median line length <
    POETRY_MAX_MEDIAN_CHARS) is split into groups at stanza boundaries —
    parent change, or recurrence of a minority "stanza head" class
    (calibre_14 in Animal Farm) — capped at `group_size` lines.
    Returns the next unused group id.
    """
    runs = []
    current = []
    for unit in units:
        if current and _run_compatible(current[-1], unit):
            current.append(unit)
        else:
            if current:
                runs.append(current)
            current = [unit]
    if current:
        runs.append(current)

    for run in runs:
        if len(run) < POETRY_MIN_RUN:
            continue
        if statistics.median(u.chars for u in run) >= POETRY_MAX_MEDIAN_CHARS:
            continue

        head_sig = run[0].signature
        sig_counts = Counter(u.signature for u in run)
        head_marks_stanza = (
            len(sig_counts) > 1
            and sig_counts[head_sig] >= 2
            and sig_counts[head_sig] / len(run) < 0.4
        )

        group = [run[0]]
        groups = [group]
        for prev, unit in zip(run, run[1:]):
            boundary = (
                unit.element.parent is not prev.element.parent
                or (head_marks_stanza and unit.signature == head_sig)
                or len(group) >= group_size
            )
            if boundary:
                group = [unit]
                groups.append(group)
            else:
                group.append(unit)

        for group in groups:
            for unit in group:
                unit.group_id = next_group_id
            next_group_id += 1

    return next_group_id


def partition_file(
    soup,
    resolver,
    file_name,
    exclude_tags=DEFAULT_EXCLUDE_TAGS,
    overrides=None,
    poetry_group_size=8,
    next_group_id=0,
):
    """partition_soup + poetry grouping; the one entry point loaders use."""
    fp = partition_soup(
        soup, resolver, file_name, exclude_tags=exclude_tags, overrides=overrides
    )
    next_group_id = assign_poetry_groups(
        fp.units, group_size=poetry_group_size, next_group_id=next_group_id
    )
    return fp, next_group_id


# ------------------------------------------------------------------- plan


class TranslationPlan:
    def __init__(self, files, exclude_tags, poetry_group_size):
        self.files = files
        self.exclude_tags = tuple(exclude_tags)
        self.poetry_group_size = poetry_group_size

    @property
    def total_chars(self):
        return sum(f.total_chars for f in self.files)

    @property
    def translate_chars(self):
        return sum(u.chars for f in self.files for u in f.units)

    @property
    def skipped_totals(self):
        total = Counter()
        for f in self.files:
            total.update(f.skipped)
        return total

    @property
    def coverage(self):
        total = self.total_chars
        if total == 0:
            return 1.0
        return self.translate_chars / total

    def signature_rows(self):
        stats = {}
        for f in self.files:
            for u in f.units:
                row = stats.setdefault(
                    u.signature,
                    {
                        "signature": u.signature,
                        "units": 0,
                        "chars": 0,
                        "sample": u.text,
                    },
                )
                row["units"] += 1
                row["chars"] += u.chars
        rows = sorted(stats.values(), key=lambda r: -r["chars"])
        total = self.total_chars or 1
        for row in rows:
            row["pct"] = round(100 * row["chars"] / total, 1)
            row["action"] = "translate"
            row["sample"] = row["sample"][:60]
        return rows

    def trivial_rows(self):
        """Aggregate what the trivial filter skipped, by signature."""
        agg = {}
        for f in self.files:
            for sig, (units, chars, samples) in f.trivial.items():
                row = agg.setdefault(
                    sig, {"signature": sig, "units": 0, "chars": 0, "samples": []}
                )
                row["units"] += units
                row["chars"] += chars
                for s in samples:
                    if (
                        s not in row["samples"]
                        and len(row["samples"]) < TRIVIAL_SAMPLE_CAP
                    ):
                        row["samples"].append(s)
        return agg

    def report(self, max_rows=25):
        lines = []
        rows = self.signature_rows()
        lines.append(
            f"Translation plan: {len(self.files)} documents, "
            f"{self.total_chars} chars, coverage "
            f"{100 * self.coverage:.1f}%"
        )
        lines.append(f"{'signature':32s} {'units':>6s} {'chars':>9s} {'%':>6s}  sample")
        for row in rows[:max_rows]:
            lines.append(
                f"{row['signature']:32s} {row['units']:6d} {row['chars']:9d} "
                f"{row['pct']:6.1f}  {row['sample']}"
            )
        if len(rows) > max_rows:
            hidden = sum(r["chars"] for r in rows[max_rows:])
            lines.append(f"... {len(rows) - max_rows} more signatures ({hidden} chars)")
        skipped = self.skipped_totals
        if skipped:
            skip_desc = ", ".join(f"{k}={v}" for k, v in skipped.most_common())
            lines.append(f"skipped: {skip_desc}")
        poetry_units = sum(
            1 for f in self.files for u in f.units if u.group_id is not None
        )
        lines.append(
            f"poetry-grouped units: {poetry_units} "
            f"(window <= {self.poetry_group_size} lines)"
        )
        return "\n".join(lines)

    def to_dict(self, book_path=None, llm_actions=None):
        signatures = self.signature_rows()
        if llm_actions:
            # LLM verdicts must round-trip as ordinary signature actions:
            # rows this plan was built without (resurrected trivia) are added,
            # and every verdict is marked as machine-decided so user edits
            # stay distinguishable
            by_sig = {r["signature"]: r for r in signatures}
            trivial = self.trivial_rows()
            total = self.total_chars or 1
            for sig, action in llm_actions.items():
                row = by_sig.get(sig)
                if row is None:
                    t = trivial.get(sig, {"units": 0, "chars": 0, "samples": []})
                    row = {
                        "signature": sig,
                        "units": t["units"],
                        "chars": t["chars"],
                        "sample": (t["samples"] or [""])[0][:60],
                        "pct": round(100 * t["chars"] / total, 1),
                    }
                    signatures.append(row)
                row["action"] = action
                row["decided_by"] = "llm"
        data = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "coverage": self.coverage,
            "total_chars": self.total_chars,
            "translate_chars": self.translate_chars,
            "skipped": dict(self.skipped_totals),
            "exclude_tags": list(self.exclude_tags),
            "poetry_group_size": self.poetry_group_size,
            "signatures": signatures,
            "book_sha256": _sha256(book_path) if book_path else None,
        }
        return data

    def save_json(self, path, book_path=None, llm_actions=None):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                self.to_dict(book_path=book_path, llm_actions=llm_actions),
                f,
                ensure_ascii=False,
                indent=1,
            )


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_plan_overrides(json_path, book_path):
    """Load user-edited signature actions from a saved plan JSON.

    Returns {signature: action} for actions != translate.  A stale plan
    (book hash mismatch) is refused loudly rather than half-applied.
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    saved_hash = data.get("book_sha256")
    if saved_hash and saved_hash != _sha256(book_path):
        raise ValueError(
            f"{json_path} was generated from a different book "
            "(sha256 mismatch); delete it or regenerate with --plan-dry-run"
        )
    saved_version = data.get("schema_version")
    if saved_version != PLAN_SCHEMA_VERSION:
        # warn only: overrides are signature-keyed, so the worst case is a
        # signature that no longer exists and simply stops matching
        print(
            f"warning: {json_path} was written by plan schema "
            f"{saved_version}, current is {PLAN_SCHEMA_VERSION}; signatures "
            f"may have shifted — regenerate with --plan-dry-run to be sure"
        )
    unknown = [
        (s.get("signature"), s["action"])
        for s in data.get("signatures", [])
        if s.get("action") and s["action"] not in VALID_PLAN_ACTIONS
    ]
    if unknown:
        listed = ", ".join(f"{sig}: {act!r}" for sig, act in unknown)
        raise ValueError(
            f"{json_path} carries unknown action(s) — {listed}. "
            f"Valid actions are: {', '.join(sorted(VALID_PLAN_ACTIONS))}. "
            f"Fix the misspelled action in that file, or delete the file "
            f"and rerun to regenerate a fresh plan."
        )
    return {
        s["signature"]: s["action"]
        for s in data.get("signatures", [])
        if s.get("action") and s["action"] != "translate"
    }


class BookCss:
    """Per-document CSS resolution.

    A chapter only obeys the stylesheets it actually links (<link
    rel="stylesheet">, resolved relative to the document) plus its inline
    <style> blocks — a chapter-local `.note {display:none}` must not hide
    same-class prose in other chapters. Documents that declare no
    stylesheets fall back to the merge of every sheet in the book.
    """

    def __init__(self, book):
        import posixpath

        self._posixpath = posixpath
        self.by_path = {}
        for item in book.get_items():
            name = getattr(item, "file_name", "") or ""
            media = getattr(item, "media_type", "") or ""
            if media == "text/css" or name.lower().endswith(".css"):
                try:
                    self.by_path[posixpath.normpath(name)] = parse_css_display(
                        item.content.decode("utf-8", "ignore")
                    )
                except Exception as e:
                    print(f"warning: could not parse stylesheet {name}: {e}")
        self.global_maps = list(self.by_path.values())

    def resolver_for(self, file_name, soup):
        posixpath = self._posixpath
        base = posixpath.dirname(file_name)
        maps = []
        for link in soup.find_all("link"):
            rel = link.get("rel") or []
            if isinstance(rel, str):
                rel = [rel]
            if "stylesheet" not in [r.lower() for r in rel] and (
                (link.get("type") or "").lower() != "text/css"
            ):
                continue
            href = (link.get("href") or "").split("#")[0]
            if not href:
                continue
            target = posixpath.normpath(posixpath.join(base, href))
            if target in self.by_path:
                maps.append(self.by_path[target])
        for style in soup.find_all("style"):
            maps.append(parse_css_display(style.get_text()))
        if not maps:
            maps = self.global_maps
        return DisplayResolver(maps)


def is_fixed_layout(book):
    """EPUB 3 ``rendition:layout: pre-paginated`` (fixed layout).

    FXL text is absolutely positioned in boxes sized for the original words;
    translation breaks the layout, and image-only FXL books (comics) have no
    text at all — callers warn, the coverage gate handles the rest.
    """
    try:
        metas = book.get_metadata(None, "meta")
    except KeyError:
        return False
    for value, attrs in metas:
        if (attrs or {}).get("property") == "rendition:layout":
            return "pre-paginated" in (value or "")
    return False


def build_plan(
    book,
    exclude_tags=DEFAULT_EXCLUDE_TAGS,
    poetry_group_size=8,
    overrides=None,
    only_files=None,
    exclude_files=None,
):
    """Build a TranslationPlan for an ebooklib book object.

    File-filter semantics mirror the loader's process_item: an only-list
    wins outright; the exclude-list applies only when no only-list is given.
    """
    from bs4 import BeautifulSoup

    css_index = BookCss(book)
    files = []
    next_group_id = 0
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        if only_files:
            if item.file_name not in only_files:
                continue
        elif exclude_files and item.file_name in exclude_files:
            continue
        soup = BeautifulSoup(item.content, "html.parser")
        fp, next_group_id = partition_file(
            soup,
            css_index.resolver_for(item.file_name, soup),
            item.file_name,
            exclude_tags=exclude_tags,
            overrides=overrides,
            poetry_group_size=poetry_group_size,
            next_group_id=next_group_id,
        )
        files.append(fp)
    return TranslationPlan(files, exclude_tags, poetry_group_size)
