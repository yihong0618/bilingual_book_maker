"""The plan classifier's two instruction texts, pinned byte for byte.

PIN (lead 260926, /simplify pass; prompt audit
docs/260920-feat-CLASSIFY_LADDER_PROMPT_AUDIT.md): `build_prompt` and the
session `TRUNK` share their two answer sentences with `CRITERIA` (the words a
Jev question describes its options with). The texts below were captured at
`ed369fe`, before the sentences were built from `CRITERIA`; a refactor that
changes one byte of what an endpoint is sent fails here.
"""

from book_maker.loader.classify.model import build_prompt
from book_maker.loader.classify.session import TRUNK, build_trunk

PAGE = [
    {
        "key": "block:p.body",
        "units": 3,
        "chars": 120,
        "pct": 80.0,
        "mean_chars": 40,
        "samples": ["One line of prose."],
    },
    {
        "key": "inline:span.ref",
        "units": 2,
        "chars": 6,
        "pct": 0.1,
        "mean_chars": 3,
        "samples": ["[1]"],
    },
]

EXPECTED_PROMPT = (
    "You are preparing a bilingual EPUB. For each content signature below, decide whether it is better to translate its text or keep it as is.\n"
    'Answer "translate" for book content a reader wants translated: prose, verse, dialogue, headings, captions.\n'
    'Answer "skip" for text to keep as is: running heads, page or line numbers, manuscript sigla, cross-reference labels, publisher boilerplate, decorative markers.\n'
    'Answer "unsure" only if the samples genuinely do not settle it. When they are merely thin, prefer translate: translating something unnecessary is cheap, losing content is not.\n'
    "If the samples show more than one kind of content, answer translate — a signature verdict applies to every occurrence, and there is no per-occurrence override.\n"
    'A "block:" signature is a block of text of that shape. An "inline:" signature is markup *inside* a sentence; skipping it leaves its text in place, untranslated, and splits the sentence around it — so skip one only when it is genuinely apparatus.\n'
    "\n"
    '1. "block:p.body" — 3 occurrence(s), 120 chars (80.0% of the book), mean 40 chars\n'
    "   Sample: One line of prose.\n"
    '2. "inline:span.ref" — 2 occurrence(s), 6 chars (0.1% of the book), mean 3 chars\n'
    "   Sample: [1]"
)

EXPECTED_TRUNK = (
    "You are preparing a bilingual EPUB. I will show you content signatures from it, a few at a time. For each one, decide whether it is better to translate its text or keep it as is.\n"
    'Answer "translate" for book content a reader wants translated: prose, verse, dialogue, headings, captions.\n'
    'Answer "skip" for text to keep as is: running heads, page or line numbers, manuscript sigla, cross-reference labels, publisher boilerplate, decorative markers.\n'
    'Answer "unsure" only if the samples genuinely do not settle it. When they are merely thin, prefer translate: translating something unnecessary is cheap, losing content is not.\n'
    "If the samples show more than one kind of content, answer translate — a signature verdict applies to every occurrence, and there is no per-occurrence override.\n"
    'A "block:" signature is a block of text of that shape. An "inline:" signature is markup *inside* a sentence; skipping it leaves its text in place, untranslated, and splits the sentence around it — so skip one only when it is genuinely apparatus.\n'
    "\n"
    "Reply with one verdict per signature, in the order I list them, separated by commas, and nothing else. Five signatures, five verdicts:\n"
    "\n"
    "skip,translate,unsure,translate,skip\n"
    "\n"
    "No numbering, no explanation, no words but the verdicts. When a message lists fewer than five signatures, reply with that many verdicts, in the same form."
)


def test_the_json_prompt_is_unchanged():
    assert build_prompt(PAGE) == EXPECTED_PROMPT


def test_the_session_trunk_is_unchanged():
    assert TRUNK == EXPECTED_TRUNK
    assert build_trunk() == EXPECTED_TRUNK
