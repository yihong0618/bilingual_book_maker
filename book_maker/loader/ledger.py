"""The classification ledger: every question the plan asks, and its answer.

One row per *content signature* — a tag plus its classes, scoped to whether
the text sits in a block of that shape (``block:p.note``) or inside inline
markup of that shape (``inline:span.line-no``). The ledger is built from the
whole partition **before** any decision is applied, which is what makes it a
record rather than a report: a signature that gets skipped keeps its row, its
evidence, and its provenance, so the same file can be loaded, re-saved, and
audited without losing what was decided or why.

Deriving rows from surviving units instead (what schema 3 did) meant a
skipped signature's row vanished the moment its action took effect — the
file could not round-trip, and nothing recorded that a decision had ever
been made about it.

The row contract, in full::

    key           "block:p.note" | "inline:span.line-no"
    scope         "block" | "inline"
    units         how many occurrences carry this signature
    chars         how much text they hold in total
    pct           share of the book's accounted characters
    mean_chars    average occurrence length
    samples       up to 5 deduped excerpts, strided across all occurrences
    conditional_css  media/@supports conditions that hide this signature on
                  *some* devices — evidence, never a verdict
    parents       inline rows only: the block keys it appears inside
    action        "translate" | "skip" | null   (null = still a question)
    decided_by    "llm" | "agent" | "user" | null
    content_type  what the decider called this text, or null
    disposition   what actually happened once the action was applied

Provenance lives in ``decided_by`` alone. The old ``llm-skip`` action
encoded *who* decided inside *what* was decided, so an affirmative
"translate" verdict had nowhere to live and was dropped on load — the exact
trap that made a model's silence indistinguishable from its agreement.
"""

import json
import os
import tempfile

# Bump whenever a change alters which units a book partitions into (their
# order, count, or text), or the row contract itself: resume caches are
# positional over the unit list, and a plan JSON written under a different
# partition names rows that no longer exist.
PLAN_SCHEMA_VERSION = 4

# Evidence carried per row. Enough that an agent (or a person) can rule on a
# signature without unzipping the book; capped because the JSON is read into
# a context window.
SIGNATURE_SAMPLE_CAP = 5
SAMPLE_MAX_CHARS = 80

VALID_ACTIONS = frozenset(["translate", "skip"])
VALID_DECIDED_BY = frozenset(["llm", "agent", "user"])
VALID_SCOPES = ("block", "inline")


class PlanLedgerError(ValueError):
    """A plan JSON that cannot be trusted to mean what it says."""


