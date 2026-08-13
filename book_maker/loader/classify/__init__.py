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

from .agent import build_agent_prompt
from .candidates import gather_candidates
from .model import (
    PlanClassifyError,
    PlanClassifyFatal,
    PlanUnresolvedError,
    classify_plan,
)

MODES = ("most", "model", "agent")

__all__ = [
    "MODES",
    "PlanClassifyError",
    "PlanClassifyFatal",
    "PlanUnresolvedError",
    "build_agent_prompt",
    "classify_plan",
    "gather_candidates",
]
