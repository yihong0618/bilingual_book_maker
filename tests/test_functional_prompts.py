"""The functional prompts: what the tool says on its own behalf.

A functional prompt is not the operator's text and not the operator's to
override. It is what the request needs in order to come back usable — the
marker contract when a unit carries markers, the structure contract when the
payload has a structure the reply has to reproduce — and it is added in front
of whatever `--prompt` supplies rather than inside it.

Two properties follow, and are pinned here:

* it is conditional. A plain single paragraph of prose earns neither, because
  a model told to preserve a structure that is not there is being taught to
  invent one, exactly as `MARKER_INSTRUCTION` is withheld from a text with no
  markers;
* it is invisible. The line a run prints at start reports what became of the
  operator's own `--prompt` sections; the functional prompts are not among
  them, and naming them there would read as something the operator chose.
"""

import json

from book_maker.cli import parse_prompt_arg, prompt_adoption_line
from book_maker.translator.base_translator import BATCH_DELIMITER, Base
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude

STRUCTURE = Base.STRUCTURE_INSTRUCTION
PLAIN = "The old major had had a strange dream and wished to communicate it."


def _openai(**kwargs):
    route = ChatGPTAPI.__new__(ChatGPTAPI)
    route.language = "simplified chinese"
    route.prompt_template = "Translate `{text}` into {language}."
    route.prompt_sys_msg = ""
    route.style_note = None
    route.glossary = None
    route.context_flag = False
    route.source_language = None
    for name, value in kwargs.items():
        setattr(route, name, value)
    return route


def _claude(**kwargs):
    route = Claude.__new__(Claude)
    route.language = "simplified chinese"
    route.prompt_template = "Translate `{text}` into {language}."
    route.prompt_sys_msg = ""
    route.style_note = None
    route.source_language = None
    for name, value in kwargs.items():
        setattr(route, name, value)
    return route


class TestWhenTheStructureContractIsSaid:
    def test_a_unit_carrying_inline_markers_gets_it(self):
        text = "Mr Jones of ⟦em1⟧the Manor Farm⟧ locked the hen-houses."
        assert STRUCTURE in _openai()._user_content(text)

    def test_a_multi_paragraph_body_gets_it(self):
        text = "All animals are equal.\n\nBut some are more equal than others."
        assert STRUCTURE in _openai()._user_content(text)

    def test_a_delimiter_batched_body_gets_it(self):
        # the delimiter rung joins a whole group into one request; the
        # delimiters are the only thing that lets the reply be split back up
        text = BATCH_DELIMITER.join([PLAIN, "The animals were happy."])
        assert STRUCTURE in _openai()._user_content(text)

    def test_a_markdown_unit_gets_it(self):
        # the markdown loader hands its units over with their markup intact
        text = "See [the seven commandments](commandments.md) for the rules."
        assert STRUCTURE in _openai()._user_content(text)

    def test_a_unit_carrying_an_html_tag_gets_it(self):
        assert STRUCTURE in _openai()._user_content("A <em>strange</em> dream.")

    def test_a_plain_single_paragraph_does_not(self):
        content = _openai()._user_content(PLAIN)
        assert STRUCTURE not in content
        # and neither contract fires: a plain request is the operator's
        # prompt and nothing else
        assert Base.MARKER_INSTRUCTION not in content

    def test_an_asterisk_in_prose_is_not_markup(self):
        # `*` is punctuation as often as it is emphasis, and a functional
        # instruction on a request that cannot apply it is noise
        assert STRUCTURE not in _openai()._user_content("A footnote marker * here.")

    def test_the_same_holds_on_the_anthropic_route(self):
        assert STRUCTURE in _claude()._user_content("A <em>strange</em> dream.")
        assert STRUCTURE not in _claude()._user_content(PLAIN)

    def test_a_structured_batch_request_gets_it(self):
        route = _openai(model="gpt-x", language_field_tag=None)
        messages = route._create_structured_batch_messages([PLAIN, PLAIN])
        turn = messages[-1]["content"]
        # every batched request carries structure: the ids in the payload are
        # what the translations are handed back by
        assert STRUCTURE in turn


class TestTheOperatorCannotOverrideIt:
    def test_a_full_custom_prompt_still_carries_the_contract(self):
        config = parse_prompt_arg(
            json.dumps(
                {
                    "user": "Only this: {text}",
                    "system": "You are terse.",
                    "style": "Clipped.",
                }
            ),
            announce=False,
        )
        route = _openai(
            prompt_template=config["user"],
            prompt_sys_msg=config["system"],
            style_note=config["style"],
        )
        content = route._user_content("A <em>strange</em> dream.")
        assert STRUCTURE in content
        # in front of the operator's template, not inside it
        assert content.index(STRUCTURE) < content.index("Only this:")

    def test_it_is_said_once(self):
        content = _openai()._user_content("A <em>strange</em> dream.")
        assert content.count(STRUCTURE) == 1


class TestTheFunctionalPromptsAreInvisible:
    def test_the_adoption_line_does_not_mention_them(self):
        line = prompt_adoption_line(
            {"user": "Only this: {text}", "system": "You are terse.", "style": "Dry."},
            ChatGPTAPI,
            "openai",
        )
        assert STRUCTURE not in line
        assert "paragraph structure" not in line
        assert Base.MARKER_INSTRUCTION not in line
