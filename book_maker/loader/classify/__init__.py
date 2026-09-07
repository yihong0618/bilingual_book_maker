"""Signature classification for plan mode — one module per entry.

The partition says what text a book *has*. Which of it a reader wants
translated is a judgment, and there are three ways to make it:

    all     no judgment, by explicit request: translate the whole partition
    model   an LLM rules, in-pipeline, one request per page of signatures
    agent   no API call: the plan JSON carries the evidence and a coding
            agent (or a person) fills in the actions, then the run repeats

`model` asks in one of two ways, and which one is not a mode the user picks:
an endpoint whose graded schema support reaches `json_object` is asked for
JSON (`model.py`), and one below that which can still hold a conversation is
asked over a plain session (`session.py`). The question, the evidence and the
verdicts are the same either way; only the wire format differs.

There is deliberately no fourth mode where nobody rules and the code
translates whatever it could not rule out — that silent default is what
made a heuristic's blind spot look like a decision. `all` is the same
outcome chosen out loud.

Each entry lives in its own module so none of them can quietly borrow
another's logic; `candidates.py` holds the one thing they share — which
rows go to a decider.
"""

from dataclasses import dataclass

from .agent import build_agent_prompt
from .candidates import gather_candidates
from .model import (
    PlanClassifyError,
    PlanClassifyFatal,
    PlanUnresolvedError,
    classify_plan as classify_with_schema,
)
from .session import (
    can_session_classify,
    classify_over_session,
    session_classify_engaged,
)
from .all import decide_everything

MODES = ("all", "model", "agent")

# "The plan was written, nothing was translated, rerun to translate." Its own
# code because the alternatives both lie to a script: 0 is what a finished
# translation returns, and agent mode reaches one or the other from the very
# same command line, so a caller reading 0 cannot tell a handed-off book from
# a translated one. A failure code would be the opposite lie — the handoff is
# the mode working as designed, and nothing needs fixing before the rerun.
# 3 keeps clear of 1 (this project's failures) and of 2 (argparse's usage
# error), and no other path here returns it.
PLAN_HANDOFF_EXIT_CODE = 3


@dataclass(frozen=True)
class ModePolicy:
    """What a mode does with the plan *file*, in one place.

    These three answers used to be conditions on `self.plan_classify`
    scattered through a 230-line method — "all" alone owned five of them.
    A mode is now a row in the table below, so adding one (or changing what
    an existing one does) is an edit in a single place instead of a hunt
    for every branch that happened to name it.
    """

    name: str
    # Read a plan JSON left by an earlier run? "all" must not: half-
    # applying an old run's skips would make it mean "all, except what
    # somebody decided once".
    reads_saved_plan: bool
    # Write one? "all" asks nothing, so it has no questions to persist.
    writes_plan_file: bool
    # Exit code when the run stops to hand rows to a person or an agent.
    # For "agent" that stop *is* the job, so it reports the handoff rather
    # than a failure. For the others it is a failure: they were asked to
    # settle the rows themselves and did not, so nothing but a fix will get
    # the book translated.
    handoff_exit_code: int


MODE_POLICY = {
    "all": ModePolicy("all", False, False, 1),
    "model": ModePolicy("model", True, True, 1),
    "agent": ModePolicy("agent", True, True, PLAN_HANDOFF_EXIT_CODE),
}


def mode_policy(name):
    """The policy for a classify mode. Unknown names fail loudly: the CLI
    constrains the flag, so one here means a caller invented a mode."""
    try:
        return MODE_POLICY[name]
    except KeyError:
        if name == "none":
            raise ValueError(
                "plan mode needs a classify mode: --plan-classify all, model "
                "or agent ('none' means no plan mode)"
            ) from None
        raise ValueError(
            f"unknown --plan-classify mode {name!r}; expected one of "
            f'{", ".join(MODES)}'
        ) from None


def classify_plan(ledger, translator, model=None):
    """Ask this translator for verdicts, in whichever way it can answer.

    The choice is the endpoint's, not the user's: a route that produces no
    JSON object cannot be asked for one, and asking anyway is what used to
    turn plan mode off there. Nothing about the two paths differs above this
    line — both return ``({key: (verdict, content_type)}, candidates)``.
    """
    if session_classify_engaged(translator, model):
        return classify_over_session(ledger, translator, model=model)
    return classify_with_schema(ledger, translator, model=model)


__all__ = [
    "MODES",
    "MODE_POLICY",
    "PLAN_HANDOFF_EXIT_CODE",
    "ModePolicy",
    "PlanClassifyError",
    "PlanClassifyFatal",
    "PlanUnresolvedError",
    "build_agent_prompt",
    "can_session_classify",
    "classify_over_session",
    "classify_plan",
    "classify_with_schema",
    "decide_everything",
    "gather_candidates",
    "mode_policy",
    "session_classify_engaged",
]
