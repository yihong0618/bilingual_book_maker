"""Coverage-complete translation planning for EPUBs.

Instead of selecting tags to translate (`--translate-tags p`) — which silently
drops any text living in unknown markup — this module *partitions* every text
node in each document's rendered body into exactly one of two states:

    TRANSLATE(unit)   |   SKIP(reason)

("body" literally: a document with a <body> is walked from there, so <head>
text — `<title>`, metadata — is outside the partition and outside the
invariant below. It is never rendered to the reader.)

Units are formed at the nearest block-level ancestor of each translatable text
node, where block/inline is resolved from the book's own stylesheets (simple
selectors only) with HTML defaults as fallback.  A block that contains another
text-bearing block is never itself credited with that text, so double
translation is impossible by construction.

Invariant (checked by tests, reported to users):
    total_chars == sum(unit.chars) + sum(skipped[reason])

Runs of short sibling units (poetry) are grouped into stanza-aligned windows
so they can be sent to the model together for context.

Partitioning is *greedy* (schema 3): only structurally free reasons skip text
(whitespace, links, symbols, hidden/ruby/pagebreak/excluded markup). Content
heuristics — numeric runs, roman numerals, sub-3-letter units — were removed
after measuring that they reclaimed 0-6% of characters while silently
dropping real content; deciding what is worth translating is the classifier's
job now (see the classify package and --plan-classify).
"""

import hashlib
import os
import posixpath
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

from .helper import is_pure_url
from .ledger import (
    # re-exported: the plan file's schema version is this module's API too —
    # callers ask .plan about the plan, not about its storage layer
    PLAN_SCHEMA_VERSION,  # noqa: F401
    VALID_ACTIONS,
    Ledger,
    make_key,
)

# Every action a plan JSON may carry; anything else is a typo and must fail
# loud — a misspelled "skip" silently treated as translate would quietly undo
# the user's decision. Schema 4 has exactly two, plus null for "not yet
# decided": who decided lives in `decided_by`, not inside the action.
VALID_PLAN_ACTIONS = VALID_ACTIONS


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

# Containers whose text renders nothing at all. A skipped node inside one of
# these does not separate the runs around it, so it must not split a segment.
INVISIBLE_CONTAINERS = frozenset(["script", "style", "template", "head", "title"])

# Elements that render something of their own while contributing no text.
# One sitting between two owned text nodes is a barrier: flat replacement
# would leave it in place and move the whole translation past it, so a
# mid-sentence image would surface at the end of the paragraph. Textless
# *non*-rendering elements (an empty <span>, an id-only <a>) are deliberately
# absent — splitting a sentence around an invisible anchor would cost
# translation quality to prevent nothing.
RENDERED_VOID_TAGS = frozenset(
    [
        "img",
        "svg",
        "math",
        "video",
        "audio",
        "canvas",
        "object",
        "iframe",
        "embed",
        "picture",
        "hr",
        "input",
    ]
)

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


_NON_SCREEN_MEDIA_RE = re.compile(r"\b(print|speech|aural)\b")
_PLAIN_SCREEN_RE = re.compile(r"(only\s+)?(screen|all)")


def _media_verdict(prelude):
    """Classify one ``@media`` prelude: unconditional, conditional, or gone.

    Comma-separated queries are *alternatives* — the rule applies if any of
    them matches — so they must be judged separately and OR'd. Reading the
    whole prelude at once made ``@media print, screen`` look like a print
    rule and dropped a stylesheet that every screen reader obeys.
    """
    query = prelude.split("@media", 1)[-1].strip().lower()
    if not query:
        return "unconditional", None
    surviving = []
    for alternative in query.split(","):
        alternative = alternative.strip()
        if not alternative:
            continue
        # `not` negates the whole query, so it inverts every verdict below:
        # `not print` applies on every medium *except* print — which is to
        # say, on the screen an ebook reader renders. Matching "print" as a
        # substring read that as a print rule and dropped it, and a
        # `display:none` that genuinely hides text on screen reached the
        # classifier as ordinary visible prose.
        negated = alternative.startswith("not ")
        core = alternative[4:].strip() if negated else alternative
        if _PLAIN_SCREEN_RE.fullmatch(core):
            if negated:
                continue  # "not screen" / "not all": never applies here
            # applies to every screen; nothing to defer to a device
            return "unconditional", None
        if _NON_SCREEN_MEDIA_RE.search(core):
            if not negated:
                continue
            # "not print": true on screen, and true unconditionally there
            return "unconditional", None
        surviving.append(alternative)
    if not surviving:
        return "drop", None
    # feature queries (max-width, orientation), device-specific media
    # (amzn-kf8), unknown media: true on some devices, false on others
    return "conditional", "@media " + ", ".join(surviving)


def _at_rule_verdict(prelude):
    prelude = prelude.strip()
    lowered = prelude.lower()
    if lowered.startswith("@media"):
        return _media_verdict(lowered)
    if lowered.startswith("@supports"):
        # a feature query is conditional by definition: the reading system
        # that lacks the feature never applies the rule
        return "conditional", " ".join(prelude.split())
    # @font-face / @page / @keyframes and friends declare no selectors we
    # can resolve display through
    return "drop", None


