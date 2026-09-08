"""Append-only history for `--use_context session`, and its compact cycle.

Window mode (`--use_context`) re-sends the last few (source, translation)
pairs on every request, which costs roughly six extra copies of the book for
three paragraphs of context. Session mode instead keeps one append-only
message list: the prefix is byte-stable, so an endpoint with prompt caching
re-reads it at its cache rate (0.1-0.23x on current endpoints) and context can
grow to chapter length for less money than window mode spends on three
paragraphs.

Two rules follow from that and are load-bearing everywhere below:

1. Nothing already sent may be rewritten. Editing an earlier message shifts
   the prefix and the cache misses, which is strictly worse than window mode.
2. Anything that varies per unit (the glossary block) rides in the fresh tail
   message, never in the prefix.

When the history reaches `--context-compact-at`, we ask the model for a
translator handoff report, start a new window seeded with it, and keep going.
The budget is one pinned number for every session run — see
DEFAULT_COMPACT_BUDGET below for what was measured and why it is pinned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from book_maker.glossary import Glossary

# Estimated, never billed. A chars-based estimate is what the budget table was
# derived from, so the knob and the math agree by construction; reading
# `usage` back would make the trigger depend on numbers the budget never used.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
_LATIN_CHARS_PER_TOKEN = 4.0
_CJK_CHARS_PER_TOKEN = 1.7

# One budget for every session run, grouped or not, on every route. An
# OWNER-SET number (260907), like the grouping constants in
# `book_maker/loader/plan.py`; what follows separates what was measured from
# what was chosen, and must keep doing so.
#
# Measured (260905 grouped session-cost eval, C in {1500, 4000, 8000, 20000}
# over three books): the cost curve is **flat** across [1500, 4000] — the
# spread inside it is single-run noise, that region held the cheapest cell on
# two of the three books, and all three solved optima C* landed in it, at
# 1580, 2183 and 2512. The old 8000 sat +9-25% above each book's own optimum,
# which is also noise-adjacent, and it was chosen for predictability rather
# than for price. The one place the curve stops being flat is the top: C=20000
# cost up to 56% more *and* was the only cell where register drift was
# observed — a shift consistent with instruction dilution across a very long
# window, not with the handoff seams. Across 44 seams at the shorter budgets
# every recurring name held, because the report re-states the terminology
# each window.
#
# Chosen: 4096. Inside the measured-flat region and above every solved C*, so
# it buys margin against the only observed failure mode without leaving the
# range anything was measured in. 4096 rather than 4000 is owner preference,
# not a measurement — the two are indistinguishable in the data.
#
# What it costs, knowingly: a smaller window compacts more often, and each
# handoff is a real request. The eval's model prices that as the
# (g/C)(F_h + pi_o*K) term — seams per unit of work times the cost of a seam —
# so halving C roughly doubles it. Two things make that bearable. The term is
# small next to the history re-read it replaces, and this change ships beside
# a halved grouping budget (`SESSION_BUDGET_FLOOR`, 2400 -> 1200): per-request
# history growth g falls with it, so 4096 holds about as many *requests* of
# history as 8000 did before the halving. The window shrank in tokens far
# more than it shrank in conversation.
#
# Still one pinned number for every run rather than a derived one, for the
# reason that has not changed: a moving target is not worth chasing for a
# difference this size, and an operator who wants another value types
# --context-compact-at.
DEFAULT_COMPACT_BUDGET = 4096


def compact_budget_notice(explicit: int | None) -> str:
    """The one line a session run prints about the window it compacts at.

    Shared so the dry-run preview and the run itself cannot drift apart:
    the preview's whole job is to say what the run will do.
    """
    if explicit is not None:
        return (
            f"session: compacting at {explicit} estimated tokens "
            f"(--context-compact-at)"
        )
    return (
        f"session: compacting at {DEFAULT_COMPACT_BUDGET} estimated tokens "
        f"(the default; --context-compact-at overrides)"
    )


def estimate_tokens(text: str) -> int:
    """Estimated tokens: CJK packs denser per character than latin script."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    latin = len(text) - cjk
    return round(latin / _LATIN_CHARS_PER_TOKEN + cjk / _CJK_CHARS_PER_TOKEN)


def compact_budget_for(model: str | None) -> int:
    """The compact budget for `model`.

    Uniform today. Kept as a function because the budget is a property of the
    endpoint's cache pricing, so a model that prices very differently would be
    special-cased here rather than at every call site.
    """
    return DEFAULT_COMPACT_BUDGET


class SessionHistory:
    """The append-only (source, translation) message list for one window."""

    def __init__(self):
        self._messages: list[dict] = []
        self._tokens = 0
        self.windows = 1

    def messages(self) -> list[dict]:
        """The cached prefix. Callers must not mutate what they get back."""
        return list(self._messages)

    def append(self, source: str, translation: str) -> None:
        self._messages.append({"role": "user", "content": source})
        self._messages.append({"role": "assistant", "content": translation})
        self._tokens += estimate_tokens(source) + estimate_tokens(translation)

    def estimated_tokens(self) -> int:
        return self._tokens

    def should_compact(self, budget: int) -> bool:
        return budget > 0 and self._tokens >= budget

    def reset(self, seed: str) -> None:
        """Start the next window, seeded with the handoff report."""
        self._messages = []
        self._tokens = 0
        self.windows += 1
        if seed:
            self._messages.append({"role": "user", "content": seed})
            self._tokens = estimate_tokens(seed)


