"""Model entry: an LLM rules on the plan's uncertain signatures.

One structured request per page of signatures shows the model a few sample
lines each and asks whether readers want them translated. Verdicts become
ordinary signature actions in the plan JSON, so they survive resume, feed
the fingerprint, and lose to the user's own edits.

Only signature-level decisions are made here. Node-level residue (a roman
numeral inside a prose sentence) has no override mechanism.
"""

from ...classifier import Classifier, Question
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


def describe_candidate(index, c):
    """The lines that show one signature to a decider.

    Shared with the session entry (`session.py`), which asks the same
    question over a plain conversation: the evidence a verdict is made on
    must not depend on which channel carried it, or two runs of the same
    book would be answering different questions.
    """
    lines = [
        f'{index}. "{c["key"]}" — {c["units"]} occurrence(s), '
        f'{c["chars"]} chars ({c.get("pct", 0)}% of the book), '
        f'mean {c.get("mean_chars", 0)} chars'
    ]
    for parent in c.get("parents") or []:
        lines.append(f'   Appears inside: {parent["key"]} ({parent["units"]})')
    for condition in c.get("conditional_css") or []:
        lines.append(f"   Hidden by CSS only under: {condition}")
    for sample in c["samples"]:
        lines.append(f"   Sample: {sample}")
    return lines


def build_prompt(candidates):
    # No current-verdict labels: the model judges the content cold instead
    # of anchoring on what the plan already decided.
    lines = [
        "You are preparing a bilingual EPUB. For each content signature "
        "below, decide whether it is better to translate its text or keep "
        "it as is.",
        *verdict_rules(),
        "",
    ]
    for i, c in enumerate(candidates, 1):
        lines.extend(describe_candidate(i, c))
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


# A backend that asks one short typed question per signature over a shared
# state (jev) is sent these instead of `build_prompt`'s paragraph (packet J,
# 260924): the state is the numbered signatures alone (`build_context`), each
# question points at one of them (`candidate_pointer`), and each answer is
# described by the prompt's own words (`CRITERIA`, from which `build_prompt`
# and the session trunk build their answer sentences; the assembled texts are
# pinned in tests/test_classify_prompt_pin.py). `build_prompt` is what the
# schema backend sends, and it is audited
# (docs/260920-feat-CLASSIFY_LADDER_PROMPT_AUDIT.md). There is no "unsure"
# option on that channel: a low-confidence answer falls back instead.
CRITERIA = {
    "translate": (
        "book content a reader wants translated: prose, verse, dialogue, "
        "headings, captions"
    ),
    "skip": (
        "text to keep as is: running heads, page or line numbers, manuscript "
        "sigla, cross-reference labels, publisher boilerplate, decorative "
        "markers"
    ),
}


def verdict_rules():
    """The lines after the opening sentence that every plan-classifier
    prompt carries: `build_prompt` (the schema channel) and the session
    `TRUNK`. The two answer sentences are `CRITERIA`'s own words, so a Jev
    option and the prompt cannot describe an answer differently.
    """
    return [
        *(f'Answer "{label}" for {words}.' for label, words in CRITERIA.items()),
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
    ]


POINTER = (
    'Signature {index} ("{key}"): translate its text, or skip it (keep it as '
    "is)? Answer translate when its samples are thin or show more than one "
    "kind of content."
)
# Appended to POINTER for an "inline:" signature only.
POINTER_INLINE = (
    " It is markup inside a sentence: skip it only when it is genuinely " "apparatus."
)


def build_context(candidates):
    """The signatures as `build_prompt` numbers them, without its paragraph."""
    lines = []
    for i, c in enumerate(candidates, 1):
        lines.extend(describe_candidate(i, c))
    return "\n".join(lines)


def candidate_pointer(index, c):
    """The short question about signature `index` of `build_context`."""
    text = POINTER.format(index=index, key=c["key"])
    if c["key"].startswith("inline:"):
        text += POINTER_INLINE
    return text


