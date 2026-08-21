"""The translate-everything entry, and what makes it different from a
default.

`most` reaches the same outcome a silent fallback would — every signature
translated — but it gets there by *recording a decision* on every open
question, attributed to the user who asked for it. That is the whole
difference, and it is why the mode exists: the failure this design was
built to remove is not "everything got translated", it is "something got
translated and nobody is on record as having decided it".

Because it asks nothing, it has no questions to persist and none to read
back: it writes no plan JSON, and it ignores one it finds rather than
half-applying an earlier run's skips. Those two policies live in
`MODE_POLICY` next door, so a mode's behaviour is one table row instead of
a condition repeated wherever the loader happens to branch.
"""

# The name a decider goes on the row under. "user" is accurate: the person
# who typed --plan-classify most made this call for every signature.
DECIDED_BY = "user"

# ...and the honest name for what they decided about. Nobody looked at this
# signature's samples, so the row must not claim a judgment about its
# content.
CONTENT_TYPE = "unclassified"


def decide_everything(ledger):
    """Record "translate" on every still-open row. Returns how many."""
    open_keys = ledger.undecided_keys()
    for key in open_keys:
        ledger.decide(key, "translate", DECIDED_BY, CONTENT_TYPE)
    return len(open_keys)
