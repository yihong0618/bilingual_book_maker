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
from dataclasses import dataclass
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
# Measured against the same 384-token maximum. Chosen: 1024 — 2.7x the
# largest summary observed, and 12.5% of the default 8192 budget. Deliberately
# looser than SEED_MAX_TOKENS: (c) is the backstop for endpoints that ignored
# (b), and a backstop that cuts reports the endpoint would have allowed is
# doing (b)'s job badly rather than its own. `seed_cap` narrows it on small
# budgets, so at the 2000 floor the effective cap is 1000.
SEED_CAP_TOKENS = 1024


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


def truncate_seed(text: str, cap_tokens: int) -> str:
    """`text` cut to `cap_tokens` estimated tokens, head first.

    Head first because models front-load a handoff report: the summary and
    the terminology come before the trailing elaboration, so cutting the tail
    loses the least (owner ruling 260913). Cuts at a line boundary so the
    next window is never seeded with half a sentence.
    """
    if not text or cap_tokens <= 0 or estimate_tokens(text) <= cap_tokens:
        return text
    lines = text.split("\n")
    kept: list[str] = []
    for line in lines:
        candidate = kept + [line]
        if estimate_tokens("\n".join(candidate)) > cap_tokens:
            break
        kept = candidate
    if not kept:
        # One line longer than the whole cap. Hand the next window the head of
        # it rather than nothing at all; `_LATIN_CHARS_PER_TOKEN` is the
        # conversion the estimate itself uses.
        kept = [lines[0][: int(cap_tokens * _LATIN_CHARS_PER_TOKEN)]]
    result = "\n".join(kept).rstrip()
    print(
        f"[yellow]ℹ the handoff report ran to {estimate_tokens(text)} "
        f"estimated tokens, over the {cap_tokens}-token seed cap; the next "
        f"window is seeded with its first {len(kept)} line(s)[/yellow]"
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
    f"Established renderings — at most {GLOSSARY_MAX_PER_COMPACT} names from "
    f"the passages above whose rendering is new or has changed. Never repeat "
    f"an entry you have already reported. If there are none, emit an empty "
    f"block. One per line as `term → translation # note` (the note is "
    f"optional), source term on the left. Wrap the list in <renderings> and "
    f"</renderings> tags so its start and end are unambiguous. This is the "
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
    field. Numbering follows what is actually included, so a fixed style does
    not leave the renderings labelled "3." in a two-section request.
    """
    sections = [_SUMMARY_REQUEST]
    if with_style:
        sections.append(_STYLE_REQUEST)
    if with_glossary:
        sections.append(_GLOSSARY_REQUEST)
    numbered = [f"{n}. {body}" for n, body in enumerate(sections, start=1)]
    # The size request closes the prompt rather than opening it: it is about
    # the answer as a whole, so it belongs after the sections it bounds.
    return "\n\n".join([_PREAMBLE, *numbered, _size_request()])


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


def _is_sane_entry(entry) -> bool:
    """Whether a parsed pair is a term and a rendering rather than prose.

    Applied inside the tags as well as outside them. The tags are a request,
    not a guarantee: a weak model answers the renderings section with a
    paragraph, a refusal, or a whole translated sentence that happens to
    contain an arrow. None of that may be stored, because a stored junk term
    is then injected into every later request whose text matches it.

    `Balaene → Balaene` is refused here too — an identity pair instructs the
    model to do what it would do anyway, and both sides of the 260913 eval's
    cells produced them.
    """
    if not entry.term or not entry.translation:
        return False
    if len(entry.term) > _MAX_TERM_LEN:
        return False
    if len(entry.translation) > _MAX_RENDERING_LEN:
        return False
    if entry.term.lower() == entry.translation.lower():
        return False
    return not entry.term.endswith(_SENTENCE_END)


def _mostly_cjk(text: str) -> bool:
    stripped = "".join(ch for ch in text if not ch.isspace())
    if not stripped:
        return False
    return len(_CJK.findall(stripped)) * 2 > len(stripped)


def _drop_reversed(entries):
    """`entries` without the pairs written backwards, and how many went.

    Observed live (260913 eval B, deepseek): `利维坦 → Leviathan` alongside
    correct pairs in the same block — the target rendering on the left and
    the source term on the right. Stored, such a pair tells a later window to
    translate the target language back into the source.

    The direction is read off the block itself rather than from the run's
    language, because a term is only "backwards" relative to which way this
    run translates: a genuine Chinese-to-English run writes every pair with
    CJK on the left. So a pair is dropped only when the rest of the block
    demonstrates the opposite direction. All-reversed blocks are therefore
    kept — there is no evidence in them to say which way round they are, and
    guessing costs more than the noise does. Flipping is never an option
    either: `X → Y` and `Y → X` are different instructions and the model's
    intent is not recoverable.
    """
    forward = [
        e for e in entries if not _mostly_cjk(e.term) and _mostly_cjk(e.translation)
    ]
    if not forward:
        return entries, 0
    kept = [
        e
        for e in entries
        if not (_mostly_cjk(e.term) and not _mostly_cjk(e.translation))
    ]
    return kept, len(entries) - len(kept)


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
        entries, dropped = _entries_from_lines(match.group(1).splitlines(), strict=True)
        if entries:
            return _harvest(entries, dropped, "tagged")

    entries, dropped = _entries_from_lines(text.splitlines(), strict=False)
    if entries:
        return _harvest(entries, dropped, "scanned")
    return HandoffGlossary(Glossary(), "missing")


def _harvest(entries, dropped, source) -> HandoffGlossary:
    """The pairs a report may contribute: backwards ones out, then capped.

    The cap keeps the *head* of the list because a report front-loads what
    matters — the same reason the seed is truncated head first.
    """
    entries, reversed_out = _drop_reversed(entries)
    kept = entries[:GLOSSARY_MAX_PER_COMPACT]
    return HandoffGlossary(
        Glossary(kept), source, dropped + reversed_out + len(entries) - len(kept)
    )


# Any markdown heading. The model writes the renderings heading in the target
# language, so it can only be recognised by where it sits, never by its words.
_HEADING = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
# A list number a model puts in front of it, mirroring the compact prompt's
# own numbering back into the report.
_LIST_NUMBER = re.compile(r"^\s{0,3}\d+[.)]\s*")


def _is_block_label(line: str) -> bool:
    """Whether `line` introduces a block rather than being part of the report.

    A markdown heading, or the shape models actually emit: the compact
    prompt's numbered section title, read back — `3. **Established
    renderings**:`. All six cells of the 260913 eval B produced one, and
    with only the `#` form recognised it survived the strip and was written
    into every handoff file above the canonical section, introducing nothing.

    Deliberately narrow. The line must be short, must carry a label's
    decoration — a number, emphasis, or a trailing colon — and must not read
    as a sentence, or an ordinary numbered line of the summary would be eaten
    along with the block.
    """
    text = line.strip()
    if not text or len(text) > _MAX_TERM_LEN:
        return False
    if _HEADING.match(line):
        return True
    body = _LIST_NUMBER.sub("", text).strip("*_ ").rstrip(":").strip()
    if not body or body == text:
        return False
    return not body.endswith(_SENTENCE_END)


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
    if end and _is_block_label(lines[end - 1]):
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
        if above >= 0 and _is_block_label(lines[above]):
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
        """Whether there is a report here at all.

        The compact reply is untrusted: cheap models answer it with nothing,
        with the prompt back, or with a renderings block and no prose. A
        report with no summary is not a cheaper handoff, it is a failed one —
        callers take the failed-compact path rather than seeding a window
        with boilerplate and overwriting a good snapshot with it.
        """
        return bool(self.summary and re.search(r"\w", self.summary))

    def seed_text(self, cap_tokens: int = SEED_CAP_TOKENS) -> str:
        """The next window's opening message: the report's prose, capped.

        Summary and style, never the renderings (owner ruling 260913): the
        established terms reach the model per unit through
        `Glossary.prompt_block`, and replaying the whole list at the head of
        every window is what let the seed grow without bound. The style half
        needs no code here — where the model was asked to describe one it is
        part of the summary prose, and a style the *operator* fixed rides the
        system message on every request instead (decision 8, pinned by
        `TestAFixedStyleRidesTheWindowStart`), so copying `style_note` in
        would put a standing instruction in a user turn as well.

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
        """
        return (
            "You are continuing a translation already in progress. "
            "The previous translator left this handoff report; keep names, "
            "terminology and register consistent with it.\n\n"
            f"{truncate_seed(self.summary, cap_tokens)}"
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

        A report with no summary is refused for the same reason, one step
        earlier: the good snapshot on disk is worth more than a junk one.
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
