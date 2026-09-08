"""`--prompt`'s three sections: parsed, placed, and announced.

`--prompt` carries `user`, `system` and `style`. The contract this file pins:

1. All three parse, and anything else is a loud refusal — a prompt is the one
   flag whose typo cannot be caught by looking at the output, because a run
   under the wrong instructions still produces a translated book.
2. Every section reaches the request on every route. Where a route has no slot
   of its own for one — no endpoint has a *style* slot, and a codex thread has
   no system slot — the text is appended to what the route does send rather
   than dropped. `PROMPT_SECTION_SLOTS` is each route's own statement of which
   it does, and the tests below hold it to it.
3. A route with a native slot does not *also* append: the section would then
   be said twice, at twice the price, in every request.
4. The run says at start what it adopted and where it landed. A run with no
   `--prompt` says nothing new (tests/test_flag_compat.py's noise guard).

The request bodies themselves are read in tests/test_prompt_capture_server.py.
"""

import json
from types import SimpleNamespace

import pytest

from book_maker.cli import (
    PROMPT_SECTIONS,
    parse_prompt_arg,
    prompt_adoption_line,
    prompt_has_system,
    read_prompt_config,
)
from book_maker.translator import FORMAT_DICT
from book_maker.translator.base_translator import NO_PROMPT_SECTIONS
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude
from book_maker.translator.codex_translator import Codex
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.google_translator import Google
from book_maker.utils import prompt_config_to_kwargs

USER = "Render `{text}` into {language}."
SYSTEM = "You are a stern nineteenth-century schoolmaster."
STYLE = "Clipped, unsentimental, no adverbs."


# ------------------------------------------------------------------- parsing


class TestParsingTheSections:
    def test_all_three_keys_parse(self):
        parsed = parse_prompt_arg(
            json.dumps({"user": USER, "system": SYSTEM, "style": STYLE}),
            announce=False,
        )
        assert parsed == {"user": USER, "system": SYSTEM, "style": STYLE}

    def test_a_user_only_json_prompt_parses(self):
        assert parse_prompt_arg(json.dumps({"user": USER}), announce=False) == {
            "user": USER
        }

    def test_a_bare_template_is_the_user_section(self):
        assert parse_prompt_arg(USER, announce=False) == {"user": USER}

    def test_a_txt_file_is_the_user_section(self, tmp_path):
        path = tmp_path / "p.txt"
        path.write_text(USER, encoding="utf-8")
        assert parse_prompt_arg(str(path), announce=False) == {"user": USER}

    def test_a_json_file_carries_every_section(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps({"user": USER, "system": SYSTEM, "style": STYLE}),
            encoding="utf-8",
        )
        assert parse_prompt_arg(str(path), announce=False) == {
            "user": USER,
            "system": SYSTEM,
            "style": STYLE,
        }

    def test_an_unknown_key_is_refused(self):
        with pytest.raises(ValueError) as refused:
            parse_prompt_arg(
                json.dumps({"user": USER, "voice": "warm"}), announce=False
            )
        assert "can only contain the keys" in str(refused.value)

    def test_a_prompt_with_no_user_section_is_refused_by_name(self):
        # Not a KeyError traceback: `--prompt '{"system": ...}'` is the typo
        # someone actually makes, and the sentence has to name what is missing.
        with pytest.raises(ValueError) as refused:
            parse_prompt_arg(json.dumps({"system": SYSTEM}), announce=False)
        assert "must contain the key of `user`" in str(refused.value)

    def test_a_user_section_without_the_text_placeholder_is_refused(self):
        with pytest.raises(ValueError) as refused:
            parse_prompt_arg(json.dumps({"user": "translate it"}), announce=False)
        assert "`{text}`" in str(refused.value)

    def test_a_json_array_is_refused_rather_than_subscripted(self):
        with pytest.raises(ValueError):
            parse_prompt_arg(json.dumps(["translate {text}"]), announce=False)

    def test_reading_the_prompt_for_a_compat_check_stays_silent(self, capsys):
        config, error = read_prompt_config(json.dumps({"user": USER, "system": SYSTEM}))
        assert error is None
        assert prompt_has_system(SimpleNamespace(prompt_config=config))
        assert capsys.readouterr().out == ""

    def test_a_prompt_that_cannot_be_read_is_carried_not_raised(self):
        # the table has to be able to ask about a malformed --prompt without
        # pre-empting the refusal written for it
        config, error = read_prompt_config('{"system": "be terse"}')
        assert config is None
        assert "must contain the key of `user`" in str(error)

    def test_the_shipped_sample_files_parse(self):
        for name in ("prompt_sections_sample.json", "prompt_session_sample.json"):
            parsed = parse_prompt_arg(name, announce=False)
            # every section is present, and `style` is shipped empty: an
            # example must show where a voice goes without imposing one on
            # whoever copies the file
            assert set(parsed) == set(PROMPT_SECTIONS), name
            assert parsed["user"] and parsed["system"], name
            assert parsed["style"] == "", name