def _flatten_at_rules(css_text, condition=None):
    """Split a stylesheet into unconditional rules and conditional ones.

    Returns ``(unconditional_css, [(condition, css), ...])``. Conditional
    rules are *kept*, not applied: a ``display:none`` that only holds on a
    narrow screen or a particular Kindle must never hide text from every
    reader, but the condition is real evidence about what the text is, so
    it travels to the classifier instead of being thrown away.

    Nested conditions AND-compose (``@media screen && @supports (...)``).
    """
    uncond, cond = [], []

    def emit(text):
        if condition is None:
            uncond.append(text)
        elif text.strip():
            cond.append((condition, text))

    i, n = 0, len(css_text)
    while i < n:
        at = css_text.find("@", i)
        if at == -1:
            emit(css_text[i:])
            break
        emit(css_text[i:at])
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
        verdict, label = _at_rule_verdict(css_text[at:brace])
        if verdict != "drop":
            inner = condition
            if verdict == "conditional":
                inner = label if condition is None else f"{condition} && {label}"
            body_uncond, body_cond = _flatten_at_rules(
                css_text[brace + 1 : j - 1], inner
            )
            uncond.append(body_uncond)
            cond.extend(body_cond)
        i = j
    return " ".join(uncond), cond


def _display_rules(css_text):
    """{(tag_or_None, class_or_None): display} for one flat block of CSS."""
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
            key = (tag, m.group(2))
            # re-declaring a selector moves it to the end, so iteration
            # order is last-declaration order — which is what decides
            # between equal-specificity rules
            mapping.pop(key, None)
            mapping[key] = value
    return mapping


def parse_css_display(css_text):
    """Extract ``display`` values for simple selectors (tag, .class, tag.class).

    Returns ``(unconditional_rules, conditional_rules)`` where the second maps
    the same selector keys to the list of conditions under which a rule
    applies. Anything fancier (descendant combinators, ids, pseudo-classes)
    is ignored — HTML defaults then apply, which is the right failure mode
    for our purpose.
    """
    css_text = re.sub(r"/\*.*?\*/", " ", css_text, flags=re.S)
    unconditional, conditional = _flatten_at_rules(css_text)
    rules = _display_rules(unconditional)
    conditions = {}
    for condition, text in conditional:
        for key in _display_rules(text):
            conditions.setdefault(key, [])
            if condition not in conditions[key]:
                conditions[key].append(condition)
    return rules, conditions


# display values we understand. An unrecognised one is not a decision: it
# falls through to the next precedence tier rather than silently meaning
# "inline".
_KNOWN_DISPLAY = BLOCKISH_DISPLAY | frozenset(
    ["none", "inline", "inline-block", "inline-flex", "inline-grid", "contents"]
)


def _inline_display(element):
    """A ``style="display:…"`` value, which outranks every stylesheet."""
    style = element.get("style")
    if not style:
        return None
    m = _CSS_DISPLAY_RE.search(style)
    return m.group(1).lower() if m else None


class DisplayResolver:
    """Resolve whether an element renders block-level.

    Precedence: css(tag.class) > css(.class) > css(tag) > HTML default —
    i.e. real CSS specificity. Within one tier the *last declaration wins*,
    as in CSS; the order the class tokens happen to appear in the HTML
    attribute has no say (``class="a b"`` and ``class="b a"`` must resolve
    identically). Later stylesheets win over earlier ones.
    """

    def __init__(self, css_maps, conditional_maps=()):
        self.rules = {}
        for m in css_maps:
            for key, value in m.items():
                self.rules.pop(key, None)  # re-declaration moves to the end
                self.rules[key] = value
        self._order = {key: i for i, key in enumerate(self.rules)}
        self.conditional = {}
        for m in conditional_maps:
            for key, conditions in m.items():
                bucket = self.conditional.setdefault(key, [])
                for condition in conditions:
                    if condition not in bucket:
                        bucket.append(condition)

    def _last_declared(self, keys):
        """The candidate declared latest in the stylesheets, or None."""
        best = None
        for key in keys:
            if key in self.rules and (
                best is None or self._order[key] > self._order[best]
            ):
                best = key
        return best

    def display_of(self, element):
        """The element's resolved display, or None to fall through.

        Precedence: style attribute > css(tag.class) > css(.class) >
        css(tag). A value we do not recognise is skipped rather than
        obeyed, so a typo cannot turn a paragraph inline.
        """
        inline = _inline_display(element)
        if inline in _KNOWN_DISPLAY:
            return inline
        tag = element.name
        classes = element.get("class") or []
        for tier in ([(tag, c) for c in classes], [(None, c) for c in classes]):
            key = self._last_declared(tier)
            if key is not None and self.rules[key] in _KNOWN_DISPLAY:
                return self.rules[key]
        value = self.rules.get((tag, None))
        return value if value in _KNOWN_DISPLAY else None

    def conditions_for(self, element):
        """Conditions under which *some* rule targets this element.

        Evidence only. A signature that a stylesheet hides on one device and
        shows on another is exactly the judgment call the classifier exists
        to make, and it cannot make it without knowing.
        """
        found = []
        tag = element.name
        classes = element.get("class") or []
        for key in (
            [(tag, c) for c in classes] + [(None, c) for c in classes] + [(tag, None)]
        ):
            for condition in self.conditional.get(key, ()):
                if condition not in found:
                    found.append(condition)
        return found

    def is_block(self, element):
        display = self.display_of(element)
        if display is not None:
            # `contents` and every inline-* value keep the element out of the
            # block partition; its descendants are owned as usual
            return display in BLOCKISH_DISPLAY
        return element.name in DEFAULT_BLOCK_TAGS