# The compact request, revised by the user 2026-08-29. Kept deliberately terse:
# the model is being asked to compress its own context, not to write a report
# for a reader.
_PREAMBLE = (
    "Context is compacting. Summarize content you translated so far in your "
    "context for brief reference of later translations."
)

_SUMMARY_REQUEST = (
    "Summary - of translated content above. What happened, who was "
    "involved, when did those happen."
)

# Capped, and scoped to deviations only. Both clauses earn their place:
# uncapped, this section grew past twenty bullets; unscoped, it restates
# defaults the model would follow anyway ("使用简体中文"), which costs a line of
# the cap and tells the next window nothing. One wording for both paths — a
# variant that dropped the scope clause without a glossary used to exist, on
# the reasoning that the glossary section is what keeps term equivalences out
# of here. But that is what "this is the only place term equivalences belong"
# does, over in the renderings section; the clause dropped was the scope cap,
# which nothing else supplies. The result was that the *default* path, with no
# glossary, was the one running unscoped.
_STYLE_REQUEST = (
    "Style — up to 3 lines of what translation style is used so far. "
    "Only note down what's different from general translation."
)

# Only *new* renderings are requested. The accumulated set is already held on
# this side and merged, and it is replayed to the model in the seed, so asking
# for the whole list again is output paid twice — and it compounds: a shorter
# budget means more compacts, each re-emitting a longer list, so the cost of
# re-listing grows with the square of the compact count. Asking only for what
# is new keeps the report flat for the length of the book.
_GLOSSARY_REQUEST = (
    "Established renderings — nouns we need to keep unified that are **not "
    "already listed above**. If none are new, emit an empty block. One per "
    "line as `term → translation # note` (the note is optional). Wrap the "
    "list in <renderings> and </renderings> tags so its start and end are "
    "unambiguous. This is the only place term equivalences belong."
)


def handoff_prompt(with_glossary: bool = False, with_style: bool = True) -> str:
    """The compact turn's request, built from the sections in play.

    Each section costs output tokens and invites the model to spend attention
    on it, so one is only asked for when something downstream consumes it —
    the renderings only when this run learns a glossary (`--glossary-auto`),
    the style only when the user has not fixed one via `--prompt`'s `style`
    field. Numbering follows what is actually included, so a fixed style does
    not leave the renderings labelled "3." in a two-section request.
    """
    sections = [_SUMMARY_REQUEST]
    if with_style:
        sections.append(_STYLE_REQUEST)
    if with_glossary:
        sections.append(_GLOSSARY_REQUEST)
    numbered = [f"{n}. {body}" for n, body in enumerate(sections, start=1)]
    return "\n\n".join([_PREAMBLE, *numbered])


# The block the report is asked to emit. Tolerant of a missing closing tag:
# a truncated answer should still yield the terms it managed to write.
_RENDERINGS = re.compile(
    r"<renderings>(.*?)(?:</renderings>|\Z)", re.DOTALL | re.IGNORECASE
)

# Fallback only. A glossary line is short and is not a sentence, which is what
# separates it from prose that happens to contain an arrow.
_MAX_TERM_LEN = 60
_SENTENCE_END = ("。", ".", "！", "!", "？", "?", "；", ";")


class HandoffGlossary(NamedTuple):
    """What a handoff report yielded, and how it had to be recovered.

    `source` is reported so a run can say out loud that the model skipped the
    block — with the derived glossary on, silently learning nothing looks
    identical to a book with no recurring terms.
    """

    glossary: Glossary
    source: str  # "tagged" | "scanned" | "missing"


def _line_entries(raw, strict):
    """The entries one line yields, or None if it is not an entry line.

    One line, one verdict — so the stripper can ask exactly the question the
    parse asked and remove precisely the lines the parse read.
    """
    line = raw.strip().lstrip("-*•").strip()
    if not line or line.startswith("#") or line.startswith("<"):
        return None
    if not strict:
        # Outside the tags, only accept things shaped like an entry.
        head = re.split(r"→|->", line)[0].strip()
        if len(head) > _MAX_TERM_LEN or line.endswith(_SENTENCE_END):
            return None
    try:
        return Glossary.parse(line).entries or None
    except ValueError:
        return None  # model output; one bad line must not lose the rest


def _entries_from_lines(lines, strict):
    entries = []
    for raw in lines:
        found = _line_entries(raw, strict)
        if found:
            entries.extend(found)
    return entries


