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
from book_maker.structured import JSON_ONLY_INSTRUCTION
from book_maker.translator.base_translator import Base
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

_GLOSSARY_BLOCK_TAIL = "Use these translations verbatim in your translation."


def _styled():
    """A translator carrying a fixed `--prompt` style, and nothing else."""
    route = ChatGPTAPI.__new__(ChatGPTAPI)
    route.language = "simplified chinese"
    route.style_note = "<STYLE>"
    return route


def _batch_tail() -> str:
    """The shape instruction a structured batch always appends to the turn.

    Read off the real assembly rather than a literal: the field names are
    derived from the target language, and a pin that retyped them would not
    notice the derivation changing.
    """
    route = ChatGPTAPI.__new__(ChatGPTAPI)
    route.language = "simplified chinese"
    route.language_field_tag = None
    route.prompt_template = "{text}"
    route.prompt_sys_msg = ""
    route.style_note = None
    route.glossary = None
    route.context_flag = False
    route.source_language = None
    content = route._create_structured_batch_messages(["one", "two"])[-1]["content"]
    return content.split("\n\n", 1)[1]


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
    # A codex turn is an agent turn by default, so the instructions have to
    # name the job and bound the reply: "translation only", and the three
    # don'ts are what stop it answering the passage, dropping part of it, or
    # summarizing instead of translating.
    "codex thread instructions": (
        BASE_INSTRUCTIONS,
        "You are a professional book translator. Your job is to translate "
        "the given text into {language}. Return {language} translation only, "
        "don't append, don't miss, don't summarize. Also, keep the source's "
        "paragraph structure and any inline markup exactly as given.",
    ),
    # Said only to requests that carry markers: a model told to preserve
    # tokens in a text that has none is being taught to invent them.
    "marker instruction": (
        Base.MARKER_INSTRUCTION,
        "The text contains placeholders formatting as ⟦code1⟧. Reproduce "
        "every one of them exactly as given, at the place it belongs in your "
        "translation. Never translate a token, and never change its spelling.",
    ),
    # The floor rung: what an endpoint that honours no schema field reads.
    "json-only instruction": (
        JSON_ONLY_INSTRUCTION,
        "Answer with a single JSON object, return JSON object only.",
    ),
    # The tail of every structured batch turn — the last thing the model
    # reads before it decodes, which is why the target language ends it.
    "structured batch tail": (
        _batch_tail(),
        "Return a JSON object whose 'simplified_chinese_paragraphs' contains "
        "EXACTLY 2 objects, one per paragraph. Each object has exactly two "
        "fields: 'id', and 'simplified_chinese_translation'. Return the 2 "
        "translations, each written in simplified chinese.",
    ),
    "default translation prompt": (
        ChatGPTAPI.DEFAULT_PROMPT,
        "Please help me to translate,`{text}` to {language}, please return "
        "only translated content not include the origin text",
    ),
    # `--prompt`'s style section, as it joins the standing instructions. No
    # endpoint has a slot for it, and it is not a per-request thing to say, so
    # this line is the whole of how a fixed style reaches a model — on every
    # route, in these words, once where a window starts.
    "style section": (
        _styled().style_section(),
        "Style to follow: <STYLE>",
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


# ---- the previous wording, which must be gone --------------------------------

# Revised 260907. A prompt half-replaced is worse than either version: the old
# clause survives in one route's copy and the two disagree on the wire.
RETIRED = (
    "The text contains placeholder tokens written like",
    "no prose, no markdown fences",
    "copied unchanged from the paragraph it translates",
    "use every id once and invent none",
    "You are a translation engine inside a book translation tool",
    "Use these translations verbatim whenever the source term appears",
)


@pytest.mark.parametrize("phrase", RETIRED)
def test_the_old_wording_is_gone_from_the_package(phrase):
    package = Path(__file__).resolve().parents[1] / "book_maker"
    hits = [
        path.relative_to(package.parent)
        for path in package.rglob("*.py")
        if phrase in path.read_text(encoding="utf-8")
    ]
    assert not hits, f"{phrase!r} still reaches a model from {hits}"
