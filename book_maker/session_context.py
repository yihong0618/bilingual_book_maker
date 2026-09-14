"""Append-only history for `--use_context session`, and its compact cycle.

The handoff a compaction produces is written to `<book>_handoff.md` as an
overwritten *snapshot* — one window's report, never a growing log — and is
bounded before it seeds the next window; see the seed-bound constants below
for why an unbounded seed is a death loop rather than an inefficiency.

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

import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from rich import print

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
# Chosen: 8192 — a hair above the old 8000, and for the same reason it was
# 8000: the fewest-seams edge of the measured band. Under the *old* budgets
# nothing separated 8000 from the flat region on cost — the 9-25% it sat
# over each book's own optimum was inside the don't-care zone. Under the
# shipped halved B that is no longer true (the table below: 8192 measures
# ~10% dearer than 4096), so the case for this number is continuity alone.
# 8192 rather than 8000 is owner preference, not a measurement — nothing in
# the data separates them.
#
# **Why this is a raise, and what the raise did not buy.** The 260907 ruling
# first set C to 4096, reasoning that the halved grouping budget shipping
# beside it (`SESSION_BUDGET_FLOOR`, 2400 -> 1200) would shrink per-request
# history growth by about as much, so 4096 would hold roughly as many
# *requests* of history as 8000 did before. The branch's price-tag eval
# measured that and it did not hold: on animal_farm through gpt-5.6-luna,
# 300 units, compactions went 8 -> 19 and the session bill +27.3% against
# the old defaults. C was raised here in response.
#
# The raise was then measured too, and the honest record is that it did not
# work as intended (animal_farm / gpt-5.6-luna / 300 units, against the same
# old-defaults baseline of 33 requests and 283k tokens):
#
#     C=4096   63 requests   361k tokens (+27.3%)   19 compactions
#     C=8192   52 requests   396k tokens (+39.8%)    9 compactions
#
# The compaction count came back to roughly the old 8, exactly as intended.
# The bill did not: 8192 measured ~10% *dearer* than 4096, not cheaper. The
# seams were never the dominant cost. What the halved B actually does is
# multiply the request count (33 -> ~52-63), and every one of those requests
# re-reads the whole carried history — so a longer window is paid for far
# more often than it used to be. Per-request prompt load tells the story:
# 7007 tokens at the old defaults, 4690 at C=4096, 6537 at C=8192. The
# (g/C)(F_h + pi_o*K) seam term is real but small next to it.
#
# 8192 stands anyway, by owner ruling and with that price known. The case is
# not cost: it is the local-device model the owner named, where losing
# context at a seam is worse than paying to carry it, and where the eval's
# cost curve — measured on a hosted endpoint with prompt caching — is not
# the curve those users are on. Note the cache barely helps here either way
# (9-21k cached against 340k prompt in these cells).
#
# If a future ruling revisits this: lowering C is the *cheaper* direction
# under the current B, not the dearer one, and the +27.3% that prompted the
# raise was mostly B's doing rather than C's. Re-run the two session cells
# before moving either number.
#
# Still one pinned number for every run rather than a derived one, for the
# reason that has not changed: a moving target is not worth chasing for a
# difference this size, and an operator who wants another value types
# --context-compact-at.
DEFAULT_COMPACT_BUDGET = 8192


# ---- seed bounds -----------------------------------------------------------
#
# A window is seeded with the previous window's handoff report. Nothing used
# to bound that report, and an unbounded seed is not an inefficiency — it is a
# death loop: once the seed alone reaches the budget, the freshly reset window
# is already over it, so the next unit compacts again, and the run pays for a
# handoff report per paragraph while translating almost nothing. (Diagnosed
# upstream by wzvideni in PR #569; the fix here is at the source, the seed's
# size, rather than in how the window is accounted.)
#
# Three layers, weakest first:
#   a) the compact prompt states an approximate target size,
#   b) the request carries a token cap where the endpoint accepts one,
#   c) the client truncates what came back, head first.
#
# Only (c) cannot be ignored by the model, so the no-death-loop invariant
# rests on it: SEED_CAP_TOKENS < MIN_COMPACT_BUDGET (cli.py) means a freshly
# seeded window always has room left for at least one unit. What that is
# worth, measured: under the *old* cumulative-glossary seed at budget 1000,
# the seed alone reached 1027 tokens by window 10 — larger than the window it
# was opening, which is the death loop exactly.
#
# All three numbers are pinned by the 260913 luna eval (14 compact reports
# over two budget cells), measured on the summary-plus-style part of the
# report — the part that becomes the seed.
#
# Measured: combined mean 246 tokens; the 4000-budget cell, the one closer to
# the default 8192 regime, means 285 with mid-run reports of 296-367. Eval B
# (gpt-4o-mini and deepseek on moby-dick, budgets 3000-5000) found report
# size flat and budget-independent at 193-268, which agrees.
# Chosen: 300 — the owner named it independently of the eval, and the eval's
# realistic regime rounds to the same number, so the request asks for about
# what a good model already writes and the instruction does not fight the
# content. It bounds the WHOLE report, not the summary alone.
SEED_TARGET_TOKENS = 300
# Measured: p95 373, largest observed 384. Chosen: 500, p95 plus ~35% margin,
# so the endpoint's cap stops a model that overruns badly while never
# truncating a report of ordinary size — a cap that fires routinely would cut
# mid-sentence on every window.
SEED_MAX_TOKENS = 500
# CHOSEN by the owner (260913): "always truncated if >~500" — the guarantee
# is that a seed is never over about 500 tokens, whatever the model does.
# Measured anchors: the same eval's p95 of 373 and largest observed 384, so
# 512 sits a third above the worst legitimate report seen. On an honest model
# truncation therefore stays rare; on a runaway one it is a real ceiling,
# which is the point — this layer exists for endpoints that ignored
# SEED_MAX_TOKENS. `seed_cap` narrows it further on small budgets.
SEED_CAP_TOKENS = 512


def seed_cap(budget: int) -> int:
    """The hard truncation cap for a seed opening a window of `budget`.

    `budget // 2` is the owner's ratio (260913; the ratio itself is still an
    open question, the invariant under it is not): whatever the constant says,
    a seed may never take more than half the window it opens, or the content
    the window exists to carry has nowhere to go.
    """
    if budget <= 0:
        return SEED_CAP_TOKENS
    return max(1, min(SEED_CAP_TOKENS, budget // 2))


class _SeedTruncations:
    """How often the hard cut has had to fire, and whether it keeps firing.

    A readout, deliberately kept as state rather than a local: truncation
    firing once is a long report, firing on window after window is a model
    that cannot hold to the size it was asked for, and a follow-on health
    guard is to read `consecutive` to say so. Nothing here gates anything
    today.
    """

    def __init__(self):
        self.total = 0
        self.consecutive = 0

    def note(self, fired: bool) -> None:
        if fired:
            self.total += 1
            self.consecutive += 1
        else:
            self.consecutive = 0

    def reset(self) -> None:
        self.total = 0
        self.consecutive = 0


seed_truncations = _SeedTruncations()


def _prefix_within(text: str, cap_tokens: int) -> str:
    """The longest head of `text` whose own estimate fits `cap_tokens`.

    Measured with `estimate_tokens` rather than converted with a
    chars-per-token constant: the estimate is script-dependent (CJK packs
    more than twice as densely as latin), so a character count derived from
    the latin ratio hands back more than twice the cap on a CJK line — which
    is exactly the seed the cap exists to prevent. Bisected, which the
    estimate allows because a longer prefix never estimates smaller.
    """
    if cap_tokens <= 0 or not text:
        return ""
    if estimate_tokens(text) <= cap_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= cap_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _has_substance(lines) -> bool:
    """Whether these lines say anything — a heading or a blank does not."""
    return any(line.strip() and not _is_block_label(line) for line in lines)


def _fit_to_cap(lines, cap_tokens: int) -> str:
    """These lines joined, shrunk until the joined text itself fits the cap.

    The last word on the size, and the only place the postcondition is
    established: every path above assembles a candidate out of parts it
    measured separately, and separately-measured parts do not add up — each
    is rounded on its own and the newlines joining them are counted by
    neither. `"## Summary\\n" + 3000 CJK characters` came back at 513
    against a cap of 512 that way.

    Shrinks in the order that loses least: a heading or a blank line at the
    head buys nothing once the body under it has to be cut, so those go
    first; only then is the text itself cut, bisected on the joined string.
    """
    kept = list(lines)
    while len(kept) > 1 and estimate_tokens("\n".join(kept).rstrip()) > cap_tokens:
        if kept[0].strip() and not _is_block_label(kept[0]):
            break
        kept.pop(0)
    text = "\n".join(kept).rstrip()
    if estimate_tokens(text) <= cap_tokens:
        return text
    return _prefix_within(text, cap_tokens)


def truncate_seed(text: str, cap_tokens: int) -> str:
    """`text` cut to `cap_tokens` estimated tokens, head first.

    Head first because models front-load a handoff report: the summary and
    the terminology come before the trailing elaboration, so cutting the tail
    loses the least (owner ruling 260913). Cuts at a line boundary so the
    next window is never seeded with half a sentence — unless keeping whole
    lines would leave the seed saying nothing, which is worse than a cut
    sentence.

    Postcondition, absolute and established by `_fit_to_cap`: the result's
    own estimate is at most `cap_tokens`, for every input. The layers above
    this one can be ignored by a model; this one cannot be, so it may not
    have exceptions of its own.
    """
    if not text or cap_tokens <= 0 or estimate_tokens(text) <= cap_tokens:
        seed_truncations.note(False)
        return text
    seed_truncations.note(True)
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        candidate = kept + [line]
        if estimate_tokens("\n".join(candidate)) > cap_tokens:
            break
        kept = candidate
    if not _has_substance(kept):
        # Nothing fitted, or only headings and blank lines did — a report
        # that opens `## Summary` on one long paragraph hits the second case,
        # and used to seed the next window with the heading and no summary
        # under it. Give it the first line that says something, cut
        # mid-sentence by `_fit_to_cap`, which is the lesser loss.
        first = next(
            (line for line in lines if line.strip() and not _is_block_label(line)),
            lines[0],
        )
        kept = kept + [first]
    result = _fit_to_cap(kept, cap_tokens)
    print(
        f"[yellow]ℹ the handoff report ran to {estimate_tokens(text)} "
        f"estimated tokens, over the {cap_tokens}-token seed cap; the next "
        f"window is seeded with its first "
        f"{len(result.splitlines()) if result else 0} line(s)[/yellow]"
    )
    return result


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


# How many renderings ONE compact report may establish. A cap on the report,
# not on what the run has learned: the total set stays unbounded by design
# (it lives in memory, per-unit injection only sends the terms that match the
# unit, and the snapshot is the one artifact that is O(total)).
#
# Why the report needs a cap at all: under this design the model is never
# shown the established list — a term reaches its context only when it matches
# the unit being translated — so it cannot deduplicate against what is already
# recorded, and its report can re-emit renderings without bound, every
# compact. That is the cycle this starves.
#
# CHOSEN, not measured: the 260913 luna eval's per-window *new*-term deltas
# ran 7-19 (p95 about 19) under the looser prompt, so 16 covers an honest
# window's worth of genuinely new names while leaving a re-emitting model
# nothing to gain. Owner may revise.
GLOSSARY_MAX_PER_COMPACT = 16


# The compact request, revised by the user 2026-08-29. Kept deliberately terse:
# the model is being asked to compress its own context, not to write a report
# for a reader.
_PREAMBLE = (
    "Context is compacting. Summarize content you translated so far in your "
    "context for brief reference of later translations. Reply in exactly "
    "this shape, keeping the English headers verbatim:"
)

# The headers are protocol tokens, not prose, and they are asked for in
# English whatever the book's target language is — a model writing its own
# `## 摘要` is fine for a reader and useless to anything that has to find the
# section. Shown as a template rather than described in a numbered list
# because models mirror numbering back into the report (all six cells of the
# 260913 eval B did), and a number read back is a line of the seed spent on
# furniture. Nothing downstream *depends* on the headers: the trim removes
# headings by shape, in any language, and the parse finds renderings by their
# arrows. They are asked for because a report shaped this way is a better
# report, not because the client needs them.
_SUMMARY_HEADER = "## Summary"
_STYLE_HEADER = "## Style"
_RENDERINGS_HEADER = "## Renderings"

_SUMMARY_REQUEST = (
    f"{_SUMMARY_HEADER}\n\n"
    "What happened in the content above, who was involved, when did those "
    "happen."
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
    f"{_STYLE_HEADER}\n\n"
    "Up to 3 lines of what translation style is used so far. Only note down "
    "what's different from general translation."
)

# Scoped to *this* window's renderings. The accumulated set is held on this
# side and merged there; it used to ride in the seed as well, which is what
# let the request say "not already listed above" — since 260913 the seed
# carries summary and style only, so the model can no longer be asked what it
# has not been shown. Deduplication moved to the client instead: a rendering
# that arrives unchanged is dropped, a changed one replaces what was held.
# That keeps the report flat for the length of the book, which is what the
# old clause was for — and the per-report cap keeps it flat even when the
# model re-emits anyway, which it cannot be told not to do when it can no
# longer see what it established (see GLOSSARY_MAX_PER_COMPACT).
_GLOSSARY_REQUEST = (
    f"{_RENDERINGS_HEADER}\n\n"
    f"<renderings>\n"
    f"term → translation # note\n"
    f"</renderings>\n\n"
    f"At most {GLOSSARY_MAX_PER_COMPACT} names from the passages above whose "
    f"rendering is new or has changed, one per line inside those tags, source "
    f"term on the left and the note optional. Never repeat an entry you have "
    f"already reported. If there are none, emit an empty block. This is the "
    f"only place term equivalences belong."
)


# Layer (a) of the seed bound: say how big the answer should be. The weakest
# of the three layers — a weak model ignores it and the client truncates
# anyway — but it is also the only one that lets the model choose *what* to
# drop, which is the one thing truncation cannot do. Stated in words as well
# as tokens because a model reasons about words.
def _size_request() -> str:
    return (
        f"Keep the whole report under about {SEED_TARGET_TOKENS} tokens "
        f"(roughly {int(SEED_TARGET_TOKENS * 0.75)} words). It is read as the "
        f"opening of the next context window, not by a person; anything past "
        f"that is cut off."
    )


def handoff_prompt(with_glossary: bool = False, with_style: bool = True) -> str:
    """The compact turn's request, built from the sections in play.

    Each section costs output tokens and invites the model to spend attention
    on it, so one is only asked for when something downstream consumes it —
    the renderings only when this run learns a glossary (`--glossary-auto`),
    the style only when the user has not fixed one via `--prompt`'s `style`
    field. A section that is not asked for leaves no trace: the template
    simply does not show that header, so there is no numbering to renumber
    and nothing saying a section was skipped.
    """
    sections = [_SUMMARY_REQUEST]
    if with_style:
        sections.append(_STYLE_REQUEST)
    if with_glossary:
        sections.append(_GLOSSARY_REQUEST)
    # The size request closes the prompt rather than opening it: it is about
    # the answer as a whole, so it belongs after the sections it bounds.
    return "\n\n".join([_PREAMBLE, *sections, _size_request()])


# The block the report is asked to emit. Tolerant of a missing closing tag:
# a truncated answer should still yield the terms it managed to write.
_RENDERINGS = re.compile(
    r"<renderings>(.*?)(?:</renderings>|\Z)", re.DOTALL | re.IGNORECASE
)

# What separates an entry from prose that happens to contain an arrow: a
# glossary line is short and is not a sentence. The term half guards the
# fallback scan *and* the tagged block (see `_is_sane_entry`); the rendering
# half is looser because a rendering may legitimately be a short phrase.
_MAX_TERM_LEN = 60
_MAX_RENDERING_LEN = 120
_SENTENCE_END = ("。", ".", "！", "!", "？", "?", "；", ";")

# A line that tried to be an entry, whether or not it succeeded.
_ARROW_SHAPED = re.compile(r"→|->")


class HandoffGlossary(NamedTuple):
    """What a handoff report yielded, and how it had to be recovered.

    `source` is reported so a run can say out loud that the model skipped the
    block — with the derived glossary on, silently learning nothing looks
    identical to a book with no recurring terms. `dropped` is how many lines
    of the tagged block were not usable pairs, for the same reason: a model
    answering the section with prose is a degraded run, not a clean one.
    """

    glossary: Glossary
    source: str  # "tagged" | "scanned" | "missing"
    dropped: int = 0
    flipped: int = 0
    ungrounded: int = 0


class WindowText(NamedTuple):
    """The window being compacted, as the text a grounding search runs over.

    The two sides are kept apart only because the callers have them apart;
    `contains` searches both, because neither side owns a kind of text (see
    `_is_grounded`). Case-folded once here rather than per entry: a window is
    thousands of characters and a report offers at most a couple of dozen
    pairs.
    """

    sources: str
    translations: str

    @classmethod
    def from_messages(cls, messages) -> "WindowText":
        """The window of a `SessionHistory`-shaped message list.

        The seed rides in as a user message and is therefore counted as
        source text. That is what it is, for this purpose: a term the
        previous window established and this one carried forward is
        genuinely part of the context being summarised.
        """
        return cls.of(
            [m.get("content") or "" for m in messages if m.get("role") == "user"],
            [m.get("content") or "" for m in messages if m.get("role") == "assistant"],
        )

    @classmethod
    def of(cls, sources, translations) -> "WindowText":
        return cls("\n".join(sources).casefold(), "\n".join(translations).casefold())

    def contains(self, needle: str) -> bool:
        """Whether this case-folded string appears anywhere in the window."""
        return needle in self.sources or needle in self.translations


def _is_grounded(entry, window: WindowText) -> bool:
    """Whether either end of this pair is actually in the window.

    The keep-test for a harvested rendering, and the one that replaced the
    format heuristics (owner ruling 260913). A pair the model invented —
    from its training data, from the prompt, or from nothing — has no end in
    the text it was just shown; a pair it observed has at least one, because
    it read the term in a source or wrote the rendering into a translation.
    Either end is enough on purpose: the model is allowed to report a term
    it rendered differently from how it appears, and inflection or a
    possessive can move the other end out of reach.

    Both ends are looked for across the whole window, not each end in "its"
    half (owner's rule: any end found in context). The halves do not divide
    by language. A name often survives untranslated — the model writes
    "Boxer" into a Chinese translation — so the source term turns up on the
    translation side; and the carried seed arrives as a user message, so the
    previous window's target-language renderings and names sit on the source
    side. Splitting the haystacks would drop exactly the pairs a working
    session produces most.

    Substring, case-folded, no word boundaries: this is a cheap negative
    filter for hallucinations, not a claim about morphology, and CJK has no
    boundaries to anchor to anyway.
    """
    for end in (entry.term, entry.translation):
        text = end.strip().casefold()
        if text and window.contains(text):
            return True
    return False


def _is_sane_entry(entry) -> bool:
    """Whether a parsed pair is a term and a rendering rather than prose.

    Applied inside the tags as well as outside them. The tags are a request,
    not a guarantee: a weak model answers the renderings section with a
    paragraph, a refusal, or a whole translated sentence that happens to
    contain an arrow. None of that may be stored, because a stored junk term
    is then injected into every later request whose text matches it.

    Structural only. Whether the pair says something usable — an identity,
    a list of alternatives — is asked after the direction has been settled,
    in `_is_usable_rendering`.
    """
    if not entry.term or not entry.translation:
        return False
    if len(entry.term) > _MAX_TERM_LEN:
        return False
    if len(entry.translation) > _MAX_RENDERING_LEN:
        return False
    return not entry.term.endswith(_SENTENCE_END)


# A rendering offering a choice rather than making one. Measured (260913
# prompt-variant eval): `Rebellion → 起义／反叛（依语境）`, `Beasts of England →
# 英格兰兽／英伦兽歌`. The block is a verbatim-substitution instruction, so it
# cannot carry alternatives or a "depending on context" qualifier — there is
# nothing downstream that could choose.
_ALTERNATIVES = re.compile(r"／|\s/\s")
_TRAILING_PAREN = re.compile(r"[（(]([^（()）]*)[)）]\s*$")
# What makes a trailing parenthetical a hedge rather than part of the name.
# A parenthesis alone is not evidence of one: `Organisation mondiale de la
# santé (OMS)` is a rendering, abbreviation included, and refusing it would
# throw away exactly the names most worth keeping unified.
_QUALIFIER_WORDS = re.compile(
    r"依语境|视语境|视上下文|按语境|看语境|context|depending", re.IGNORECASE
)


def _is_usable_rendering(entry) -> bool:
    """Whether the pair makes one substitution, once settled on a direction.

    `Balaene → Balaene` is refused: an identity pair instructs the model to
    do what it would do anyway, and both sides of the 260913 eval produced
    them. So is a rendering that offers a choice instead of making one —
    there is nothing downstream that could choose.
    """
    if entry.term.lower() == entry.translation.lower():
        return False
    if _ALTERNATIVES.search(entry.translation):
        return False
    trailing = _TRAILING_PAREN.search(entry.translation)
    return not (trailing and _QUALIFIER_WORDS.search(trailing.group(1)))


# Target languages written in CJK script, by name and by tag. The one script
# axis this module can see: `_CJK` separates CJK from everything else, and
# nothing here distinguishes Latin from Cyrillic or Arabic.
_CJK_LANGUAGE_WORDS = ("chinese", "japanese", "korean", "cantonese", "mandarin")
_CJK_LANGUAGE_TAGS = {"zh", "ja", "ko", "yue", "zh-hans", "zh-hant", "zh-yue"}


def target_is_cjk(language) -> bool:
    """Whether this run translates *into* a CJK script, by language or tag."""
    text = (language or "").strip().lower()
    if not text:
        return False
    if text in _CJK_LANGUAGE_TAGS:
        return True
    return any(word in text for word in _CJK_LANGUAGE_WORDS)


def _mostly_cjk(text: str) -> bool:
    stripped = "".join(ch for ch in text if not ch.isspace())
    if not stripped:
        return False
    return len(_CJK.findall(stripped)) * 2 > len(stripped)


def _flip_reversed(entries, target_language):
    """`entries` with the backwards pairs turned round, and how many turned.

    Observed live (260913 eval B, deepseek): `利维坦 → Leviathan` beside
    correct pairs in the same block; and eval's prompt matrix caught
    gpt-5.6-luna emitting a whole 16-of-16 block reversed under the correct
    instruction. Stored as written, such a pair tells a later window to
    render the target language back into the source — and it can never match
    anything either, since matching runs against *source* text.

    Normalised rather than dropped (owner ruling 260913): both halves are
    there and legible, so the pair is emitted the right way round instead of
    thrown away. It runs first, before the identity and alternatives checks
    and before the per-report cap, so those see the pair as it will be
    stored.

    Only on a cross-script run, and only the CJK axis: a genuine
    Chinese-to-English run writes every pair with CJK on the left, and
    reading that as reversed would invert the operator's whole glossary. A
    same-script run (en->fr) is left alone entirely — the reversal is
    invisible there, and guessing is worse than accepting what was written.
    """
    if not target_is_cjk(target_language):
        return entries, 0
    flipped = 0
    out = []
    for entry in entries:
        if _mostly_cjk(entry.term) and not _mostly_cjk(entry.translation):
            entry = replace(entry, term=entry.translation, translation=entry.term)
            flipped += 1
        out.append(entry)
    return out, flipped


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
        entries = Glossary.parse(line).entries
    except ValueError:
        return None  # model output; one bad line must not lose the rest
    sane = tuple(entry for entry in entries if _is_sane_entry(entry))
    return sane or None


def _entries_from_lines(lines, strict):
    """The entries these lines yield, and how many lines were refused.

    A refused line is only counted when it tried to be an entry — it carries
    an arrow — so ordinary prose between the tags is not reported as damage.
    """
    entries, dropped = [], 0
    for raw in lines:
        found = _line_entries(raw, strict)
        if found:
            entries.extend(found)
        elif _ARROW_SHAPED.search(raw):
            dropped += 1
    return entries, dropped


def parse_handoff_glossary(
    text: str, target_language=None, window: WindowText | None = None
) -> HandoffGlossary:
    """Read the renderings the handoff report established.

    Preferred shape is the tagged block the prompt asks for. Models drop it,
    so the backup is a grep: every line of the reply that carries an arrow,
    tags or no tags, anywhere in it. Neither path judges the reply as a whole
    — a compact reply is never refused for its format (owner ruling 260913) —
    and the guards that remain decide only what gets STORED.

    `target_language` is what the run is translating into; without it a pair
    written backwards cannot be recognised, so it is passed wherever the
    result is stored. `window` is the text being compacted, for the grounding
    test; without it grounding is skipped, which is right for the stripper —
    which lines were *read* is the same question however few survive.
    """
    if not text:
        return HandoffGlossary(Glossary(), "missing")

    match = _RENDERINGS.search(text)
    if match:
        entries, dropped = _entries_from_lines(match.group(1).splitlines(), strict=True)
        if entries:
            return _harvest(entries, dropped, "tagged", target_language, window)

    entries, dropped = _entries_from_lines(text.splitlines(), strict=False)
    if entries:
        return _harvest(entries, dropped, "scanned", target_language, window)
    return HandoffGlossary(Glossary(), "missing")


def _harvest(entries, dropped, source, target_language, window) -> HandoffGlossary:
    """The pairs a report may contribute: turned round, filtered, then capped.

    In that order. The flip decides what the pair says, so it has to happen
    before anything judges what was said — grounding included, which looks
    the term up in the source text and would miss a pair still written
    target-first — and before the cap counts, or a report could spend its
    allowance on entries that are then discarded.

    The cap keeps the *head* of the list because a report front-loads what
    matters, the same reason the seed is truncated head first.
    """
    entries, flipped = _flip_reversed(entries, target_language)
    ungrounded = 0
    if window is not None:
        grounded = [entry for entry in entries if _is_grounded(entry, window)]
        ungrounded = len(entries) - len(grounded)
        entries = grounded
    usable = [entry for entry in entries if _is_usable_rendering(entry)]
    dropped += len(entries) - len(usable)
    kept = usable[:GLOSSARY_MAX_PER_COMPACT]
    return HandoffGlossary(
        Glossary(kept),
        source,
        dropped + len(usable) - len(kept),
        flipped,
        ungrounded,
    )


# Any markdown heading. The model writes the renderings heading in the target
# language, so it can only be recognised by where it sits, never by its words.
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
# A list number a model puts in front of it, mirroring the compact prompt's
# own numbering back into the report.
_LIST_NUMBER = re.compile(r"^\s{0,3}\d+[.)]\s*")
# A whole line wrapped in markdown emphasis: `**Established renderings**`.
_EMPHASISED = re.compile(r"^(\*\*|__|\*|_)(.+?)\1$")


def _is_block_label(line: str) -> bool:
    """Whether `line` introduces a block rather than being part of the report.

    A markdown heading, or the shape models actually emit: the compact
    prompt's numbered section title, read back — `3. **Established
    renderings**:`. All six cells of the 260913 eval B produced one, and
    with only the `#` form recognised it survived the strip and was written
    into every handoff file above the canonical section, introducing nothing.

    COSMETIC ONLY (owner ruling 260913): this decides what is tidied out of
    the summary before it is stored and seeded, and nothing else. It is never
    an input to whether a compact succeeded — a reply is refused for being
    empty and for nothing else — so being wrong here costs a stray heading in
    the seed, never a thrown-away window. The judgement version of this
    question, `report_is_usable`, is gone; do not reintroduce one.

    Deliberately narrow all the same. A list number alone is NOT a label:
    models number the beats of a summary, so `1. Napoleon seizes power` is
    content. The line must carry decoration a sentence does not have —
    emphasis around the whole of it, or a trailing colon — and must not read
    as a sentence even then.
    """
    text = line.strip()
    if not text or len(text) > _MAX_TERM_LEN:
        return False
    if _HEADING.match(line):
        return True
    body = _LIST_NUMBER.sub("", text).strip()
    labelled = body.endswith(":") or bool(_EMPHASISED.match(body))
    if not labelled:
        return False
    core = body.rstrip(":").strip()
    emphasised = _EMPHASISED.match(core)
    if emphasised:
        core = emphasised.group(2).strip()
    if not core:
        return False
    return not core.endswith(_SENTENCE_END)


# A line that is nothing but a markup tag: the `<renderings>` fence the
# prompt asks for, and whatever else a model wraps a section in.
_TAG_ONLY = re.compile(r"^</?[A-Za-z][^>]*>$")


def _heading_key(line: str):
    """Which protocol section this line heads, or None.

    The one place an English word is matched, and it is matched against our
    own template's headers rather than against anything the model invented —
    they are protocol tokens, asked for verbatim in `handoff_prompt`. When
    they do not come back (a model that writes `## 摘要`, or none at all) this
    returns None for every line and the caller falls to the shape backoff,
    which is why no keyword list here has to cover any language but ours.
    """
    if not _HEADING.match(line):
        return None
    text = line.strip().lstrip("#").strip().rstrip(":").strip().lower()
    return text if text in ("summary", "style", "renderings") else None


def split_handoff_sections(text: str, with_style: bool = True) -> tuple[str, str]:
    """The reply split into (summary prose, style prose), by a ladder.

    1. The protocol headers, when they came back: each section is what sits
       under its header, and anything above the first header joins the
       summary.
    2. When they did not: the renderings are carved out by their arrows, and
       if a style was asked for and exactly two prose blocks remain, the
       LONGER is the summary and the shorter is the style (owner ruling
       260913). Models answer the template in order and a style note is
       three lines at most, so length separates them where the labels are
       gone.
    3. Otherwise everything is summary.

    The tie-breaks all lean the same way, because the costs are not
    symmetric: a summary misfiled as style is missing from the seed, where
    it was the whole point, while a style note misfiled into the summary
    costs a few tokens of a capped seed and nothing else. So an ambiguous
    reply — one block, or three, or a style that was never asked for — files
    as summary.

    This runs BEFORE `trim_handoff_prose`: the headings are what the split
    reads, and only then are they furniture. A user-fixed style is not
    overwritten by anything here — `with_style` is False in that case and
    the caller keeps its own note — so the guarantee is structural.
    """
    if not text or not text.strip():
        return "", ""
    lines = text.split("\n")
    marked = [
        (key, i)
        for i, key in ((i, _heading_key(l)) for i, l in enumerate(lines))
        if key
    ]
    if any(key in ("summary", "style") for key, _ in marked):
        sections = {}
        for n, (key, start) in enumerate(marked):
            end = marked[n + 1][1] if n + 1 < len(marked) else len(lines)
            sections.setdefault(key, []).extend(lines[start + 1 : end])
        preamble = lines[: marked[0][1]]
        summary = "\n".join(preamble + sections.get("summary", [])).strip()
        style = "\n".join(sections.get("style", [])).strip() if with_style else ""
        return summary, style

    if with_style:
        blocks = _prose_blocks(lines)
        # Sequence AND length, both: the template asks for the style after
        # the summary and caps it at three lines, so the style candidate is
        # the last block and the shorter one. When either test fails the
        # reply is not the two-section shape and everything files as summary
        # — the cheap mistake rather than the expensive one.
        if len(blocks) == 2 and len(blocks[1]) < len(blocks[0]):
            return blocks[0], blocks[1]
    return text.strip(), ""


def _prose_blocks(lines) -> list[str]:
    """The reply's prose, in blank-line separated blocks.

    Prose: the renderings are carved out by their arrows and our fence lines
    with them, and a block that is left holding nothing but a heading is not
    a block at all — otherwise an empty `<renderings>` fence would count as
    one of the two sections and the summary would be filed as the style.
    """
    kept = [
        line
        for line in lines
        if not _ARROW_SHAPED.search(line) and not _TAG_ONLY.match(line.strip())
    ]
    blocks = [b.strip() for b in re.split(r"\n\s*\n", "\n".join(kept)) if b.strip()]
    return [b for b in blocks if _has_substance(b.split("\n"))]


def trim_handoff_prose(text: str) -> str:
    """The report's prose: what is left once the furniture is removed.

    Used for both things the summary becomes — the snapshot section and the
    next window's seed — so they cannot disagree about what the report said.

    By SHAPE, never by keyword (owner ruling 260913). Models write their
    section headers in the target language, so `## Summary` and `## 摘要` and
    `## Résumé` are all the same line to a reader and unmatchable to a
    keyword list; matching the shape is the only version of this that works
    in every language. Four shapes go:

    * lines carrying an arrow — the renderings, tagged or loose, which are
      parsed into `glossary_lines` and would otherwise be recorded twice and
      sent again in the seed;
    * every markdown heading, whatever it says. A heading is navigation for
      a document, and the seed is not one: its preamble already says what
      the text under it is, so a header above it introduces nothing;
    * tag-only lines, which are our fence and not the report;
    * decorated label lines — the prompt's own section titles read back with
      emphasis or a trailing colon.

    Then blank runs collapse, so removing a block does not leave a hole.
    Nothing here judges the reply: what survives is seeded whatever it says.
    """
    if not text:
        return ""
    kept = []
    for raw in text.split("\n"):
        stripped = raw.strip()
        if _ARROW_SHAPED.search(raw):
            continue
        if _HEADING.match(raw) or _TAG_ONLY.match(stripped):
            continue
        if _is_block_label(raw):
            continue
        kept.append(raw)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# The snapshot file's format, and how a file written by another version is
# recognised. Before 260913 `<book>_handoff.md` was an append log: every
# window's report, one after another, so the file grew with the book and the
# same vocabulary was written into it once per compaction. It is now one
# overwritten snapshot — size O(terms), independent of the window count — and
# a log left by an older run must be *ignored* rather than parsed, because its
# last section is a window's report with no way to tell it from a truncated
# tail. Hence a version marker, and hence the refusal below when it is absent.
SNAPSHOT_VERSION = 1
_SNAPSHOT_MARKER = (
    "<!-- bilingual-book-maker handoff snapshot v{version}, window {window} -->"
)
_SNAPSHOT_RE = re.compile(
    r"<!--\s*bilingual-book-maker handoff snapshot "
    r"v(?P<version>\d+),\s*window\s*(?P<window>\d+)\s*-->"
)
# Section boundaries the parser reads. Markdown comments rather than the
# headings beside them: the headings are for whoever opens the file, and the
# summary is model prose that may contain headings of its own.
_SECTION_MARKERS = {
    "summary": "<!-- bbm:summary -->",
    "style": "<!-- bbm:style -->",
    "renderings": "<!-- bbm:renderings -->",
}
# The old append log's section marker, recognised only to say so.
_LOG_MARKER = "## Window "


class HandoffSnapshot(NamedTuple):
    """What `<book>_handoff.md` held, read back for `--resume`."""

    window: int
    summary: str
    style_note: str
    glossary: Glossary


@dataclass
class HandoffReport:
    """One window's handoff: the `<book>_handoff.md` snapshot, and the seed."""

    window: int
    summary: str
    # The renderings this run has established so far, pins included, rendered
    # canonically. Written to the snapshot so an operator can read back what
    # the run taught itself, and so `--resume --glossary-auto` can restore it
    # — but *not* into the seed: `Glossary.prompt_block` already delivers the
    # matching terms next to each unit, and replaying the whole list every
    # window is what made the seed grow without bound (owner ruling 260913).
    # It never reaches the book: the translation metadata stamp records the
    # `--glossary` file the operator wrote, nothing derived.
    glossary_lines: str = ""
    # A style the user fixed via --prompt's `style` field. It is not asked of
    # the model, so it is written in here instead — otherwise the next window
    # would inherit a report with no style at all.
    style_note: str = ""

    def has_summary(self) -> bool:
        """Whether anything at all was left after the renderings came out.

        Emptiness, and strictly emptiness (owner ruling 260913). Whether
        what is here reads like a summary is not asked and must not be: the
        reply is never judged for format, so a weak model's junk summary is
        stored and seeded, bounded by the cap, and corrects itself next
        window. What is *empty* is a different matter — there is nothing to
        write, so the snapshot already on disk is worth more than an empty
        one replacing it, and the next window opens unseeded.
        """
        return bool(self.summary and self.summary.strip())

    def seed_text(self, cap_tokens: int = SEED_CAP_TOKENS) -> str:
        """The next window's opening message: the report's summary, capped.

        The summary, and nothing else. Not the renderings (owner ruling
        260913): those reach the model per unit through
        `Glossary.prompt_block`, and replaying the whole list at the head of
        every window is what let the seed grow without bound. And not the
        style note either — a style is a standing instruction, so it rides
        the system channel once per window (`style_section`, folded into the
        turn on the routes that have no system channel) and is written to
        the handoff file verbatim so the model cannot erode it. Copying it
        here would put a standing instruction in a user turn as well.

        The cap is applied here, on the one method every route calls, so
        truncation is unconditional — layer (c) of the seed bound assumes the
        request cap was ignored, so it cannot itself be something a route can
        forget to ask for.

        It bounds the *report*, not the line introducing it: that line is a
        fixed ~30 tokens of ours, it is what tells the next window what it is
        reading, and cutting the instruction while keeping the summary would
        be backwards. The seed can therefore exceed `cap_tokens` by that
        much, which the no-death-loop margin (cap vs MIN_COMPACT_BUDGET)
        covers many times over.

        Empty when there is no summary to introduce — a reply that was
        nothing but renderings leaves nothing behind once they are parsed
        out, and the next window then opens the ordinary unseeded way rather
        than being told to keep faith with a report that is not there.
        """
        summary = truncate_seed(self.summary, cap_tokens)
        if not summary.strip():
            return ""
        return (
            "You are continuing a translation already in progress. "
            "The previous translator left this handoff report; keep names, "
            "terminology and register consistent with it.\n\n"
            f"{summary}"
        )

    def render(self) -> str:
        """The report body, as shown on screen and held in the snapshot."""
        body = self.summary
        if self.style_note:
            body += f"\n\n### Style\n\n{self.style_note}"
        if self.glossary_lines:
            body += f"\n\n### Established renderings\n\n{self.glossary_lines}"
        return body

    def snapshot_text(self) -> str:
        """The whole file: the marked-up body a later run can read back."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        parts = [
            _SNAPSHOT_MARKER.format(version=SNAPSHOT_VERSION, window=self.window),
            f"# Translator handoff — window {self.window}",
            f"*Written {stamp}. This file is a snapshot of the current "
            f"handoff: every compaction overwrites it.*",
            f"{_SECTION_MARKERS['summary']}\n## Summary\n\n{self.summary}",
        ]
        if self.style_note:
            parts.append(f"{_SECTION_MARKERS['style']}\n## Style\n\n{self.style_note}")
        if self.glossary_lines:
            parts.append(
                f"{_SECTION_MARKERS['renderings']}\n## Established renderings\n\n"
                f"{self.glossary_lines.rstrip()}"
            )
        return "\n\n".join(parts) + "\n"

    def write_snapshot(self, path) -> bool:
        """Overwrite `path` with this report, atomically. True if it was written.

        Written to a sibling temp file and moved into place with `os.replace`,
        which is atomic on every platform we run on: a crash — or a full disk
        — partway through must leave the *previous* snapshot standing rather
        than a half-written file that neither a person nor `parse_snapshot`
        can read.

        A report with an EMPTY summary is refused for the same reason, one
        step earlier: there is nothing to write, and the snapshot already on
        disk is worth more than a blank one. Emptiness is the whole of the
        test (owner ruling 260913) — a summary that reads like junk is
        written, because refusing it would be judging the reply's format.
        """
        path = Path(path)
        if not self.has_summary():
            print(
                f"[yellow]ℹ the handoff report carried no summary; keeping "
                f"the previous {path.name} rather than overwriting it"
                f"[/yellow]"
            )
            return False
        tmp = path.with_name(f"{path.name}.tmp")
        try:
            tmp.write_text(self.snapshot_text(), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            # Best effort: a leftover .tmp beside the book is litter, and the
            # next successful write overwrites it anyway.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return True


def _section(body: str, name: str) -> str:
    """One marked section of a snapshot, without its heading line."""
    marker = _SECTION_MARKERS[name]
    start = body.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    ends = [
        body.find(other, start)
        for other in _SECTION_MARKERS.values()
        if body.find(other, start) >= 0
    ]
    text = body[start : min(ends)] if ends else body[start:]
    lines = text.strip("\n").split("\n")
    # The human heading sits under the marker and is not part of the content.
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def parse_snapshot(path) -> HandoffSnapshot | None:
    """Read `<book>_handoff.md` back, or None when there is nothing to read.

    Never raises: this file is on the operator's disk, may have been
    hand-edited, and may have been written by a different version of the
    tool. Anything unreadable is one warning and no restore — a resume that
    cannot be seeded is a normal run, not a failed one.
    """
    path = Path(path)
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        # Not there, or not readable. Silent: a run with no handoff beside
        # the book is the ordinary case, not a degraded one.
        return None
    except (ValueError, UnicodeDecodeError):
        # Hand-edited and saved in another encoding. Worth a line, because
        # the operator has a file they believe is being read.
        print(
            f"[yellow]ℹ {path.name} is not valid UTF-8, so it cannot be read "
            f"back; ignoring it[/yellow]"
        )
        return None
    if not body.strip():
        return None
    match = _SNAPSHOT_RE.search(body)
    if not match or int(match.group("version")) != SNAPSHOT_VERSION:
        if _LOG_MARKER in body:
            print(
                f"[yellow]ℹ {path.name} is a handoff log from an older "
                f"version of this tool, not a snapshot; ignoring it. Delete "
                f"it to stop seeing this.[/yellow]"
            )
        else:
            print(
                f"[yellow]ℹ {path.name} is not a handoff snapshot this "
                f"version can read; ignoring it[/yellow]"
            )
        return None
    summary = _section(body, "summary")
    if not summary:
        print(f"[yellow]ℹ {path.name} holds no handoff summary; ignoring it[/yellow]")
        return None
    renderings = _section(body, "renderings")
    # Not capped by GLOSSARY_MAX_PER_COMPACT: that bounds one report, and
    # this section is the whole set the previous run accumulated — which is
    # unbounded by design, and is exactly what a resume wants back.
    entries, _ = _entries_from_lines(renderings.splitlines(), strict=True)
    return HandoffSnapshot(
        window=int(match.group("window")),
        summary=summary,
        style_note=_section(body, "style"),
        glossary=Glossary(entries),
    )


def handoff_path(book_path) -> Path:
    """`<book>_handoff.md`, beside the book."""
    path = Path(book_path)
    return path.with_name(f"{path.stem}_handoff.md")
