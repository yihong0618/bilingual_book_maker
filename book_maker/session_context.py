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
2. Anything that varies per unit rides in the fresh tail message, never in
   the prefix.

When the history reaches `--context-compact-at`, we ask the model for a
translator handoff report, start a new window seeded with it, and keep going.
The default budget is the point where session mode spends about what window
mode spent while carrying several times the context; see DEFAULT_COMPACT_BUDGET
below for the measured figures.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Estimated, never billed. A chars-based estimate is what the budget table was
# derived from, so the knob and the math agree by construction; reading
# `usage` back would make the trigger depend on numbers the budget never used.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
_LATIN_CHARS_PER_TOKEN = 4.0
_CJK_CHARS_PER_TOKEN = 1.7

# One budget for every model — but only for the *ungrouped* session run this
# was measured on (0.53x window mode on a cheap-cache endpoint, 1.10x on a
# dearer one, for several times the context). A grouped run derives a shorter
# budget below, and the 260905 whole-book eval retired the old worry that a
# shorter window trades cost for drift: across 44 handoff seams every
# recurring name held, because the report re-states the terminology each
# window — the only register drift observed was in the *longest*-window run.
DEFAULT_COMPACT_BUDGET = 8000

# Fitted 260905 (gpt-5.6-luna, official endpoint) for the *grouped* session
# run, where the request count is small enough that the compaction turns are a
# visible share of the bill rather than a rounding error.
HANDOFF_PROMPT_TOKENS = 72  # F_h: the compact request's own prompt
HANDOFF_REPORT_TOKENS = 538  # K: mean handoff report, 139 reports
OUTPUT_PRICE_RATIO = 4.0  # pi_o: output price / input price
HISTORY_GROWTH_PER_BUDGET_TOKEN = 1.4  # g/B: history growth per budget token
# The measured cost-vs-budget curve is a shallow bowl: flat across this range,
# with a steep wall on the long side. Clamping to it keeps a derived value
# inside the region the eval actually walked.
DERIVED_COMPACT_FLOOR = 1500
DERIVED_COMPACT_CEILING = 4000


def derived_compact_budget(request_budget: int) -> int:
    """Cost-optimal --context-compact-at for a grouped session run.

    C* = sqrt(2*g*(F_h + pi_o*K) / c_h): carried history costs c_h*C/2 per
    request and rises with C; the compaction rate g/C falls with C; the
    minimum balances them. Constants fitted 260905 (gpt-5.6-luna, official
    endpoint); c_h ~= 1.0 folded in. The measured curve is flat across
    [1500, 4000] and rises steeply past it, hence the clamp.
    """
    g = HISTORY_GROWTH_PER_BUDGET_TOKEN * request_budget
    c_star = math.sqrt(
        2 * g * (HANDOFF_PROMPT_TOKENS + OUTPUT_PRICE_RATIO * HANDOFF_REPORT_TOKENS)
    )
    return int(min(max(c_star, DERIVED_COMPACT_FLOOR), DERIVED_COMPACT_CEILING))


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
# the cap and tells the next window nothing.
_STYLE_REQUEST = (
    "Style — up to 3 lines of what translation style is used so far. "
    "Only note down what's different from general translation."
)


def handoff_prompt(with_style: bool = True) -> str:
    """The compact turn's request, built from the sections in play.

    Each section costs output tokens and invites the model to spend attention
    on it, so one is only asked for when something downstream consumes it —
    the style only when the user has not fixed one via `--prompt`'s `style`
    field. Numbering follows what is actually included, so a fixed style does
    not leave the summary alone under a "1." it does not need.
    """
    sections = [_SUMMARY_REQUEST]
    if with_style:
        sections.append(_STYLE_REQUEST)
    numbered = [f"{n}. {body}" for n, body in enumerate(sections, start=1)]
    return "\n\n".join([_PREAMBLE, *numbered])


@dataclass
class HandoffReport:
    """One window's handoff, persisted to `<book>_handoff.md` and re-seeded."""

    window: int
    summary: str
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
        return seed

    def render(self) -> str:
        """The report body: what is written to the file and shown on screen."""
        body = self.summary
        if self.style_note:
            body += f"\n\n### Style\n\n{self.style_note}"
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
