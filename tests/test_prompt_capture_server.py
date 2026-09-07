"""What the endpoint actually receives, for every `--prompt` section.

Every other test of the prompt sections reads a string a translator built.
This file reads the HTTP request bodies a real run sent, because that is the
only place a section can be proved to have arrived: the openai route assembles
its user turn in three different places (the per-paragraph message, the
structured batch, the delimiter batch), each is reached by a different mode,
and a section can be present in one and missing from another without any of
them looking wrong on their own. `style` was missing from all three until
260905, and the code read as if it were carried.

The endpoint here is a stdlib `http.server` bound to 127.0.0.1 on an ephemeral
port. It speaks just enough of the OpenAI chat-completions shape to answer the
capability probe, the route probe, a structured batch and a single paragraph,
and it keeps every request body it was sent. The CLI is driven as a
subprocess, unmodified, with `--api_base` pointed at it — no key, no network,
no monkeypatching of the code under test.
"""

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from book_maker.translator.base_translator import BATCH_DELIMITER
from book_maker.translator.capabilities import (
    PROBE_EXPECTED,
    PROBE_KEY,
    ROUTE_PROBE_PROMPT,
)
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
)

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"

# What the command says, and what the run makes of it: a bare `--language`
# takes a tag, and the readable name resolved from it is what reaches the
# prompt and the schema field names. Both are needed here — one to drive the
# CLI, one to read the request. `--language TAG:NAME` splits those two apart
# on purpose, and TestTheLanguageSplit below is where that is read.
CLI_LANGUAGE = "zh-hans"
LANGUAGE = "simplified chinese"
BATCH_FIELD = batch_field_name(LANGUAGE)
ITEM_FIELD = single_field_name(LANGUAGE)

# What the fake endpoint answers with. Distinctive so it can be told apart
# from anything the loader might substitute for a failed request.
RENDERED = "译文"

# Credentials the CLI would otherwise fall back to. Left in place, a developer's
# exported key would decide which endpoint these tests talk to.
KEY_ENV_VARS = (
    "BBM_API_KEY",
    "OPENAI_API_KEY",
    "BBM_OPENAI_API_KEY",
    "OPENAI_API_SYS_MSG",
    "BBM_CHATGPTAPI_SYS_MSG",
    "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "ANTHROPIC_API_KEY",
    "BBM_CLAUDE_API_KEY",
)

_COUNT_RE = re.compile(r"EXACTLY (\d+) translations")

# The opening words of the plan classifier's question, on both the structured
# and the session channel. Nothing it sends is a translation request.
CLASSIFY_MARKER = "You are preparing a bilingual EPUB"


