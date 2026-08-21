import json
import threading
import time
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    LengthFinishReasonError,
    NotFoundError,
    RateLimitError,
)

from book_maker.structured import StructuredJSONFailed
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    StructuredOutputUnsupported,
    batch_field_name,
    batch_translation_model,
    single_field_name,
    single_translation_model,
    single_translation_schema,
)
from book_maker.translator.groq_translator import GroqClient

# Every translator built by `_translator` uses this language, so the structured
# fields are named after it.
LANGUAGE = "Chinese"
SINGLE_FIELD = single_field_name(LANGUAGE)
BATCH_FIELD = batch_field_name(LANGUAGE)


def _single(text):
    """`.parsed` for a single translation in the fixture's language."""
    return SimpleNamespace(**{SINGLE_FIELD: text})


def _batch(paragraphs):
    """`.parsed` for a batch translation in the fixture's language."""
    return SimpleNamespace(**{BATCH_FIELD: paragraphs})


def _completion(content, finish_reason="stop"):
    """Raw `.create` style completion (probe / plain path)."""
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _parsed_completion(parsed=None, refusal=None):
    """`.parse` style completion: exposes `.parsed` and `.refusal`."""
    message = SimpleNamespace(parsed=parsed, refusal=refusal, content=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")]
    )


def _api_error(cls, status_code, message="boom"):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls(message, response=response, body=None)


def _translator(create=None, parse=None, cls=ChatGPTAPI):
    translator = cls.__new__(cls)
    translator.model = "test-model"
    translator.model_list = None
    translator.keys = cycle(["k"])
    translator.temperature = 1.0
    translator.extra_body = {}
    translator.context_flag = False
    translator.context_list = []
    translator.context_translated_list = []
    translator.context_paragraph_limit = 0
    translator.system_content = ""
    translator.prompt_sys_msg = ""
    translator.prompt_template = ChatGPTAPI.DEFAULT_PROMPT
    translator.language = "Chinese"
    translator._api_lock = threading.Lock()
    translator._structured_lock = threading.RLock()
    translator._structured_support = {}
    translator._temperature_unsupported = {}
    translator._structured_failures = {}
    translator._probe_deferred = set()
    translator._rung_refusals = {}
    translator.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create or Mock(return_value=_completion("plain")),
                parse=parse or Mock(return_value=_parsed_completion()),
            ),
        )
    )
    return translator


# --------------------------------------------------------------------------
# Item 1: the probe must grade the response body, not the absence of an error
# --------------------------------------------------------------------------


def test_probe_asks_for_non_json_while_pinning_the_schema():
    create = Mock(return_value=_completion('{"probe":"schema_ok"}'))
    translator = _translator(create=create)

    translator._test_structured_outputs()

    request = create.call_args.kwargs
    # The prompt must fight the schema: a relaying proxy yields plain text.
    assert "json" in request["messages"][0]["content"].lower()
    schema = request["response_format"]["json_schema"]
    assert request["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    # Single-value enum: constrained decoding has exactly one legal output, and
    # that value never appears in the prompt.
    assert schema["schema"]["properties"]["probe"]["enum"] == ["schema_ok"]
    assert "schema_ok" not in request["messages"][0]["content"]


def test_probe_sends_no_temperature_and_no_token_cap():
    create = Mock(return_value=_completion('{"probe":"schema_ok"}'))
    translator = _translator(create=create)

    translator._test_structured_outputs()

    request = create.call_args.kwargs
    assert "temperature" not in request
    # A cap is rejected by o-series/gpt-5 and eaten by reasoning tokens.
    assert "max_tokens" not in request
    assert "max_completion_tokens" not in request


def test_probe_accepts_exact_constrained_value():
    translator = _translator(
        create=Mock(return_value=_completion('{"probe":"schema_ok"}'))
    )

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] == "strict"


def test_probe_records_shape_when_values_are_ignored():
    """Structure honored, `enum` ignored: usable for classification only."""
    translator = _translator(
        create=Mock(return_value=_completion('{"probe":"ignored"}'))
    )

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] == "shape"


@pytest.mark.parametrize("verdict", ["shape", "json", False])
def test_only_a_strict_endpoint_gets_a_schema_for_translation(verdict):
    # our translation schema pins the target language as a *value*; an
    # endpoint that ignores values would drop that pin (#544), which is
    # worse than the delimiter method stating the language in the prompt
    translator = _translator()
    translator._structured_support["test-model"] = verdict

    assert translator._ensure_structured_support() is False