# ------------------------------------------------------------- predicates


def classify_skip(text):
    """Return a skip reason for a whole segment, or None if it needs
    translation.

    Only *structural* reasons live here — whitespace / link / symbol — each
    free to decide and safe to act on. Content heuristics (numbers, roman
    numerals) were deleted in schema 3: they reclaimed 0-6% of characters
    while silently dropping real text, and judging content is now the
    classifier's job (--plan-classify). Reasons stay a closed set so the
    coverage report can account for every skipped character.

    Judged over the *segment*, never over a lone text node: markup routinely
    isolates a comma (``<em>Hello</em>, <strong>world</strong>!``), and
    character-class rules applied per node charged that comma and the
    exclamation mark to "symbol" — deleting grammar from the model's input.
    A segment keeps its punctuation because the segment as a whole has
    letters in it; a standalone symbol run still dies.
    """
    t = text.strip()
    if not t:
        return "whitespace"
    if is_pure_url(t):
        return "link"
    if not any(c.isalnum() for c in t):
        return "symbol"
    return None


def _signature(element):
    """``tag`` or ``tag.class1.class2`` with classes sorted.

    Sorted and complete, because neither the order nor the count of class
    tokens is semantic: ``class="a b"`` and ``class="b a"`` are the same
    element to CSS, and keying on the first token alone made them two
    different questions — and merged genuinely different elements that
    happened to share a first class.
    """
    classes = sorted(element.get("class") or [])
    if classes:
        return f"{element.name}." + ".".join(classes)
    return element.name


# Decoration a folio or a verse number routinely wears. Stripped from the
# ends before the numeral test, never from the middle: "1984 was a cold year"
# must stay one prose token sequence.
_NUMERAL_DECORATION = " \t\r\n[](){}<>.,;:!?*·§¶\"'‘’“”«»—–-−_/\\|"
# Separators *inside* a numeric run: "12-13", "1. 2. 3", "4/5".
_NUMERAL_SEPARATORS = re.compile(r"[\s.,;:/\-–—−]+")

# Roman numerals are deliberately NOT detected. Measured over the 45-book
# corpus, a strict canonical matcher produced five rows and four of them were
# wrong: `h3.groupletter` (an index's group letters C, D, I, L, M) and
# `td.character` (character specimens C, I, V, d, m) are alphabet labels that
# happen to spell numerals, and splitting them left A, B, E… in one row and
# C, D, I… in another — a worse partition than the mixed row it replaced.
# The one true hit was 2 units / 4 chars. Roman folios stay a known
# limitation; see docs/260813-fix-FOLIO_SIGNATURE_SUFFIX.md.


def _numeral_kind(text):
    """``"num"`` if this run is made of numbers rather than words, else None.

    Evidence for a *key*, never a verdict. Nothing here skips anything: a
    verse number is content, and schema 3 deleted the content heuristics
    that used to decide otherwise. What this restores is only the classifier's
    ability to answer the question at all — `<h3>190</h3>` and
    `<h3>BIBLIOGRAPHY</h3>` are one row without it, and no single verdict is
    right for both.

    ASCII digits only. `str.isdigit()` is true of ideographic and enclosed
    forms too, which filed jlreq's character-specimen table (`０`, `①`, `⓶`,
    `❸`) as page numbers — those are the book's subject matter, not its
    apparatus.
    """
    core = text.strip(_NUMERAL_DECORATION)
    if not core:
        return None
    tokens = [t for t in _NUMERAL_SEPARATORS.split(core) if t]
    if tokens and all(t.isascii() and t.isdigit() for t in tokens):
        return "num"
    return None


def _owner_numeral_kinds(runs):
    """{id(owner): kind} for owners whose every surviving run is that kind.

    Decided per *owner* rather than per run so that one element always
    answers to one row. The alternative splits an owner's runs across two
    keys, and an inline row's ``parent_key`` — which the disposition reads to
    ask whether any block holding its text survived — can then name a row
    that holds none of it.

    Mixed owners keep the bare signature: a run this cannot name is exactly
    the case the classifier should still see whole.
    """
    kinds = {}
    for owner, text in runs:
        key = id(owner)
        # A second run means this owner's text was cut by something rendering
        # between the pieces — an excluded <code>/<sup>, a nested block. Each
        # piece may read as a numeral while the sentence it came from does
        # not: epub30-spec's `0: <code>PK…</code>, 30: <code>mimetype</code>`
        # leaves the fragments "0:", ", 30:", ", 38:". A folio is a whole
        # element's text, so require that.
        kinds[key] = None if key in kinds else _numeral_kind(text)
    return {k: v for k, v in kinds.items() if v is not None}


# Whitespace, because a class token cannot contain any: `class="a b"` is two
# tokens, so no element can ever produce a signature holding this separator.
# `#` looked natural and was not safe — linear-algebra.epub carries 1152 real
# class names with `#` in them (`span.broken.fcla-xml-2.30li6.xhtml#x7-6000…`),
# and a suffix a document can spell itself is a key collision waiting to
# merge two different questions into one row.
_ROW_SIGNATURE_SEPARATOR = " #"


