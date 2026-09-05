"""Model entry, part two: verdicts over a plain conversation.

`model.py` asks for JSON. That needs an endpoint which at least returns a
JSON object, and plenty do not: the codex route reaches a sidecar that was
never asked to, resellers and legacy gateways answer prose whatever
`response_format` says. Plan mode used to turn itself off there, which loses
the partition, the grouping and the coverage guard on exactly the books the
45-book sweep found them mattering on.

So this entry asks the same question with no JSON anywhere:

    trunk       what skip/translate mean and the reply format, sent once
    turn        three signatures, numbered; the reply must be exactly
                `skip,translate,unsure` — three comma-separated tokens
    append      the session is append-only, so a provider that caches its
                prefix pays for the trunk once per session

Three lead policies hold it together. `unsure` becomes `translate`, because
over-translation is recoverable and a wrong skip loses content. A reply that
does not parse is re-asked one unit at a time in the same session, and a
single that still fails becomes `translate` too. And there is no handoff at
the window edge: a verdict does not depend on earlier verdicts, so when the
estimated history reaches the compact budget the classifier simply restarts
with the trunk re-sent — no summary turn to pay for.
"""

from ...session_context import estimate_tokens
from .candidates import gather_candidates
from .model import PlanClassifyError, PlanClassifyFatal, describe_candidate

# Signatures per turn. Three is the user's shape: enough that the trunk is
# amortized, few enough that one bad token costs three re-asks and no more.
UNITS_PER_TURN = 3

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

# The two circuit breakers, both scoped to what has been asked so far.
FORMAT_WARNING = (
    "plan: this endpoint keeps missing the reply format — singles cost "
    "three times the turns; --plan-classify all skips classification"
)

# Fraction of triples that may fall back to singles, and of units that may
# stay unanswerable as singles, before each breaker trips.
FAILURE_RATE = 0.2

# How much evidence each breaker needs before it fires. A rate on its own
# trips on the first turn — one bad triple out of one is 100% — which is one
# flaky reply diagnosing an endpoint, and for the second breaker it would
# abandon a whole book's classification over it.
MIN_TRIPLES_BEFORE_WARNING = 5
MIN_UNITS_BEFORE_STOPPING = 15

TRUNK = """\
You are preparing a bilingual EPUB. I will show you content signatures from \
it, three at a time. For each one, decide whether it is better to translate \
its text or keep it as is.
Answer "translate" for book content a reader wants translated: prose, verse, \
dialogue, headings, captions.
Answer "skip" for text to keep as is: running heads, page or line numbers, \
manuscript sigla, cross-reference labels, publisher boilerplate, decorative \
markers.
Answer "unsure" only if the samples genuinely do not settle it. When they \
are merely thin, prefer translate: translating something unnecessary is \
cheap, losing content is not.
If the samples show more than one kind of content, answer translate — a \
signature verdict applies to every occurrence, and there is no \
per-occurrence override.
A "block:" signature is a block of text of that shape. An "inline:" \
signature is markup *inside* a sentence; skipping it leaves its text in \
place, untranslated, and splits the sentence around it — so skip one only \
when it is genuinely apparatus.

Reply with one verdict per signature, in the order I list them, separated by \
commas, and nothing else. Three signatures, three verdicts:

skip,translate,unsure

No numbering, no explanation, no words but the verdicts. When a message \
lists fewer than three signatures, reply with that many verdicts, in the \
same form."""


def build_trunk():
    """The instructions a classifier session opens with."""
    return TRUNK


def render_turn(candidates):
    """One turn's text: the signatures, numbered from 1, and nothing else.

    Nothing about the format is repeated here. Repeating it would grow every
    turn by the same lines the trunk already paid for once, which is the
    whole economy of an append-only session.
    """
    lines = []
    for i, candidate in enumerate(candidates, 1):
        lines.extend(describe_candidate(i, candidate))
    return "\n".join(lines)


def parse_verdicts(reply, count):
    """`count` verdicts from a reply, or None when it does not parse.

    Liberal in, strict out: the reply is trimmed, split on commas and the
    first `count` tokens taken; case, surrounding whitespace and a trailing
    period are tolerated, and anything past the `count`th token is ignored.
    There is deliberately no fuzzy matching — a token that is not one of the
    three words is a reply we did not understand, and guessing at it is how
    a hallucinated skip loses a chapter.
    """
    if not isinstance(reply, str):
        return None
    tokens = reply.strip().split(",")
    if len(tokens) < count:
        return None
    verdicts = []
    for token in tokens[:count]:
        word = token.strip().rstrip(".").strip().lower()
        if word not in VERDICTS:
            return None
        verdicts.append(word)
    return verdicts


def can_session_classify(translator):
    """Whether this route can hold a conversation for the classifier.

    Derived from the implementation, exactly as `supports_structured_json`
    is, so a route cannot advertise a session it never built. Asked without
    opening one: `classify_session` may cost a request (the codex route
    opens a thread), and this question is answered before plan mode has
    decided to spend anything.
    """
    from ...translator.base_translator import Base

    factory = getattr(type(translator), "classify_session", None)
    return factory is not None and factory is not Base.classify_session


def has_schema_verdict(translator, model=None):
    """Whether the endpoint's graded schema support reaches `json_object`.

    The verdict is cached by the capability ledger, so asking here costs
    nothing the run has not already paid for. A probe that cannot answer is
    not a verdict, and a route with no probe at all has none either.
    """
    probe = getattr(translator, "_probe_verdict", None)
    if probe is None:
        return False
    try:
        verdict = probe(model) if model else probe()
    except Exception:
        return False
    return verdict in ("strict", "shape", "json")


