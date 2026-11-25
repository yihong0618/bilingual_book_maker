"""The built-in prompt is `prompt_template.json`'s pair (owner 261125).

When the operator supplies no prompt, the openai family, Gemini and Claude
send the repository's `prompt_template.json` -- its system message and its
user template -- instead of the one-liner (and, on Claude, the triple-backtick
template) they used to send with an empty system message.

Pinned here, in the order of the contract the change came with:

1. the code constant is the file, word for word, and nothing edits it;
2. which routes take it, and that the ones writing a prompt of their own
   (codex, qwen, the fixed MT engines, the subtitle loader) do not;
3. the precedence: the built-in system message goes out only when the
   operator supplied neither section, decided by where the text came from;
4. "stock" still means the built-in user template, and Claude's fence unwrap
   still runs under it and nowhere else;
5. every request shape carries the built-in system message exactly once;
6. the measured prompt overhead counts it, and the stock prompt still gets
   the grouping floor;
7. the run identities move: the EPUB resume fingerprint and the PDF bundle's.

No network: every client is a stand-in that records what it was sent.
"""

import json
import pickle
import re
import sys
from hashlib import sha256
from itertools import cycle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from ebooklib import epub

from book_maker.cli import PROMPT_ENV_VARS, parse_prompt_arg
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.plan import (
    SESSION_BUDGET_FLOOR,
    SUBSTRICT_BUDGET_FLOOR,
    derived_token_budget,
)
from book_maker.loader.srt_loader import DEFAULT_PROMPT_CONFIG, SRTBookLoader
from book_maker.session_context import HandoffReport, handoff_prompt
from book_maker.translator import FORMAT_DICT
from book_maker.translator.base_translator import (
    BATCH_DELIMITER,
    BUILTIN_PROMPT,
    NO_PROMPT_SECTIONS,
    Base,
)
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
)
from book_maker.translator.claude_translator import Claude
from book_maker.translator.codex_translator import Codex
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.groq_translator import GroqClient
from book_maker.translator.litellm_translator import liteLLM
from book_maker.translator.orcarouter_translator import OrcaRouterTranslator
from book_maker.translator.qwen_translator import QwenTranslator
from book_maker.translator.xai_translator import XAIClient
from book_maker.utils import num_tokens_from_text, prompt_config_to_kwargs

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    FIXTURE,
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_fixture,
)

REPO = Path(__file__).resolve().parent.parent
LANGUAGE = "Chinese"

# The two sections as a default run sends them, placeholders filled.
SYSTEM = BUILTIN_PROMPT["system"].format(language=LANGUAGE)


def USER(text):
    return BUILTIN_PROMPT["user"].format(text=text, language=LANGUAGE, crlf="\n")


CUSTOM_USER = "Render {text} into {language}, plainly."
CUSTOM_SYSTEM = "You are a stern nineteenth-century schoolmaster."

# Every variable a prompt section can come from, on the routes read here.
PROMPT_VARIABLES = (
    "OPENAI_API_SYS_MSG",
    "BBM_CHATGPTAPI_SYS_MSG",
    "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "BBM_GEMINIAPI_SYS_MSG",
    "BBM_GEMINIAPI_USER_MSG_TEMPLATE",
)


@pytest.fixture(autouse=True)
def no_prompt_variables(monkeypatch):
    """A developer's exported prompt must not decide what these tests see."""
    for name in PROMPT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _system_count(messages):
    """How many times the built-in system message appears in one request."""
    return sum((m.get("content") or "").count(SYSTEM) for m in messages)


# ------------------------------------------------------------ stand-in clients


def _paragraph_ids(content):
    """The ids of a structured batch's `{"paragraphs": [...]}` payload."""
    start = content.find('{"paragraphs":')
    if start < 0:
        return None
    payload, _ = json.JSONDecoder().raw_decode(content[start:])
    return [row["id"] for row in payload["paragraphs"]]


def _delimited_count(content):
    match = re.search(r"Output EXACTLY (\d+) translations", content)
    return int(match.group(1)) if match else None


def _plain_reply(content):
    count = _delimited_count(content)
    if count:
        return BATCH_DELIMITER.join(f"译{i}" for i in range(count))
    return "译文"


def _openai(verdict="unsupported", **kwargs):
    """A ChatGPTAPI built by its real __init__, answering from a stand-in.

    `verdict` is the endpoint's structured-output degree, which decides the
    request shape: "unsupported" sends single units plain and groups on the
    delimiter rung, "strict" through Structured Outputs, "json" as a
    json_object batch.
    """
    t = ChatGPTAPI(key="k", language=LANGUAGE, **kwargs)
    t.model = "test-model"
    t.capabilities.record("test-model", verdict)
    t.sent = []
    batch_field = batch_field_name(LANGUAGE)
    item_field = single_field_name(LANGUAGE)

    def completion(message):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
        )

    def create(**call):
        t.sent.append(call)
        content = call["messages"][-1]["content"]
        ids = _paragraph_ids(content)
        if ids is not None:  # the json degree: a json_object batch
            rows = [{"id": i, item_field: f"译{i}"} for i in ids]
            text = json.dumps({batch_field: rows}, ensure_ascii=False)
        else:
            text = _plain_reply(content)
        return completion(SimpleNamespace(content=text, refusal=None))

    def parse(**call):
        t.sent.append(call)
        ids = _paragraph_ids(call["messages"][-1]["content"])
        if ids is None:
            parsed = SimpleNamespace(**{item_field: "译文"})
        else:
            items = [SimpleNamespace(id=i, **{item_field: f"译{i}"}) for i in ids]
            parsed = SimpleNamespace(**{batch_field: items})
        return completion(SimpleNamespace(parsed=parsed, refusal=None, content=None))

    t.openai_client = SimpleNamespace(
        api_key="k",
        chat=SimpleNamespace(completions=SimpleNamespace(create=create, parse=parse)),
    )
    return t


