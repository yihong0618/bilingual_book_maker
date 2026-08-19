"""Which rows go to a decider, and in what order.

Every undecided row does. There is no shape test standing between a
signature and the question "should this be translated?" — that test was the
bug: it kept `pre.screen` (long, varied, therefore "prose") and `p.editor`
(short, repetitive, therefore "poetry") away from the classifier, and the
greedy `translate` default answered for both without anyone noticing it had.

A filter we tune is a judgment we own. Cost is not the reason to have one:
questions scale with a book's *signature count*, not its length, so even a
500-signature book is ~42 paged requests — noise beside translating it.
"""


def gather_candidates(ledger):
    """Every row still holding a question, largest first.

    Largest first because paging is the only thing that could ever truncate
    the list, and the rows that matter most should never be the ones a
    budget cuts. Nothing is dropped here.
    """
    return [ledger.rows[key] for key in ledger.undecided_keys()]