def test_strict_endpoints_get_a_schema_for_translation():
    translator = _translator()
    translator._structured_support["test-model"] = "strict"

    assert translator._ensure_structured_support() is True


@pytest.mark.parametrize(
    "verdict,entry",
    [
        ("strict", "json_schema"),
        ("shape", "json_schema"),
        ("json", "json_object"),
        (False, "prompt"),
    ],
)
def test_the_verdict_only_picks_where_classification_starts(verdict, entry):
    """No verdict refuses classification any more.

    An endpoint that drops `response_format` entirely still answers a schema
    described in the prompt — the lint is what establishes whether it did the
    job. The verdict just saves a request by starting at the right rung.
    """
    translator = _translator()
    translator._structured_support["test-model"] = verdict

    rungs = translator.structured_rungs("classify", {"schema": {}})

    assert rungs[0][0] == entry


def test_shape_endpoint_translates_via_delimiter_but_classifies_structured():
    create = Mock(
        side_effect=[
            _completion('{"probe":"ignored"}'),  # shape verdict
            _completion("一\n\n@@\n\n二"),  # delimiter translation
            _completion('{"p.header": {"verdict": "skip"}}'),  # classification
        ]
    )
    translator = _translator(create=create)

    assert translator.translate_list(["one", "two"]) == ["一", "二"]
    assert translator.structured_json("classify", {"schema": {}}) == {
        "p.header": {"verdict": "skip"}
    }

    translate_call = create.call_args_list[1].kwargs
    classify_call = create.call_args_list[2].kwargs
    assert "response_format" not in translate_call
    assert classify_call["response_format"]["type"] == "json_schema"


@pytest.mark.parametrize(
    "content",
    [
        "ignored",  # proxy dropped response_format entirely
        '```json\n{"probe":"schema_ok"}\n```',  # fences: json mode is not on
        "",  # empty body
    ],
)
def test_probe_rejects_servers_that_produce_no_json(content):
    translator = _translator(create=Mock(return_value=_completion(content)))

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] is False


@pytest.mark.parametrize(
    "content",
    [
        '{"probe":"schema_ok","extra":1}',  # additionalProperties not enforced
        '{"answer":"ignored"}',  # json mode only, schema ignored
        '{"probe":42}',  # wrong type
        "[1,2,3]",  # not an object
    ],
)
def test_probe_grades_json_mode_apart_from_prose(content):
    """Right JSON, wrong keys: the schema was dropped but json mode is on.

    Worth its own verdict — such an endpoint should start classification at
    the json_object rung instead of being lumped in with prose-only ones.
    """
    translator = _translator(create=Mock(return_value=_completion(content)))

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] == "json"


def test_probe_rejects_truncated_probe_response():
    translator = _translator(
        create=Mock(
            return_value=_completion('{"probe":"schema', finish_reason="length")
        )
    )

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] is False


# --------------------------------------------------------------------------
# Item 5: only capability answers may be swallowed
# --------------------------------------------------------------------------


def test_probe_treats_bad_request_as_no_schema_support():
    translator = _translator(create=Mock(side_effect=_api_error(BadRequestError, 400)))

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] is False


@pytest.mark.parametrize(
    "error",
    [
        _api_error(AuthenticationError, 401),
        _api_error(NotFoundError, 404),
    ],
)
def test_probe_reraises_permanent_endpoint_errors(error):
    """A bad key or a wrong model name must not read as 'no schema support'."""
    translator = _translator(create=Mock(side_effect=error))

    with pytest.raises(type(error)):
        translator._test_structured_outputs()

    assert translator._structured_support == {}


@pytest.mark.parametrize(
    "error",
    [
        APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        ),
        APITimeoutError(
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        ),
        _api_error(RateLimitError, 429),
    ],
)
def test_probe_defers_on_a_router_outage_instead_of_ending_the_run(error):
    """Gateways come back. A blip must not raise and must not cache a verdict."""
    translator = _translator(create=Mock(side_effect=error))

    assert translator._ensure_structured_support() is False
    assert translator._structured_support == {}  # nothing learned, nothing cached


def test_deferred_probe_is_retried_on_the_next_paragraph():
    outage = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    )
    create = Mock(side_effect=[outage, _completion('{"probe":"schema_ok"}')])
    translator = _translator(create=create)

    assert translator._ensure_structured_support() is False
    assert translator._ensure_structured_support() is True
    assert create.call_count == 2


