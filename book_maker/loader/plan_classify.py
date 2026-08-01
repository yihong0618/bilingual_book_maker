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
SAMPLES_PER_SIGNATURE = 3
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

CLASSIFY_SCHEMA = {
    "name": "plan_signature_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "signature": {"type": "string"},
                        "verdict": {
                            "type": "string",
                            "enum": ["translate", "skip", "unsure"],
                        },
                    },
                    "required": ["signature", "verdict"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    },
}


class PlanClassifyError(Exception):
    """The translator cannot produce a structured verdict."""


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
                "samples": [
                    t[:SAMPLE_MAX_CHARS] for t in uniq[::step][:SAMPLES_PER_SIGNATURE]
                ],
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
                "samples": [s[:SAMPLE_MAX_CHARS] for s in row["samples"]],
            }
        )
    return out


def gather_candidates(plan, overrides=None):
    """All uncertain signatures, largest first, capped at MAX_CANDIDATES.

    Returns (candidates, dropped_count) — the cap must never truncate
    silently.
    """
    translate = translate_candidates(plan, overrides)
    trivial = trivial_candidates(
        plan, overrides, exclude_sigs={c["signature"] for c in translate}
    )
    cands = sorted(translate + trivial, key=lambda c: -c["chars"])
    dropped = max(0, len(cands) - MAX_CANDIDATES)
    return cands[:MAX_CANDIDATES], dropped


def build_prompt(candidates):
    lines = [
        "You are preparing a bilingual EPUB and deciding which text groups "
        "get a translation.",
        "Each numbered group below is an HTML signature (tag.class) with "
        "sample lines from the book.",
        'Answer "translate" if the samples are book content a reader wants '
        "translated: prose, verse, dialogue, headings, captions.",
        'Answer "skip" if they are apparatus that should stay untranslated: '
        "running heads, page or line numbers, manuscript sigla, "
        "cross-reference labels, publisher boilerplate, decorative markers.",
        'Answer "unsure" if the samples do not settle it.',
        "Return exactly one verdict per signature.",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        status = (
            "currently planned for translation"
            if c["kind"] == "translate"
            else "currently skipped as too short"
        )
        lines.append(
            f'{i}. signature "{c["signature"]}" ({c["units"]} occurrence(s), {status}):'
        )
        for s in c["samples"]:
            lines.append(f"   - {s!r}")
    return "\n".join(lines)


def merge_verdicts(result, candidates):
    """Verdicts -> signature actions; only confident changes make one.

    "unsure" and status-quo verdicts produce no action: the heuristic
    decision stands unless the model affirmatively overturns it. Verdicts
    for signatures never asked about are ignored.
    """
    by_sig = {c["signature"]: c for c in candidates}
    actions = {}
    for v in result.get("verdicts", []):
        cand = by_sig.get(v.get("signature"))
        if cand is None:
            continue
        verdict = v.get("verdict")
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
        result = structured(build_prompt(candidates), CLASSIFY_SCHEMA, model=model)
    except Exception as e:
        raise PlanClassifyError(f"classification request failed: {e}") from e
    if result is None:
        target = model or getattr(translator, "model", "the model")
        raise PlanClassifyError(f"'{target}' does not honor JSON schemas")
    return merge_verdicts(result, candidates), candidates
