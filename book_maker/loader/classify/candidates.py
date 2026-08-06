"""Which signatures are worth asking about, and with what evidence.

Shared by every classification entry: the model asks an LLM about these,
the agent entry writes the same rows into the plan JSON for a human or a
coding agent to judge. Keeping the selection in one place is what makes
the two entries comparable — they answer the same question, differently.
"""

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


def clip(text):
    """Truncate a sample visibly: a silent mid-word cut reads as corrupted
    text and biases the judgment toward "skip"."""
    if len(text) <= SAMPLE_MAX_CHARS:
        return text
    return text[:SAMPLE_MAX_CHARS] + "…"


def uncertain_candidates(plan, overrides=None):
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
                "units": row["units"],
                "chars": row["chars"],
                "samples": [clip(t) for t in uniq[::step][:SAMPLES_PER_SIGNATURE]],
            }
        )
    return out


def gather_candidates(plan, overrides=None):
    """Every uncertain signature, largest first.

    Nothing is dropped: greedy partitioning made the candidate set the
    honest question list, and model mode pages through it rather than
    truncating (a silently dropped signature is a silently unreviewed one).
    """
    return sorted(uncertain_candidates(plan, overrides), key=lambda c: -c["chars"])
