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

from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    StructuredOutputUnsupported,
)
from book_maker.translator.groq_translator import GroqClient


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

    assert translator._structured_support["test-model"] is True


def test_probe_accepts_correct_shape_with_wrong_value():
    """Servers that honor structure but ignore `enum` are still usable."""
    translator = _translator(
        create=Mock(return_value=_completion('{"probe":"ignored"}'))
    )

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] is True


@pytest.mark.parametrize(
    "content",
    [
        "ignored",  # proxy dropped response_format entirely
        '```json\n{"probe":"schema_ok"}\n```',  # fenced, not raw JSON
        '{"probe":"schema_ok","extra":1}',  # additionalProperties not enforced
        '{"answer":"ignored"}',  # json mode only, schema ignored
        '{"probe":42}',  # wrong type
        "[1,2,3]",  # not an object
        "",  # empty body
    ],
)
def test_probe_rejects_servers_that_do_not_apply_the_schema(content):
    translator = _translator(create=Mock(return_value=_completion(content)))

    translator._test_structured_outputs()

    assert translator._structured_support["test-model"] is False


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
    parse = Mock(
        return_value=_parsed_completion(parsed=SimpleNamespace(translated="你好"))
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True

    assert translator.get_translation("hello") == "你好"
    assert parse.call_args.kwargs["response_format"].__name__ == "SingleTranslation"


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
    translator._structured_support["test-model"] = True

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
    translator._structured_support["test-model"] = True

    assert translator.get_translation("hello") == "完整的翻譯"
    assert create.call_count == 1
    # Truncation is a token-budget accident, not a capability answer.
    assert translator._structured_support["test-model"] is True


def test_refusal_raises_loudly():
    translator = _translator(
        parse=Mock(return_value=_parsed_completion(refusal="nope"))
    )
    translator._structured_support["test-model"] = True

    with pytest.raises(ValueError, match="refused"):
        translator._structured_single_translation("hello")


# --------------------------------------------------------------------------
# Item 1 (cont.): demote on the first real structured failure
# --------------------------------------------------------------------------


def test_single_path_demotes_and_retries_plainly_when_schema_is_ignored():
    parse = Mock(side_effect=StructuredOutputUnsupported("server ignored schema"))
    create = Mock(return_value=_completion("plain translation"))
    translator = _translator(create=create, parse=parse)
    translator._structured_support["test-model"] = True

    # First failure falls back for this paragraph but keeps structured mode on:
    # one garbled proxy answer must not cost a whole book its schema support.
    assert translator.get_translation("hello") == "plain translation"
    assert translator._structured_support["test-model"] is True

    assert translator.get_translation("hello") == "plain translation"
    assert translator._structured_support["test-model"] is False

    assert parse.call_count == 2
    assert create.call_count == 2


def test_a_working_structured_call_clears_the_failure_streak():
    parse = Mock(
        side_effect=[
            StructuredOutputUnsupported("blip"),
            _parsed_completion(parsed=SimpleNamespace(translated="你好")),
            StructuredOutputUnsupported("blip"),
        ]
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True

    translator.get_translation("a")  # streak 1
    assert translator.get_translation("b") == "你好"  # streak reset
    translator.get_translation("c")  # streak 1 again, not 2

    assert translator._structured_support["test-model"] is True


def test_batch_path_demotes_without_burning_retries():
    parse = Mock(side_effect=StructuredOutputUnsupported("server ignored schema"))
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    assert translator._do_structured_batch_translate(["a", "b"]) == ["t:a", "t:b"]
    assert translator._do_structured_batch_translate(["a", "b"]) == ["t:a", "t:b"]

    assert parse.call_count == 2  # one attempt each, not 3 tenacity attempts
    assert translator._structured_support["test-model"] is False


def test_batch_length_mismatch_is_retried_then_falls_back_one_by_one():
    parse = Mock(
        return_value=_parsed_completion(parsed=SimpleNamespace(paragraphs=["only one"]))
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    result = translator._do_structured_batch_translate(["a", "b"])

    assert result == ["t:a", "t:b"]
    assert parse.call_count == 3  # a model error, not a capability answer
    # A count mismatch says nothing about schema support.
    assert translator._structured_support["test-model"] is True


def test_batch_success_returns_paragraphs():
    parse = Mock(
        return_value=_parsed_completion(parsed=SimpleNamespace(paragraphs=["一", "二"]))
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True

    assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
    request = parse.call_args.kwargs
    assert request["response_format"].__name__ == "BatchTranslation"
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
# Item 6: temperature must not be forced onto models that only accept their
# default, and a temperature 400 must not be blamed on the JSON schema
# --------------------------------------------------------------------------


def test_default_temperature_is_not_sent():
    """1.0 is the API default, so sending it is a no-op — except on models that
    reject any explicit temperature."""
    parse = Mock(
        return_value=_parsed_completion(parsed=SimpleNamespace(translated="你好"))
    )
    translator = _translator(parse=parse)
    translator.temperature = 1.0
    translator._structured_support["test-model"] = True

    translator._structured_single_translation("hello")

    assert "temperature" not in parse.call_args.kwargs


def test_explicit_temperature_is_sent():
    parse = Mock(
        return_value=_parsed_completion(parsed=SimpleNamespace(translated="你好"))
    )
    translator = _translator(parse=parse)
    translator.temperature = 0.1
    translator._structured_support["test-model"] = True

    translator._structured_single_translation("hello")

    assert parse.call_args.kwargs["temperature"] == 0.1


def test_plain_path_also_honors_the_default_temperature_rule():
    create = Mock(return_value=_completion("plain"))
    translator = _translator(create=create)
    translator.temperature = 1.0

    translator.create_chat_completion("hello")

    assert "temperature" not in create.call_args.kwargs


def test_temperature_rejection_retries_once_without_it_and_is_cached():
    ok = _parsed_completion(parsed=SimpleNamespace(translated="你好"))
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
    translator._structured_support["test-model"] = True

    assert translator._structured_single_translation("hello") == "你好"
    assert parse.call_count == 2
    assert "temperature" not in parse.call_args.kwargs
    assert translator._temperature_unsupported["test-model"] is True

    # Cached: the second translation never sends it again.
    translator._structured_single_translation("world")
    assert parse.call_count == 3
    assert "temperature" not in parse.call_args.kwargs


def test_temperature_rejection_does_not_demote_structured_outputs():
    ok = _parsed_completion(parsed=SimpleNamespace(translated="你好"))
    parse = Mock(
        side_effect=[
            _api_error(BadRequestError, 400, "temperature is not supported"),
            ok,
        ]
    )
    translator = _translator(parse=parse)
    translator.temperature = 0.1
    translator._structured_support["test-model"] = True

    translator._structured_single_translation("hello")

    assert translator._structured_support["test-model"] is True


def test_schema_rejection_is_still_a_capability_answer():
    parse = Mock(
        side_effect=_api_error(
            BadRequestError, 400, "response_format of type json_schema is not supported"
        )
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True

    with pytest.raises(StructuredOutputUnsupported):
        translator._structured_single_translation("hello")


def test_unrelated_bad_request_is_not_blamed_on_the_schema():
    parse = Mock(
        side_effect=_api_error(BadRequestError, 400, "context length exceeded")
    )
    translator = _translator(parse=parse)
    translator._structured_support["test-model"] = True

    with pytest.raises(BadRequestError):
        translator._structured_single_translation("hello")

    # Still enabled: the model never said anything about schemas.
    assert translator._structured_support["test-model"] is True


def test_batch_path_applies_the_same_temperature_rule():
    parse = Mock(
        return_value=_parsed_completion(parsed=SimpleNamespace(paragraphs=["一", "二"]))
    )
    translator = _translator(parse=parse)
    translator.temperature = 1.0
    translator._structured_support["test-model"] = True

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
        "message": {"content": '{"translated":"你好"}'},
    }

    assert ChatGPTAPI._read_batch_choice(choice, "id-1") == "你好"


def test_batch_choice_passes_through_plain_content():
    choice = {"finish_reason": "stop", "message": {"content": "plain text"}}

    assert ChatGPTAPI._read_batch_choice(choice, "id-1") == "plain text"


def test_batch_choice_rejects_truncated_result():
    choice = {
        "finish_reason": "length",
        "message": {"content": '{"translated":"半'},
    }

    with pytest.raises(ValueError, match="truncated"):
        ChatGPTAPI._read_batch_choice(choice, "id-1")


def test_batch_choice_rejects_refusal():
    choice = {"finish_reason": "stop", "message": {"refusal": "nope", "content": None}}

    with pytest.raises(ValueError, match="refused"):
        ChatGPTAPI._read_batch_choice(choice, "id-1")


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


def test_structured_json_returns_none_when_schema_unsupported():
    create = Mock(return_value=_completion("not json at all"))
    translator = _translator(create=create)

    assert translator.structured_json("classify", {"schema": {}}) is None
    # only the probe went out; no classification request followed
    assert create.call_count == 1


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
