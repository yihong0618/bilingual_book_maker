"""Model entry: an LLM rules on the plan's uncertain signatures.

One structured request per page of signatures shows the model a few sample
lines each and asks whether readers want them translated. Verdicts become
ordinary signature actions in the plan JSON, so they survive resume, feed
the fingerprint, and lose to the user's own edits.

Only signature-level decisions are made here. Node-level residue (a roman
numeral inside a prose sentence) has no override mechanism.
"""

from ...structured import StructuredJSONFailed
from .candidates import gather_candidates

# Signatures per request. Small on purpose: one page is one schema, and a
# schema with a hundred pinned properties degrades verdict quality long
# before it hits a token limit.
PAGE_SIZE = 12

VERDICTS = ["translate", "skip", "unsure"]


class PlanClassifyError(Exception):
    """The translator cannot produce a usable verdict.

    `fatal` marks the failures that dividing or retrying cannot help —
    auth, quota, a model that does not exist. Those abort the whole run at
    once instead of repeating the same rejection for every page.
    """

    fatal = False

    def __init__(self, *args):
        super().__init__(*args)
        # Per instance, never class-level: a dict on the class is shared by
        # every exception ever raised, so one failure's evidence would leak
        # into the next run's.
        #
        # {key: content_type} for rows a verdict *named* without ruling on
        # them. An "unsure" is a refusal to decide, not a refusal to look,
        # and the name it produced is evidence worth keeping across a later
        # failure — losing it means paying for the same look again.
        self.considered = {}
        # {key: verdict} answered before the failure, carried out through the
        # recursion so a page's other answers survive one signature nobody
        # can settle. Same principle as `considered`, one level up.
        self.verdicts = {}


class PlanClassifyFatal(PlanClassifyError):
    fatal = True


def build_schema(candidates):
    """One required {content_type, verdict} property per signature.

    Constrained decoding then guarantees exactly one verdict for every
    signature asked about — no hallucinated names, no silent omissions.
    `content_type` is declared before `verdict` on purpose: generation
    follows schema property order, so the model names what the samples are
    before committing to an answer instead of rationalizing one after.
    """
    return {
        "name": "plan_signature_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                c["key"]: {
                    "type": "object",
                    "description": (f'Classification of the "{c["key"]}" samples'),
                    "properties": {
                        "content_type": {
                            "type": "string",
                            "description": (
                                "What this text is in the book, e.g. "
                                "prose, verse, dialogue, heading, caption, "
                                "running head, page or line number, "
                                "manuscript sigla, cross-reference label, "
                                "publisher boilerplate, decorative marker"
                            ),
                        },
                        "verdict": {
                            "type": "string",
                            "enum": VERDICTS,
                            "description": (
                                '"translate" = book content a reader wants '
                                'translated; "skip" = keep as is; '
                                '"unsure" = the samples do not settle it'
                            ),
                        },
                    },
                    "required": ["content_type", "verdict"],
                    "additionalProperties": False,
                }
                for c in candidates
            },
            "required": [c["key"] for c in candidates],
            "additionalProperties": False,
        },
    }