def test_translate_list_survives_a_probe_outage():
    """`translate_list` probes outside any tenacity wrapper, so a blip there
    used to take down the run with zero retries."""
    outage = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    )
    translator = _translator(create=Mock(side_effect=outage))
    translator._do_batch_translate = Mock(return_value=["t:a", "t:b"])

    assert translator.translate_list(["a", "b"]) == ["t:a", "t:b"]


def test_probe_falls_back_on_ambiguous_server_error():
    """A 500 from a quirky local server must degrade, not crash the run."""

    class WeirdServerError(Exception):
        pass

    translator = _translator(create=Mock(side_effect=WeirdServerError("boom")))

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] is False


# --------------------------------------------------------------------------
# Item 1 (cont.): cache is per model and probed once under the lock
# --------------------------------------------------------------------------


def test_support_is_cached_per_model():
    create = Mock(return_value=_completion('{"probe":"schema_ok"}'))
    translator = _translator(create=create)

    translator._ensure_structured_support()
    translator._ensure_structured_support()
    assert create.call_count == 1

    translator.model = "other-model"
    translator._ensure_structured_support()
    assert create.call_count == 2
    assert set(translator._structured_support) == {"test-model", "other-model"}


def test_concurrent_workers_probe_a_model_only_once():
    def slow_create(**kwargs):
        time.sleep(0.05)  # wide enough for unsynchronized workers to pile in
        return _completion('{"probe":"schema_ok"}')

    create = Mock(side_effect=slow_create)
    translator = _translator(create=create)

    threads = [
        threading.Thread(target=translator._ensure_structured_support) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert create.call_count == 1


# --------------------------------------------------------------------------
# Item 2 + 7: parse-based single translation must never leak broken JSON
# --------------------------------------------------------------------------


def test_single_translation_returns_parsed_field():
    parse = Mock(return_value=_parsed_completion(parsed=_single("你好")))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"

    assert translator.get_translation("hello") == "你好"
    assert parse.call_args.kwargs["response_format"] is single_translation_model(
        LANGUAGE
    )


def test_truncated_response_raises_instead_of_leaking_json_fragment():
    error = LengthFinishReasonError(
        completion=SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"translated":"半'),
                    finish_reason="length",
                )
            ],
        )
    )
    translator = _translator(parse=Mock(side_effect=error))
    translator._structured_support["test-model"] = "strict"

    with pytest.raises(LengthFinishReasonError):
        translator._structured_single_translation("hello")


def test_truncation_retranslates_plainly_instead_of_ending_the_run():
    """No partial JSON in the book, but no dead run either: the plain path has
    no JSON to truncate, so one long paragraph goes through it."""
    error = LengthFinishReasonError(
        completion=SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"translated":"半'),
                    finish_reason="length",
                )
            ],
        )
    )
    create = Mock(return_value=_completion("完整的翻譯"))
    translator = _translator(create=create, parse=Mock(side_effect=error))
    translator._structured_support["test-model"] = "strict"

    assert translator.get_translation("hello") == "完整的翻譯"
    assert create.call_count == 1
    # Truncation is a token-budget accident, not a capability answer.
    assert translator._structured_support["test-model"] == "strict"


def test_refusal_raises_loudly():
    translator = _translator(
        parse=Mock(return_value=_parsed_completion(refusal="nope"))
    )
    translator._structured_support["test-model"] = "strict"

    with pytest.raises(ValueError, match="refused"):
        translator._structured_single_translation("hello")


# --------------------------------------------------------------------------
# Item 1 (cont.): demote on the first real structured failure
# --------------------------------------------------------------------------


def test_single_path_demotes_and_retries_plainly_when_schema_is_ignored():
    parse = Mock(side_effect=StructuredOutputUnsupported("server ignored schema"))
    create = Mock(return_value=_completion("plain translation"))
    translator = _translator(create=create, parse=parse)
    translator._structured_support["test-model"] = "strict"

    # First failure falls back for this paragraph but keeps structured mode on:
    # one garbled proxy answer must not cost a whole book its schema support.
    assert translator.get_translation("hello") == "plain translation"
    assert translator._structured_support["test-model"] == "strict"

    assert translator.get_translation("hello") == "plain translation"
    assert translator._structured_support["test-model"] is False

    assert parse.call_count == 2
    assert create.call_count == 2


