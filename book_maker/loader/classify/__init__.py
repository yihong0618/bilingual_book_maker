"""Signature classification for plan mode — one module per entry.

The partition says what text a book *has*. Which of it a reader wants
translated is a judgment, and there are three ways to make it:

    most    no judgment, by explicit request: translate the whole partition
    model   an LLM rules, in-pipeline, one request per page of signatures
    agent   no API call: the plan JSON carries the evidence and a coding
            agent (or a person) fills in the actions, then the run repeats

There is deliberately no fourth mode where nobody rules and the code
translates whatever it could not rule out — that silent default is what
made a heuristic's blind spot look like a decision. `most` is the same
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
    classify_plan,
)
from .most import decide_everything

MODES = ("most", "model", "agent")


@dataclass(frozen=True)
class ModePolicy:
    """What a mode does with the plan *file*, in one place.

    These three answers used to be conditions on `self.plan_classify`
    scattered through a 230-line method — "most" alone owned five of them.
    A mode is now a row in the table below, so adding one (or changing what
    an existing one does) is an edit in a single place instead of a hunt
    for every branch that happened to name it.
    """

    name: str
    # Read a plan JSON left by an earlier run? "most" must not: half-
    # applying an old run's skips would make it mean "most, except what
    # somebody decided once".
    reads_saved_plan: bool
    # Write one? "most" asks nothing, so it has no questions to persist.
    writes_plan_file: bool
    # Exit code when the run stops to hand rows to a person or an agent.
    # For "agent" that stop *is* the job, so it is a success.
    handoff_exit_code: int


MODE_POLICY = {
    "most": ModePolicy("most", False, False, 1),
    "model": ModePolicy("model", True, True, 1),
    "agent": ModePolicy("agent", True, True, 0),
}


def mode_policy(name):
    """The policy for a classify mode. Unknown names fail loudly: the CLI
    constrains the flag, so one here means a caller invented a mode."""
    try:
        return MODE_POLICY[name]
    except KeyError:
        raise ValueError(
            f"unknown --plan-classify mode {name!r}; expected one of "
            f'{", ".join(MODES)}'
        ) from None


__all__ = [
    "MODES",
    "MODE_POLICY",
    "ModePolicy",
    "PlanClassifyError",
    "PlanClassifyFatal",
    "PlanUnresolvedError",
    "build_agent_prompt",
    "classify_plan",
    "decide_everything",
    "gather_candidates",
    "mode_policy",
]