def build_prompt(candidates):
    # No current-verdict labels: the model judges the content cold instead
    # of anchoring on what the plan already decided.
    lines = [
        "You are preparing a bilingual EPUB. For each content signature "
        "below, decide whether it is better to translate its text or keep "
        "it as is.",
        'Answer "translate" for book content a reader wants translated: '
        "prose, verse, dialogue, headings, captions.",
        'Answer "skip" for text to keep as is: running heads, page or line '
        "numbers, manuscript sigla, cross-reference labels, publisher "
        "boilerplate, decorative markers.",
        'Answer "unsure" only if the samples genuinely do not settle it. '
        "When they are merely thin, prefer translate: translating something "
        "unnecessary is cheap, losing content is not.",
        "If the samples show more than one kind of content, answer "
        "translate — a signature verdict applies to every occurrence, and "
        "there is no per-occurrence override.",
        'A "block:" signature is a block of text of that shape. An '
        '"inline:" signature is markup *inside* a sentence; skipping it '
        "leaves its text in place, untranslated, and splits the sentence "
        "around it — so skip one only when it is genuinely apparatus.",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        head = (
            f'{i}. "{c["key"]}" — {c["units"]} occurrence(s), '
            f'{c["chars"]} chars ({c.get("pct", 0)}% of the book), '
            f'mean {c.get("mean_chars", 0)} chars'
        )
        lines.append(head)
        for parent in c.get("parents") or []:
            lines.append(f'   Appears inside: {parent["key"]} ({parent["units"]})')
        for condition in c.get("conditional_css") or []:
            lines.append(f"   Hidden by CSS only under: {condition}")
        for sample in c["samples"]:
            lines.append(f"   Sample: {sample}")
    return "\n".join(lines)


def lint_verdicts(result, candidates):
    """Normalize a raw response into ({key: (verdict, content_type)}, answered).

    No rung can guarantee the schema was applied, so every field is untrusted
    here rather than at the use site: a missing key, a non-dict entry, a
    verdict outside the enum, or an empty content_type all become "unsure" —
    the one verdict that changes nothing on its own.

    A legal verdict with no content_type counts as *unanswered*. The field
    exists to make the model name what it is looking at before ruling on it;
    a reply that skipped the naming did not do the reasoning the schema asked
    for, and treating it as considered is how an echoed schema once passed
    for twelve answers.

    The second return value is which keys were genuinely answered. Without
    it a coerced "unsure" is indistinguishable from a deliberate one.
    """
    if not isinstance(result, dict):
        raise PlanClassifyError(f"malformed classification response: {result!r}")
    verdicts, answered = {}, set()
    for cand in candidates:
        key = cand["key"]
        entry = result.get(key)
        if not isinstance(entry, dict):
            entry = {}
        verdict = entry.get("verdict")
        content_type = entry.get("content_type")
        if not isinstance(content_type, str) or not content_type.strip():
            content_type = None
        if verdict in VERDICTS and content_type:
            verdicts[key] = (verdict, content_type.strip())
            answered.add(key)
        else:
            verdicts[key] = ("unsure", content_type)
    return verdicts, answered


def verdict_decisions(verdicts):
    """Verdicts -> ledger decisions.

    Every verdict is recorded, not only the ones that change what gets
    translated. An affirmative "translate" is the model saying it looked and
    agreed; schema 3 stored nothing for it, so agreement and silence were
    the same bytes on disk and a plan could not be audited at all.

    "unsure" stays undecided on purpose: it is the model refusing to answer,
    and the run stops to let a person or an agent answer instead.
    """
    return {
        key: (verdict, content_type)
        for key, (verdict, content_type) in verdicts.items()
        if verdict in ("translate", "skip")
    }


def verdict_names(verdicts):
    """``{key: content_type}`` for every row a verdict *named*.

    Includes the "unsure" ones, which `verdict_decisions` drops because they
    change nothing about what gets translated. The name is still evidence:
    it says the model looked, and what it thought it was looking at.
    """
    return {
        key: content_type
        for key, (_verdict, content_type) in verdicts.items()
        if content_type
    }


def _pages(candidates, size=PAGE_SIZE):
    for i in range(0, len(candidates), size):
        yield candidates[i : i + size]


def _answers_all(result, page):
    """The ladder's terminating condition: every signature got a verdict."""
    try:
        _, answered = lint_verdicts(result, page)
    except PlanClassifyError:
        return False
    return len(answered) == len(page)


def _split(page, unanswered):
    """Re-ask only what went unanswered; halve when nothing was answered.

    Halving is the fallback rather than the rule because a page that answered
    eleven of twelve should cost one more request, not seven.
    """
    if unanswered and len(unanswered) < len(page):
        return [unanswered]
    middle = len(page) // 2
    return [page[:middle], page[middle:]]


class _Budget:
    """Request accounting, and a backstop against a runaway recursion.

    The recursion is provably finite — every branch either answers or splits
    into strictly smaller pages — so hitting the cap means a bug, and paying
    for an unbounded number of requests to find out is not acceptable.
    """

    def __init__(self, total_candidates):
        self.cap = 4 * total_candidates + 8
        self.requests = 0
        self.splits = 0

    def charge(self):
        self.requests += 1
        if self.requests > self.cap:
            raise PlanClassifyFatal(
                f"classification exceeded its request budget "
                f"({self.cap}); refusing to keep spending"
            )


def _ask_page(structured, page, model):
    """One classification request. Returns (parsed result or None, why not)."""
    try:
        result = structured(
            build_prompt(page),
            build_schema(page),
            model=model,
            accept=lambda obj: _answers_all(obj, page),
        )
    except StructuredJSONFailed as e:
        # Every rung was tried and none produced JSON. Not terminal yet: a
        # smaller page is an easier request, so the caller divides first.
        return None, str(e)
    except PlanClassifyError:
        raise
    except Exception as e:
        # Auth, quota, transport, a model that does not exist: dividing cannot
        # help and would multiply the failure by the page count.
        raise PlanClassifyFatal(f"classification request failed: {e}") from e
    return result, None


def _resolve(structured, page, model, budget):
    """Verdicts for every signature in `page`, dividing until they are had.

    Composes with the rung ladder underneath: `structured` descends rungs for
    one request, this divides the request. Only a single signature that
    survives both is terminal — at that point the model has been shown one
    property described in prose, which is the easiest question we can ask.
    """
    budget.charge()
    result, note = _ask_page(structured, page, model)
    verdicts, answered = ({}, set())
    if result is not None:
        verdicts, answered = lint_verdicts(result, page)

    resolved = {sig: v for sig, v in verdicts.items() if sig in answered}
    unanswered = [c for c in page if c["key"] not in answered]
    if not unanswered:
        return resolved

    if len(page) == 1:
        raise PlanClassifyError(
            f"'{page[0]['key']}' could not be classified: "
            f"{note or 'no legal verdict in the reply'}"
        )

    budget.splits += 1
    failures = []
    for part in _split(page, unanswered):
        try:
            resolved.update(_resolve(structured, part, model, budget))
        except PlanClassifyError as e:
            # A branch that failed may still have answered some of what it
            # was asked before it got stuck. Those answers were requested,
            # paid for and linted; unwinding past them buys them again next
            # run — and buys nothing at all if the same row fails again.
            resolved.update(e.verdicts)
            if e.fatal:
                e.verdicts = dict(resolved)
                raise
            failures.append(str(e))
    if failures:
        error = PlanClassifyError("; ".join(failures))
        error.verdicts = dict(resolved)
        raise error
    return resolved


class PlanUnresolvedError(PlanClassifyError):
    """Classification finished without a verdict for every question.

    A subclass of PlanClassifyError: not deciding *is* a classification
    failure, and callers that only care that classification did not
    succeed keep working unchanged.

    Carries what *was* obtained so the caller can persist a partial ledger
    and hand the remainder to a person or an agent, rather than throwing
    away paid-for answers and silently translating with defaults.
    """

    def __init__(
        self, message, resolved=None, unresolved=None, rows=None, considered=None
    ):
        super().__init__(message)
        self.resolved = resolved or {}
        self.unresolved = list(unresolved or [])
        self.rows = list(rows or [])
        self.considered = dict(considered or {})


def classify_plan(ledger, translator, model=None):
    """Ask the translator about every undecided row, one page at a time.

    Returns ``({key: (verdict, content_type)}, candidates)``. Every answer is
    recorded, including affirmative "translate" ones.

    Raises PlanUnresolvedError when some row cannot be decided at all — with
    the answers already obtained attached, because the caller's job is to
    save them and stop, not to discard them and guess.
    """
    candidates = gather_candidates(ledger)
    if not candidates:
        return {}, []
    structured = _structured_json(translator)

    pages = list(_pages(candidates))
    if len(pages) > 1:
        print(f"classifying {len(candidates)} signature(s) in {len(pages)} requests")

    budget = _Budget(len(candidates))
    verdicts = {}
    failed = []
    for page in pages:
        try:
            verdicts.update(_resolve(structured, page, model, budget))
        except PlanClassifyError as e:
            # whatever this page did answer before it got stuck
            verdicts.update(e.verdicts)
            failed.append(str(e))
            if e.fatal:
                # auth, quota, a model that does not exist: every later page
                # would buy the same rejection, so stop asking. What earlier
                # pages answered is still true and still paid for, so it is
                # reported rather than discarded.
                if not verdict_decisions(verdicts):
                    # Nothing actionable — but earlier pages may still have
                    # *named* what they looked at. Carry those names out so
                    # the caller can persist them instead of re-buying them.
                    e.considered = verdict_names(verdicts)
                    raise
                break
            # One page that cannot be answered must not discard the pages
            # that were.

    if budget.splits:
        print(
            f"[yellow]ℹ {budget.splits} page(s) had to be re-asked in smaller "
            f"pieces: {budget.requests} request(s) for {len(pages)} page(s)"
            f"[/yellow]"
        )

    decisions = verdict_decisions(verdicts)
    unresolved = [c["key"] for c in candidates if c["key"] not in decisions]
    if unresolved:
        detail = f" ({'; '.join(failed)})" if failed else ""
        raise PlanUnresolvedError(
            f"{len(unresolved)} of {len(candidates)} signature(s) were not "
            f"decided{detail}",
            resolved=decisions,
            unresolved=unresolved,
            rows=candidates,
            considered=verdict_names(verdicts),
        )
    return decisions, candidates


def _structured_json(translator):
    """The translator's structured-question channel, or a loud refusal.

    Every LLM-backed translator has one now (the bottom rung is a plain
    prompt). Dedicated MT engines — google, deepl, caiyun, tencent transmart,
    qwen-mt, a custom translate endpoint — do not and never will: handing them
    a question returns a translation of the question.
    """
    structured = getattr(translator, "structured_json", None)
    supports = getattr(translator, "supports_structured_json", None)
    if structured is None or (supports is not None and not supports()):
        raise PlanClassifyError(
            f"{type(translator).__name__} has no structured-output support"
        )
    return structured