def test_a_working_structured_call_clears_the_failure_streak():
    parse = Mock(
        side_effect=[
            StructuredOutputUnsupported("blip"),
            _parsed_completion(parsed=_single("你好")),
            StructuredOutputUnsupported("blip"),
        ]
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"

    translator.get_translation("a")  # streak 1
    assert translator.get_translation("b") == "你好"  # streak reset
    translator.get_translation("c")  # streak 1 again, not 2

    assert translator._structured_support["test-model"] == "strict"


def test_batch_path_demotes_without_burning_retries():
    parse = Mock(side_effect=StructuredOutputUnsupported("server ignored schema"))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    assert translator._do_structured_batch_translate(["a", "b"]) == ["t:a", "t:b"]
    assert translator._do_structured_batch_translate(["a", "b"]) == ["t:a", "t:b"]

    assert parse.call_count == 2  # one attempt each, not 3 tenacity attempts
    assert translator._structured_support["test-model"] is False


def test_batch_length_mismatch_is_retried_then_falls_back_one_by_one():
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["only one"])))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    result = translator._do_structured_batch_translate(["a", "b"])

    assert result == ["t:a", "t:b"]
    assert parse.call_count == 3  # a model error, not a capability answer
    # A count mismatch says nothing about schema support.
    assert translator._structured_support["test-model"] == "strict"


def test_batch_empty_slot_for_nonempty_input_is_retried_then_falls_back():
    # Count is not alignment: a model that merges two verse lines into one
    # slot keeps the count by padding another slot with "". The strict path
    # must treat that pad as the model error it is, not accept the window.
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["5a+5b merged", ""])))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    result = translator._do_structured_batch_translate(["5a", "5b"])

    assert result == ["t:5a", "t:5b"]
    assert parse.call_count == 3  # a model error, not a capability answer
    assert translator._structured_support["test-model"] == "strict"


def test_batch_empty_output_for_empty_input_is_accepted():
    # the complement: only *non-empty* inputs may not come back empty —
    # an empty slot mirroring an empty input is well-formed output
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", ""])))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"

    assert translator._do_structured_batch_translate(["a", "  "]) == ["一", ""]
    assert parse.call_count == 1


def test_batch_success_returns_paragraphs():
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", "二"])))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"

    assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
    request = parse.call_args.kwargs
    assert request["response_format"] is batch_translation_model(LANGUAGE)
    assert json.loads  # payload is built by the SDK, not hand-rolled


# --------------------------------------------------------------------------
# Subclasses that do not route through openai_client must not be probed
# --------------------------------------------------------------------------


def test_groq_never_probes_for_structured_outputs():
    create = Mock(return_value=_completion("plain"))
    translator = _translator(create=create, cls=GroqClient)

    translator._ensure_structured_support()

    assert GroqClient.SUPPORTS_STRUCTURED_OUTPUTS is False
    assert translator._structured_support["test-model"] is False
    assert create.call_count == 0


# --------------------------------------------------------------------------
# OrcaRouter: named OpenAI-compatible gateway route
# --------------------------------------------------------------------------


def test_orcarouter_uses_orca_endpoint_and_default_model():
    from book_maker.translator.orcarouter_translator import OrcaRouterTranslator

    translator = OrcaRouterTranslator("sk-orca-test", "Chinese")
    assert translator.api_base == "https://api.orcarouter.ai/v1"
    assert translator.openai_client.base_url == "https://api.orcarouter.ai/v1/"
    translator.rotate_model()
    assert translator.model == "orcarouter/auto"


def test_orcarouter_honors_custom_api_base():
    from book_maker.translator.orcarouter_translator import OrcaRouterTranslator

    translator = OrcaRouterTranslator("sk-orca-test", "Chinese", api_base="http://proxy.local/v1")
    assert translator.api_base == "http://proxy.local/v1"
    assert translator.openai_client.base_url == "http://proxy.local/v1/"


# --------------------------------------------------------------------------
# Item 6: temperature must not be forced onto models that only accept their
# default, and a temperature 400 must not be blamed on the JSON schema
# --------------------------------------------------------------------------


def test_default_temperature_is_not_sent():
    """1.0 is the API default, so sending it is a no-op — except on models that
    reject any explicit temperature."""
    parse = Mock(return_value=_parsed_completion(parsed=_single("你好")))
    translator = _translator(parse=parse)
    translator.temperature = 1.0
    translator._structured_support["test-model"] = "strict"

    translator._structured_single_translation("hello")

    assert "temperature" not in parse.call_args.kwargs


def test_explicit_temperature_is_sent():
    parse = Mock(return_value=_parsed_completion(parsed=_single("你好")))
    translator = _translator(parse=parse)
    translator.temperature = 0.1
    translator._structured_support["test-model"] = "strict"

    translator._structured_single_translation("hello")

    assert parse.call_args.kwargs["temperature"] == 0.1