def session_classify_engaged(translator, model=None):
    """Whether classification should run over a plain session.

    Below `json_object` and able to hold a conversation. Schema-capable
    routes keep the JSON path; google and the other MT engines can hold no
    conversation, so plan classification stays off there as before.
    """
    return can_session_classify(translator) and not has_schema_verdict(
        translator, model
    )


class _Conversation:
    """The classifier's session, restarted rather than compacted.

    The trunk is sent with the first turn of each session and never again.
    When the estimated history reaches the compact budget the next turn
    opens a fresh session carrying the trunk — no handoff report is asked
    for, because a verdict depends on the signatures in front of it and on
    nothing that was said earlier.
    """

    def __init__(self, session, trunk, budget):
        self.session = session
        self.trunk = trunk
        self.budget = budget or 0
        self.tokens = 0
        self.sessions = 0
        self._open = False

    def ask(self, text):
        if not self._open:
            self.session.start(self.trunk)
            self.tokens = estimate_tokens(self.trunk)
            self.sessions += 1
            self._open = True
        reply = self.session.ask(text)
        self.tokens += estimate_tokens(text) + estimate_tokens(reply or "")
        if self.budget > 0 and self.tokens >= self.budget:
            # The next ask starts over. Closing here rather than opening the
            # replacement now keeps the last session of a run from paying
            # for a trunk nobody uses.
            self._open = False
        return reply


def _groups(candidates, size=UNITS_PER_TURN):
    for i in range(0, len(candidates), size):
        yield candidates[i : i + size]


# What a row not answered at all is recorded as. Not a verdict the model
# gave: `parse_verdicts` never returns it, and it exists so a defaulted row
# is distinguishable from a translated one in the plan JSON.
UNANSWERED = "unanswered"


def _decisions_for(candidates, verdicts):
    """Verdicts -> ledger decisions, with everything unsettled on translate.

    Both defaults are the same lead policy: a wrong skip loses content, a
    wrong translate costs a little money. The content_type says which of the
    three routes a row took, because that is the only part of the reasoning
    a three-token reply leaves room to record.
    """
    named = {
        "unsure": ("translate", NAMED_UNSURE),
        UNANSWERED: ("translate", NAMED_UNANSWERED),
    }
    return {
        candidate["key"]: named.get(verdict, (verdict, NAMED_BY_SESSION))
        for candidate, verdict in zip(candidates, verdicts)
    }


def _enough_to_stop(units, failed_units, total):
    """Whether the failure rate has been measured on enough to act on.

    `MIN_UNITS_BEFORE_STOPPING` attempts, normally. A book with fewer
    signatures than that would never reach the floor at all, so it stops on
    the other evidence there is: nothing asked has been answerable. Grinding
    singles through the rest of such a book buys three turns per signature
    and no verdicts.
    """
    if units >= MIN_UNITS_BEFORE_STOPPING:
        return True
    return total < MIN_UNITS_BEFORE_STOPPING and failed_units == units


def classify_over_session(ledger, translator, model=None, session=None):
    """Ask the translator about every undecided row, three at a time.

    Returns ``({key: (verdict, content_type)}, candidates)`` like the JSON
    entry. Every row comes back decided: `unsure`, an unparseable reply and
    a tripped breaker all resolve to `translate`, so this entry never leaves
    the run with questions it cannot answer — the coverage guard polices the
    skip side, which is the side that loses content.
    """
    candidates = gather_candidates(ledger)
    if not candidates:
        return {}, []
    if session is None:
        session = translator.classify_session(model=model)
    if session is None:
        raise PlanClassifyError(
            f"{type(translator).__name__} cannot hold a classifier session"
        )

    print(ENGAGE_WARNING, flush=True)
    conversation = _Conversation(session, build_trunk(), session.budget())

    decisions = {}
    triples = failed_triples = 0
    units = failed_units = 0
    warned = stopped = False

    for group in _groups(candidates):
        if stopped:
            decisions.update(_decisions_for(group, [UNANSWERED] * len(group)))
            continue
        reply = _ask(conversation, render_turn(group))
        triples += 1
        verdicts = parse_verdicts(reply, len(group))
        if verdicts is None:
            failed_triples += 1
            verdicts = []
            for candidate in group:
                single = parse_verdicts(_ask(conversation, render_turn([candidate])), 1)
                if single is None:
                    failed_units += 1
                    verdicts.append(UNANSWERED)
                else:
                    verdicts.append(single[0])
        units += len(group)
        decisions.update(_decisions_for(group, verdicts))

        if (
            not warned
            and triples >= MIN_TRIPLES_BEFORE_WARNING
            and failed_triples > FAILURE_RATE * triples
        ):
            print(FORMAT_WARNING, flush=True)
            warned = True
        if failed_units > FAILURE_RATE * units and _enough_to_stop(
            units, failed_units, len(candidates)
        ):
            stopped = True
            remaining = len(candidates) - units
            print(
                f"plan: this endpoint could not answer {failed_units} of "
                f"{units} signature(s) even one at a time"
                + (
                    f"; classification stops here and the remaining "
                    f"{remaining} are translated"
                    if remaining
                    else "; those rows are translated"
                ),
                flush=True,
            )

    return decisions, candidates


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
