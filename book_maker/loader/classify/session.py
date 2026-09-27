"""Model entry, part two: verdicts over a plain conversation.

`model.py` asks for JSON. That needs an endpoint which at least returns a
JSON object, and plenty do not: the codex route reaches a sidecar that was
never asked to, resellers and legacy gateways answer prose whatever
`response_format` says. Plan mode used to turn itself off there, which loses
the partition, the grouping and the coverage guard on exactly the books the
45-book sweep found them mattering on.

So this entry asks the same question with no JSON anywhere:

    trunk       what skip/translate mean and the reply format, sent once
    turn        the current rung's signatures, numbered; the reply must be
                that many comma-separated verdicts and nothing else
    append      the session is append-only, so a provider that caches its
                prefix pays for the trunk once per session

How many signatures a turn carries is not a constant but a ladder —
**5 → 3 → 1 → none** — and the endpoint's own replies walk it down. Two
*consecutive* turns that miss the reply format degrade exactly one rung and
reset the counter, so a fail pair is never spent twice and reaching `none`
takes three separate pairs; there is no re-promotion. At `none` nothing more
is asked and every remaining signature is translated by policy. Each step
prints one `plan:` line naming the rung it left and the rung it is on.

That ladder replaces the two rate breakers the shipped version carried (a
format warning and a stop). A rate needs a floor of evidence before it means
anything, and below that floor the breakers were either silent while an
endpoint ground out three turns per signature, or abandoning a whole book's
classification on the strength of one flaky reply. Consecutive misses at a
known group size are the same evidence without either failure mode, and the
answer to them is a smaller question rather than a warning the operator has
to act on.

Three lead policies hold it together. `unsure` becomes `translate`, because
over-translation is recoverable and a wrong skip loses content. A reply that
does not parse at the 5- or 3-rung is re-asked one unit at a time in the same
session, and a single that still fails becomes `translate` too. And there is
no handoff at the window edge: a verdict does not depend on earlier verdicts,
so when the estimated history reaches the compact budget the classifier
simply restarts with the trunk re-sent — no summary turn to pay for.
"""

from tqdm import tqdm

from ...classifier import (
    SESSION_FIRST,
    Classifier,
    Conversation,
    can_session_classify,
)
from ...session_context import estimate_tokens
from .candidates import gather_candidates
from .model import (
    PlanClassifyError,
    PlanClassifyFatal,
    describe_candidate,
    verdict_rules,
)

# Signatures per turn, most first; `None` is the floor, where no turn is
# asked at all. Five is the top because the trunk is the expensive part of
# this conversation and amortizing it over more signatures is free while the
# endpoint holds the format. Three is the shape the shipped version ran at,
# kept as the first fallback. One is the shape that cannot be misaligned.
RUNGS = (5, 3, 1, None)

# Consecutive failed turns at the current rung before it degrades. They must
# be consecutive, and the counter resets on the degradation as well as on any
# success — so one fail pair is spent once, and reaching the floor takes three
# separate pairs. There is no re-promotion: an endpoint that lost the format
# at five is not asked to prove it again at the price of two more misses.
FAILS_BEFORE_DEGRADING = 2

VERDICTS = ("skip", "translate", "unsure")

# What a row's content_type says when the reply format carries no room to
# name what the text is. It is still the honest record of how the verdict was
# reached, and the plan JSON is audited on exactly that.
NAMED_BY_SESSION = "unnamed (plain-session verdict)"
NAMED_UNSURE = "unnamed (unsure — translated by policy)"
NAMED_UNANSWERED = "unnamed (no usable reply — translated by policy)"

# Printed once, when this entry takes the classification. It says what is
# different about this run: nothing constrains the reply, so the parser is
# the only thing standing between a stray word and a verdict.
ENGAGE_WARNING = (
    "plan: classifying over a plain session (this endpoint has no "
    "structured output); replies are checked verbatim"
)

# The progress line. `tqdm` renders exactly `Classifying epub tags 12/49...`
# under this bar format and rewrites it in place over `\r` — the same
# mechanism the loaders' bars use, and the same `disable=` switch, so
# `--quiet` silences it without a flag of its own.
PROGRESS_DESC = "Classifying epub tags"
PROGRESS_BAR_FORMAT = "{desc} {n_fmt}/{total_fmt}..."