def test_plain_path_also_honors_the_default_temperature_rule():
    create = Mock(return_value=_completion("plain"))
    translator = _translator(create=create)
    translator.temperature = 1.0

    translator.create_chat_completion("hello")

    assert "temperature" not in create.call_args.kwargs


def test_temperature_rejection_retries_once_without_it_and_is_cached():
    ok = _parsed_completion(parsed=_single("你好"))
    parse = Mock(
        side_effect=[
            _api_error(
                BadRequestError,
                400,
                "Unsupported value: 'temperature' does not support 0.1 with this model",
            ),
            ok,
            ok,
        ]
    )
    translator = _translator(parse=parse)
    translator.temperature = 0.1
    translator._structured_support["test-model"] = "strict"

    assert translator._structured_single_translation("hello") == "你好"
    assert parse.call_count == 2
    assert "temperature" not in parse.call_args.kwargs
    assert translator._temperature_unsupported["test-model"] is True

    # Cached: the second translation never sends it again.
    translator._structured_single_translation("world")
    assert parse.call_count == 3
    assert "temperature" not in parse.call_args.kwargs


def test_temperature_rejection_does_not_demote_structured_outputs():
    ok = _parsed_completion(parsed=_single("你好"))
    parse = Mock(
        side_effect=[
            _api_error(BadRequestError, 400, "temperature is not supported"),
            ok,
        ]
    )
    translator = _translator(parse=parse)
    translator.temperature = 0.1
    translator._structured_support["test-model"] = "strict"

    translator._structured_single_translation("hello")

    assert translator._structured_support["test-model"] == "strict"