def _row_signature(element, kind):
    """The ledger key's signature: element shape, plus what its text is made of."""
    signature = _signature(element)
    return f"{signature}{_ROW_SIGNATURE_SEPARATOR}{kind}" if kind else signature


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


def inline_subtree_root(node, resolver):
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


def _text_node_records(body, resolver, exclude_tags):
    """One document-order pass: every text node, its size, why it is skipped
    (if it is), and which block owns it."""
    records = []
    for node in body.descendants:
        if type(node) not in TEXT_NODE_TYPES:
            continue
        stripped = str(node).strip()
        if not stripped:
            # no accountable characters, but still glue inside its owner
            records.append(
                (node, 0, "whitespace", _nearest_block(node, resolver) or body)
            )
            continue
        reason = _ancestor_skip_reason(node, exclude_tags, resolver)
        owner = _nearest_block(node, resolver) or body
        records.append((node, len(stripped), reason, owner))
    return records


def _nearest_block(node, resolver, stop=None):
    for ancestor in node.parents:
        if stop is not None and ancestor is stop:
            return None
        if resolver.is_block(ancestor):
            return ancestor
    return None


# ----------------------------------------------------------- run barriers


def _renders_between(node, owner, resolver):
    """Does this retained-but-unowned text actually render between two runs?

    Hidden text and ruby annotations stay in the DOM without separating the
    words around them — furigana renders *above* its base, a display:none
    span renders nowhere — so neither may split a segment. Splitting on them
    would fragment exactly the books that use them most (Japanese verse,
    every EPUB with popup notes).
    """
    for ancestor in node.parents:
        if ancestor is owner:
            return True
        if (
            ancestor.name in RUBY_ANNOTATION_TAGS
            or ancestor.name in INVISIBLE_CONTAINERS
        ):
            return False
        if resolver.display_of(ancestor) == "none" or _inline_hidden(ancestor):
            return False
    return True


def _iter_owner_events(owner, owned_ids, resolver):
    """Walk one owner's own inline content, in document order.

    Yields ``(kind, node, barrier)`` where kind is:

    ``owned``      a text node this owner's segments are made of;
    ``glue``       whitespace — carried into segment text so words do not
                   merge across inline tag boundaries;
    ``invisible``  retained text that renders nothing here (hidden, ruby
                   annotation, <script>): it neither joins the text nor
                   separates it, but its surrounding whitespace still does.

    Text under a descendant block belongs to another owner and is not
    yielded at all; crossing one only records a barrier.

    ``barrier`` is what makes two owned runs *non-adjacent in the rendered
    document*, so one translation written across both would land in the
    wrong place. Three kinds:

    ``block``    a descendant block element — its text belongs to another
                 owner and renders between these two runs;
    ``skipped``  a retained node that renders between them without being in
                 either one (skip-classified visible text, an excluded
                 <code>, a rendered void like <img>);
    ``br``       a line break outside <pre>.

    Whitespace-only text never forms a barrier: it is glue, and gluing is
    exactly what keeps words apart across inline tags.

    Inline *markup* boundaries are deliberately not barriers. Splitting
    ``See <a href="ch3">chapter 3</a> for details.`` into three would
    fragment prose at every cross-reference — common in exactly the
    reference books this mode exists for — and each fragment translates
    worse than the sentence. Placement is solved instead by the insertion
    container rule; the cost is that single-translate mode cannot keep
    inline link markup *inside* a rewritten sentence (bilingual mode keeps
    the original, links and all). Preserving it needs the placeholder
    protocol the 260811 review ranked highest-complexity and did not
    recommend for v1.
    """
    state = {"pending": None}

    def walk(element):
        for child in element.children:
            if isinstance(child, Tag):
                if resolver.is_block(child):
                    # a nested block owns its own text and renders between
                    # this owner's runs
                    state["pending"] = state["pending"] or "block"
                    continue
                if child.name == "br":
                    if child.find_parent("pre") is None:
                        state["pending"] = state["pending"] or "br"
                    continue
                if child.name in RENDERED_VOID_TAGS:
                    # Replaced elements usually hold nothing, but <canvas>,
                    # <object>, <video> and <iframe> may carry fallback text
                    # that a reading system without the feature *does* show.
                    # It is real content: it has to be reachable, and it has
                    # to be accounted for. A barrier on each side keeps it
                    # separate from the prose it interrupts.
                    state["pending"] = state["pending"] or "skipped"
                    if any(
                        id(n) in owned_ids
                        for n in child.descendants
                        if type(n) in TEXT_NODE_TYPES
                    ):
                        yield from walk(child)
                        state["pending"] = state["pending"] or "skipped"
                    continue
                yield from walk(child)
                continue
            if type(child) not in TEXT_NODE_TYPES:
                continue
            if id(child) in owned_ids:
                barrier = state["pending"]
                state["pending"] = None
                yield "owned", child, barrier
            elif not str(child).strip():
                yield "glue", child, None
            elif _renders_between(child, owner, resolver):
                state["pending"] = state["pending"] or "skipped"
            else:
                yield "invisible", child, None

    yield from walk(owner)