def _claude(**kwargs):
    """A Claude built by its real __init__, answering from a stand-in."""
    with patch("book_maker.translator.claude_translator.Anthropic"):
        t = Claude("k", LANGUAGE, **kwargs)
    t.sent = []

    def create(**call):
        t.sent.append(call)
        text = _plain_reply(call["messages"][-1]["content"])
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], usage=None
        )

    t.client = SimpleNamespace(api_key="k", messages=SimpleNamespace(create=create))
    return t


def _claude_replying(replies, **kwargs):
    """A Claude whose stand-in answers with `replies`, in order."""
    t = _claude(**kwargs)
    queued = list(replies)

    def create(**call):
        t.sent.append(call)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=queued.pop(0))], usage=None
        )

    t.client.messages.create = create
    return t


class _GeminiChat:
    def __init__(self, config, sent):
        self.config = config
        self.sent = sent

    def send_message(self, message, config=None):
        # a batch names its own config; a single turn rides the chat's
        self.sent.append({"message": message, "config": config or self.config})
        count = re.search(r"exactly (\d+) translated texts", message)
        if count:
            texts = [f"译{i}" for i in range(int(count.group(1)))]
            text = json.dumps({"translated_paragraphs": texts}, ensure_ascii=False)
        else:
            text = "译文"
        return SimpleNamespace(text=text, usage_metadata=None)

    def get_history(self):
        return []


def _gemini(**kwargs):
    """A Gemini built by its real __init__, its chats a stand-in."""
    g = Gemini("k", LANGUAGE, **kwargs)
    g.interval = 0
    g.sent = []
    g.client = SimpleNamespace(
        chats=SimpleNamespace(
            create=lambda model, config, history=None: _GeminiChat(config, g.sent)
        )
    )
    g.model_list = cycle(["gemini-test"])
    g.rotate_model()
    return g


# ------------------------------------------- 1. the constant is the file


def test_the_builtin_pair_is_prompt_template_json_word_for_word():
    shipped = json.loads((REPO / "prompt_template.json").read_text(encoding="utf-8"))
    assert dict(BUILTIN_PROMPT) == shipped


def test_the_shipped_file_still_passes_as_a_prompt():
    # `--prompt prompt_template.json` is how a run asked for this text before
    # it was the default; the explicit form must keep reading as the same two
    # strings
    parsed = parse_prompt_arg(str(REPO / "prompt_template.json"), announce=False)
    assert parsed == dict(BUILTIN_PROMPT)


def test_the_shared_pair_cannot_be_edited_in_place():
    with pytest.raises(TypeError):
        BUILTIN_PROMPT["system"] = "something else"


def test_building_routes_leaves_the_shared_pair_alone(monkeypatch):
    before = dict(BUILTIN_PROMPT)
    monkeypatch.setenv("BBM_CHATGPTAPI_SYS_MSG", "env system")
    _openai(prompt_template=CUSTOM_USER, style_note="Terse.")
    _openai()
    _claude(prompt_sys_msg=CUSTOM_SYSTEM)
    _gemini(prompt_template=CUSTOM_USER)
    assert dict(BUILTIN_PROMPT) == before


# --------------------------------------------------- 2. which routes take it


@pytest.mark.parametrize(
    "route", [ChatGPTAPI, GroqClient, XAIClient, liteLLM, OrcaRouterTranslator]
)
def test_the_openai_family_sends_the_builtin_pair(route):
    t = route("k", LANGUAGE)
    assert t.prompt_template == BUILTIN_PROMPT["user"]
    assert t.standing_instructions() == SYSTEM


def test_gemini_sends_the_builtin_pair():
    g = _gemini()
    assert g.prompt == BUILTIN_PROMPT["user"]
    assert g._system_instruction() == SYSTEM


def test_claude_sends_the_builtin_pair():
    t = _claude()
    assert t.prompt_template == BUILTIN_PROMPT["user"]
    assert t.standing_instructions() == SYSTEM


class _CodexSidecar:
    """The codex app server, as far as one translated turn needs it."""

    def __init__(self):
        self.threads = []
        self.turns = []

    def start(self):
        return self

    def latest_rate_limits(self):
        return None

    def rate_limits(self):
        return None

    def start_thread(self, model=None, base_instructions=None, cwd=None):
        self.threads.append(base_instructions)
        return f"th-{len(self.threads)}"

    def run_turn(self, thread_id, text, output_schema=None, timeout=None):
        self.turns.append(text)
        return "译文"