class _Handler(BaseHTTPRequestHandler):
    """One OpenAI-shaped endpoint, answering from the request it was given."""

    def log_message(self, *args):  # keep pytest output readable
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(body)
        self._reply(_answer(body, self.server.mode))

    def do_GET(self):
        # `--model_list` verification and the listing hint behind a refusal.
        self._reply_json({"object": "list", "data": [{"id": "gpt-4o-mini"}]})

    def _reply(self, content):
        self._reply_json(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    def _reply_json(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "user":
            return message.get("content") or ""
    return ""


def _schema_fields(body):
    """The batch and item field names *this* request asked for.

    Read off the request rather than assumed, because the field name is part
    of what is under test: `--language zh-hant:Traditional Chinese` asks for
    `zh_hant_paragraphs`, and a server that always answered
    `simplified_chinese_paragraphs` would fail the run instead of proving
    what it sent.
    """
    fmt = body.get("response_format") or {}
    schema = (fmt.get("json_schema") or {}).get("schema") or {}
    for field, spec in (schema.get("properties") or {}).items():
        if spec.get("type") == "array":
            item_props = _item_properties(schema, spec.get("items") or {})
            item = next((key for key in item_props if key != "id"), ITEM_FIELD)
            return field, item
    return BATCH_FIELD, ITEM_FIELD


def _item_properties(schema, items):
    """The item object's properties, through a `$ref` when there is one.

    The SDK builds the batch schema from a nested Pydantic model, so the
    array's items arrive as a reference into `$defs`; the hand-built
    json_object schema inlines them. Both shapes reach this server.
    """
    if "$ref" in items:
        name = items["$ref"].rsplit("/", 1)[-1]
        items = (schema.get("$defs") or {}).get(name) or {}
    return items.get("properties") or {}


def _answer(body, mode="schema"):
    """The reply this request has earned, in the shape it asked for."""
    text = _user_text(body)
    fmt = body.get("response_format") or {}
    name = (fmt.get("json_schema") or {}).get("name")

    if name == "structured_output_probe":
        # The capability probe. Answering it exactly is what puts this run on
        # the structured rung, which is the rung plan mode uses. In "prose"
        # mode it answers the prompt instead — what a proxy that accepts
        # `response_format` and drops it does — and the run descends to the
        # delimiter rung, which assembles its request somewhere else again.
        if mode == "prose":
            return "ignored"
        return json.dumps({PROBE_KEY: PROBE_EXPECTED})
    if ROUTE_PROBE_PROMPT in text:
        return "PONG"

    payload = _paragraphs(text)
    if payload is not None:
        batch_field, item_field = _schema_fields(body)
        rows = [
            {"id": item["id"], item_field: f"{RENDERED}{item['id']}"}
            for item in payload
        ]
        return json.dumps({batch_field: rows}, ensure_ascii=False)

    count = _COUNT_RE.search(text)
    if count:
        # The delimiter rung: the request says how many pieces it wants back.
        n = int(count.group(1))
        return BATCH_DELIMITER.join(f"{RENDERED}{i}" for i in range(n))
    return RENDERED


def _paragraphs(text):
    """The structured batch's paragraph list, when this is one."""
    marker = '{"paragraphs":'
    start = text.find(marker)
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(text[start:])
    except ValueError:
        return None
    rows = obj.get("paragraphs")
    return rows if isinstance(rows, list) else None


class CaptureEndpoint:
    """A running endpoint plus the bodies it was sent."""

    def __init__(self, mode="schema"):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.mode = mode
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def api_base(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    @property
    def requests(self):
        return list(self.server.requests)

    def translation_requests(self):
        """Every request that carried book text.

        Probes and plan classification are excluded: neither translates
        anything, and the classifier writes its own prompt, so a section that
        never reached a *translation* request would still be found in one.
        """
        return [
            body
            for body in self.requests
            if (body.get("response_format") or {}).get("json_schema", {}).get("name")
            != "structured_output_probe"
            and ROUTE_PROBE_PROMPT not in _user_text(body)
            and CLASSIFY_MARKER not in _user_text(body)
        ]

    def user_messages(self):
        return [_user_text(body) for body in self.translation_requests()]

    def system_messages(self):
        out = []
        for body in self.translation_requests():
            for message in body.get("messages") or []:
                if message.get("role") == "system":
                    out.append(message.get("content") or "")
                    break
        return out

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def endpoint():
    running = CaptureEndpoint()
    try:
        yield running
    finally:
        running.close()


@pytest.fixture
def prose_endpoint():
    """An endpoint that does not really honour schemas — the delimiter rung."""
    running = CaptureEndpoint(mode="prose")
    try:
        yield running
    finally:
        running.close()


def _env():
    env = dict(os.environ)
    for name in KEY_ENV_VARS:
        env.pop(name, None)
    # The suite may run with tests/hermetic on PYTHONPATH (the offline
    # harness). These tests are the opposite bargain — a real subprocess
    # against a real local endpoint — and the harness riding into the
    # subprocess replaces the openai route with an offline stand-in that
    # swallows the very requests this server exists to capture.
    kept = [
        p
        for p in env.get("PYTHONPATH", "").split(os.pathsep)
        if p and Path(p).name != "hermetic"
    ]
    env["PYTHONPATH"] = os.pathsep.join(kept)
    return env


def _run(endpoint, tmp_path, *args, expect_ok=True, language=CLI_LANGUAGE):
    book = tmp_path / BOOK.name
    book.write_bytes(BOOK.read_bytes())
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(book),
            "--api_base",
            endpoint.api_base,
            "--key",
            "sk-capture-test",
            "--model",
            "gpt-4o-mini",
            "--language",
            language,
            "--test",
            "--test_num",
            "3",
            *args,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=_env(),
        timeout=300,
    )
    if expect_ok:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


# Plan mode over the whole partition: no classification requests, so every
# captured body is a translation request and the structured batch path is the
# one under test.
PLAN = ("--plan-classify", "all")
# ... and the legacy path: no plan, no partition, one request per paragraph.
# Said explicitly because `--plan-classify auto` is the default and this
# endpoint answers the capability probe, which turns plan mode on.
LEGACY = ("--plan-classify", "none")
CUSTOM_USER = "Render this into {language} and nothing else: {text}"
CUSTOM_SYSTEM = "You are a stern nineteenth-century schoolmaster."
CUSTOM_STYLE = "Clipped, unsentimental, no adverbs."


def _prompt(tmp_path, **sections):
    path = tmp_path / "prompt.json"
    path.write_text(json.dumps(sections), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------- the default


class TestTheDefaultPrompt:
    def test_the_default_prompt_arrives_filled_in(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *LEGACY)
        sent = endpoint.user_messages()
        assert sent, "the run made no translation request"
        for text in sent:
            # the default template, with the language substituted
            assert "please return only translated content" in text
            assert LANGUAGE in text
            assert "{text}" not in text
            assert "{language}" not in text
            assert "{crlf}" not in text

    def test_a_default_run_has_no_style_and_no_custom_system(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *LEGACY)
        for text in endpoint.user_messages():
            assert ChatGPTAPI.STYLE_HEADING not in text
        # `$OPENAI_API_SYS_MSG` is unset and no --prompt was given, so the
        # system message carries nothing of the operator's.
        assert all(text == "" for text in endpoint.system_messages())


# ------------------------------------------------- the sections, in each mode


@pytest.mark.parametrize("mode", ["legacy", "plan"], ids=["legacy", "plan"])
class TestEachSectionReachesTheRequest:
    """b, c, d and e of the brief, run twice: once per translation mode."""

    def _flags(self, mode):
        return LEGACY if mode == "legacy" else PLAN

    def test_a_custom_user_prompt_replaces_the_default(self, endpoint, tmp_path, mode):
        _run(
            endpoint,
            tmp_path,
            "--prompt",
            _prompt(tmp_path, user=CUSTOM_USER),
            *self._flags(mode),
        )
        sent = endpoint.user_messages()
        assert sent
        for text in sent:
            assert "Render this into" in text
            assert "please return only translated content" not in text
            assert "{text}" not in text

    def test_a_system_section_rides_as_the_system_message(
        self, endpoint, tmp_path, mode
    ):
        _run(
            endpoint,
            tmp_path,
            "--prompt",
            _prompt(tmp_path, user=CUSTOM_USER, system=CUSTOM_SYSTEM),
            *self._flags(mode),
        )
        systems = endpoint.system_messages()
        assert systems
        assert all(CUSTOM_SYSTEM in text for text in systems)

    def test_a_style_section_reaches_the_user_message(self, endpoint, tmp_path, mode):
        _run(
            endpoint,
            tmp_path,
            "--prompt",
            _prompt(tmp_path, user=CUSTOM_USER, style=CUSTOM_STYLE),
            *self._flags(mode),
        )
        sent = endpoint.user_messages()
        assert sent
        for text in sent:
            # appended, because no endpoint has a slot for it
            assert f"{ChatGPTAPI.STYLE_HEADING} {CUSTOM_STYLE}" in text

    def test_all_three_sections_travel_together(self, endpoint, tmp_path, mode):
        _run(
            endpoint,
            tmp_path,
            "--prompt",
            _prompt(
                tmp_path,
                user=CUSTOM_USER,
                system=CUSTOM_SYSTEM,
                style=CUSTOM_STYLE,
            ),
            *self._flags(mode),
        )
        for text in endpoint.user_messages():
            assert "Render this into" in text
            assert CUSTOM_STYLE in text
        assert all(CUSTOM_SYSTEM in t for t in endpoint.system_messages())


# ------------------------------------------------------------- the glossary


# A term that occurs in some of the units this run translates and not in
# others — which is the whole behaviour under test.
GLOSSARY_TERM = "Animal Farm"
GLOSSARY_RENDERING = "动物农场"


@pytest.fixture
def glossary_file(tmp_path):
    path = tmp_path / "pins.txt"
    path.write_text(f"{GLOSSARY_TERM} → {GLOSSARY_RENDERING}\n", encoding="utf-8")
    return str(path)


class TestTheGlossaryBlock:
    @pytest.mark.parametrize("flags", [LEGACY, PLAN], ids=["legacy", "plan"])
    def test_no_glossary_flag_sends_no_block(self, endpoint, tmp_path, flags):
        _run(endpoint, tmp_path, *flags)
        assert endpoint.user_messages()
        assert all("<glossary>" not in t for t in endpoint.user_messages())

    def test_the_block_rides_only_with_a_request_carrying_the_term(
        self, endpoint, tmp_path, glossary_file
    ):
        # The legacy path sends one request per paragraph, so "only where the
        # term occurs" is a claim about which requests carry the block and
        # which do not. Plan mode batches, and is covered below.
        _run(endpoint, tmp_path, "--glossary", glossary_file, *LEGACY)
        sent = endpoint.user_messages()
        assert sent
        carried = [t for t in sent if GLOSSARY_TERM in t]
        assert carried, "no request carried the pinned term"
        assert len(carried) < len(sent), "every unit carried the term; nothing proved"
        for text in sent:
            assert ("<glossary>" in text) == (GLOSSARY_TERM in text)
            if "<glossary>" in text:
                assert GLOSSARY_RENDERING in text

    def test_a_batch_carrying_the_term_carries_the_block(
        self, endpoint, tmp_path, glossary_file
    ):
        _run(endpoint, tmp_path, "--glossary", glossary_file, *PLAN)
        sent = endpoint.user_messages()
        assert sent
        for text in sent:
            assert ("<glossary>" in text) == (GLOSSARY_TERM in text)
        assert any("<glossary>" in t for t in sent)


# ---------------------------------------------------------------- session mode


class TestSessionMode:
    """The prefix is the history, so a section must be in the very first
    request: nothing later can add it without moving the cached prefix."""

    def test_every_section_is_in_the_first_session_request(self, endpoint, tmp_path):
        _run(
            endpoint,
            tmp_path,
            "--use_context",
            "session",
            "--prompt",
            _prompt(
                tmp_path,
                user=CUSTOM_USER,
                system=CUSTOM_SYSTEM,
                style=CUSTOM_STYLE,
            ),
            *PLAN,
        )
        sent = endpoint.translation_requests()
        assert sent
        first = sent[0]
        assert CUSTOM_SYSTEM in first["messages"][0]["content"]
        assert CUSTOM_STYLE in _user_text(first)
        assert "Render this into" in _user_text(first)


# ------------------------------------------------------------ the delimiter rung


class TestTheDelimiterRung:
    """The third place a request is assembled, reached only when the endpoint
    turns out not to honour schemas. It borrows the system message for the
    length of one group, which is how a `--prompt` system message came to be
    dropped there: the openai route handed it the `$OPENAI_API_SYS_MSG`
    attribute, which is empty on a run that only passed `--prompt`.
    """

    # Plan mode, because that is what sends a *group* to one request:
    # `--accumulated_num` on the legacy path joins paragraphs into one string
    # and translates that, which never reaches `translate_list`.
    FLAGS = PLAN

    def test_the_rung_is_actually_reached(self, prose_endpoint, tmp_path):
        _run(prose_endpoint, tmp_path, *self.FLAGS)
        assert any(
            "translations" in text and BATCH_DELIMITER.strip() in text
            for text in prose_endpoint.user_messages()
        ), "no delimiter batch was sent; this class tested nothing"

    def test_the_system_section_survives_the_batch_system_message(
        self, prose_endpoint, tmp_path
    ):
        _run(
            prose_endpoint,
            tmp_path,
            "--prompt",
            _prompt(tmp_path, user=CUSTOM_USER, system=CUSTOM_SYSTEM),
            *self.FLAGS,
        )
        systems = prose_endpoint.system_messages()
        assert systems
        assert all(CUSTOM_SYSTEM in text for text in systems)
        # the batch contract is added to it, not substituted for it
        assert any("segments separated by" in text for text in systems)

    def test_the_style_section_rides_the_batch_too(self, prose_endpoint, tmp_path):
        _run(
            prose_endpoint,
            tmp_path,
            "--prompt",
            _prompt(tmp_path, user=CUSTOM_USER, style=CUSTOM_STYLE),
            *self.FLAGS,
        )
        sent = prose_endpoint.user_messages()
        assert sent
        for text in sent:
            assert f"{ChatGPTAPI.STYLE_HEADING} {CUSTOM_STYLE}" in text


# ----------------------------------------------------------- what the run says


class TestTheRunAnnouncesWhatItAdopted:
    """The notice is printed by the run, not by a helper — a line that only
    exists in a unit test is a line no operator ever sees."""

    def test_a_custom_prompt_is_announced_with_where_the_style_landed(
        self, endpoint, tmp_path
    ):
        proc = _run(
            endpoint,
            tmp_path,
            "--prompt",
            _prompt(
                tmp_path, user=CUSTOM_USER, system=CUSTOM_SYSTEM, style=CUSTOM_STYLE
            ),
            *LEGACY,
        )
        # rich wraps at 80 columns with no tty, so compare on words
        out = " ".join(proc.stdout.split())
        assert (
            "prompt: user+system+style from --prompt "
            "(style appended to the user message on this route)" in out
        ), proc.stdout

    def test_a_run_without_the_flag_says_nothing_about_prompts(
        self, endpoint, tmp_path
    ):
        proc = _run(endpoint, tmp_path, *LEGACY)
        assert "from --prompt" not in proc.stdout
        assert "prompt config" not in proc.stdout


# ------------------------------------------------- the tag / name split


class TestTheLanguageSplit:
    """`--language TAG:NAME` sends the two halves to two different places.

    Read off the wire because that is the only place the split can be
    proved: the name and the tag are one string everywhere upstream of the
    request, and a run that quietly used one for both would look right in
    every log it prints.
    """

    PINNED = "zh-hant:Traditional Chinese"

    def test_the_name_is_what_the_model_is_asked_for(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *PLAN, language=self.PINNED)
        sent = endpoint.user_messages()
        assert sent, "the run made no translation request"
        for text in sent:
            assert "Traditional Chinese" in text
            # the tag is a stamp, not something to say to a model
            assert "zh-hant" not in text

    def test_the_tag_is_what_the_structured_field_is_named(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *PLAN, language=self.PINNED)
        schemas = [
            (body.get("response_format") or {}).get("json_schema") or {}
            for body in endpoint.translation_requests()
        ]
        named = [schema for schema in schemas if schema.get("name")]
        assert named, "no structured request was sent"
        for schema in named:
            assert schema["name"] == batch_field_name("zh-hant")
            assert schema["name"] == "zh_hant_paragraphs"
            item_props = _item_properties(
                schema["schema"],
                schema["schema"]["properties"]["zh_hant_paragraphs"]["items"],
            )
            assert single_field_name("zh-hant") in item_props
            assert "zh_hant_translation" in item_props

    def test_a_bare_language_keeps_the_field_name_it_always_had(
        self, endpoint, tmp_path
    ):
        """The compatibility half of the split: nothing about a command line
        that does not use it may move."""
        _run(endpoint, tmp_path, *PLAN)
        named = [
            (body.get("response_format") or {}).get("json_schema") or {}
            for body in endpoint.translation_requests()
        ]
        named = [schema for schema in named if schema.get("name")]
        assert named, "no structured request was sent"
        for schema in named:
            assert schema["name"] == BATCH_FIELD == "simplified_chinese_paragraphs"


class TestTheSourceLanguageEvidence:
    """`--source_lang` is where the source is stated, and it reaches the
    prompt on this route — not only the routes that put it in a field."""

    def test_the_flag_reaches_the_system_message(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *PLAN, "--source_lang", "english")
        said = endpoint.system_messages()
        assert said, "the run sent no system message"
        for text in said:
            assert "Translate from english" in text

    def test_a_code_is_spelled_out(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *PLAN, "--source_lang", "en")
        said = endpoint.system_messages()
        assert said
        for text in said:
            assert "Translate from english" in text

    def test_auto_states_nothing(self, endpoint, tmp_path):
        _run(endpoint, tmp_path, *PLAN)
        for text in endpoint.system_messages():
            assert "Translate from" not in text
