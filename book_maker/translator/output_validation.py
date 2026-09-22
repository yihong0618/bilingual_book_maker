"""Detect model-process commentary that must never become book text.

The rules deliberately look for combinations.  A novel may legitimately
contain English, JSON, the word ``Wait`` or a code fence; none of those is a
failure on its own.  High-confidence findings require model-work language or
a schema/JSON fragment next to that language.  Medium findings are warnings
for the final delivery report, not reasons to discard a translation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

PROMPT_POLICY_VERSION = "2026-09-22.1"
CONTAMINATION_DETECTOR_VERSION = "2026-09-22.1"
RETRANSLATION_POLICY_VERSION = "2026-09-22.1"

TRANSLATION_OUTPUT_CONTRACT = (
    "Japanese quotation marks may intentionally open in one paragraph and "
    "close in a later paragraph. This is valid source structure. Do not "
    "repair, question, or comment on it. Return translation only. Never "
    "include analysis, reasoning, self-checks, schema text, JSON fragments, "
    "or translation notes in translated content."
)

BATCH_CONTINUITY_CONTRACT = (
    "Adjacent paragraph ids may be consecutive parts of one continuing "
    "quotation or speech. Translate each id without forcing its punctuation "
    "to close inside that id."
)


@dataclass(frozen=True)
class OutputIssue:
    confidence: str
    rule: str
    excerpt: str
    start: int
    end: int


def _excerpt(text: str, start: int, end: int, radius: int = 90) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    value = " ".join(text[left:right].split())
    if left:
        value = "…" + value
    if right < len(text):
        value += "…"
    return value[:240]


_META_PATTERNS = (
    re.compile(
        r"\bwait\s*[,.:;!?-]*\s+(?:the\s+)?source\s+"
        r"(?:last|text|paragraph|sentence)\b",
        re.I,
    ),
    re.compile(
        r"\bsource\s+(?:last|text|paragraph|sentence)\s+"
        r"(?:has|have|contains?|ends?|starts?|opens?|closes?)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:opening|closing|unclosed|unterminated)\s+"
        r"(?:Japanese\s+)?quot(?:e|ation)(?:\s+mark)?\b"
        r"|\bno\s+(?:close|closing)\s*(?:quote|mark)?\s*\?",
        re.I,
    ),
)

_MODEL_WORK = re.compile(
    r"\bI\s+(?:need|must|should|will|have)\s+to\s+"
    r"(?:translate|fix|ensure|check|preserve|return|remove|add)\b",
    re.I,
)

_WORK_OBJECT = re.compile(
    r"\b(?:source|output|translation|translated\s+content|translation\s+field|"
    r"JSON|schema|punctuation|paragraph|Japanese\s+quote|target\s+language)\b",
    re.I,
)

_JSON_TAIL = re.compile(r"(?:}\s*]\s*}|]\s*}|}\s*})")
_FENCE = re.compile(r"```(?:json|javascript|python|xml)?", re.I)
_PROCESS_LABEL = re.compile(
    r"(?im)^\s*(?:analysis|reasoning|self[- ]?check|translation\s+note|translator['’]s\s+note)\s*:"
)
_PROCESS_WORD = re.compile(
    r"\b(?:analysis|reasoning|self[- ]?check|translation\s+note)\b",
    re.I,
)
_STRUCTURED_FRAGMENT = re.compile(
    r"[\[{]\s*[\"']?(?:id|translated|translation|paragraphs?)[\"']?\s*:",
    re.I,
)
_AI_DISCLAIMER = re.compile(r"\bas an AI(?: language model)?\b", re.I)


def detect_translation_contamination(source: str, output: str) -> list[OutputIssue]:
    """Return high/medium confidence process-text findings in ``output``.

    ``source`` is accepted so callers share one stable interface and future
    rules can compare source and target.  It is intentionally not used for
    quote balancing: Japanese dialogue may open in one paragraph and close in
    another, which is valid book structure.
    """

    del source
    text = str(output or "")
    issues: list[OutputIssue] = []
    seen: set[tuple[str, int, int]] = set()

    def add(confidence: str, rule: str, match: re.Match | tuple[int, int]) -> None:
        start, end = match.span() if hasattr(match, "span") else match
        key = (rule, start, end)
        if key in seen:
            return
        seen.add(key)
        issues.append(
            OutputIssue(confidence, rule, _excerpt(text, start, end), start, end)
        )

    meta_matches = [m for pattern in _META_PATTERNS for m in pattern.finditer(text)]
    for match in meta_matches:
        add("high", "model-source-self-check", match)

    for match in _MODEL_WORK.finditer(text):
        sentence_start = max(
            text.rfind("\n", 0, match.start()), text.rfind("。", 0, match.start())
        )
        sentence_end_candidates = [
            pos
            for pos in (
                text.find("\n", match.end()),
                text.find("。", match.end()),
                text.find(".", match.end()),
            )
            if pos >= 0
        ]
        sentence_end = (
            min(sentence_end_candidates)
            if sentence_end_candidates
            else min(len(text), match.end() + 220)
        )
        if _WORK_OBJECT.search(text[max(0, sentence_start) : sentence_end]):
            add("high", "model-work-statement", match)

    for tail in _JSON_TAIL.finditer(text):
        nearby = text[tail.end() : tail.end() + 280]
        if any(
            pattern.search(nearby) for pattern in _META_PATTERNS
        ) or _MODEL_WORK.search(nearby):
            add("high", "json-tail-followed-by-self-check", tail)

    for fence in _FENCE.finditer(text):
        nearby = text[max(0, fence.start() - 80) : fence.end() + 320]
        if _PROCESS_WORD.search(nearby) and (
            _STRUCTURED_FRAGMENT.search(nearby) or _PROCESS_LABEL.search(nearby)
        ):
            add("high", "code-fence-with-analysis-schema", fence)

    for match in _AI_DISCLAIMER.finditer(text):
        nearby = text[match.start() : match.end() + 220]
        if _WORK_OBJECT.search(nearby):
            add("high", "ai-process-disclaimer", match)

    for match in _PROCESS_LABEL.finditer(text):
        if not any(issue.start <= match.start() <= issue.end for issue in issues):
            add("medium", "process-label", match)

    for match in _STRUCTURED_FRAGMENT.finditer(text):
        nearby = text[max(0, match.start() - 120) : match.end() + 240]
        if _PROCESS_WORD.search(nearby) and not any(
            issue.start <= match.start() <= issue.end for issue in issues
        ):
            add("medium", "schema-like-fragment", match)

    return sorted(issues, key=lambda issue: (issue.start, issue.rule))


def high_confidence_issues(source: str, output: str) -> list[OutputIssue]:
    return [
        issue
        for issue in detect_translation_contamination(source, output)
        if issue.confidence == "high"
    ]


class TranslationContamination(Exception):
    """A translated field contains model-process text after bounded retries."""

    def __init__(
        self,
        item_issues: dict[int, list[OutputIssue]],
        attempts: int = 0,
        locations: dict[int, tuple[str, str]] | None = None,
    ):
        self.item_issues = item_issues
        self.attempts = attempts
        self.locations = locations or {}
        super().__init__(self._message())

    def _message(self):
        details = []
        for index, issues in sorted(self.item_issues.items()):
            rules = ", ".join(sorted({issue.rule for issue in issues}))
            excerpt = issues[0].excerpt if issues else ""
            location = self.locations.get(index)
            where = f" {location[0]}#{location[1]}" if location else ""
            details.append(f"item {index}{where}: {rules} ({excerpt})")
        suffix = (
            f" after {self.attempts} correction attempt(s)" if self.attempts else ""
        )
        return "translation contamination" + suffix + ": " + "; ".join(details)

    def with_locations(self, locations: dict[int, tuple[str, str]]):
        self.locations.update(locations)
        self.args = (self._message(),)
        return self