def test_codex_still_sends_bare_text_under_its_own_instructions():
    server = _CodexSidecar()
    codex = Codex(key="", language=LANGUAGE, server=server)
    codex.translate("The quick fox.")

    assert server.turns == ["The quick fox."]
    assert SYSTEM not in server.threads[0]
    assert not hasattr(Codex, "DEFAULT_PROMPT")
    assert codex.resolved_prompt_parts() == {"user": "", "system": "", "style": ""}


@pytest.mark.parametrize(
    "name",
    sorted(
        name
        for name, cls in FORMAT_DICT.items()
        if getattr(cls, "PROMPT_SECTION_SLOTS", None) == NO_PROMPT_SECTIONS
    ),
)
def test_a_route_that_builds_no_prompt_still_sends_none(name):
    # qwen's translation models, the fixed MT engines and the custom
    # endpoint: nothing inherits the pair, and the run's identity says so
    cls = FORMAT_DICT[name]
    assert not issubclass(cls, (ChatGPTAPI, Gemini, Claude))
    assert not hasattr(cls, "DEFAULT_PROMPT")
    route = cls.__new__(cls)
    assert route.resolved_prompt_parts() == {"user": "", "system": "", "style": ""}


def test_qwen_is_among_the_routes_that_build_no_prompt():
    assert QwenTranslator.PROMPT_SECTION_SLOTS == NO_PROMPT_SECTIONS


