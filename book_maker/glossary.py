"""Pinned vocabulary: a term the user fixes to one translation.

Two consumers share this module: the `--glossary` / `--terminology` file the
user maintains across books, and (behind `--glossary-auto`) the renderings a
session-mode compact turn hands off. Both end up as a `Glossary`, and both
reach the model the same way — through `prompt_block`, which emits *only* the
terms that actually occur in the unit being translated.

The two are kept apart on purpose, and only the file half is ever recorded
anywhere: the pinned file is something the operator wrote and can be shown
alongside the book, while the derived half is a runtime artefact of one run's
compaction turns and never leaves the process.

That "only the hits" rule is not a token micro-optimization. In session mode
the request prefix must stay byte-stable or the cache read is lost, so a
varying glossary block can only ride in the fresh tail message next to the
unit. Dumping the whole file there would then be paid for on every single
request at full input price.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ASCII "->" is accepted because a plain keyboard produces it; the arrow is
# what we write back out.
_ARROW = re.compile(r"\s*(?:→|->)\s*")
_ARROW_OUT = " → "

# Han (including the supplementary-plane extensions, where many rarer name
# characters live), Hiragana, Katakana, Hangul. CJK is written without
# spaces, so these characters have no word boundary to anchor to.
_CJK = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
    "\U00020000-\U0002ffff\U00030000-\U0003134f]"
)


# A term's edge must not sit inside a longer word. `\w` alone is wrong because
# Python counts CJK as word characters, so it would refuse "AI模型" inside
# "这个AI模型"; a latin-only class is equally wrong, since it stops guarding
# Cyrillic and would match "Анна" inside "Жанна". The guard is therefore "not a
# word character, or a CJK one" — correct for every script.
_CJK_CLASS = (
    "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
    "\U00020000-\U0002ffff\U00030000-\U0003134f"
)
_LEFT_GUARD = f"(?:(?<!\\w)|(?<=[{_CJK_CLASS}]))"
_RIGHT_GUARD = f"(?:(?!\\w)|(?=[{_CJK_CLASS}]))"


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    translation: str
    note: str = ""
    case_sensitive: bool = False

    def to_line(self) -> str:
        line = f"{self.term}{_ARROW_OUT}{self.translation}"
        return f"{line} # {self.note}" if self.note else line


@dataclass(frozen=True)
class GlossaryConflict:
    """One term two sources translate differently. Reported, never resolved."""

    term: str
    kept: str
    dropped: str

    def describe(self) -> str:
        return f"{self.term}: kept {self.kept!r}, dropped {self.dropped!r}"


def _is_cjk(term: str) -> bool:
    return bool(_CJK.search(term))


def _matcher(entry: GlossaryEntry) -> re.Pattern:
    """A compiled pattern that finds this term in source text.

    Latin scripts get word boundaries so "Ann" does not fire inside
    "Announcement"; CJK has no such boundary and matches as a substring.
    The term is always escaped — "C++" is a literal, not a quantifier.
    """
    literal = re.escape(entry.term)
    # Guard each edge on its own. A term is often mixed script ("AI模型"), and
    # deciding by "contains any CJK" would drop the boundary the latin edge
    # still needs — letting "AI模型" fire inside "XAI模型".
    left = "" if _is_cjk(entry.term[0]) else _LEFT_GUARD
    right = "" if _is_cjk(entry.term[-1]) else _RIGHT_GUARD
    flags = 0 if entry.case_sensitive else re.IGNORECASE
    return re.compile(f"{left}{literal}{right}", flags)


class Glossary:
    """An ordered, de-duplicated set of pinned terms."""

    def __init__(self, entries=()):
        # Keyed with lower(), matching re.IGNORECASE. casefold() folds harder
        # (ß -> ss), so it would merge two terms the matcher still treats as
        # distinct — and the survivor would then not match the other's text.
        deduped: dict[str, GlossaryEntry] = {}
        for entry in entries:
            deduped[entry.term.lower()] = entry
        self.entries = tuple(deduped.values())
        self._matchers = [(e, _matcher(e)) for e in self.entries]

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __eq__(self, other) -> bool:
        return isinstance(other, Glossary) and self.entries == other.entries

    # ---- construction ----------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "Glossary":
        """Parse `term → translation  # note` lines. Fails loud on garbage.

        A malformed glossary is a user typo, and silently dropping the line
        would mean the pin they asked for never fires — with no signal.
        """
        entries = []
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            line, _, note = line.partition("#")
            parts = _ARROW.split(line.strip(), maxsplit=1)
            if len(parts) != 2:
                raise ValueError(
                    f"glossary line {lineno}: expected 'term → translation', got {raw!r}"
                )
            term, translation = parts[0].strip(), parts[1].strip()
            if not term or not translation:
                raise ValueError(
                    f"glossary line {lineno}: both sides of the arrow are required, got {raw!r}"
                )
            entries.append(GlossaryEntry(term, translation, note.strip()))
        return cls(entries)

    @classmethod
    def from_file(cls, path) -> "Glossary":
        return cls.parse(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def from_json(cls, rows) -> "Glossary":
        """Build from the compact turn's JSON glossary.

        Incomplete rows are dropped rather than raising: this input is model
        output, and one malformed row should not discard a whole window's
        learned vocabulary. Callers report the count they kept.
        """
        if not isinstance(rows, list):
            raise ValueError(
                f"glossary JSON must be a list of objects, got {type(rows).__name__}"
            )
        entries = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            term = str(row.get("term") or "").strip()
            translation = str(row.get("translation") or "").strip()
            if not term or not translation:
                continue
            entries.append(
                GlossaryEntry(term, translation, str(row.get("note") or "").strip())
            )
        return cls(entries)

    # ---- lookup and matching ---------------------------------------------

    def lookup(self, term: str) -> GlossaryEntry | None:
        folded = term.lower()
        for entry in self.entries:
            if entry.term.lower() == folded:
                return entry
        return None

    def matches(self, source_text: str) -> list[GlossaryEntry]:
        """The entries whose term occurs in `source_text`, in glossary order."""
        if not source_text:
            return []
        return [e for e, pattern in self._matchers if pattern.search(source_text)]

    def prompt_block(self, source_text: str) -> str:
        """The `<glossary>` block for this unit, or "" when nothing hits.

        Belongs in the fresh tail message only — see the module docstring.
        """
        hits = self.matches(source_text)
        if not hits:
            return ""
        lines = "\n".join(
            f"{e.term} → {e.translation}" + (f" ({e.note})" if e.note else "")
            for e in hits
        )
        return (
            "<glossary>\n"
            f"{lines}\n"
            "</glossary>\n"
            "Use these translations verbatim in your translation."
        )

    # ---- combination ------------------------------------------------------

    def merge(self, other: "Glossary") -> tuple["Glossary", list[GlossaryConflict]]:
        """Union with `other`; this glossary wins, disagreements are reported.

        Used to fold a learned (handoff) glossary into the user's pinned one,
        and to fold parallel slices' glossaries together. Conflicts are
        returned for loud reporting, never silently resolved — the user is the
        one who decides which rendering is right.
        """
        conflicts = []
        merged = list(self.entries)
        for entry in other.entries:
            mine = self.lookup(entry.term)
            if mine is None:
                merged.append(entry)
            elif mine.translation != entry.translation:
                conflicts.append(
                    GlossaryConflict(entry.term, mine.translation, entry.translation)
                )
        return Glossary(merged), conflicts

    def to_lines(self) -> str:
        return "".join(f"{e.to_line()}\n" for e in self.entries)