def _owner_segments(owner, owned_ids, resolver):
    """Split one owner's owned text into maximal barrier-free runs.

    Returns ``[(nodes, text), ...]`` in document order. Each run is a place
    in the document a translation can actually be written back to.
    """
    segments = []
    nodes, parts = [], []
    for kind, node, barrier in _iter_owner_events(owner, owned_ids, resolver):
        if kind == "owned":
            if barrier is not None and nodes:
                segments.append((nodes, parts))
                nodes, parts = [], []
            nodes.append(node)
            parts.append(str(node))
        elif not nodes:
            # leading whitespace of a run carries no separation of its own
            continue
        elif kind == "glue":
            parts.append(str(node))
        else:  # invisible: contributes no characters, may still separate
            _append_glue(parts, node)
    if nodes:
        segments.append((nodes, parts))
    return [(ns, _normalize_text("".join(ps))) for ns, ps in segments]


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


def element_segments(element, resolver, exclude_tags=DEFAULT_EXCLUDE_TAGS):
    """The translatable segments this element owns, statelessly recomputed.

    Same rules the partition uses, over one element: text belonging to a
    nested block is another owner's, ancestor-skipped text is nobody's, and
    what is left splits at run barriers.
    """
    _separate_brs(element)
    owned = []
    for node in element.descendants:
        if type(node) not in TEXT_NODE_TYPES:
            continue
        if not str(node).strip():
            continue
        if _nearest_block(node, resolver, stop=element) is not None:
            continue
        if _ancestor_skip_reason(node, exclude_tags, resolver) is None:
            owned.append(node)
    owned_ids = {id(n) for n in owned}
    return [
        (nodes, text)
        for nodes, text in _owner_segments(element, owned_ids, resolver)
        if classify_skip(text) is None
    ]


def unit_clean_text(element, resolver, exclude_tags=DEFAULT_EXCLUDE_TAGS):
    """An element's translatable text, all its segments joined.

    Kept for verification and tests: a single-segment element (the ordinary
    case) recomputes exactly its unit's text.
    """
    return " ".join(
        text for _nodes, text in element_segments(element, resolver, exclude_tags)
    )


# -------------------------------------------------------------- partition


@dataclass
class Unit:
    """One contiguous run of an owner's text: a place a translation fits.

    Not "a block's text" — a block interrupted by a nested paragraph owns
    two runs, and writing one translation across both put the second half
    before the paragraph that separated them.
    """

    element: object
    file_name: str
    signature: str
    text: str
    chars: int
    group_id: int = None
    nodes: list = None  # the exact text nodes this unit owns (same soup)
    run_index: int = 0  # which of its owner's runs, in document order
    ordinal: int = 0  # position among the file's units, in document order
    # how many runs the owner produced in total, counting ones this plan
    # dropped: a translated *clone* of the owner is only safe when the owner
    # holds exactly one run, and a dropped run still renders between the
    # others, so it counts here even though it never became a unit
    owner_runs: int = 1
    # the document's display resolver: insertion has to ask the same
    # block/inline questions the partition asked, and asking a different
    # cascade would place the translation somewhere the plan never meant
    resolver: object = None

    @property
    def key(self):
        return make_key("block", self.signature)


@dataclass
class FilePlan:
    file_name: str
    units: list = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    total_chars: int = 0
    # the document's own resolver, kept so callers can re-ask structural
    # questions (barriers, block-ness) about this file's units without
    # rebuilding the CSS cascade
    resolver: object = None
    # every unit the document partitions into, *before* any decision is
    # applied — the ledger is built from these, so a skipped signature keeps
    # its row, its evidence and its provenance
    all_units: list = field(default_factory=list)
    # one entry per classed inline element that owns text: the rows through
    # which line numbers, sigla and other in-sentence apparatus can be ruled
    # on without hard-coding what their class names mean
    inline_rows: list = field(default_factory=list)


class UnsafeSingleTranslateError(Exception):
    """Flat replacement would write this unit's translation into the wrong
    place in the document."""


def is_simple_owner(element, resolver):
    """Can this element be cloned to carry a translated copy?

    Only when nothing block-level lives inside it. Cloning a wrapper that
    contains a nested paragraph duplicates that paragraph's text into the
    copy and drops the translation somewhere after the whole wrapper —
    which is how ``<div>Before <p>x</p> After.</div>`` came out with its
    two halves reordered. <body> and <html> are never clonable at all: a
    document with two <body> elements is not a document.
    """
    if element.name in ("body", "html"):
        return False
    return not any(
        isinstance(child, Tag) and resolver.is_block(child)
        for child in element.descendants
    )


def file_segment_hazards(fp):
    """Every unit of a file that cannot be written back where its text is.

    Yields ``(unit, hazards)``. One walk per *owner*, not per unit: asking
    each unit separately re-walks its owner's whole subtree, which is
    quadratic where it matters most — `mahabharata.epub` hangs a book that
    hangs 158,000 verse lines directly off <body>, half of them spanning
    more than one text node.

    The check is exact rather than approximate: re-segmenting the owner from
    the units' own nodes must reproduce the units. Text that was dropped
    (a symbol-only run, a skipped signature) stays in the document and
    therefore still separates what surrounds it, so the boundaries line up.
    """
    by_owner = {}
    for unit in fp.units:
        entry = by_owner.setdefault(id(unit.element), (unit.element, []))
        entry[1].append(unit)

    for element, units in by_owner.values():
        owned = {id(n) for unit in units for n in unit.nodes}
        expected = [
            [id(n) for n in nodes]
            for nodes, _text in _owner_segments(element, owned, fp.resolver)
        ]
        actual = [[id(n) for n in unit.nodes] for unit in units]
        if expected == actual:
            continue
        # Something spans a boundary. Report per unit so the message names
        # the text, not just the file.
        boundaries = {tuple(run) for run in expected}
        for unit in units:
            if tuple(id(n) for n in unit.nodes) not in boundaries:
                yield unit, ["noncontiguous"]


