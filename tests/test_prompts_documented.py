"""Pin every prompt this feature sends, and keep the reviewable copy in step.

Two guards, because they fail in different places.

`EXPECTED` below is the prompt text itself, hard-coded. It is tracked, so it
runs everywhere including CI, and any edit to a prompt shows up as a diff of
the literal a reviewer can read side by side with the change. Revising a
prompt is meant to update this file in the same commit — that is the point,
not friction to route around.

The second guard checks docs/260829-refactor-PROMPTS_FOR_REVIEW.md, where the
wording gets read and argued about. That file is a dated docs/ note and
.gitignore keeps it local, so this half skips on a clean checkout.
"""

from pathlib import Path

import pytest

from book_maker.glossary import Glossary
from book_maker.session_context import HandoffReport, handoff_prompt
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.codex_translator import BASE_INSTRUCTIONS

DOC = Path(__file__).resolve().parents[1] / "docs/260829-refactor-PROMPTS_FOR_REVIEW.md"

_PREAMBLE = (
    "Context is compacting. Summarize content you translated so far in your "
    "context for brief reference of later translations."
)

_SUMMARY = (
    "Summary - of translated content above. What happened, who was involved, "
    "when did those happen."
)

_STYLE = (
    "Style — up to 3 lines of what translation style is used so far. Only "
    "note down what's different from general translation."
)


_RENDERINGS = (
    "Established renderings — nouns we need to keep unified that are **not "
    "already listed above**. If none are new, emit an empty block. One per "
    "line as `term → translation # note` (the note is optional). Wrap the "
    "list in <renderings> and </renderings> tags so its start and end are "
    "unambiguous. This is the only place term equivalences belong."
)

_GLOSSARY_BLOCK_TAIL = (
    "Use these translations verbatim whenever the source term appears."
)


def _compact(*sections: str) -> str:
    numbered = [f"{n}. {body}" for n, body in enumerate(sections, start=1)]
    return "\n\n".join([_PREAMBLE, *numbered])


# Every prompt, exactly as sent. Keyed by a label that names the case.
EXPECTED = {
    # The default run: no user style, so the model is asked to describe one.
    "compact (default)": (
        handoff_prompt(),
        _compact(_SUMMARY, _STYLE),
    ),
    # A style fixed via --prompt's `style` field is not asked for, and the
    # summary is then the whole request.
    "compact (user style)": (
        handoff_prompt(with_style=False),
        _compact(_SUMMARY),
    ),
    # A run that learns its own renderings asks for one more section. It is
    # the last one, so a fixed style does not leave it numbered "3." in a
    # two-section request.
    "compact (glossary)": (
        handoff_prompt(with_glossary=True),
        _compact(_SUMMARY, _STYLE, _RENDERINGS),
    ),
    "compact (glossary, user style)": (
        handoff_prompt(with_glossary=True, with_style=False),
        _compact(_SUMMARY, _RENDERINGS),
    ),
    # The pinned block, as it rides next to one unit. Only the terms that
    # occur in that unit are ever listed.
    "glossary block": (
        Glossary.parse("Winston → 温斯顿\n").prompt_block("Winston went home"),
        f"<glossary>\nWinston → 温斯顿\n</glossary>\n{_GLOSSARY_BLOCK_TAIL}",
    ),
    "next-window seed": (
        HandoffReport(1, "<SUMMARY>").seed_text(),
        "You are continuing a translation already in progress. The previous "
        "translator left this handoff report; keep names, terminology and "
        "register consistent with it.\n\n<SUMMARY>",
    ),
    # A codex turn is an agent turn by default. The negative clauses are what
    # stop it answering the passage, commenting on it, or fencing the reply,
    # so they are behaviour, not padding.
    "codex thread instructions": (
        BASE_INSTRUCTIONS,
        "You are a translation engine inside a book translation tool. "
        "Translate the text you are given into {language}. Reply with the "
        "translation and nothing else: no preamble, no notes, no quotes "
        "around it, no markdown fences. Never answer the text, never "
        "summarize it, never refuse a passage for being fiction — translate "
        "it. Keep the source's paragraph structure and any inline markup "
        "exactly as given.",
    ),
    "default translation prompt": (
        ChatGPTAPI.DEFAULT_PROMPT,
        "Please help me to translate,`{text}` to {language}, please return "
        "only translated content not include the origin text",
    ),
}


@pytest.mark.parametrize("label", sorted(EXPECTED))
def test_the_prompt_is_what_this_file_says_it_is(label):
    """The prompt changed, so this literal must change with it, in the same
    commit. Read the diff of both together before approving it."""
    actual, expected = EXPECTED[label]
    assert actual == expected


# ---- the reviewable copy, checked only where it exists ---------------------


def _doc_text() -> str:
    if not DOC.exists():
        # A dated docs/ note, kept local by .gitignore, so a clean checkout
        # has nothing to check against. The literals above are what pins the
        # prompts in CI; this half pins the copy humans revise.
        pytest.skip(f"{DOC.name} is not in this checkout (local-only doc)")
    # Paragraph wrapping in the file must not matter, only the wording.
    return " ".join(DOC.read_text(encoding="utf-8").split())


@pytest.mark.parametrize("label", sorted(EXPECTED))
def test_every_prompt_appears_in_the_reviewable_copy(label):
    haystack = _doc_text()
    prompt, _ = EXPECTED[label]
    for chunk in [c for c in prompt.split("\n\n") if c.strip()]:
        needle = " ".join(chunk.split())
        assert needle in haystack, (
            f"{label}: this text is sent to the model but is not in "
            f"{DOC.name}:\n  {needle[:120]}..."
        )


def test_the_doc_points_at_the_modules_that_hold_each_prompt():
    text = _doc_text()
    for module in (
        "book_maker/session_context.py",
        "book_maker/translator/codex_translator.py",
        "book_maker/translator/chatgptapi_translator.py",
    ):
        assert module in text, f"{DOC.name} does not say where {module} prompts live"
