"""Signature classification for plan mode — one module per entry.

Greedy partitioning translates everything it cannot structurally rule out,
which is the safe default but keeps apparatus (running heads, page numbers,
sigla) in the book. Deciding what to drop is a judgment call, and there are
three ways to make it:

    none    no judgment: translate the whole partition (the default)
    model   an LLM rules, in-pipeline, one request per page of signatures
    agent   no API call: the plan JSON carries the evidence and a coding
            agent edits the actions, then the run is repeated

Each entry lives in its own module so none of them can quietly borrow
another's logic; `candidates.py` holds the one thing they must share — which
signatures are worth asking about, and with what evidence.
"""

from .agent import build_agent_prompt
from .candidates import gather_candidates
from .model import PlanClassifyError, classify_plan

MODES = ("none", "model", "agent")

__all__ = [
    "MODES",
    "PlanClassifyError",
    "build_agent_prompt",
    "classify_plan",
    "gather_candidates",
]