class TestTheSectionsReachTheTranslator:
    def test_each_section_becomes_the_kwarg_the_routes_take(self):
        kwargs = prompt_config_to_kwargs(
            {"user": USER, "system": SYSTEM, "style": STYLE}
        )
        assert kwargs == {
            "prompt_template": USER,
            "prompt_sys_msg": SYSTEM,
            "style_note": STYLE,
        }

    @pytest.mark.parametrize(
        "route", [ChatGPTAPI, Claude, Gemini, Codex], ids=lambda c: c.__name__
    )
    def test_every_prompt_route_accepts_all_three(self, route):
        # A route that swallows one into **kwargs discards it in silence —
        # which is exactly what gemini did with `style_note` until 260905.
        instance = route.__new__(route)
        instance.language = "simplified chinese"
        for name, value in prompt_config_to_kwargs(
            {"user": USER, "system": SYSTEM, "style": STYLE}
        ).items():
            setattr(instance, name, value)
        parts = instance.resolved_prompt_parts()
        assert parts["system"] == SYSTEM
        assert parts["style"] == STYLE
        assert parts["user"] == USER


# ------------------------------------------------------- where a section lands


def _openai(**kwargs):
    route = ChatGPTAPI.__new__(ChatGPTAPI)
    route.language = "simplified chinese"
    route.prompt_template = USER
    route.prompt_sys_msg = ""
    route.system_content = ""
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
    route.prompt_template = USER
    route.prompt_sys_msg = ""
    route.style_note = None
    route.source_language = None
    for name, value in kwargs.items():
        setattr(route, name, value)
    return route


def _gemini(**kwargs):
    route = Gemini.__new__(Gemini)
    route.language = "simplified chinese"
    route.prompt = USER
    route.prompt_sys_msg = None
    route.style_note = None
    route.source_language = None
    for name, value in kwargs.items():
        setattr(route, name, value)
    return route


class TestTheStyleSectionIsAppended:
    """No endpoint has a style slot, so it rides in the turn — once, and in
    the same words whichever route carries it."""

    def test_the_openai_unit_message_carries_it(self):
        content = _openai(style_note=STYLE)._user_content("Some prose.")
        assert content.endswith(f"\n\n{ChatGPTAPI.STYLE_HEADING} {STYLE}")

    def test_the_openai_structured_batch_carries_it(self):
        route = _openai(style_note=STYLE)
        messages = route._create_structured_batch_messages(["one", "two"])
        content = messages[-1]["content"]
        assert f"{ChatGPTAPI.STYLE_HEADING} {STYLE}" in content
        # before the shape instruction: the last thing the model reads has to
        # stay the shape and the target language
        assert content.index(STYLE) < content.index("Return a JSON object")

    def test_the_claude_unit_message_carries_it(self):
        content = _claude(style_note=STYLE)._user_content("Some prose.")
        assert content.endswith(f"\n\n{Claude.STYLE_HEADING} {STYLE}")

    def test_the_gemini_turn_carries_it(self):
        content = _gemini(style_note=STYLE)._user_content("Some prose.")
        assert content.endswith(f"\n\n{Gemini.STYLE_HEADING} {STYLE}")

    @pytest.mark.parametrize("build", [_openai, _claude, _gemini])
    def test_no_style_adds_nothing(self, build):
        assert ChatGPTAPI.STYLE_HEADING not in build()._user_content("Some prose.")

    def test_it_is_said_once_not_twice(self):
        content = _openai(style_note=STYLE)._user_content("Some prose.")
        assert content.count(STYLE) == 1