def degrade_line(old, new):
    """The one line a step down the ladder prints.

    It names both rungs because the number is the only thing an operator can
    act on: a run that finished at one per turn bought five times the turns a
    run at five did, and nothing else in the log says so.
    """
    return (
        f"plan: this endpoint missed the reply format twice at {old} per "
        f"turn; continuing at {new} per turn"
    )


def stop_line(remaining):
    """The last step: below one per turn there is no smaller question."""
    line = (
        "plan: this endpoint missed the reply format twice even one at a "
        "time; classification stops here"
    )
    if remaining:
        line += f" and the remaining {remaining} signature(s) are translated"
    return line


TRUNK = "\n".join(
    [
        "You are preparing a bilingual EPUB. I will show you content "
        "signatures from it, a few at a time. For each one, decide whether it "
        "is better to translate its text or keep it as is.",
        *verdict_rules(),
        "",
        "Reply with one verdict per signature, in the order I list them, "
        "separated by commas, and nothing else. Five signatures, five "
        "verdicts:",
        "",
        "skip,translate,unsure,translate,skip",
        "",
        "No numbering, no explanation, no words but the verdicts. When a "
        "message lists fewer than five signatures, reply with that many "
        "verdicts, in the same form.",
    ]
)


def build_trunk():
    """The instructions a classifier session opens with.

    Count-agnostic on purpose: the rung can change mid-session while the
    session is append-only, so a trunk that named a per-turn count would be
    contradicted by the very next turn and could not be re-sent unchanged
    after a restart. It states the *form* and lets each turn say how many.
    """
    return TRUNK


def render_turn(candidates):
    """One turn's text: the signatures, numbered from 1, and nothing else.

    Nothing about the format is repeated here — not the vocabulary and not
    the count. Repeating it would grow every turn by the same lines the
    trunk already paid for once, which is the whole economy of an
    append-only session, and a per-turn count would have to change with the
    rung inside a history that can never be rewritten.
    """
    lines = []
    for i, candidate in enumerate(candidates, 1):
        lines.extend(describe_candidate(i, candidate))
    return "\n".join(lines)


# ------------------------------------------------------- the demonstration
#
# The trunk describes the reply format; a weak endpoint still answers the
# first turn in prose about half the time. So the conversation opens on a
# turn that has already been answered correctly — one shown exchange, in
# exactly the shape a real turn has, before the first real one is asked.
#
# It is deliberately one pair and no more. The session is append-only and its
# whole economy is a prefix a caching endpoint pays for once, so every line
# added here is bought again on every restart; one demonstration is what buys
# first-turn compliance, and a second buys nothing.

# Five synthetic signatures — the top rung's shape, so the demonstration
# matches the first turn that follows it — covering all three verdicts. The
# keys are book furniture that no real book emits: `.example-*` classes, so a
# human reading a transcript sees a demonstration rather than wondering which
# chapter these came from. Nothing ever parses this pair — it is static text
# on both routes.
EXAMPLE_CANDIDATES = (
    {
        # a clear skip: page numbers, many occurrences and almost no text
        "key": "block:span.example-folio",
        "units": 96,
        "chars": 288,
        "pct": 0.2,
        "mean_chars": 3,
        "samples": ["142", "143", "144"],
    },
    {
        # a clear translate: the book's own prose, most of its text
        "key": "block:p.example-body",
        "units": 214,
        "chars": 96300,
        "pct": 62.4,
        "mean_chars": 450,
        "samples": [
            "The lamp was still burning when she came back down the stairs.",
            "He had said nothing all evening, and there was nothing left to say.",
        ],
    },
    {
        # genuinely unsettled: one cryptic sample of an inline label that
        # some languages translate and some keep. Thin *and* ambiguous — the
        # trunk says merely-thin prefers translate, so the example must not
        # teach "thin means unsure".
        "key": "inline:abbr.example-ref",
        "units": 4,
        "chars": 24,
        "pct": 0.0,
        "mean_chars": 6,
        "samples": ["Fig. A"],
    },
    {
        # a translate that is short and repetitive, so size is visibly not
        # what decides it: chapter headings are book content the trunk names
        "key": "block:h2.example-chapter",
        "units": 12,
        "chars": 168,
        "pct": 0.1,
        "mean_chars": 14,
        "samples": ["Chapter One", "Chapter Two"],
    },
    {
        # the counterpart, and the pair that makes the point: the same shape
        # and nearly the same size as the headings above, skipped, because a
        # running head is apparatus rather than content
        "key": "block:p.example-runhead",
        "units": 148,
        "chars": 2072,
        "pct": 1.3,
        "mean_chars": 14,
        "samples": ["THE LAMP AND THE STAIR"],
    },
)