def test_schema_rejection_is_still_a_capability_answer():
    parse = Mock(
        side_effect=_api_error(
            BadRequestError, 400, "response_format of type json_schema is not supported"
        )
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"

    with pytest.raises(StructuredOutputUnsupported):
        translator._structured_single_translation("hello")


def test_unrelated_bad_request_is_not_blamed_on_the_schema():
    parse = Mock(
        side_effect=_api_error(BadRequestError, 400, "context length exceeded")
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = "strict"

    with pytest.raises(BadRequestError):
        translator._structured_single_translation("hello")

    # Still enabled: the model never said anything about schemas.
    assert translator._structured_support["test-model"] == "strict"


def test_batch_path_applies_the_same_temperature_rule():
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", "二"])))
    translator = _translator(parse=parse)
    translator.temperature = 1.0
    translator._structured_support["test-model"] = "strict"

    translator._do_structured_batch_translate(["a", "b"])

    assert "temperature" not in parse.call_args.kwargs


def test_temperature_support_is_tracked_per_model():
    translator = _translator()
    translator.temperature = 0.1
    translator._temperature_unsupported["test-model"] = True

    assert translator._sampling_kwargs() == {}
    assert translator._sampling_kwargs("other-model") == {"temperature": 0.1}


def test_batch_api_body_omits_default_temperature():
    translator = _translator()
    translator.temperature = 1.0
    translator.batch_model = "batch-model"
    translator._structured_support["batch-model"] = False
    translator.create_batch_context_messages = Mock(return_value=[])
    translator.custom_id = Mock(return_value="id-1")

    body = translator.make_batch_request(0, "hello")["body"]

    assert "temperature" not in body


# --------------------------------------------------------------------------
# Item 2, Batch API flavour: results are read by a later process that never
# probed, so the payload itself must decide
# --------------------------------------------------------------------------


def test_batch_choice_unwraps_structured_content_without_cached_state():
    choice = {
        "finish_reason": "stop",
        "message": {"content": json.dumps({SINGLE_FIELD: "你好"})},
    }

    assert ChatGPTAPI._read_batch_choice(choice, "id-1", LANGUAGE) == "你好"


def test_batch_choice_passes_through_plain_content():
    choice = {"finish_reason": "stop", "message": {"content": "plain text"}}

    assert ChatGPTAPI._read_batch_choice(choice, "id-1", LANGUAGE) == "plain text"


def test_batch_choice_rejects_truncated_result():
    choice = {
        "finish_reason": "length",
        "message": {"content": '{"translated":"半'},
    }

    with pytest.raises(ValueError, match="truncated"):
        ChatGPTAPI._read_batch_choice(choice, "id-1", LANGUAGE)


def test_batch_choice_rejects_refusal():
    choice = {"finish_reason": "stop", "message": {"refusal": "nope", "content": None}}

    with pytest.raises(ValueError, match="refused"):
        ChatGPTAPI._read_batch_choice(choice, "id-1", LANGUAGE)


def test_batch_choice_rejects_structured_object_from_another_language():
    """A result file written under a different --language must not be pasted
    into the book as raw JSON."""
    choice = {
        "finish_reason": "stop",
        "message": {"content": json.dumps({"german_translation": "hallo"})},
    }

    with pytest.raises(ValueError, match=SINGLE_FIELD):
        ChatGPTAPI._read_batch_choice(choice, "id-1", LANGUAGE)


# --------------------------------------------------------------------------
# The schema itself carries the target language: field name, description and
# schema name. Without it the last thing the model reads before decoding says
# only what shape to emit, never which language.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "single", "batch"),
    [
        ("Chinese", "chinese_translation", "chinese_paragraphs"),
        (
            "simplified chinese",
            "simplified_chinese_translation",
            "simplified_chinese_paragraphs",
        ),
        (
            "Simplified Chinese",
            "simplified_chinese_translation",
            "simplified_chinese_paragraphs",
        ),
        # cli.py maps codes through LANGUAGES, but a raw code must still slug.
        ("zh-hans", "zh_hans_translation", "zh_hans_paragraphs"),
        ("", "translated", "paragraphs"),  # nothing usable: language-free names
        (None, "translated", "paragraphs"),
    ],
)
def test_field_names_follow_the_target_language(language, single, batch):
    assert single_field_name(language) == single
    assert batch_field_name(language) == batch


def test_models_expose_the_language_named_field_and_say_so():
    model = single_translation_model("simplified chinese")
    schema = model.model_json_schema()
    field = schema["properties"]["simplified_chinese_translation"]

    assert schema["required"] == ["simplified_chinese_translation"]
    assert "simplified chinese" in field["description"]
    assert model.model_config["extra"] == "forbid"

    batch = batch_translation_model("simplified chinese").model_json_schema()
    assert "simplified chinese" in (
        batch["properties"]["simplified_chinese_paragraphs"]["description"]
    )


def test_models_are_cached_per_language():
    """One `create_model` per language, not one per paragraph."""
    assert single_translation_model("Chinese") is single_translation_model("Chinese")
    assert single_translation_model("Chinese") is not single_translation_model("German")


def test_hand_built_batch_schema_matches_the_sdk_model():
    """The Batch API body is hand-rolled; it must not drift from the model."""
    for language in ("Chinese", "simplified chinese", ""):
        schema = single_translation_schema(language)
        field = single_field_name(language)

        assert schema["strict"] is True
        # The SDK sends the model's class name as the schema name, so both
        # transports must land on the same one.
        assert schema["name"] == field
        assert single_translation_model(language).__name__ == field
        assert batch_translation_model(language).__name__ == batch_field_name(language)
        assert schema["schema"]["required"] == [field]
        assert schema["schema"]["additionalProperties"] is False
        assert list(schema["schema"]["properties"]) == [field]
        assert (
            schema["schema"]["properties"][field]["description"]
            == single_translation_model(language).model_json_schema()["properties"][
                field
            ]["description"]
        )


def test_batch_request_pins_the_language_schema():
    translator = _translator()
    translator.batch_model = "test-model"
    translator.custom_id = lambda index: f"id-{index}"
    translator.context_flag = False
    # the probe store holds verdicts now, and translation requires "strict"
    translator._structured_support["test-model"] = "strict"

    body = translator.make_batch_request(0, "hello")["body"]

    assert body["response_format"]["json_schema"] == single_translation_schema(LANGUAGE)


def test_structured_batch_prompt_ends_on_the_target_language():
    """Recency matters: a shape-only tail leaves `{language}` buried behind the
    source JSON blob."""
    translator = _translator()

    content = translator._create_structured_batch_messages(["a", "b"])[-1]["content"]

    assert content.rstrip().endswith(f"each written in {LANGUAGE}.")
    assert f"'{BATCH_FIELD}'" in content
    assert "EXACTLY 2" in content


# --------------------------------------------------------------------------
# Unrelated probe, kept from before
# --------------------------------------------------------------------------


def test_model_validation_probe_uses_model_defaults():
    create = Mock(return_value=_completion("ok"))
    translator = _translator(create=create)

    translator._validate_model_with_test("test-model", "Test")

    request = create.call_args.kwargs
    assert request["model"] == "test-model"
    assert request["max_tokens"] == 10
    assert "temperature" not in request


# --------------------------------------------------------------------------
# structured_json: one-off schema'd request for plan classification
# --------------------------------------------------------------------------


def test_structured_json_returns_parsed_object():
    create = Mock(
        side_effect=[
            _completion('{"probe":"schema_ok"}'),
            _completion('{"verdicts":[{"signature":"p.x","verdict":"skip"}]}'),
        ]
    )
    translator = _translator(create=create)

    result = translator.structured_json(
        "classify this", {"name": "s", "strict": True, "schema": {}}
    )

    assert result == {"verdicts": [{"signature": "p.x", "verdict": "skip"}]}
    request = create.call_args.kwargs
    assert request["response_format"]["type"] == "json_schema"
    assert request["messages"][0]["content"] == "classify this"


def test_structured_json_raises_when_no_rung_yields_json():
    # probe says unsupported, and the prompt rung answers prose anyway: the
    # ladder is exhausted, so the caller is told what each rung did rather
    # than handed a None to guess about
    create = Mock(return_value=_completion("not json at all"))
    translator = _translator(create=create)

    with pytest.raises(StructuredJSONFailed, match="no JSON object"):
        translator.structured_json("classify", {"schema": {}})
    assert create.call_count == 2  # probe + the prompt rung


def test_structured_json_targets_the_requested_model():
    create = Mock(
        side_effect=[
            _completion('{"probe":"schema_ok"}'),
            _completion('{"verdicts":[]}'),
        ]
    )
    translator = _translator(create=create)

    translator.structured_json("classify", {"schema": {}}, model="clf-model")

    # both the probe and the real request must hit the chosen model,
    # not the translating one
    probed = create.call_args_list[0].kwargs["model"]
    asked = create.call_args_list[1].kwargs["model"]
    assert probed == asked == "clf-model"


# --------------------------------------------------------------------------
# Delimiter batches must feed --use_context one pair per paragraph, never the
# joined blob: the blob would put "@@" markers into every later prompt and
# collapse three paragraphs of context into one unusable entry.
# --------------------------------------------------------------------------


def _delimiter_translator(joined_translation):
    translator = _translator(create=Mock(return_value=_completion(joined_translation)))
    # no structured support -> translate_list takes the delimiter path
    translator._structured_support["test-model"] = False
    translator.context_flag = True
    translator.context_paragraph_limit = 5
    return translator


def test_delimiter_batch_saves_context_per_paragraph():
    translator = _delimiter_translator("一\n\n@@\n\n二")

    assert translator.translate_list(["one", "two"]) == ["一", "二"]
    assert translator.context_list == ["one", "two"]
    assert translator.context_translated_list == ["一", "二"]


def test_delimiter_batch_never_stores_the_joined_blob():
    translator = _delimiter_translator("一\n\n@@\n\n二")

    translator.translate_list(["one", "two"])

    assert not any("@@" in c for c in translator.context_list)
    assert not any("@@" in c for c in translator.context_translated_list)


def test_delimiter_batch_context_survives_the_one_by_one_fallback():
    # a short response forces the per-item fallback; each single translate
    # saves its own context, and the blob must still not be stored
    create = Mock(
        side_effect=[_completion("只有一段"), _completion("一"), _completion("二")]
    )
    translator = _delimiter_translator("unused")
    translator.openai_client.chat.completions.create = create

    assert translator.translate_list(["one", "two"]) == ["一", "二"]
    assert translator.context_list == ["one", "two"]
    assert translator.context_translated_list == ["一", "二"]


def test_context_flag_is_restored_when_the_batch_call_raises():
    create = Mock(side_effect=RuntimeError("boom"))
    translator = _delimiter_translator("unused")
    translator.openai_client.chat.completions.create = create

    with pytest.raises(Exception):
        translator.translate_list(["one", "two"])
    assert translator.context_flag is True


# --------------------------------------------------------------------------
# structured_json ladder: json_schema -> json_object -> plain completion.
# A 反代 that drops response_format must still be able to classify.
# --------------------------------------------------------------------------


def test_json_mode_rung_describes_the_schema_in_the_prompt():
    create = Mock(
        side_effect=[
            _completion('{"answer":"ignored"}'),  # probe: json mode, no schema
            _completion('{"p.header": {"verdict": "skip"}}'),
        ]
    )
    translator = _translator(create=create)

    result = translator.structured_json(
        "classify", {"schema": {"type": "object", "properties": {"p.header": {}}}}
    )

    assert result == {"p.header": {"verdict": "skip"}}
    rung = create.call_args_list[1].kwargs
    assert rung["response_format"] == {"type": "json_object"}
    # the schema travels in the prompt as an example instance; serializing the
    # schema itself is what made models echo the envelope back
    content = rung["messages"][0]["content"]
    assert "Shaped like this example" in content
    assert '"type": "object"' not in content


def test_ladder_falls_to_a_plain_completion_when_json_object_is_rejected():
    create = Mock(
        side_effect=[
            _completion('{"answer":"ignored"}'),  # probe: json mode only
            _api_error(BadRequestError, 400, "response_format is not supported"),
            _completion('```json\n{"p.header": {"verdict": "skip"}}\n```'),
        ]
    )
    translator = _translator(create=create)

    assert translator.structured_json("classify", {"schema": {}}) == {
        "p.header": {"verdict": "skip"}
    }
    bottom = create.call_args_list[2].kwargs
    assert "response_format" not in bottom
    assert "no markdown fences" in bottom["messages"][0]["content"]


def test_any_refusal_descends_a_rung_not_just_a_response_format_one():
    # matching on the words of an error message was never sound: a proxy that
    # refuses json mode with its own wording must still fall through
    create = Mock(
        side_effect=[
            _completion('{"answer":"ignored"}'),  # probe: json mode only
            _api_error(BadRequestError, 400, "unsupported parameter"),
            _completion('{"p.header": {"verdict": "skip"}}'),
        ]
    )
    translator = _translator(create=create)

    assert translator.structured_json("classify", {"schema": {}}) == {
        "p.header": {"verdict": "skip"}
    }


def test_a_rung_refused_twice_stops_being_offered():
    # not on the first refusal: a 400 is as often about the page as about the
    # shape, and the caller's retry with a smaller page needs the rung intact
    create = Mock(
        side_effect=[
            _completion('{"answer":"ignored"}'),  # probe: json mode only
            _api_error(BadRequestError, 400, "unsupported parameter"),
            _completion('{"a": 1}'),
            _api_error(BadRequestError, 400, "unsupported parameter"),
            _completion('{"b": 2}'),
            _completion('{"c": 3}'),
        ]
    )
    translator = _translator(create=create)

    assert translator.structured_json("q", {"schema": {}}) == {"a": 1}
    assert translator.structured_json("q", {"schema": {}}) == {"b": 2}
    assert translator.structured_json("q", {"schema": {}}) == {"c": 3}
    # probe + (refused, prompt) + (refused, prompt) + prompt only = 6
    assert create.call_count == 6


def test_a_dead_endpoint_is_reported_not_walked_down():
    # auth and quota say nothing about request shape; descending cannot fix
    # them and would pay for the same failure once per rung
    create = Mock(
        side_effect=[
            _completion('{"probe":"schema_ok"}'),
            _api_error(AuthenticationError, 401, "invalid api key"),
        ]
    )
    translator = _translator(create=create)

    with pytest.raises(AuthenticationError):
        translator.structured_json("classify", {"schema": {}})
    assert create.call_count == 2


def test_an_exhausted_ladder_reports_every_rungs_error():
    # nothing is swallowed: a 400 that no rung survives still reaches the user
    create = Mock(
        side_effect=[
            _completion('{"probe":"schema_ok"}'),
            _api_error(BadRequestError, 400, "context length exceeded"),
            _api_error(BadRequestError, 400, "context length exceeded"),
            _api_error(BadRequestError, 400, "context length exceeded"),
        ]
    )
    translator = _translator(create=create)

    with pytest.raises(StructuredJSONFailed, match="context length exceeded"):
        translator.structured_json("classify", {"schema": {}})


def test_a_strict_endpoint_that_answers_prose_falls_through():
    # the probe passing is not a promise about the next request: a page schema
    # is far bigger than the one-key probe schema, and used to abort the run
    create = Mock(
        side_effect=[
            _completion('{"probe":"schema_ok"}'),  # strict verdict
            _completion("I'm afraid I can't help with that."),
            _completion('{"p.header": {"verdict": "skip"}}'),
        ]
    )
    translator = _translator(create=create)

    assert translator.structured_json("classify", {"schema": {}}) == {
        "p.header": {"verdict": "skip"}
    }


@pytest.mark.parametrize(
    "reply,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here you go:\n{"a": 1}\nHope that helps.', {"a": 1}),
        ('{"a": "brace } inside a string"}', {"a": "brace } inside a string"}),
        ("not json at all", None),
        ("", None),
    ],
)
def test_json_extraction_survives_fences_and_prose(reply, expected):
    assert ChatGPTAPI._extract_json_object(reply) == expected