class TestTheSystemSectionKeepsItsNativeSlot:
    """A route with a system slot must not also append the system text to the
    user turn: that is the same instruction twice, paid for on every request.
    """

    def test_the_openai_user_message_does_not_repeat_the_system_message(self):
        route = _openai(prompt_sys_msg=SYSTEM)
        messages = route.create_messages("Some prose.")
        assert messages[0]["content"] == SYSTEM
        assert SYSTEM not in messages[-1]["content"]

    def test_the_claude_system_message_is_the_section(self):
        assert _claude(prompt_sys_msg=SYSTEM)._system_message() == SYSTEM
        assert SYSTEM not in _claude(prompt_sys_msg=SYSTEM)._user_content("x")

    def test_the_gemini_system_instruction_is_the_section(self):
        assert _gemini(prompt_sys_msg=SYSTEM)._system_instruction() == SYSTEM
        assert SYSTEM not in _gemini(prompt_sys_msg=SYSTEM)._user_content("x")

    def test_gemini_sends_no_empty_system_instruction(self):
        # the SDK takes an absent instruction; "" is a different request
        assert _gemini()._system_instruction() is None


class TestARouteWithoutASystemSlotAppendsIt:
    """codex opens a thread; there is no system message on a turn. The section
    joins the thread instructions instead of vanishing."""

    def _codex(self, **kwargs):
        route = Codex.__new__(Codex)
        route.language = "simplified chinese"
        route.prompt_template = None
        route.prompt_sys_msg = None
        route.style_note = None
        route.source_language = None
        for name, value in kwargs.items():
            setattr(route, name, value)
        return route

    def test_the_thread_instructions_carry_both_sections(self):
        instructions = self._codex(
            prompt_sys_msg=SYSTEM, style_note=STYLE
        )._instructions()
        assert SYSTEM in instructions
        assert f"{Codex.STYLE_HEADING} {STYLE}" in instructions

    def test_the_base_instructions_are_kept_underneath(self):
        # replacing them wholesale lets the model answer the passage instead
        # of translating it
        instructions = self._codex(prompt_sys_msg=SYSTEM)._instructions()
        assert "translation engine" in instructions

    def test_the_turn_does_not_repeat_them(self):
        route = self._codex(prompt_sys_msg=SYSTEM, style_note=STYLE)
        assert route._unit_text("Some prose.") == "Some prose."

    def test_a_user_template_still_shapes_the_turn(self):
        route = self._codex(prompt_template=USER)
        assert (
            route._unit_text("Some prose.")
            == "Render `Some prose.` into simplified chinese."
        )


class TestTheDocumentedPlaceholders:
    """`{text}`, `{language}` and `{crlf}` are what `--prompt` documents. Every
    route filled the first two; `{crlf}` used to raise KeyError on claude and
    gemini and work on openai and codex."""

    TEMPLATE = "To {language}:{crlf}{text}"

    def test_openai(self):
        assert _openai(prompt_template=self.TEMPLATE)._user_content("x") == (
            "To simplified chinese:\nx"
        )

    def test_claude(self):
        assert _claude(prompt_template=self.TEMPLATE)._user_content("x") == (
            "To simplified chinese:\nx"
        )

    def test_gemini(self):
        assert _gemini(prompt=self.TEMPLATE)._user_content("x") == (
            "To simplified chinese:\nx"
        )

    def test_an_undocumented_brace_in_an_optional_section_is_left_alone(self):
        # `system` and `style` have no required placeholder, so a `{` in them
        # is the operator's own text — a JSON example, a regex — and must not
        # kill a run mid-book.
        odd = 'Answer as {"tone": "dry"}.'
        assert _openai(prompt_sys_msg=odd)._system_message() == odd

    def test_a_documented_placeholder_in_an_optional_section_is_filled(self):
        route = _openai(prompt_sys_msg="You translate into {language}.")
        assert route._system_message() == "You translate into simplified chinese."


# ------------------------------------------------------ the slot declarations


