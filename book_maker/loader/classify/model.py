"""Model entry: an LLM rules on the plan's uncertain signatures.

One structured request per page of signatures shows the model a few sample
lines each and asks whether readers want them translated. Verdicts become
ordinary signature actions in the plan JSON, so they survive resume, feed
the fingerprint, and lose to the user's own edits.

Only signature-level decisions are made here. Node-level residue (a roman
numeral inside a prose sentence) has no override mechanism.
"""

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
    """Normalize a raw response into {signature: verdict}, all values legal.

    Rungs below strict decoding cannot guarantee the schema was applied, so
    every field is treated as untrusted here rather than at the use site:
    a missing signature, a non-dict entry, or a verdict outside the enum all
    become "unsure" — the one verdict that changes nothing. This is the only
    place that decides what an ill-formed answer means.
    """
    if not isinstance(result, dict):
        raise PlanClassifyError(f"malformed classification response: {result!r}")
    linted = {}
    for cand in candidates:
        entry = result.get(cand["signature"])
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        linted[cand["signature"]] = verdict if verdict in VERDICTS else "unsure"
    return linted


def merge_verdicts(result, candidates):
    """Verdicts -> signature actions; only confident changes make one.

    Under greedy partitioning every candidate is already planned for
    translation, so only an affirmative "skip" changes anything; "translate"
    and "unsure" leave the plan alone.
    """
    linted = lint_verdicts(result, candidates)
    return {sig: "llm-skip" for sig, verdict in linted.items() if verdict == "skip"}


def _pages(candidates, size=PAGE_SIZE):
    for i in range(0, len(candidates), size):
        yield candidates[i : i + size]


def classify_plan(plan, translator, overrides=None, model=None):
    """Ask the translator about every uncertain signature, one page at a time.

    Returns ({signature: action}, candidates). Raises PlanClassifyError when
    no usable verdict can be had — the caller owns the policy (an explicitly
    chosen classifier model blocks, the default degrades with a notice). One
    failing page fails the whole run on purpose: a half-classified plan looks
    exactly like a fully classified one in the JSON.
    """
    candidates = gather_candidates(plan, overrides)
    if not candidates:
        return {}, []
    structured = getattr(translator, "structured_json", None)
    if structured is None:
        raise PlanClassifyError(
            f"{type(translator).__name__} has no structured-output support"
        )

    pages = list(_pages(candidates))
    if len(pages) > 1:
        print(
            f"classifying {len(candidates)} uncertain signature(s) "
            f"in {len(pages)} requests"
        )

    actions = {}
    for page in pages:
        try:
            result = structured(build_prompt(page), build_schema(page), model=model)
        except Exception as e:
            raise PlanClassifyError(f"classification request failed: {e}") from e
        if result is None:
            target = model or getattr(translator, "model", "the model")
            raise PlanClassifyError(f"'{target}' cannot produce JSON verdicts")
        actions.update(merge_verdicts(result, page))
    return actions, candidates