def parse_handoff_glossary(text: str) -> HandoffGlossary:
    """Read the renderings the handoff report established.

    Preferred shape is the tagged block the prompt asks for. Models drop it,
    so there is a fallback: scan loose `term → translation` lines, guarded so
    ordinary prose containing an arrow is not mistaken for an entry.
    """
    if not text:
        return HandoffGlossary(Glossary(), "missing")

    match = _RENDERINGS.search(text)
    if match:
        entries = _entries_from_lines(match.group(1).splitlines(), strict=True)
        if entries:
            return HandoffGlossary(Glossary(entries), "tagged")

    entries = _entries_from_lines(text.splitlines(), strict=False)
    if entries:
        return HandoffGlossary(Glossary(entries), "scanned")
    return HandoffGlossary(Glossary(), "missing")


# Any markdown heading. The model writes the renderings heading in the target
# language, so it can only be recognised by where it sits, never by its words.
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")


def _drop_introducing_heading(before: str) -> str:
    """`before` without a heading left dangling at its end.

    `before` is the prose that ran up to a block being removed, so a heading
    sitting at the end of it — blank lines aside — is that block's own
    heading and goes with it. A heading anywhere else is the report's.
    """
    lines = before.split("\n")
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    if end and _HEADING.match(lines[end - 1]):
        del lines[end - 1]
    return "\n".join(lines)


def _drop_loose_entry_lines(text: str) -> str:
    """`text` without the loose lines the fallback parse read as entries."""
    lines = text.split("\n")
    dropped = {i for i, raw in enumerate(lines) if _line_entries(raw, strict=False)}
    for i in sorted(dropped):
        if i - 1 in dropped:
            continue  # same run; its heading was already considered
        above = i - 1
        while above >= 0 and not lines[above].strip():
            above -= 1
        if above >= 0 and _HEADING.match(lines[above]):
            dropped.add(above)
    return "\n".join(line for i, line in enumerate(lines) if i not in dropped)


def strip_handoff_glossary(text: str) -> str:
    """The report's prose, without the renderings the parse recovered.

    Those entries are re-rendered canonically into the report, so any copy
    left in the prose is written twice into `<book>_handoff.md` and sent
    twice in the next window's seed. Whatever the parse recovered therefore
    has to come out here: the tagged block when the model emitted one, and
    the loose lines the parse fell back to when it did not — the fallback
    used to be stripped by neither, so a report without tags duplicated
    every term it established.

    A report that established nothing is returned as written. Deleting its
    last heading anyway — which this did, on the guess that a heading in
    that position introduced the block — took a genuine section title off a
    report that never had a renderings block at all.
    """
    if not text:
        return text
    blocks = list(_RENDERINGS.finditer(text))
    # An empty or unparseable block still sends the parse to the loose lines,
    # so "there were tags" is not the same question as "which lines were read".
    scanned = parse_handoff_glossary(text).source == "scanned"
    if not blocks and not scanned:
        return text.strip()

    parts, cursor = [], 0
    for block in blocks:
        parts.append(_drop_introducing_heading(text[cursor : block.start()]))
        cursor = block.end()
    parts.append(text[cursor:])
    without = "".join(parts)
    if scanned:
        without = _drop_loose_entry_lines(without)
    return re.sub(r"\n{3,}", "\n\n", without).strip()


@dataclass
class HandoffReport:
    """One window's handoff, persisted to `<book>_handoff.md` and re-seeded."""

    window: int
    summary: str
    # The renderings this run has established so far, pins included, rendered
    # canonically. Written to the handoff file and replayed in the seed so the
    # next window keeps the same names — and so an operator can read back what
    # the run taught itself. It never reaches the book: the translation metadata stamp
    # records the `--glossary` file the operator wrote, nothing derived.
    glossary_lines: str = ""
    # A style the user fixed via --prompt's `style` field. It is not asked of
    # the model, so it is written in here instead — otherwise the next window
    # would inherit a report with no style at all.
    style_note: str = ""

    _MARKER = "## Window "

    def seed_text(self) -> str:
        seed = (
            "You are continuing a translation already in progress. "
            "The previous translator left this handoff report; keep names, "
            "terminology and register consistent with it.\n\n"
            f"{self.summary}"
        )
        if self.glossary_lines:
            seed += f"\n\nEstablished renderings:\n{self.glossary_lines}"
        return seed

    def render(self) -> str:
        """The report body: what is written to the file and shown on screen."""
        body = self.summary
        if self.style_note:
            body += f"\n\n### Style\n\n{self.style_note}"
        if self.glossary_lines:
            body += f"\n\n### Established renderings\n\n{self.glossary_lines}"
        return body

    def append_to(self, path) -> None:
        """Append this window's report. Inspectable and hand-editable by design."""
        path = Path(path)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n{self._MARKER}{self.window} — {stamp}\n\n{self.render()}\n"
            )

    @classmethod
    def latest_seed(cls, path) -> str:
        """The last window's report, for resuming a run mid-book."""
        path = Path(path)
        if not path.exists():
            return ""
        body = path.read_text(encoding="utf-8")
        _, sep, tail = body.rpartition(cls._MARKER)
        return (sep + tail).strip() if sep else body.strip()


def handoff_path(book_path) -> Path:
    """`<book>_handoff.md`, beside the book."""
    path = Path(book_path)
    return path.with_name(f"{path.stem}_handoff.md")