def test_the_subtitle_loader_keeps_its_own_pair(tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello.\n", encoding="utf-8")
    loader = SRTBookLoader(str(srt), ChatGPTAPI, "k", False, LANGUAGE)
    assert loader.translate_model.prompt_template == DEFAULT_PROMPT_CONFIG["user"]
    assert loader.translate_model.prompt_sys_msg == DEFAULT_PROMPT_CONFIG["system"]


# ------------------------------------------------------------ 3. precedence


def _sections(route, **kwargs):
    """(user template, system message) the route resolved."""
    if route is Gemini:
        g = _gemini(**kwargs)
        return g.prompt, g.prompt_sys_msg
    t = (_claude if route is Claude else _openai)(**kwargs)
    return t.prompt_template, t.prompt_sys_msg


# The value each route has always used for "no system message".
NO_SYSTEM = {ChatGPTAPI: "", Claude: "", Gemini: None}
ROUTES = [ChatGPTAPI, Gemini, Claude]


@pytest.mark.parametrize("route", ROUTES)
class TestThePrecedenceTable:
    """The table of the brief, one row per test, on every route that takes it."""

    def test_nothing_supplied_sends_the_builtin_pair(self, route):
        assert _sections(route) == (BUILTIN_PROMPT["user"], BUILTIN_PROMPT["system"])

    def test_a_user_template_alone_keeps_an_empty_system_message(self, route):
        # as at aed40c7: a custom template was written against no system
        # message, and the built-in one must not appear under it
        assert _sections(route, prompt_template=CUSTOM_USER) == (
            CUSTOM_USER,
            NO_SYSTEM[route],
        )

    def test_a_system_message_alone_gets_the_builtin_user_template(self, route):
        assert _sections(route, prompt_sys_msg=CUSTOM_SYSTEM) == (
            BUILTIN_PROMPT["user"],
            CUSTOM_SYSTEM,
        )

    def test_both_supplied_send_both(self, route):
        assert _sections(
            route, prompt_template=CUSTOM_USER, prompt_sys_msg=CUSTOM_SYSTEM
        ) == (CUSTOM_USER, CUSTOM_SYSTEM)

    def test_a_style_alone_rides_with_the_builtin_pair(self, route):
        assert _sections(route, style_note="Terse.") == (
            BUILTIN_PROMPT["user"],
            BUILTIN_PROMPT["system"],
        )

    def test_a_user_template_that_reads_like_the_builtin_is_still_supplied(self, route):
        # provenance, never a text comparison
        assert _sections(route, prompt_template=BUILTIN_PROMPT["user"]) == (
            BUILTIN_PROMPT["user"],
            NO_SYSTEM[route],
        )

    def test_an_explicitly_empty_system_message_stays_empty(self, route):
        assert _sections(route, prompt_sys_msg="") == (
            BUILTIN_PROMPT["user"],
            NO_SYSTEM[route],
        )

    def test_an_empty_user_template_falls_through_to_the_builtin_pair(self, route):
        # the `or` every route has always had: an empty template is no
        # template, so the run is a stock run
        assert _sections(route, prompt_template="") == (
            BUILTIN_PROMPT["user"],
            BUILTIN_PROMPT["system"],
        )


def test_style_composes_after_the_builtin_system_message():
    t = _openai(style_note="Terse.")
    assert t.standing_instructions() == f"{SYSTEM}\n\n{Base.STYLE_HEADING} Terse."


def test_the_source_language_note_composes_after_the_builtin_system_message():
    t = _openai()
    t.source_language = "English"
    assert t.standing_instructions() == (
        f"{SYSTEM} Translate from English into {LANGUAGE}."
    )


class TestTheEnvironment:
    """The variables keep their place behind `--prompt`, and count as supplied."""

    def test_a_user_template_variable_alone_keeps_an_empty_system(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_USER_MSG_TEMPLATE", CUSTOM_USER)
        assert _sections(ChatGPTAPI) == (CUSTOM_USER, "")

    @pytest.mark.parametrize(
        "variable", ["BBM_CHATGPTAPI_SYS_MSG", "OPENAI_API_SYS_MSG"]
    )
    def test_a_system_variable_alone_gets_the_builtin_user(self, monkeypatch, variable):
        monkeypatch.setenv(variable, CUSTOM_SYSTEM)
        assert _sections(ChatGPTAPI) == (BUILTIN_PROMPT["user"], CUSTOM_SYSTEM)

    def test_gemini_reads_its_own_variables(self, monkeypatch):
        monkeypatch.setenv("BBM_GEMINIAPI_SYS_MSG", CUSTOM_SYSTEM)
        assert _sections(Gemini) == (BUILTIN_PROMPT["user"], CUSTOM_SYSTEM)
        monkeypatch.delenv("BBM_GEMINIAPI_SYS_MSG")
        monkeypatch.setenv("BBM_GEMINIAPI_USER_MSG_TEMPLATE", CUSTOM_USER)
        assert _sections(Gemini) == (CUSTOM_USER, None)

    def test_a_file_user_and_an_environment_system_mix(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_SYS_MSG", CUSTOM_SYSTEM)
        assert _sections(ChatGPTAPI, prompt_template=CUSTOM_USER) == (
            CUSTOM_USER,
            CUSTOM_SYSTEM,
        )

    def test_a_file_system_and_an_environment_user_mix(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_USER_MSG_TEMPLATE", CUSTOM_USER)
        assert _sections(ChatGPTAPI, prompt_sys_msg=CUSTOM_SYSTEM) == (
            CUSTOM_USER,
            CUSTOM_SYSTEM,
        )

    def test_the_flag_outranks_the_environment_as_before(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_USER_MSG_TEMPLATE", "env {text}")
        monkeypatch.setenv("BBM_CHATGPTAPI_SYS_MSG", "env system")
        monkeypatch.setenv("OPENAI_API_SYS_MSG", "legacy system")
        assert _sections(
            ChatGPTAPI, prompt_template=CUSTOM_USER, prompt_sys_msg=CUSTOM_SYSTEM
        ) == (CUSTOM_USER, CUSTOM_SYSTEM)
        # and the deprecated variable still outranks the BBM_ one
        assert _sections(ChatGPTAPI, prompt_template=CUSTOM_USER) == (
            CUSTOM_USER,
            "legacy system",
        )

    def test_an_empty_system_variable_stays_empty(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_SYS_MSG", "")
        assert _sections(ChatGPTAPI) == (BUILTIN_PROMPT["user"], "")

    def test_an_empty_flag_still_falls_through_to_a_variable(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_SYS_MSG", CUSTOM_SYSTEM)
        assert _sections(ChatGPTAPI, prompt_sys_msg="") == (
            BUILTIN_PROMPT["user"],
            CUSTOM_SYSTEM,
        )

    def test_an_empty_user_variable_is_a_stock_run(self, monkeypatch):
        monkeypatch.setenv("BBM_CHATGPTAPI_USER_MSG_TEMPLATE", "")
        assert _sections(ChatGPTAPI) == (
            BUILTIN_PROMPT["user"],
            BUILTIN_PROMPT["system"],
        )


class TestTheShippedPromptFiles:
    """The example files, through the same parse and hand-off the CLI does."""

    def _route(self, name):
        config = parse_prompt_arg(str(REPO / name), announce=False)
        return config, _openai(**prompt_config_to_kwargs(config))

    def test_prompt_md_json_sends_both_of_its_own_sections(self):
        # it carries a system message of its own beside the three-step XML
        config, t = self._route("prompt_md.json")
        assert t.prompt_template == config["user"]
        assert t.prompt_sys_msg == config["system"]

    def test_the_three_step_xml_template_alone_sends_no_system_message(self):
        # the same template as a user-only prompt: exactly what aed40c7 sent
        config = parse_prompt_arg(str(REPO / "prompt_md.json"), announce=False)
        t = _openai(**prompt_config_to_kwargs({"user": config["user"]}))
        messages = t.create_messages("The quick fox.")
        assert messages[0] == {"role": "system", "content": ""}
        assert "<step3_refined_translation>" in messages[-1]["content"]

    def test_the_txt_sample_is_user_only_and_sends_no_system_message(self):
        config, t = self._route("prompt_template_sample.txt")
        assert set(config) == {"user"}
        assert t.prompt_sys_msg == ""
        assert t.create_messages("x")[0] == {"role": "system", "content": ""}


# --------------------------------------------------- 4. what "stock" means


class TestClaudeStockPrompt:
    """The fence unwrap runs under the built-in template and nowhere else."""

    def test_the_builtin_prompt_is_stock(self):
        assert _claude()._uses_stock_prompt is True

    def test_a_system_message_alone_is_still_stock(self):
        # the user template is the built-in one, which is all stock means
        assert _claude(prompt_sys_msg=CUSTOM_SYSTEM)._uses_stock_prompt is True

    def test_a_supplied_template_is_never_stock(self):
        assert _claude(prompt_template=CUSTOM_USER)._uses_stock_prompt is False
        # even one that reads like the built-in: provenance, not text
        assert (
            _claude(prompt_template=BUILTIN_PROMPT["user"])._uses_stock_prompt is False
        )

    def test_an_accidental_outer_fence_is_removed_under_the_builtin_prompt(self):
        t = _claude_replying(["```\n译文\n```"])
        assert t.translate("The quick fox.") == "译文"

    def test_a_source_that_is_one_fenced_block_keeps_its_fence(self):
        reply = "```python\nprint('你好')\n```"
        t = _claude_replying([reply])
        assert t.translate("```python\nprint('hello')\n```") == reply

    def test_a_custom_prompt_s_fenced_reply_is_untouched(self):
        t = _claude_replying(["```md\n译文\n```"], prompt_template=CUSTOM_USER)
        assert t.translate("The quick fox.") == "```md\n译文\n```"


# ------------------------------------------------ 5. every request shape


class TestOpenAIRequests:
    """The full request each shape sends under the built-in pair."""

    def test_a_single_plain_request(self):
        t = _openai()
        t.translate("The quick fox.")
        (call,) = t.sent
        assert call["messages"] == [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER("The quick fox.")},
        ]

    def test_a_single_structured_request(self):
        t = _openai("strict")
        t.translate("The quick fox.")
        (call,) = t.sent
        assert call["messages"] == [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER("The quick fox.")},
        ]
        assert call["response_format"] is not None

    @pytest.mark.parametrize("verdict", ["strict", "json"])
    def test_a_structured_batch(self, verdict):
        t = _openai(verdict)
        assert t.translate_list(["One.", "Two."]) == ["译0", "译1"]
        (call,) = t.sent
        system, user = call["messages"]
        assert system == {"role": "system", "content": SYSTEM}
        payload = json.dumps(
            {"paragraphs": [{"id": 0, "text": "One."}, {"id": 1, "text": "Two."}]},
            ensure_ascii=False,
        )
        # the functional preamble, the built-in template around the payload,
        # then the shape tail -- in that order, and the system message once
        assert user["content"].startswith(f"{Base.STRUCTURE_INSTRUCTION}\n\n")
        assert USER(payload) in user["content"]
        assert user["content"].index(USER(payload)) < user["content"].index(
            "Return a JSON object whose"
        )
        assert _system_count(call["messages"]) == 1
        if verdict == "json":
            assert call["response_format"] == {"type": "json_object"}
            assert user["content"].endswith(
                f"Every translation must be written in {LANGUAGE}."
            )

    def test_the_delimiter_batch_composes_with_the_builtin_system_message(self):
        t = _openai("unsupported")
        assert t.translate_list(["One.", "Two."]) == ["译0", "译1"]
        (call,) = t.sent
        system, user = call["messages"]
        # composed with, not overwritten by, the batch contract
        assert system["content"] == (
            f"{SYSTEM} Input has 2 segments separated by '{BATCH_DELIMITER}'. "
            f"Output 2 translations with '{BATCH_DELIMITER}' between each."
        )
        assert user["content"] == (
            f"{Base.STRUCTURE_INSTRUCTION}\n\n"
            f"Translate the following 2 text segments to {LANGUAGE}. "
            f"Separate each translation with '{BATCH_DELIMITER}'. "
            f"Output EXACTLY 2 translations.\n\n"
            + USER(BATCH_DELIMITER.join(["One.", "Two."]))
        )
        assert _system_count(call["messages"]) == 1

    def test_the_delimiter_batch_puts_the_system_message_back(self):
        t = _openai("unsupported")
        t.translate_list(["One.", "Two."])
        t.translate("Three.")
        assert t.prompt_sys_msg == BUILTIN_PROMPT["system"]
        assert t.sent[-1]["messages"][0] == {"role": "system", "content": SYSTEM}

    def test_a_session_request_after_several_turns(self):
        t = _openai("strict", context_flag=True, context_mode="session")
        for text in ("One.", "Two.", "Three.", "Four."):
            t.translate(text)
        last = t.sent[-1]["messages"]
        assert last[0] == {"role": "system", "content": SYSTEM}
        # the history replays what was sent, and none of it repeats the
        # system message
        assert [m["content"] for m in last[1:] if m["role"] == "user"] == [
            USER("One."),
            USER("Two."),
            USER("Three."),
            USER("Four."),
        ]
        assert _system_count(last) == 1

    def _compacted(self):
        """A session that compacted after its first unit, and the compact turn."""
        t = _openai(
            "strict", context_flag=True, context_mode="session", context_compact_at=1
        )
        t.translate("One.")
        t.translate("Two.")
        handoff = handoff_prompt()
        compact = next(c for c in t.sent if c["messages"][-1]["content"] == handoff)
        return t, compact

    def test_the_first_request_after_a_compaction(self):
        t, compact = self._compacted()
        after = t.sent[t.sent.index(compact) + 1]["messages"]
        # the standing instructions survive the handoff: the same system
        # message, then the seed, then the new unit -- and the seed does not
        # repeat the system message
        assert after[0] == {"role": "system", "content": SYSTEM}
        assert after[1]["content"].startswith(HandoffReport(1, "").seed_text()[:40])
        assert after[-1] == {"role": "user", "content": USER("Two.")}
        assert _system_count(after) == 1

    @pytest.mark.xfail(
        strict=True,
        reason="found 261125, predates the built-in prompt: the openai compact "
        "turn sends the window with no system message at all (Claude's sends "
        "standing_instructions); tracked separately, not fixed here",
    )
    def test_the_compact_turn_runs_under_the_standing_instructions(self):
        _, compact = self._compacted()
        assert compact["messages"][0] == {"role": "system", "content": SYSTEM}


class TestClaudeRequests:
    """The `system` parameter carries it; the turns never repeat it."""

    def _system(self, call):
        return call["system"]

    def test_a_single_request(self):
        t = _claude()
        t.translate("The quick fox.")
        (call,) = t.sent
        assert call["system"] == SYSTEM
        assert call["messages"] == [{"role": "user", "content": USER("The quick fox.")}]

    def test_the_delimiter_batch(self):
        t = _claude()
        assert t.translate_list(["One.", "Two."]) == ["译0", "译1"]
        (call,) = t.sent
        assert call["system"] == (
            f"{SYSTEM} Input has 2 segments separated by '{BATCH_DELIMITER}'. "
            f"Output 2 translations with '{BATCH_DELIMITER}' between each."
        )
        assert call["messages"][-1]["content"].endswith(
            USER(BATCH_DELIMITER.join(["One.", "Two."]))
        )
        assert _system_count(call["messages"]) == 0
        # and it is put back for the next request
        t.translate("Three.")
        assert t.sent[-1]["system"] == SYSTEM

    def test_a_session_request_after_several_turns(self):
        t = _claude(context_flag=True, context_mode="session")
        for text in ("One.", "Two.", "Three."):
            t.translate(text)
        call = t.sent[-1]
        assert call["system"] == SYSTEM
        assert [m["content"] for m in call["messages"] if m["role"] == "user"] == [
            USER("One."),
            USER("Two."),
            USER("Three."),
        ]
        assert _system_count(call["messages"]) == 0

    def test_the_first_request_after_a_compaction(self):
        t = _claude(context_flag=True, context_mode="session", context_compact_at=1)
        t.translate("One.")
        t.translate("Two.")
        handoff = handoff_prompt(with_glossary=False)
        compact = next(c for c in t.sent if c["messages"][-1]["content"] == handoff)
        after = t.sent[t.sent.index(compact) + 1]
        assert compact["system"] == SYSTEM
        assert after["system"] == SYSTEM
        assert after["messages"][-1] == {"role": "user", "content": USER("Two.")}
        assert _system_count(after["messages"]) == 0


class TestGeminiRequests:
    """Its native `system_instruction`, never folded into the turn."""

    def test_a_single_request(self):
        g = _gemini()
        assert g.translate("The quick fox.") == "译文"
        (sent,) = g.sent
        assert sent["message"] == USER("The quick fox.")
        assert sent["config"].system_instruction == SYSTEM

    def test_a_batch(self):
        g = _gemini()
        assert g.translate_list(["One.", "Two."]) == ["译0", "译1"]
        (sent,) = g.sent
        assert sent["config"].system_instruction == SYSTEM
        assert sent["message"].startswith(USER("One.\n\nTwo."))
        assert SYSTEM not in sent["message"]

    def test_a_user_template_alone_sends_no_system_instruction(self):
        g = _gemini(prompt_template=CUSTOM_USER)
        g.translate("The quick fox.")
        assert g.sent[0]["config"].system_instruction is None


# ----------------------------------------------------------------- 6. budget


def test_the_overhead_counts_the_builtin_system_message():
    stock = _openai()
    # the same built-in user template with the system message explicitly off
    bare = _openai(prompt_sys_msg="")
    assert stock.prompt_overhead_tokens() - bare.prompt_overhead_tokens() == (
        num_tokens_from_text(SYSTEM) - num_tokens_from_text("")
    )


@pytest.mark.parametrize(
    "route,kwargs,expected",
    [
        ("session", {"context_flag": True, "context_mode": "session"}, None),
        ("schema", {}, None),
        ("substrict", {}, SUBSTRICT_BUDGET_FLOOR),
    ],
)
def test_the_stock_prompt_still_groups_at_the_floor(route, kwargs, expected):
    # `--accumulated_num`'s help says "{SESSION_BUDGET_FLOOR} with the stock
    # prompts": the built-in pair is bigger than the one-liner, and still
    # measures under a third of the floor
    overhead = _openai(**kwargs).prompt_overhead_tokens()
    assert 3 * overhead < SESSION_BUDGET_FLOOR
    assert derived_token_budget(overhead, route) == (expected or SESSION_BUDGET_FLOOR)


# ---------------------------------------------------------------- 7. identity

# What aed40c7 -- the last revision with the one-liner -- recorded for the
# configuration `_epub_loader` builds (language "Simplified Chinese", the
# openai route, no model list, no glossary). Computed by running this same
# configuration against a `git archive aed40c7` copy; they are the values a
# resume cache written before this change carries.
AED40C7_DEFAULT_FINGERPRINT = (
    "1ace0d6906fa18de8e6976921d6b3f8d7d48f497c04e3bc08b3ca2cf3c8c3f05"
)
AED40C7_USER_ONLY_FINGERPRINT = (
    "da8f014f423a0970add78dde5e1d780e053ce51bcdc648500eef0d6fde47ae0c"
)


def _write_epub(path):
    book = epub.EpubBook()
    book.set_identifier("default-prompt")
    book.set_title("Default prompt")
    book.set_language("en")
    item = epub.EpubHtml(title="Chapter", file_name="chapter.xhtml", lang="en")
    item.content = "<html><body><p>one</p><p>two</p></body></html>"
    book.add_item(item)
    book.toc = (item,)
    book.spine = [item]
    epub.write_epub(str(path), book)
    return path


def _epub_loader(source, resume=False, prompt_config=None):
    return EPUBBookLoader(
        str(source),
        ChatGPTAPI,
        key="sk-fingerprint",
        resume=resume,
        language="Simplified Chinese",
        prompt_config=prompt_config,
    )


class TestTheEpubResumeFingerprint:
    def test_the_new_default_is_not_the_old_one(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        assert _epub_loader(source)._run_fingerprint() != AED40C7_DEFAULT_FINGERPRINT

    def test_the_new_default_is_the_explicit_template_file(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        explicit = parse_prompt_arg(str(REPO / "prompt_template.json"), announce=False)
        assert (
            _epub_loader(source)._run_fingerprint()
            == _epub_loader(source, prompt_config=explicit)._run_fingerprint()
        )

    def test_a_user_only_prompt_fingerprints_as_it_did_before(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        loader = _epub_loader(source, prompt_config={"user": CUSTOM_USER})
        assert loader._run_fingerprint() == AED40C7_USER_ONLY_FINGERPRINT

    def _checkpoint(self, source, fingerprint=None):
        loader = _epub_loader(source)
        loader._planned_job_ids = ["job-0", "job-1"]
        loader.p_to_save = ["<T>one</T>"]
        loader._save_progress()
        if fingerprint is not None:
            with open(loader.bin_path, "rb") as handle:
                payload = pickle.load(handle)
            payload["run_fingerprint"] = fingerprint
            with open(loader.bin_path, "wb") as handle:
                pickle.dump(payload, handle)

    def test_the_same_configuration_resumes(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        self._checkpoint(source)
        _epub_loader(source, resume=True)._check_resume_run_fingerprint()
        assert "different language" not in capsys.readouterr().out

    def test_a_cache_from_the_old_default_is_refused_by_name(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        self._checkpoint(source, fingerprint=AED40C7_DEFAULT_FINGERPRINT)
        resumed = _epub_loader(source, resume=True)
        with pytest.raises(SystemExit) as stopped:
            resumed._check_resume_run_fingerprint()
        assert stopped.value.code == 1
        out = " ".join(capsys.readouterr().out.split())
        assert "written by a run with a different language, prompt or model" in out
        assert "Delete" in out


class TestThePdfBundleIdentity:
    """The sparse identity cannot see a default; one version entry can."""

    def test_every_identity_carries_the_version(self):
        from book_maker.pipeline.translate import (
            IDENTITY_VERSION,
            option_identity,
            parse_bbm_options,
        )

        assert isinstance(IDENTITY_VERSION, int) and IDENTITY_VERSION >= 1
        for command in (
            [],
            ["--model", "gpt-5.6-luna", "--language", "ja"],
            ["--prompt", str(REPO / "prompt_template.json")],
        ):
            identity = option_identity(parse_bbm_options(command))
            assert identity[-1] == ("identity_version", IDENTITY_VERSION)

    @pytest.fixture
    def bundle(self, tmp_path, monkeypatch):
        from book_maker.pipeline.bundle import Bundle
        from book_maker.pipeline.importer import import_markdown

        pandoc = pandoc_or_skip()
        register_fake_format(monkeypatch)
        book = write_fixture(tmp_path / "src", FIXTURE)
        bundle = Bundle(tmp_path / "bundle").create()
        import_markdown(bundle, book, pandoc=pandoc)
        bundle.pandoc = pandoc
        return bundle

    OPTIONS = ["--api_format", "faketest", "--language", "zh-hans"]

    def _record_the_pre_version_fingerprint(self, bundle, monkeypatch):
        """Write down what aed40c7 recorded: the same identity, no version."""
        from book_maker.pipeline import translate

        current = translate.option_identity
        with monkeypatch.context() as m:
            m.setattr(
                translate,
                "option_identity",
                lambda options: [
                    entry
                    for entry in current(options)
                    if entry[0] != "identity_version"
                ],
            )
            old = translate.translation_fingerprint(bundle, self.OPTIONS)
        assert old != translate.translation_fingerprint(bundle, self.OPTIONS)
        bundle.update_manifest(translation={"fingerprint": old})

    def test_a_bundle_finished_before_the_change_is_translated_again(
        self, bundle, monkeypatch
    ):
        from book_maker.pipeline.translate import translate_bundle

        translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        self._record_the_pre_version_fingerprint(bundle, monkeypatch)

        FakeTranslator.instances = []
        translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        assert [
            t for i in FakeTranslator.instances for t in i.translated
        ], "a bundle translated under the old default was reused as finished"

    def test_an_interrupted_bundle_from_before_the_change_is_refused(
        self, bundle, monkeypatch
    ):
        from book_maker.pipeline.errors import PipelineError
        from book_maker.pipeline.messages import SETTINGS_CHANGED
        from book_maker.pipeline.translate import translate_bundle

        FakeTranslator.fail_after = 2
        with pytest.raises(PipelineError):
            translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        self._record_the_pre_version_fingerprint(bundle, monkeypatch)

        FakeTranslator.fail_after = None
        with pytest.raises(PipelineError) as refused:
            translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        assert refused.value.detail == SETTINGS_CHANGED

    # ------------------------------------------- the prompt variables (review 1)
    #
    # An exported empty system variable sends no system message, an unset one
    # the built-in one, and the namespace the identity is built from sees
    # neither. Each set variable is written down by name and a hash of its
    # value; an unset one not at all.

    # What `option_identity` returned at 3e8a9cb, before the variables went
    # in, with none of them set.
    BEFORE_THE_VARIABLES = {
        (): [["identity_version", 1]],
        ("--model", "gpt-5.6-luna", "--language", "ja"): [
            ["language", "ja"],
            ["model", "gpt-5.6-luna"],
            ["identity_version", 1],
        ],
        ("--prompt", "prompt_template.json"): [
            [
                "prompt_arg",
                "file:d3f2986093e2f353c50a59721dcc3b09c76cb1f910e6ad93ad873c548dcba736",
            ],
            ["identity_version", 1],
        ],
    }

    def test_the_identity_watches_every_prompt_variable_the_routes_read(self):
        assert set(PROMPT_VARIABLES) == set(PROMPT_ENV_VARS)

    def test_with_no_prompt_variable_set_the_identity_is_unchanged(self, monkeypatch):
        from book_maker.pipeline.translate import option_identity, parse_bbm_options

        monkeypatch.chdir(REPO)
        for command, before in self.BEFORE_THE_VARIABLES.items():
            identity = option_identity(parse_bbm_options(list(command)))
            assert json.loads(json.dumps(identity)) == before, command

    @pytest.mark.parametrize("name", sorted(PROMPT_VARIABLES))
    def test_unset_empty_and_set_are_three_identities(self, name, monkeypatch):
        from book_maker.pipeline.translate import (
            IDENTITY_VERSION,
            option_identity,
            parse_bbm_options,
        )

        options = parse_bbm_options(["--language", "ja"])
        identities = []
        for value in (None, "", "Answer as a ship's purser."):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
            identity = option_identity(options)
            assert identity[-1] == ("identity_version", IDENTITY_VERSION)
            identities.append(identity)
        assert len({json.dumps(i) for i in identities}) == 3
        assert [e for e in identities[1] if e[0].startswith("env:")] == [
            (f"env:{name}", f"sha256:{sha256(b'').hexdigest()}")
        ]

    def test_a_variable_value_is_never_written_in_clear(self, monkeypatch):
        from book_maker.pipeline.translate import option_identity, parse_bbm_options

        values = {name: f"Speak like {name.lower()} would." for name in PROMPT_ENV_VARS}
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        written = json.dumps(option_identity(parse_bbm_options([])))
        for name, value in values.items():
            assert value not in written
            assert f"sha256:{sha256(value.encode()).hexdigest()}" in written

    def test_a_bundle_finished_under_an_empty_system_variable_is_translated_again(
        self, bundle, monkeypatch
    ):
        from book_maker.pipeline.translate import translate_bundle

        monkeypatch.setenv("BBM_CHATGPTAPI_SYS_MSG", "")
        translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)

        monkeypatch.delenv("BBM_CHATGPTAPI_SYS_MSG")
        FakeTranslator.instances = []
        translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        assert [
            t for i in FakeTranslator.instances for t in i.translated
        ], "a bundle translated with no system message was reused under the built-in one"

    def test_a_bundle_interrupted_under_a_system_variable_is_refused_when_it_is_empty(
        self, bundle, monkeypatch
    ):
        from book_maker.pipeline.errors import PipelineError
        from book_maker.pipeline.messages import SETTINGS_CHANGED
        from book_maker.pipeline.translate import translate_bundle

        monkeypatch.setenv("OPENAI_API_SYS_MSG", "Answer as a ship's purser.")
        FakeTranslator.fail_after = 2
        with pytest.raises(PipelineError):
            translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        assert "purser" not in bundle.manifest_path.read_text(encoding="utf-8")

        monkeypatch.setenv("OPENAI_API_SYS_MSG", "")
        FakeTranslator.fail_after = None
        with pytest.raises(PipelineError) as refused:
            translate_bundle(bundle, self.OPTIONS, pandoc=bundle.pandoc)
        assert refused.value.detail == SETTINGS_CHANGED