def clean_content_type(value):
    """A blank content_type is an absent one: `""` records no judgment."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def check_row_state(key, action, decided_by, content_type):
    """The row state machine, enforced identically wherever a row is written
    or read back.

    Two legal states, and nothing between them:

    ``action is None``     nobody has ruled. ``decided_by`` must be null too —
                           crediting a decider for a decision that was not made
                           is how "an action happened but nobody recorded
                           deciding it" gets back in. ``content_type`` *may* be
                           set: a model that answered "unsure" still named what
                           it was looking at, and that evidence is worth keeping.

    ``action`` is a verb   somebody ruled, so the row must say who
                           (``decided_by``) and what they judged
                           (``content_type``). Naming the content before ruling
                           on it is the discipline the whole schema exists to
                           enforce; a verdict without it is a snap judgment
                           with no audit trail.
    """
    # `x in frozenset` needs a hashable x, and these fields arrive from a
    # file people are told to hand-edit: `"decided_by": ["llm"]` is one
    # bracket away, and it used to come back as a raw TypeError traceback
    # instead of this module's promise — a plan that cannot be trusted
    # fails loud, but clean.
    if action is not None and not isinstance(action, str):
        raise PlanLedgerError(
            f"{key}: action must be a string, not {type(action).__name__} "
            f"({action!r})"
        )
    if decided_by is not None and not isinstance(decided_by, str):
        raise PlanLedgerError(
            f"{key}: decided_by must be a string, not "
            f"{type(decided_by).__name__} ({decided_by!r})"
        )
    if action is not None and action not in VALID_ACTIONS:
        raise PlanLedgerError(
            f"{key}: invalid action {action!r} — use "
            f'{" or ".join(sorted(VALID_ACTIONS))}, or null for "still a question"'
        )
    if decided_by is not None and decided_by not in VALID_DECIDED_BY:
        raise PlanLedgerError(
            f"{key}: invalid decided_by {decided_by!r} — use one of "
            f'{", ".join(sorted(VALID_DECIDED_BY))}'
        )
    if action is None:
        if decided_by is not None:
            raise PlanLedgerError(
                f"{key}: decided_by is {decided_by!r} but action is null. "
                f"Nobody can be credited with a decision that was not made — "
                f"either set an action, or clear decided_by to null."
            )
        return
    if decided_by is None:
        raise PlanLedgerError(
            f'{key}: action is "{action}" with decided_by null. Every decision '
            f"has to name who made it: set decided_by to one of "
            f'{", ".join(sorted(VALID_DECIDED_BY))} ("user" if you decided it '
            f"by hand)."
        )
    if content_type is None:
        raise PlanLedgerError(
            f'{key}: action is "{action}" with no content_type. Name what the '
            f'text is ("prose", "running head", "page number", …) before ruling '
            f"on it — the name is the reasoning, and without it the verdict "
            f"cannot be audited."
        )


def clip_sample(text):
    """Truncate a sample visibly: a silent mid-word cut reads as corrupted
    text and biases the judgment toward "skip"."""
    if len(text) <= SAMPLE_MAX_CHARS:
        return text
    return text[:SAMPLE_MAX_CHARS] + "…"


def stride_samples(texts, cap=SIGNATURE_SAMPLE_CAP):
    """Up to `cap` deduped excerpts spread across *all* occurrences, always
    including the longest one.

    Striding, not slicing: the first N occurrences of a signature are the
    front matter of whatever document happens to sort first, which is how a
    103-occurrence code signature came to be judged on five consecutive
    lines of the same listing.

    The longest occurrence is reserved a slot because a stride can miss the
    only unit that shows what a row really holds. Measured live (tier 2,
    260813): `GhV-oeb-page.epub`'s `block:article` strided to five short
    credit lines — "Direction de l'ouvrage : …", "© 2010, Hachette Livre" —
    so the model named it "credits / publisher boilerplate" and skipped all
    4102 chars, including a 1552-char acknowledgements paragraph of ordinary
    French prose that no sample had shown. `mean_chars` hinted at it; nothing
    the model could *read* did.
    """
    uniq = list(dict.fromkeys(t for t in texts if t))
    if not uniq:
        return []
    if len(uniq) <= cap:
        chosen = uniq
    else:
        step = len(uniq) / cap
        chosen = [uniq[int(i * step)] for i in range(cap)]
        longest = max(uniq, key=len)
        if longest not in chosen:
            # the last slot, so the stride's own coverage of the book is kept
            chosen[-1] = longest
    return [clip_sample(t) for t in chosen]


def make_key(scope, signature):
    if scope not in VALID_SCOPES:
        raise ValueError(f"unknown ledger scope {scope!r}")
    return f"{scope}:{signature}"


def split_key(key):
    scope, _, signature = key.partition(":")
    return scope, signature


class Ledger:
    """Every signature in a book, with its evidence and its verdict."""

    def __init__(self, rows=None):
        # insertion-ordered; build() sorts by -chars before handing over
        self.rows = dict(rows or {})
        # inline key -> every block key it was seen inside. The row's own
        # "parents" is capped at three for whoever reads the file; a
        # disposition asks whether *any* block carrying this text survived,
        # and a truncated list would answer that question wrong.
        self.inline_parents = {}

    # ------------------------------------------------------------- build

    def add_occurrence(self, scope, signature, chars, text, parent_key=None):
        key = make_key(scope, signature)
        row = self.rows.get(key)
        if row is None:
            row = self.rows[key] = {
                "key": key,
                "scope": scope,
                "units": 0,
                "chars": 0,
                "_texts": [],
                "_parents": {},
                "conditional_css": [],
                "action": None,
                "decided_by": None,
                "content_type": None,
                "disposition": None,
            }
        row["units"] += 1
        row["chars"] += chars
        row["_texts"].append(text)
        if parent_key is not None:
            row["_parents"][parent_key] = row["_parents"].get(parent_key, 0) + 1
        return row

    def note_conditional_css(self, scope, signature, conditions):
        key = make_key(scope, signature)
        row = self.rows.get(key)
        if row is None or not conditions:
            return
        for condition in conditions:
            if condition not in row["conditional_css"]:
                row["conditional_css"].append(condition)

    def finalize(self, total_chars):
        """Compute derived evidence and settle row order (largest first)."""
        total = total_chars or 1
        for row in self.rows.values():
            row["pct"] = round(100 * row["chars"] / total, 1)
            row["mean_chars"] = round(row["chars"] / max(1, row["units"]), 1)
            row["samples"] = stride_samples(row.pop("_texts"))
            parents = row.pop("_parents")
            if row["scope"] == "inline":
                self.inline_parents[row["key"]] = set(parents)
                row["parents"] = [
                    {"key": k, "units": n}
                    for k, n in sorted(parents.items(), key=lambda kv: -kv[1])[:3]
                ]
        self.rows = dict(
            sorted(self.rows.items(), key=lambda kv: (-kv[1]["chars"], kv[0]))
        )
        return self

    # ---------------------------------------------------------- decisions

    def decide(self, key, action, decided_by, content_type=None):
        row = self.rows.get(key)
        if row is None:
            # Under ask-everything every key handed to a decider came from
            # this ledger, so an unknown one means the decider answered
            # about something the book does not contain.
            raise PlanLedgerError(
                f"decision for {key!r}, which this plan never asked about"
            )
        # a name carried in from an earlier "unsure" still names this content
        content = clean_content_type(content_type) or clean_content_type(
            row.get("content_type")
        )
        check_row_state(key, action, decided_by, content)
        row["action"] = action
        row["decided_by"] = decided_by
        row["content_type"] = content
        return row

    def note_content_type(self, key, content_type):
        """Record what a decider called this text *without* ruling on it.

        The "unsure" case: the model looked, named what it saw, and declined
        to decide. Dropping the name loses the only part of that answer worth
        anything and means paying to look again. The row stays a question —
        `action` and `decided_by` are untouched.
        """
        row = self.rows.get(key)
        name = clean_content_type(content_type)
        if row is None or name is None or row["action"] is not None:
            return None
        row["content_type"] = name
        return row

    def parents_of(self, key):
        """Every block key an inline row was seen inside.

        Complete for a ledger built from a partition; a ledger loaded from
        JSON has only the row's capped `parents` evidence to go on, which is
        why this is the one place that distinction is handled.
        """
        known = self.inline_parents.get(key)
        if known is not None:
            return set(known)
        row = self.rows.get(key) or {}
        return {
            p.get("key")
            for p in row.get("parents") or []
            if isinstance(p, dict) and p.get("key")
        }

    def undecided_keys(self):
        return [k for k, r in self.rows.items() if r["action"] is None]

    def skip_keys(self):
        """{key: decided_by} for every row whose text must not be translated."""
        return {
            k: r["decided_by"] for k, r in self.rows.items() if r["action"] == "skip"
        }

    def set_disposition(self, key, disposition):
        row = self.rows.get(key)
        if row is not None:
            row["disposition"] = disposition

    def require_decided(self, path):
        """Refuse to translate while any question is unanswered — or while any
        answer is unaccountable.

        The state check repeats what `decide` and `load` already enforce, on
        purpose: this is the last gate before money is spent, and a row that
        reached it in a state neither of those allows means something wrote
        the ledger without going through them.
        """
        undecided = self.undecided_keys()
        if undecided:
            listed = ", ".join(undecided[:10])
            raise PlanLedgerError(
                f"{path} has {len(undecided)} undecided signature(s) — {listed}. "
                f"Each null action is a question the plan is asking; set every one "
                f'to "translate" or "skip" (judge from its samples), then rerun. '
                f"Translation refuses to start while any remain."
            )
        for key, row in self.rows.items():
            check_row_state(
                key,
                row.get("action"),
                row.get("decided_by"),
                clean_content_type(row.get("content_type")),
            )

    # ------------------------------------------------------------ storage

    def to_dict(self, meta):
        data = dict(meta)
        data["schema_version"] = PLAN_SCHEMA_VERSION
        data["signatures"] = [dict(r) for r in self.rows.values()]
        return data

    def save(self, path, meta):
        """Write the ledger atomically.

        A partial classification is written *before* the run stops, so the
        file must never be observed half-written: a reader that finds
        truncated JSON cannot tell a crashed writer from a damaged plan.
        """
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(
            dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(meta), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path, expected_sha256=None):
        """Parse and validate a plan JSON. Undecided rows are allowed here —
        `require_decided` is the separate translate-time gate."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        saved_hash = data.get("book_sha256")
        if not saved_hash:
            raise PlanLedgerError(
                f"{path} carries no book_sha256 — it is damaged or not a plan "
                f"JSON; delete it and rerun to regenerate"
            )
        if expected_sha256 is not None and saved_hash != expected_sha256:
            raise PlanLedgerError(
                f"{path} was generated from a different book (sha256 "
                f"mismatch); delete it or regenerate with --plan-dry-run"
            )
        saved_version = data.get("schema_version")
        if saved_version != PLAN_SCHEMA_VERSION:
            # Schema 3 keyed rows by bare `tag.firstclass` over a different
            # unit partition. Applying those keys here would silently match
            # some rows, miss others, and translate a book nobody reviewed.
            raise PlanLedgerError(
                f"{path} was written by plan schema {saved_version}, and this "
                f"build writes schema {PLAN_SCHEMA_VERSION}; its signatures "
                f"and units no longer correspond. Delete it and rerun to "
                f"regenerate the plan."
            )
        rows_in = data.get("signatures")
        if not isinstance(rows_in, list) or not rows_in:
            raise PlanLedgerError(
                f"{path} has no signature rows — it is damaged or truncated; "
                f"delete it and rerun to regenerate"
            )

        rows, bad = {}, []
        for raw in rows_in:
            if not isinstance(raw, dict):
                bad.append(("<malformed row>", "not an object"))
                continue
            key = raw.get("key")
            if not isinstance(key, str) or not key.strip():
                bad.append(("<malformed row>", f"key {key!r}"))
                continue
            if key in rows:
                # last-wins would make the effective decision depend on JSON
                # order, and hide one of two contradictory edits
                bad.append((key, "duplicate key"))
                continue
            scope, signature = split_key(key)
            if scope not in VALID_SCOPES or not signature:
                bad.append((key, f"unusable key (scope {scope!r})"))
                continue
            if "scope" in raw and raw["scope"] != scope:
                # the key *is* the scope; a contradicting field means the row
                # was edited into something that names two different things
                bad.append((key, f'scope {raw["scope"]!r} contradicts the key'))
                continue
            # Every row this program writes carries these three. A missing one
            # is damage, not a default — the comment used to say so while
            # `.get(...)` quietly supplied null anyway.
            missing = [
                f for f in ("action", "decided_by", "content_type") if f not in raw
            ]
            if missing:
                bad.append((key, f'missing field(s) {", ".join(missing)}'))
                continue
            malformed_evidence = [
                name
                for name, kind in (
                    ("samples", list),
                    ("conditional_css", list),
                    ("parents", list),
                    ("units", int),
                    ("chars", int),
                )
                if name in raw and not isinstance(raw[name], kind)
            ]
            if malformed_evidence:
                bad.append((key, f'malformed {", ".join(malformed_evidence)}'))
                continue
            try:
                check_row_state(
                    key,
                    raw["action"],
                    raw["decided_by"],
                    clean_content_type(raw["content_type"]),
                )
            except PlanLedgerError as e:
                bad.append((key, str(e).split(": ", 1)[-1]))
                continue
            row = dict(raw)
            row["scope"] = scope
            row["content_type"] = clean_content_type(raw["content_type"])
            row.setdefault("disposition", None)
            row.setdefault("conditional_css", [])
            rows[key] = row
        if bad:
            listed = "\n  ".join(f"{k}: {v}" for k, v in bad[:5])
            more = f"\n  (+{len(bad) - 5} more)" if len(bad) > 5 else ""
            raise PlanLedgerError(
                f"{path} carries {len(bad)} invalid row(s):\n  {listed}{more}\n"
                f"Fix them in that file, or delete it and rerun to regenerate a "
                f"fresh plan."
            )
        return cls(rows)
