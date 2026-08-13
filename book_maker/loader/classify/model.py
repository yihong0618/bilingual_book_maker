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
    """The translator cannot produce a usable verdict."""


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
                c["signature"]: {
                    "type": "object",
                    "description": (
                        f'Classification of the "{c["signature"]}" samples'
                    ),
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
            "required": [c["signature"] for c in candidates],
            "additionalProperties": False,
        },
    }


def build_prompt(candidates):
    # No current-verdict labels: the model judges the content cold instead
    # of anchoring on what the plan already decided.
    lines = [
        "You are preparing a bilingual EPUB. For each HTML tag signature "
        "below, decide whether it is better to translate its text or keep "
        "it as is.",
        'Answer "translate" for book content a reader wants translated: '
        "prose, verse, dialogue, headings, captions.",
        'Answer "skip" for text to keep as is: running heads, page or line '
        "numbers, manuscript sigla, cross-reference labels, publisher "
        "boilerplate, decorative markers.",
        'Answer "unsure" if the samples do not settle it.',
        "",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(f'{i}. "{c["signature"]}" ({c["units"]} occurrence(s)):')
        for s in c["samples"]:
            lines.append(f"   Sample: {s}")
    return "\n".join(lines)


def lint_verdicts(result, candidates):
    """Normalize a raw response into ({signature: verdict}, answered).

    No rung can guarantee the schema was applied, so every field is untrusted
    here rather than at the use site: a missing signature, a non-dict entry,
    or a verdict outside the enum all become "unsure" — the one verdict that
    changes nothing.

    The second return value is which signatures were *genuinely answered*.
    Without it a coerced "unsure" is indistinguishable from a deliberate one,
    and that is how a page of echoed schema (see `unwrap_schema_echo`) passed
    silently for twelve considered answers. Callers, not the linter, decide
    what to do about the difference.
    """
    if not isinstance(result, dict):
        raise PlanClassifyError(f"malformed classification response: {result!r}")
    verdicts, answered = {}, set()
    for cand in candidates:
        signature = cand["signature"]
        entry = result.get(signature)
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        if verdict in VERDICTS:
            verdicts[signature] = verdict
            answered.add(signature)
        else:
            verdicts[signature] = "unsure"
    return verdicts, answered


def verdict_actions(verdicts):
    """Verdicts -> signature actions; only confident changes make one.

    Under greedy partitioning every candidate is already planned for
    translation, so only an affirmative "skip" changes anything; "translate"
    and "unsure" leave the plan alone.
    """
    return {sig: "llm-skip" for sig, verdict in verdicts.items() if verdict == "skip"}


def merge_verdicts(result, candidates):
    """One raw response -> the actions it justifies."""
    verdicts, _ = lint_verdicts(result, candidates)
    return verdict_actions(verdicts)


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
            raise PlanClassifyError(
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
        raise PlanClassifyError(f"classification request failed: {e}") from e
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
    unanswered = [c for c in page if c["signature"] not in answered]
    if not unanswered:
        return resolved

    if len(page) == 1:
        raise PlanClassifyError(
            f"'{page[0]['signature']}' could not be classified: "
            f"{note or 'no legal verdict in the reply'}"
        )

    budget.splits += 1
    for part in _split(page, unanswered):
        resolved.update(_resolve(structured, part, model, budget))
    return resolved


def classify_plan(plan, translator, overrides=None, model=None):
    """Ask the translator about every uncertain signature, one page at a time.

    Returns ({signature: action}, candidates). Raises PlanClassifyError when
    some signature cannot be classified at all — the caller owns the policy
    (an explicitly chosen classifier model blocks, the default degrades with a
    notice).

    A plan that comes back is *fully* answered: every "unsure" in it is the
    model's own verdict, never a coercion artifact. That is the property the
    original all-or-nothing page rule was reaching for, restored without
    throwing away eleven good verdicts because the twelfth came back garbled.
    """
    candidates = gather_candidates(plan, overrides)
    if not candidates:
        return {}, []
    structured = _structured_json(translator)

    pages = list(_pages(candidates))
    if len(pages) > 1:
        print(
            f"classifying {len(candidates)} uncertain signature(s) "
            f"in {len(pages)} requests"
        )

    budget = _Budget(len(candidates))
    verdicts = {}
    for page in pages:
        verdicts.update(_resolve(structured, page, model, budget))

    if budget.splits:
        print(
            f"[yellow]ℹ {budget.splits} page(s) had to be re-asked in smaller "
            f"pieces: {budget.requests} request(s) for {len(pages)} page(s)"
            f"[/yellow]"
        )
    return verdict_actions(verdicts), candidates


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