def _classed_inline_ancestors(node, owner):
    """Inline elements between a text node and its owner that carry a class.

    A class is what makes an inline element *nameable* — something a verdict
    can be about, and something the same book uses consistently. Classless
    ``<em>``/``<b>`` are typography, not apparatus, and asking about them
    would fill the ledger with rows nobody can act on.

    Every level is listed, not just the outermost: a ``span.line-no`` inside
    a classless ``<a>`` is exactly the apparatus this exists to catch.
    """
    found = []
    for ancestor in node.parents:
        if ancestor is owner:
            break
        if ancestor.get("class"):
            found.append(ancestor)
    return found


def _action_of(decision):
    if isinstance(decision, tuple):
        return decision[0]
    return decision


def _skip_reason(decision):
    decided_by = decision[1] if isinstance(decision, tuple) else None
    return "llm-excluded" if decided_by == "llm" else "user-excluded"


def _inline_override_for(node, owner, overrides):
    """The skip reason an inline verdict imposes on this text, or None.

    Outermost wins: once an enclosing subtree is skipped, what its inner
    spans would have said no longer applies to text that is already gone.
    """
    for element in reversed(_classed_inline_ancestors(node, owner)):
        decision = overrides.get(make_key("inline", _signature(element)))
        if _action_of(decision) == "skip":
            return _skip_reason(decision)
    return None


def partition_soup(
    soup,
    resolver,
    file_name,
    exclude_tags=DEFAULT_EXCLUDE_TAGS,
    overrides=None,
):
    """Partition every text node of a document into segments and skip reasons.

    ``overrides`` maps scoped ledger keys (``block:p.note``) to a decision
    tuple ``(action, decided_by)``. Applied last, so ``fp.all_units`` still
    holds everything the document partitions into and the ledger can record
    a decision about text that this run will not translate.
    """
    fp = FilePlan(file_name=file_name, resolver=resolver)
    body = soup.body or soup
    _separate_brs(body)

    overrides = overrides or {}
    records = _text_node_records(body, resolver, exclude_tags)

    # Inline rows are collected over every classed inline element that owns
    # text, *before* any verdict is applied — same reason the block ledger
    # is: a skipped signature must keep its row and its provenance.
    inline_seen = {}
    for node, chars, reason, owner in records:
        if reason is not None or not chars:
            continue
        for element in _classed_inline_ancestors(node, owner):
            entry = inline_seen.setdefault(
                id(element),
                {
                    "element": element,
                    "signature": _signature(element),
                    "owner": owner,
                    "chars": 0,
                    "parts": [],
                },
            )
            entry["chars"] += chars
            entry["parts"].append(str(node))
    position = {}
    owners, owner_order = {}, []
    for index, (node, chars, reason, owner) in enumerate(records):
        position[id(node)] = index
        fp.total_chars += chars
        if reason is None and chars:
            # An inline verdict removes the subtree's text before segments
            # are formed: what stays in the document renders between the
            # runs around it, which is exactly what a barrier means.
            removed_by = _inline_override_for(node, owner, overrides)
            if removed_by is not None:
                reason = removed_by
        if reason is not None:
            if chars:
                fp.skipped[reason] += chars
            continue
        key = id(owner)
        if key not in owners:
            owners[key] = (owner, [])
            owner_order.append(key)
        owners[key][1].append(node)

    # Segments are emitted per owner but ordered by where they *start* in the
    # document: an owner interrupted by a nested block contributes runs on
    # both sides of it, and collecting owner-first would order them
    # div/0, div/1, p instead of div/0, p, div/1 — which is the order every
    # positional consumer (checkpoints, context windows, --test) then means.
    found = []
    for key in owner_order:
        owner, nodes = owners[key]
        owned_ids = {id(n) for n in nodes}
        segments = _owner_segments(owner, owned_ids, resolver)
        for run_index, (seg_nodes, text) in enumerate(segments):
            found.append(
                (
                    position[id(seg_nodes[0])],
                    owner,
                    run_index,
                    len(segments),
                    seg_nodes,
                    text,
                )
            )
    found.sort(key=lambda s: s[0])

    # What each owner's text is made of, over the runs that survive to become
    # units — a dropped run is not text anybody will be asked about.
    numeral_kinds = _owner_numeral_kinds(
        (owner, text)
        for _pos, owner, _ri, _or, _nodes, text in found
        if classify_skip(text) is None
    )

    for _pos, owner, run_index, owner_runs, seg_nodes, text in found:
        chars = sum(len(str(n).strip()) for n in seg_nodes)
        reason = classify_skip(text)
        if reason is not None:
            fp.skipped[reason] += chars
            continue
        fp.all_units.append(
            Unit(
                element=owner,
                file_name=file_name,
                signature=_row_signature(owner, numeral_kinds.get(id(owner))),
                text=text,
                chars=chars,
                nodes=seg_nodes,
                run_index=run_index,
                owner_runs=owner_runs,
                resolver=resolver,
            )
        )

    # Inline rows are emitted here, after the owners' kinds are known, so that
    # `parent_key` names the same row the owner's units carry.
    for entry in inline_seen.values():
        fp.inline_rows.append(
            {
                "signature": entry["signature"],
                "chars": entry["chars"],
                "text": _normalize_text("".join(entry["parts"])),
                "parent_key": make_key(
                    "block",
                    _row_signature(
                        entry["owner"], numeral_kinds.get(id(entry["owner"]))
                    ),
                ),
                # Resolved here, while the element is still in hand: the row
                # outlives it, and a `@media (max-width: 600px) {display:none}`
                # on span.line-no is exactly the evidence an inline verdict
                # turns on. Block rows get this too, from their own elements.
                "conditional_css": (
                    resolver.conditions_for(entry["element"])
                    if getattr(resolver, "conditional", None)
                    else []
                ),
            }
        )

    fp.units = list(fp.all_units)
    if overrides:
        kept = []
        for unit in fp.units:
            decision = overrides.get(unit.key)
            if _action_of(decision) == "skip":
                fp.skipped[_skip_reason(decision)] += unit.chars
            else:
                kept.append(unit)
        fp.units = kept
    for ordinal, unit in enumerate(fp.units):
        unit.ordinal = ordinal

    return fp


