"""LLM classification of a plan's uncertain signatures.

The heuristics settle most of a book confidently; what remains is a residue
of signatures whose shape alone cannot decide them — short repetitive lines
that could be running heads or could be captions, two-letter table cells
that could be manuscript sigla or could be dialogue. One structured-output
request shows the model a few sample lines per uncertain signature and asks
whether readers want them translated. Verdicts become ordinary signature
actions in the plan JSON, so they survive resume, feed the fingerprint, and
lose to the user's own edits.

Only signature-level decisions are made here. Node-level residue (a roman
numeral inside a prose sentence) has no override mechanism and stays with
the heuristics.
"""

MAX_CANDIDATES = 12
# 5, not a bare minimum: a misjudged signature loses real content, and a
# few more sample lines per signature are cheap insurance on a single call
SAMPLES_PER_SIGNATURE = 5
SAMPLE_MAX_CHARS = 80
# Above this share of the book a signature is its prose spine; whether the
# spine gets translated is never in question.
UNCERTAIN_MAX_PCT = 10.0
UNCERTAIN_MEAN_CHARS = 50
UNCERTAIN_UNIQUE_RATIO = 0.5
# Headings are structural content, never uncertain: they are short and
# repetitive by nature (exactly the apparatus shape), heading-shaped
# apparatus is a print-era artifact, and a wrong "skip" silently loses
# every chapter title. gpt-4o-mini demoted h2.chapter_title on the first
# live run of this classifier.
CERTAIN_TAGS = frozenset(["h1", "h2", "h3", "h4", "h5", "h6"])

VERDICTS = ["translate", "skip", "unsure"]


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


class PlanClassifyError(Exception):
    """The translator cannot produce a structured verdict."""


def _clip(text):
    """Truncate a sample visibly: a silent mid-word cut reads as corrupted
    text and biases the model toward "skip"."""
    if len(text) <= SAMPLE_MAX_CHARS:
        return text
    return text[:SAMPLE_MAX_CHARS] + "…"


def translate_candidates(plan, overrides=None):
    """Planned-for-translation signatures whose shape leaves room for doubt.

    Candidates are small (never the prose spine) and either short-lined or
    repetitive — the shape of running heads, labels and apparatus. Poetry
    groups are exempt: verse is short-lined by nature and must translate.
    """
    overrides = overrides or {}
    total = plan.total_chars or 1
    stats = {}
    poetry_sigs = set()
    for f in plan.files:
        for u in f.units:
            if u.group_id is not None:
                poetry_sigs.add(u.signature)
            row = stats.setdefault(u.signature, {"units": 0, "chars": 0, "texts": []})
            row["units"] += 1
            row["chars"] += u.chars
            if len(row["texts"]) < 50:
                row["texts"].append(u.text)

    out = []
    for sig, row in stats.items():
        if sig in overrides or sig in poetry_sigs:
            continue
        if sig.split(".", 1)[0] in CERTAIN_TAGS:
            continue
        if 100 * row["chars"] / total >= UNCERTAIN_MAX_PCT:
            continue
        mean_chars = row["chars"] / row["units"]
        uniq = list(dict.fromkeys(row["texts"]))
        unique_ratio = len(uniq) / len(row["texts"])
        if mean_chars > UNCERTAIN_MEAN_CHARS and unique_ratio > UNCERTAIN_UNIQUE_RATIO:
            continue
        step = max(1, len(uniq) // SAMPLES_PER_SIGNATURE)
        out.append(
            {
                "signature": sig,
                "kind": "translate",
                "units": row["units"],
                "chars": row["chars"],
                "samples": [_clip(t) for t in uniq[::step][:SAMPLES_PER_SIGNATURE]],
            }
        )
    return out


def trivial_candidates(plan, overrides=None, exclude_sigs=frozenset()):
    """Trivially-skipped signatures that carry actual letters ("No" cells).

    A signature that also produced real units is excluded: one verdict cannot
    both demote the units and resurrect the trivia, so mixed signatures keep
    their heuristic split.
    """
    overrides = overrides or {}
    out = []
    for sig, row in plan.trivial_rows().items():
        if sig in overrides or sig in exclude_sigs:
            continue
        if not any(any(c.isalpha() for c in s) for s in row["samples"]):
            continue
        out.append(
            {
                "signature": sig,
                "kind": "trivial",
                "units": row["units"],
                "chars": row["chars"],
                "samples": [_clip(s) for s in row["samples"]],
            }
        )
    return out


def gather_candidates(plan, overrides=None):
    """All uncertain signatures, largest first, capped at MAX_CANDIDATES.

    Returns (candidates, dropped_count) — the cap must never truncate
    silently.
    """
    translate = translate_candidates(plan, overrides)
    unit_sigs = {u.signature for f in plan.files for u in f.units}
    trivial = trivial_candidates(plan, overrides, exclude_sigs=unit_sigs)
    cands = sorted(translate + trivial, key=lambda c: -c["chars"])
    dropped = max(0, len(cands) - MAX_CANDIDATES)
    return cands[:MAX_CANDIDATES], dropped


def build_prompt(candidates):
    # No current-verdict labels: the model judges the content cold instead
    # of anchoring on what the heuristics already decided.
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


def merge_verdicts(result, candidates):
    """Verdicts -> signature actions; only confident changes make one.

    The result is schema-pinned to {signature: {content_type, verdict}};
    content_type only exists so the model names the content before ruling
    on it, and is dropped here. "unsure" and status-quo verdicts produce no
    action: the heuristic decision stands unless the model affirmatively
    overturns it. Anything outside the enum (a shape-only endpoint ignoring
    value constraints) counts as unsure.
    """
    actions = {}
    for cand in candidates:
        entry = result.get(cand["signature"])
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        if cand["kind"] == "translate" and verdict == "skip":
            actions[cand["signature"]] = "llm-skip"
        elif cand["kind"] == "trivial" and verdict == "translate":
            actions[cand["signature"]] = "force-translate"
    return actions


def classify_plan(plan, translator, overrides=None, model=None):
    """Ask the translator about the plan's uncertain signatures.

    Returns ({signature: action}, candidates). Raises PlanClassifyError when
    no structured verdict can be had — the caller owns the policy (an
    explicitly chosen classifier model blocks, the default degrades with a
    notice).
    """
    candidates, dropped = gather_candidates(plan, overrides)
    if not candidates:
        return {}, []
    if dropped:
        print(
            f"note: classifying the {len(candidates)} largest uncertain "
            f"signature(s); {dropped} smaller one(s) keep the heuristic plan"
        )
    structured = getattr(translator, "structured_json", None)
    if structured is None:
        raise PlanClassifyError(
            f"{type(translator).__name__} has no structured-output support"
        )
    try:
        result = structured(
            build_prompt(candidates), build_schema(candidates), model=model
        )
    except Exception as e:
        raise PlanClassifyError(f"classification request failed: {e}") from e
    if result is None:
        target = model or getattr(translator, "model", "the model")
        raise PlanClassifyError(f"'{target}' does not honor JSON schemas")
    if not isinstance(result, dict):
        raise PlanClassifyError(f"malformed classification response: {result!r}")
    return merge_verdicts(result, candidates), candidates