def page_question(page):
    """The page as a `Question`: this module's prompt and schema, verbatim.

    Each candidate may answer with a `{content_type, verdict}` object whose
    `verdict` is one of `VERDICTS`; `unsure` is the answer that settles
    nothing. `context`, `per_candidate` and `criteria` are the lean form a
    backend that asks one question per candidate over a shared state reads
    (jev); the other backends send `prompt`.
    """
    return Question(
        prompt=build_prompt(page),
        schema=build_schema(page),
        candidates={c["key"]: tuple(VERDICTS) for c in page},
        field="verdict",
        abstain="unsure",
        accept=lambda obj: _answers_all(obj, page),
        per_candidate={
            c["key"]: candidate_pointer(i, c) for i, c in enumerate(page, 1)
        },
        context=build_context(page),
        criteria=dict(CRITERIA),
        # a wrong translate costs tokens, a wrong skip loses content
        fallback="translate",
    )


# What a row's content_type says when the backend names nothing (jev answers
# with a label and a probability, no words): how the verdict was reached,
# which is what the plan JSON is audited on.
NAMED_BY_JEV = "unnamed (jev verdict {verdict}, confidence {confidence:.2f})"
# The same, for an answer the backend's confidence gate replaced with the
# question's fallback: what jev chose, how sure it was, what was recorded.
NAMED_BY_JEV_FELL_BACK = (
    "unnamed (jev verdict {choice} at confidence {confidence:.2f}, below the "
    "gate: {verdict})"
)


def _named(answer):
    """`answer`'s values as `{key: {content_type, verdict}}` entries.

    A backend that returns bare labels (jev) gets its content_type written
    from its own probability; an object answer is passed through, and the
    values the shared lint set aside as invalid ride along so the name a
    reply gave an out-of-enum verdict is still recorded as evidence.
    """
    entries = {**answer.values, **answer.invalid}
    fell_back = getattr(answer.raw, "fell_back", None) or {}

    def name(key, value):
        confidence = answer.confidence.get(key, 0.0)
        if key in fell_back:
            return NAMED_BY_JEV_FELL_BACK.format(
                choice=fell_back[key], confidence=confidence, verdict=value
            )
        return NAMED_BY_JEV.format(verdict=value, confidence=confidence)

    return {
        key: (
            value
            if isinstance(value, dict)
            else {"verdict": value, "content_type": name(key, value)}
        )
        for key, value in entries.items()
    }


def _ask_page(classifier, page, model=None):
    """One classification request. Returns (parsed result or None, why not)."""
    try:
        answer = classifier.ask(page_question(page))
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
    if not isinstance(answer.raw, dict):
        # a reply that is not an object at all: `lint_verdicts` names it
        return answer.raw, None
    return _named(answer), None


def _resolve(classifier, page, model, budget):
    """Verdicts for every signature in `page`, dividing until they are had.

    Composes with the rung ladder underneath: the classifier's schema
    backend descends rungs for
    one request, this divides the request. Only a single signature that
    survives both is terminal — at that point the model has been shown one
    property described in prose, which is the easiest question we can ask.
    """
    budget.charge()
    result, note = _ask_page(classifier, page, model)
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
            resolved.update(_resolve(classifier, part, model, budget))
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
    classifier = _classifier(translator, model)

    pages = list(_pages(candidates))
    if len(pages) > 1:
        # flushed: with stdout redirected, a stalled request would otherwise
        # leave the log ending before this line, hiding what was running
        print(
            f"classifying {len(candidates)} signature(s) in {len(pages)} requests",
            flush=True,
        )

    budget = _Budget(len(candidates))
    verdicts = {}
    failed = []
    for page in pages:
        try:
            verdicts.update(_resolve(classifier, page, model, budget))
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


def _classifier(translator, model=None):
    """The classifier to ask, or a loud refusal when nothing can answer.

    `translator` is a `Classifier` (the run's classify endpoint) or a bare
    translator, which is asked through its own backends. Every LLM-backed
    translator has a structured channel now (the bottom rung is a plain
    prompt). Dedicated MT engines — google, deepl, caiyun, tencent transmart,
    qwen-mt, a custom translate endpoint — do not and never will: handing them
    a question returns a translation of the question.
    """
    if isinstance(translator, Classifier):
        classifier = translator
    else:
        classifier = Classifier(translator, model)
    if classifier.backend("schema") is None and classifier.backend("jev") is None:
        raise PlanClassifyError(
            f"{type(classifier.translator).__name__} has no structured-output "
            f"support"
        )
    return classifier