# --------------------------------------------------------- context windows

# A window is a *batching* shape — a run of short, same-shaped siblings that
# reads better translated together. It is deliberately not a claim about
# genre: the shape-based "poetry" flag this replaced marked 49.5% of the
# 45-book corpus, including thousands of table cells and list items, and
# that label went on to suppress classification of everything it touched.
# A false window now costs nothing: its members still translate
# individually, they merely share one request's context.
WINDOW_MIN_RUN = 3
WINDOW_MAX_MEDIAN_CHARS = 70


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


def assign_context_windows(units, group_size=8, next_group_id=0):
    """Window runs of short sibling units so they share a request's context.

    A qualifying run (>= WINDOW_MIN_RUN units, median line length <
    WINDOW_MAX_MEDIAN_CHARS) is split into groups at stanza boundaries —
    parent change, or recurrence of a minority "stanza head" class
    (calibre_14 in Animal Farm) — capped at `group_size` lines.
    Returns the next unused group id.

    Stanza-shaped windows are the only grouping. A second tier that swept
    leftover short units into windows was measured across four real books at
    5-33 saved requests each (0.5-4%) — not worth its window-membership
    nondeterminism, and it caused the tier-2/poetry classification
    conflation bug. Removed; the classifier judges short apparatus
    signature-by-signature instead.
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
        if len(run) < WINDOW_MIN_RUN:
            continue
        if statistics.median(u.chars for u in run) >= WINDOW_MAX_MEDIAN_CHARS:
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
    """partition_soup + grouping; the one entry point loaders use."""
    fp = partition_soup(
        soup, resolver, file_name, exclude_tags=exclude_tags, overrides=overrides
    )
    next_group_id = assign_context_windows(
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

    def build_ledger(self, decisions=None):
        """Every signature this book contains, with its evidence.

        Built from `all_units` — the partition *before* decisions are
        applied — so a skipped signature keeps its row. `decisions` is a
        loaded ledger whose answers and provenance are carried forward onto
        the fresh rows.
        """
        ledger = Ledger()
        for f in self.files:
            # one pass per file, not two: the conditional-CSS evidence is
            # gathered as each unit goes by. A second walk cost another
            # O(units) on books whose unit count runs to six figures.
            resolver = f.resolver
            conditional = bool(getattr(resolver, "conditional", None))
            for u in f.all_units:
                ledger.add_occurrence("block", u.signature, u.chars, u.text)
                if conditional:
                    conditions = resolver.conditions_for(u.element)
                    if conditions:
                        ledger.note_conditional_css("block", u.signature, conditions)
            for row in f.inline_rows:
                ledger.add_occurrence(
                    "inline",
                    row["signature"],
                    row["chars"],
                    row["text"],
                    parent_key=row["parent_key"],
                )
                if row.get("conditional_css"):
                    ledger.note_conditional_css(
                        "inline", row["signature"], row["conditional_css"]
                    )
        ledger.finalize(self.total_chars)
        if decisions is not None:
            for key, row in ledger.rows.items():
                prior = decisions.rows.get(key)
                if prior is None:
                    continue
                row["action"] = prior.get("action")
                row["decided_by"] = prior.get("decided_by")
                row["content_type"] = prior.get("content_type")
        self.record_dispositions(ledger)
        return ledger

    def record_dispositions(self, ledger):
        """What actually happened to each row once its action was applied."""
        translated = Counter()
        for f in self.files:
            for u in f.units:
                translated[u.key] += u.chars
        translated_keys = set(translated)
        for key, row in ledger.rows.items():
            if row["action"] is None:
                # No action has been applied, so nothing has happened to this
                # text and there is nothing to report. The greedy partition
                # does hold it — but a run refuses to start while any row is
                # undecided, so "translated: N chars" would describe a run
                # that will not happen. That claim survived in the file an
                # agent handoff writes, and contradicted the very skip the
                # agent then recorded on the same row.
                row["disposition"] = None
            elif row["action"] == "skip":
                row["disposition"] = f"skipped: {row['chars']} chars"
            elif key in translated:
                row["disposition"] = f"translated: {translated[key]} chars"
            elif row["scope"] == "inline":
                # An inline row carries no unit of its own: its text travels
                # inside the block that holds it. That only makes it
                # translated if such a block survived — when every block
                # holding it was skipped, its text stayed in the document
                # untranslated, and claiming otherwise puts a false entry in
                # the one record an auditor reads.
                row["disposition"] = (
                    "translated with its block"
                    if ledger.parents_of(key) & translated_keys
                    else "not translated: every block holding it was skipped"
                )
            else:
                row["disposition"] = "removed-by: an enclosing skip"
        return ledger

    def report(self, ledger=None, max_rows=25):
        lines = []
        ledger = ledger if ledger is not None else self.build_ledger()
        rows = list(ledger.rows.values())
        lines.append(
            f"Translation plan: {len(self.files)} documents, "
            f"{self.total_chars} chars, coverage "
            f"{100 * self.coverage:.1f}%"
        )
        lines.append(f"{'signature':38s} {'units':>6s} {'chars':>9s} {'%':>6s}  sample")
        for row in rows[:max_rows]:
            sample = row["samples"][0][:60] if row["samples"] else ""
            lines.append(
                f"{row['key']:38s} {row['units']:6d} {row['chars']:9d} "
                f"{row['pct']:6.1f}  {sample}"
            )
        if len(rows) > max_rows:
            hidden = sum(r["chars"] for r in rows[max_rows:])
            lines.append(f"... {len(rows) - max_rows} more signatures ({hidden} chars)")
        skipped = self.skipped_totals
        if skipped:
            skip_desc = ", ".join(f"{k}={v}" for k, v in skipped.most_common())
            lines.append(f"skipped: {skip_desc}")
        windowed = sum(1 for f in self.files for u in f.units if u.group_id is not None)
        lines.append(
            f"context windows: {windowed} unit(s) batched "
            f"(window <= {self.poetry_group_size} lines)"
        )
        return "\n".join(lines)

    def plan_meta(self, book_path):
        if not book_path:
            # the hash is what binds a plan to its book; a plan without one
            # cannot be validated on load, so it must never be written
            raise ValueError("plan JSON requires book_path to bind its sha256")
        return {
            "coverage": self.coverage,
            "total_chars": self.total_chars,
            "translate_chars": self.translate_chars,
            "skipped": dict(self.skipped_totals),
            "exclude_tags": list(self.exclude_tags),
            "poetry_group_size": self.poetry_group_size,
            "book_sha256": file_sha256(book_path),
        }

    def save_json(self, path, book_path=None, ledger=None):
        ledger = ledger if ledger is not None else self.build_ledger()
        ledger.save(path, self.plan_meta(book_path))
        return ledger


# One run hashes the same book up to three times — validating a saved plan,
# stamping the plan it writes, and feeding the resume fingerprint — and the
# file is streamed in 1 MB chunks each time. Keyed on identity *and* the
# stat that changes when the bytes do, so a book replaced mid-run is hashed
# again rather than remembered wrong.
_SHA256_CACHE = {}


def file_sha256(path):
    stat = os.stat(path)
    key = (os.path.abspath(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
    cached = _SHA256_CACHE.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _SHA256_CACHE[key] = digest
    return digest


def load_plan_overrides(json_path, book_path):
    """Load a saved plan and turn its answers into partition overrides.

    Returns ``(ledger, overrides)`` where overrides maps a scoped key to
    ``(action, decided_by)`` for every row that must not be translated.
    The ledger comes back whole — affirmative "translate" rows included,
    with their provenance — so the next save records what was decided
    rather than silently re-asking. Schema 3 dropped those rows on load,
    which is why a model's agreement and its silence looked identical.

    A stale plan (book hash mismatch, older schema) is refused loudly. An
    *unanswered* one is not: `--plan-dry-run` and agent mode both write a
    plan whose rows are null by design, and refusing it here would make the
    documented workflow — draft the plan, then classify or edit it — fail on
    the file we told the user to generate. Unanswered rows are questions,
    and the caller's undecided gate is where questions get asked;
    `require_decided` is the separate last gate before money is spent.
    """
    ledger = Ledger.load(json_path, expected_sha256=file_sha256(book_path))
    overrides = {
        key: (row["action"], row["decided_by"])
        for key, row in ledger.rows.items()
        if row["action"] == "skip"
    }
    return ledger, overrides


class BookCss:
    """Per-document CSS resolution.

    A chapter only obeys the stylesheets it actually links (<link
    rel="stylesheet">, resolved relative to the document) plus its inline
    <style> blocks — a chapter-local `.note {display:none}` must not hide
    same-class prose in other chapters. Documents that declare no
    stylesheets fall back to the merge of every sheet in the book.
    """

    def __init__(self, book):
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

    @staticmethod
    def _resolver(maps):
        return DisplayResolver([m[0] for m in maps], [m[1] for m in maps])

    def resolver_for(self, file_name, soup):
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
        return self._resolver(maps)


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