# The reply that pair is answered with: the exact form the trunk asks for,
# all three tokens shown, and nothing else. It is the same five verdicts the
# trunk's own example prints, so the two cannot teach different things.
EXAMPLE_REPLY = "skip,translate,unsure,translate,skip"


def build_example_turn():
    """The user half of the demonstration, rendered like any other turn.

    Through `render_turn`, not written out by hand, so the example cannot
    drift from the shape a real turn has — a demonstration of a format the
    turns no longer use would teach the endpoint the wrong thing.
    """
    return render_turn(EXAMPLE_CANDIDATES)


def example_tokens():
    """What the demonstration costs, counted wherever the trunk is.

    Both routes send it — one as a message pair, one folded into the trunk
    text — so the estimate that decides when to restart has to see it, or
    every session runs that much past its budget. Counted at the *inline*
    shape, which is the larger of the two (the pair plus its framing
    lines), so the estimate is never under what either route actually
    sends — an early restart costs one re-sent trunk, a late one risks
    the window.
    """
    # The inline composition already carries the turn, the reply and the
    # framing, so it is the whole cost in one string.
    return estimate_tokens(trunk_with_inline_example(""))


def trunk_with_inline_example(trunk=None):
    """The trunk with the demonstration folded in as prose.

    For a route whose conversation has no room for a fabricated assistant
    turn: the codex thread carries the trunk as its base instructions and its
    turns are all genuinely the model's own, so there is nowhere to put a
    reply nobody made. Same rule as the prompt-sectioning fallback — a
    channel the route lacks degrades to text, it never silently drops.

    `TRUNK` itself stays exactly what it was: the openai-shaped route gets
    the demonstration as real messages, and one shared trunk means the two
    routes cannot drift apart.
    """
    return (
        f"{build_trunk() if trunk is None else trunk}"
        "\n\nExample. Given:\n"
        f"{build_example_turn()}\n"
        "You reply exactly:\n"
        f"{EXAMPLE_REPLY}"
    )


def parse_verdicts(reply, count):
    """`count` verdicts from a reply, or None when it does not parse.

    Liberal in, strict out: the reply is trimmed and split on commas, and
    case, surrounding whitespace and a trailing period are tolerated. What
    is not tolerated is a count that does not match — *exactly* `count`
    tokens, all of them verdicts, or this is not a reply we understood.

    Taking the first `count` of a longer list is the tempting leniency, and
    it is the wrong one: a reply carrying more verdicts than there were
    signatures is a model that lost track of what it was answering, and its
    first tokens are no more trustworthy than its last. Five signatures
    answered with six verdicts do not mean the first five are right — they
    mean the ordering the whole verdict list depends on is in doubt, and a
    wrong skip loses content. So it falls to the one-at-a-time singles path,
    which re-asks each signature on its own and cannot be misaligned.
    """
    if not isinstance(reply, str):
        return None
    tokens = reply.strip().split(",")
    if len(tokens) != count:
        return None
    verdicts = []
    for token in tokens:
        word = token.strip().rstrip(".").strip().lower()
        if word not in VERDICTS:
            return None
        verdicts.append(word)
    return verdicts


class _Conversation(Conversation):
    """The classifier's session, restarted rather than compacted.

    `Conversation` with the plan's demonstrated exchange counted beside the
    trunk: it rides in every session the trunk does, on both routes.
    """

    def __init__(self, session, trunk, budget):
        super().__init__(session, trunk, budget, seed_tokens=example_tokens())


# What a row not answered at all is recorded as. Not a verdict the model
# gave: `parse_verdicts` never returns it, and it exists so a defaulted row
# is distinguishable from a translated one in the plan JSON.
UNANSWERED = "unanswered"


def _decisions_for(candidates, verdicts):
    """Verdicts -> ledger decisions, with everything unsettled on translate.

    Both defaults are the same lead policy: a wrong skip loses content, a
    wrong translate costs a little money. The content_type says which of the
    three routes a row took, because that is the only part of the reasoning
    a bare-verdict reply leaves room to record.
    """
    named = {
        "unsure": ("translate", NAMED_UNSURE),
        UNANSWERED: ("translate", NAMED_UNANSWERED),
    }
    return {
        candidate["key"]: named.get(verdict, (verdict, NAMED_BY_SESSION))
        for candidate, verdict in zip(candidates, verdicts)
    }