class TestEveryRouteDeclaresItsSlots:
    @pytest.fixture(autouse=True)
    def _real_registry(self, monkeypatch):
        # The hermetic harness (tests/hermetic, when it is on PYTHONPATH)
        # swaps two FORMAT_DICT entries for offline stand-ins. These tests
        # are about the real routes' declarations, so put the real classes
        # back for their duration.
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI
        from book_maker.translator.google_translator import Google

        monkeypatch.setitem(FORMAT_DICT, "openai", ChatGPTAPI)
        monkeypatch.setitem(FORMAT_DICT, "google", Google)

    def test_every_registered_route_declares_all_three_sections(self):
        for name, route in sorted(FORMAT_DICT.items()):
            slots = route.PROMPT_SECTION_SLOTS
            assert set(slots) == set(PROMPT_SECTIONS), name
            assert set(slots.values()) <= {"native", "appended", "none"}, name

    def test_a_route_that_appends_says_where(self):
        for name, route in sorted(FORMAT_DICT.items()):
            if "appended" in route.PROMPT_SECTION_SLOTS.values():
                assert route.PROMPT_APPEND_TARGET, name

    def test_the_engines_that_take_no_prompt_say_so(self):
        for name in (
            "google",
            "caiyun",
            "deepl",
            "deeplfree",
            "tencent",
            "qwen",
            "customapi",
        ):
            assert FORMAT_DICT[name].PROMPT_SECTION_SLOTS == NO_PROMPT_SECTIONS, name

    def test_the_llm_routes_carry_the_user_section_natively(self):
        for name in (
            "openai",
            "anthropic",
            "gemini",
            "codex",
            "groq",
            "xai",
            "litellm",
        ):
            assert FORMAT_DICT[name].PROMPT_SECTION_SLOTS["user"] == "native", name


# ------------------------------------------------------- the adoption notice


class TestTheAdoptionNotice:
    def test_no_prompt_says_nothing(self):
        assert prompt_adoption_line(None, ChatGPTAPI, "openai") is None
        assert prompt_adoption_line({}, ChatGPTAPI, "openai") is None

    def test_a_user_only_prompt_needs_no_explanation(self):
        line = prompt_adoption_line({"user": USER}, ChatGPTAPI, "openai")
        assert line == "prompt: user from --prompt"

    def test_a_style_section_says_where_it_landed(self):
        line = prompt_adoption_line(
            {"user": USER, "style": STYLE}, ChatGPTAPI, "openai"
        )
        assert line == (
            "prompt: user+style from --prompt "
            "(style appended to the user message on this route)"
        )

    def test_codex_names_the_thread_instructions(self):
        line = prompt_adoption_line(
            {"user": USER, "system": SYSTEM, "style": STYLE}, Codex, "codex"
        )
        assert "system and style appended to the thread instructions" in line

    def test_a_route_that_carries_no_prompt_says_so(self):
        line = prompt_adoption_line({"user": USER, "style": STYLE}, Google, "google")
        assert "ignored" in line
        assert "the google route builds no prompt" in line

    def test_the_sections_are_named_in_a_fixed_order(self):
        line = prompt_adoption_line(
            {"style": STYLE, "system": SYSTEM, "user": USER}, ChatGPTAPI, "openai"
        )
        assert line.startswith("prompt: user+system+style from --prompt")


# ------------------------------------------------------------- the fingerprint


class TestTheFingerprintSeesEverySection:
    """`resolved_prompt_parts` is what the resume checkpoint compares. A
    section that changed the book must move it."""

    def test_a_style_only_change_moves_it(self):
        before = _openai(style_note="wooden, literal").resolved_prompt_parts()
        after = _openai(style_note="breezy").resolved_prompt_parts()
        assert before != after

    def test_a_system_only_change_moves_it(self):
        before = _openai(prompt_sys_msg=SYSTEM).resolved_prompt_parts()
        after = _openai(prompt_sys_msg="Be terse.").resolved_prompt_parts()
        assert before != after

    def test_it_reports_the_settled_system_message_not_the_flag(self):
        # $OPENAI_API_SYS_MSG outranks --prompt's system section (the CLI's A9
        # rule says so out loud); the fingerprint has to report what the run
        # will actually send.
        route = _openai(system_content="from the environment", prompt_sys_msg=SYSTEM)
        assert route.resolved_prompt_parts()["system"] == "from the environment"

    def test_the_source_language_note_is_part_of_it(self):
        route = _openai(prompt_sys_msg=SYSTEM, source_language="english")
        assert "english" in route.resolved_prompt_parts()["system"]