def _recover(conversation, group):
    """A missed turn, re-asked in the only shape that cannot be misaligned.

    At the 5- and 3-rungs the group is worth re-asking one signature at a
    time: a mis-shaped reply says nothing about whether the endpoint can
    answer *one* question, and the singles path has no ordering to lose. At
    the 1-rung there is no smaller shape left, so the signature is
    translated by policy rather than bought a second time at the same price.
    """
    if len(group) == 1:
        return [UNANSWERED]
    verdicts = []
    for candidate in group:
        single = parse_verdicts(_ask(conversation, render_turn([candidate])), 1)
        verdicts.append(UNANSWERED if single is None else single[0])
    return verdicts


def classify_over_session(ledger, translator, model=None, session=None):
    """Ask the translator about every undecided row, a rung's worth at a time.

    Returns ``({key: (verdict, content_type)}, candidates)`` like the JSON
    entry. Every row comes back decided: `unsure`, an unparsable reply and
    the bottom of the ladder all resolve to `translate`, so this entry never
    leaves the run with questions it cannot answer — the coverage guard
    polices the skip side, which is the side that loses content.

    `translator` may be a `Classifier`: the session is then its `session`
    backend's, opened on the classify endpoint (`--classify-model`). The
    ladder, its restarts and its recovery stay here: a turn's size is this
    entry's paging, not the request layer's.
    """
    candidates = gather_candidates(ledger)
    if not candidates:
        return {}, []
    if isinstance(translator, Classifier):
        classifier = translator
    else:
        classifier = Classifier(
            translator, model, prefer=SESSION_FIRST, session=session
        )
    translator = classifier.translator
    backend = classifier.backend("session")
    session = backend.open() if backend is not None else None
    if session is None:
        raise PlanClassifyError(
            f"{type(translator).__name__} cannot hold a classifier session"
        )

    print(ENGAGE_WARNING, flush=True)
    conversation = _Conversation(session, build_trunk(), session.budget())

    decisions = {}
    pending = list(candidates)
    rung = 0  # index into RUNGS
    misses = 0  # consecutive failed turns at RUNGS[rung]
    # `--quiet` reaches here on the translator, where the CLI already sets
    # it; there is no module flag to invent and nothing new to thread
    # through plan mode.
    progress = tqdm(
        total=len(candidates),
        desc=PROGRESS_DESC,
        bar_format=PROGRESS_BAR_FORMAT,
        disable=bool(getattr(translator, "quiet", False)),
    )
    try:
        while pending:
            size = RUNGS[rung]
            if size is None:
                # The floor. Nothing more is asked and the rest of the book
                # is translated by policy — the `--plan-classify all`
                # outcome, reached because the endpoint could not answer
                # rather than chosen.
                decisions.update(_decisions_for(pending, [UNANSWERED] * len(pending)))
                progress.update(len(pending))
                pending = []
                break

            group, pending = pending[:size], pending[size:]
            verdicts = parse_verdicts(
                _ask(conversation, render_turn(group)), len(group)
            )
            if verdicts is not None:
                # Any success clears the pair: the two misses that degrade a
                # rung have to be consecutive, or one flaky reply per book
                # would walk the ladder to the floor on its own.
                misses = 0
            else:
                misses += 1
                verdicts = _recover(conversation, group)
                if misses >= FAILS_BEFORE_DEGRADING:
                    # Reset as well as step: a fail pair is spent once, so
                    # the next rung is judged on its own two misses.
                    misses = 0
                    rung += 1
                    _announce_degradation(RUNGS[rung - 1], RUNGS[rung], len(pending))

            decisions.update(_decisions_for(group, verdicts))
            progress.update(len(group))
    finally:
        progress.close()

    return decisions, candidates


def _announce_degradation(old, new, remaining):
    """One line per step, naming where the ladder was and where it is now."""
    print(stop_line(remaining) if new is None else degrade_line(old, new), flush=True)


def _ask(conversation, text):
    """One turn, with transport failures marked terminal.

    Same rule as the JSON entry's `_ask_page`: auth, quota, a dead sidecar —
    asking again in a smaller shape cannot help, and a per-unit retry would
    multiply one rejection by the signature count.
    """
    try:
        return conversation.ask(text)
    except Exception as e:
        raise PlanClassifyFatal(f"classification turn failed: {e}") from e
