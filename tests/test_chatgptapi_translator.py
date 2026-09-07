import json
import threading
import time
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
from book_maker.translator.base_translator import BatchMismatch
from book_maker.translator.capabilities import (
    ROUTE_PROBE_PROMPT,
    CapabilityLedger,
    ModelUnavailable,
)
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    StructuredOutputUnsupported,
    StructuredRefusal,
    batch_field_name,
    batch_translation_model,
    single_field_name,
    single_translation_model,
    single_translation_schema,
)

# Every translator built by `_translator` uses this language, so the structured
# fields are named after it.
LANGUAGE = "Chinese"
SINGLE_FIELD = single_field_name(LANGUAGE)
BATCH_FIELD = batch_field_name(LANGUAGE)


def _single(text):
    """`.parsed` for a single translation in the fixture's language."""
    return SimpleNamespace(**{SINGLE_FIELD: text})


def _batch(paragraphs, ids=None):
    """`.parsed` for a batch translation in the fixture's language.

    Items echo the id they were sent with; `ids` overrides the natural
    0..n-1 run for the tests that pin a bad reply.
    """
    ids = range(len(paragraphs)) if ids is None else ids
    items = [
        SimpleNamespace(**{"id": i, SINGLE_FIELD: text})
        for i, text in zip(ids, paragraphs)
    ]
    return SimpleNamespace(**{BATCH_FIELD: items})


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
    translator.capabilities = CapabilityLedger()
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

    translator._probe_verdict()

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

    translator._probe_verdict()

    request = create.call_args.kwargs
    assert "temperature" not in request
    # A cap is rejected by o-series/gpt-5 and eaten by reasoning tokens.
    assert "max_tokens" not in request
    assert "max_completion_tokens" not in request


def test_probe_accepts_exact_constrained_value():
    translator = _translator(
        create=Mock(return_value=_completion('{"probe":"schema_ok"}'))
    )

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_probe_records_shape_when_values_are_ignored():
    """Structure honored, `enum` ignored: usable for classification only."""
    translator = _translator(
        create=Mock(return_value=_completion('{"probe":"ignored"}'))
    )

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] == "shape"


@pytest.mark.parametrize("verdict", ["shape", "json", False])
def test_only_a_strict_endpoint_gets_a_schema_for_translation(verdict):
    # our translation schema pins the target language as a *value*; an
    # endpoint that ignores values would drop that pin (#544), which is
    # worse than the delimiter method stating the language in the prompt
    translator = _translator()
    translator.capabilities.verdicts["test-model"] = verdict

    assert translator._ensure_structured_support() is False


def test_strict_endpoints_get_a_schema_for_translation():
    translator = _translator()
    translator.capabilities.verdicts["test-model"] = "strict"

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
    translator.capabilities.verdicts["test-model"] = verdict

    rungs = translator.structured_rungs("classify", {"schema": {}})

    assert rungs[0][0] == entry


def test_shape_endpoint_batches_with_a_schema_and_classifies_structured():
    # The batch schema pins no *values* — the target language rides in the
    # field name and the prose tail — so shape-only decoding is enough for
    # it, unlike the single-translation schema (#544).
    create = Mock(
        side_effect=[
            _completion('{"probe":"ignored"}'),  # shape verdict
            _completion('{"p.header": {"verdict": "skip"}}'),  # classification
        ]
    )
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", "二"])))
    translator = _translator(create=create, parse=parse)

    assert translator.translate_list(["one", "two"]) == ["一", "二"]
    assert translator.structured_json("classify", {"schema": {}}) == {
        "p.header": {"verdict": "skip"}
    }

    assert parse.call_args.kwargs["response_format"] is batch_translation_model(
        LANGUAGE, 2
    )
    classify_call = create.call_args_list[1].kwargs
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

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] is False


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

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] == "json"


def test_probe_rejects_truncated_probe_response():
    translator = _translator(
        create=Mock(
            return_value=_completion('{"probe":"schema', finish_reason="length")
        )
    )

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] is False


# --------------------------------------------------------------------------
# Item 5: only capability answers may be swallowed
# --------------------------------------------------------------------------


def test_probe_treats_bad_request_as_no_schema_support():
    translator = _translator(create=Mock(side_effect=_api_error(BadRequestError, 400)))

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] is False


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
        translator._probe_verdict()

    assert translator.capabilities.verdicts == {}


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
    assert translator.capabilities.verdicts == {}  # nothing learned, nothing cached


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

    translator._probe_verdict()

    assert translator.capabilities.verdicts["test-model"] is False


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
    assert set(translator.capabilities.verdicts) == {"test-model", "other-model"}


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
    translator.capabilities.verdicts["test-model"] = "strict"

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
    translator.capabilities.verdicts["test-model"] = "strict"

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
    translator.capabilities.verdicts["test-model"] = "strict"

    assert translator.get_translation("hello") == "完整的翻譯"
    assert create.call_count == 1
    # Truncation is a token-budget accident, not a capability answer.
    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_refusal_raises_its_own_exception():
    """A refusal is its own answer — neither a capability verdict nor a
    transport error, so it gets an exception the caller can act on."""
    translator = _translator(
        parse=Mock(return_value=_parsed_completion(refusal="nope"))
    )
    translator.capabilities.verdicts["test-model"] = "strict"

    with pytest.raises(StructuredRefusal, match="nope"):
        translator._structured_single_translation("hello")


def test_refusal_retranslates_plainly_instead_of_ending_the_run():
    """Observed live: the refusal field held a complete, correct translation
    while tenacity retried three times and then killed the book. One paragraph
    the schema path would not answer goes through the plain path instead."""
    create = Mock(return_value=_completion("完整的翻譯"))
    translator = _translator(
        create=create,
        parse=Mock(return_value=_parsed_completion(refusal="I cannot help")),
    )
    translator.capabilities.verdicts["test-model"] = "strict"

    assert translator.get_translation("hello") == "完整的翻譯"
    assert create.call_count == 1
    # A refusal says nothing about schema support: demoting on it would cost
    # the rest of the book its structured mode after two paragraphs.
    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_batch_refusal_is_not_retried_before_falling_back():
    """The batch caller already degrades to one-by-one; retrying a refusal
    three times first only buys three more refusals."""
    parse = Mock(return_value=_parsed_completion(refusal="nope"))
    create = Mock(return_value=_completion("plain"))
    translator = _translator(create=create, parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    assert translator._do_structured_batch_translate(["a", "b"]) == ["plain"] * 2

    batch_model = batch_translation_model(LANGUAGE, 2)
    batch_calls = [
        c for c in parse.call_args_list if c.kwargs["response_format"] is batch_model
    ]
    assert len(batch_calls) == 1
    assert translator.capabilities.verdicts["test-model"] == "strict"


# --------------------------------------------------------------------------
# Item 1 (cont.): demote on the first real structured failure
# --------------------------------------------------------------------------


def test_single_path_demotes_and_retries_plainly_when_schema_is_ignored():
    parse = Mock(side_effect=StructuredOutputUnsupported("server ignored schema"))
    create = Mock(return_value=_completion("plain translation"))
    translator = _translator(create=create, parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    # First failure falls back for this paragraph but keeps structured mode on:
    # one garbled proxy answer must not cost a whole book its schema support.
    assert translator.get_translation("hello") == "plain translation"
    assert translator.capabilities.verdicts["test-model"] == "strict"

    assert translator.get_translation("hello") == "plain translation"
    assert translator.capabilities.verdicts["test-model"] is False

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
    translator.capabilities.verdicts["test-model"] = "strict"

    translator.get_translation("a")  # streak 1
    assert translator.get_translation("b") == "你好"  # streak reset
    translator.get_translation("c")  # streak 1 again, not 2

    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_batch_path_demotes_without_burning_retries():
    parse = Mock(side_effect=StructuredOutputUnsupported("server ignored schema"))
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    assert translator._do_structured_batch_translate(["a", "b"]) == ["t:a", "t:b"]
    assert translator._do_structured_batch_translate(["a", "b"]) == ["t:a", "t:b"]

    assert parse.call_count == 2  # one attempt each, not 3 tenacity attempts
    assert translator.capabilities.verdicts["test-model"] is False


def test_batch_length_mismatch_raises_for_the_loader_to_divide():
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["only one"])))
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    with pytest.raises(BatchMismatch):
        translator._do_structured_batch_translate(["a", "b"])

    # one attempt: the reply is well-formed JSON that answers the wrong
    # question, so neither a retry nor a per-item sweep here — the loader's
    # ladder halves the chunk, at about twice the batch instead of n singles
    assert parse.call_count == 1
    assert translator.translate.call_count == 0
    # A count mismatch says nothing about schema support.
    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_batch_empty_slot_for_nonempty_input_raises():
    # Count is not alignment: a model that merges two verse lines into one
    # slot keeps the count by padding another slot with "". The strict path
    # must treat that pad as the model error it is, not accept the window.
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["5a+5b merged", ""])))
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"
    translator.translate = Mock(side_effect=lambda text, _=True: f"t:{text}")

    with pytest.raises(BatchMismatch):
        translator._do_structured_batch_translate(["5a", "5b"])

    assert parse.call_count == 1
    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_batch_empty_output_for_empty_input_is_accepted():
    # the complement: only *non-empty* inputs may not come back empty —
    # an empty slot mirroring an empty input is well-formed output
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", ""])))
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    assert translator._do_structured_batch_translate(["a", "  "]) == ["一", ""]
    assert parse.call_count == 1


def test_batch_success_returns_paragraphs():
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", "二"])))
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
    request = parse.call_args.kwargs
    assert request["response_format"] is batch_translation_model(LANGUAGE, 2)
    assert json.loads  # payload is built by the SDK, not hand-rolled


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

    translator = OrcaRouterTranslator(
        "sk-orca-test", "Chinese", api_base="http://proxy.local/v1"
    )
    assert translator.api_base == "http://proxy.local/v1"
    assert translator.openai_client.base_url == "http://proxy.local/v1/"


def test_orcarouter_route_is_still_checked_at_the_first_paid_call():
    # the class names its model in __init__ rather than through
    # set_model_list, and the route check must not be skipped for that
    from book_maker.translator.orcarouter_translator import OrcaRouterTranslator

    translator = OrcaRouterTranslator("sk-orca-test", "Chinese")
    with patch(
        "book_maker.translator.chatgptapi_translator.verify_model_routes",
        return_value={"success": True, "available_models": ["orcarouter/auto"]},
    ) as verify:
        translator._ensure_models_routable()
        translator._ensure_models_routable()

    verify.assert_called_once()
    assert verify.call_args.args[1] == ["orcarouter/auto"]


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
    translator.capabilities.verdicts["test-model"] = "strict"

    translator._structured_single_translation("hello")

    assert "temperature" not in parse.call_args.kwargs


def test_explicit_temperature_is_sent():
    parse = Mock(return_value=_parsed_completion(parsed=_single("你好")))
    translator = _translator(parse=parse)
    translator.temperature = 0.1
    translator.capabilities.verdicts["test-model"] = "strict"

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
    translator.capabilities.verdicts["test-model"] = "strict"

    assert translator._structured_single_translation("hello") == "你好"
    assert parse.call_count == 2
    assert "temperature" not in parse.call_args.kwargs
    assert translator.capabilities.temperature_unsupported["test-model"] is True

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
    translator.capabilities.verdicts["test-model"] = "strict"

    translator._structured_single_translation("hello")

    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_schema_rejection_is_still_a_capability_answer():
    parse = Mock(
        side_effect=_api_error(
            BadRequestError, 400, "response_format of type json_schema is not supported"
        )
    )
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    with pytest.raises(StructuredOutputUnsupported):
        translator._structured_single_translation("hello")


def test_unrelated_bad_request_is_not_blamed_on_the_schema():
    parse = Mock(
        side_effect=_api_error(BadRequestError, 400, "context length exceeded")
    )
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    with pytest.raises(BadRequestError):
        translator._structured_single_translation("hello")

    # Still enabled: the model never said anything about schemas.
    assert translator.capabilities.verdicts["test-model"] == "strict"


def test_batch_path_applies_the_same_temperature_rule():
    parse = Mock(return_value=_parsed_completion(parsed=_batch(["一", "二"])))
    translator = _translator(parse=parse)
    translator.temperature = 1.0
    translator.capabilities.verdicts["test-model"] = "strict"

    translator._do_structured_batch_translate(["a", "b"])

    assert "temperature" not in parse.call_args.kwargs


def test_temperature_support_is_tracked_per_model():
    translator = _translator()
    translator.temperature = 0.1
    translator.capabilities.temperature_unsupported["test-model"] = True

    assert translator._sampling_kwargs() == {}
    assert translator._sampling_kwargs("other-model") == {"temperature": 0.1}


def test_batch_api_body_omits_default_temperature():
    translator = _translator()
    translator.temperature = 1.0
    translator.batch_model = "batch-model"
    translator.capabilities.verdicts["batch-model"] = False
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

    batch = batch_translation_model("simplified chinese", 2).model_json_schema()
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
        assert batch_translation_model(language, 2).__name__ == batch_field_name(
            language
        )
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
    translator.capabilities.verdicts["test-model"] = "strict"

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
    translator.capabilities.verdicts["test-model"] = False
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


def test_delimiter_batch_context_is_untouched_by_a_misaligned_reply():
    # a short response is a mismatch now: the route raises for the loader's
    # ladder to divide, and nothing about the discarded exchange — least of
    # all the joined blob — may be left in the context window
    create = Mock(
        side_effect=[_completion("只有一段"), _completion("一"), _completion("二")]
    )
    translator = _delimiter_translator("unused")
    translator.openai_client.chat.completions.create = create

    with pytest.raises(BatchMismatch):
        translator.translate_list(["one", "two"])

    assert create.call_count == 1  # no per-item self-repair
    assert translator.context_list == []
    assert translator.context_translated_list == []


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


def test_the_model_list_rotates_in_the_order_it_was_given():
    # a set() pass here made the first model depend on hash order, so the
    # same command could start on a different model between runs
    translator = ChatGPTAPI.__new__(ChatGPTAPI)

    translator.set_model_list(["gpt-b", "gpt-a", " gpt-b ", "", "gpt-c"])

    assert translator.model == "gpt-b"
    assert [next(translator.model_list) for _ in range(4)] == [
        "gpt-b",
        "gpt-a",
        "gpt-c",
        "gpt-b",
    ]


# --------------------------------------------------------------------------
# The route probe: which models this endpoint serves, asked lazily
# --------------------------------------------------------------------------


def _no_listing():
    """An endpoint with `/chat/completions` and nothing else."""
    return SimpleNamespace(list=Mock(side_effect=_api_error(NotFoundError, 404)))


def _routed(create, models=("gpt-a",), listing=None):
    """A translator whose `--model_list` has been recorded but not yet checked."""
    translator = _translator(create=create)
    translator.openai_client.models = listing or _no_listing()
    translator.set_model_list(list(models))
    return translator


def test_set_model_list_asks_the_endpoint_nothing():
    # the list is recorded, not verified: a run that never translates
    # (--plan-dry-run, the agent handoff) must not pay a startup round trip
    create = Mock()
    listing = _no_listing()
    translator = _routed(create, models=["gpt-a", "gpt-b"], listing=listing)

    assert create.call_count == 0
    assert listing.list.call_count == 0
    assert translator.model == "gpt-a"
    assert translator._model_names == ["gpt-a", "gpt-b"]


def test_the_route_is_checked_at_the_first_paid_call():
    create = Mock(
        side_effect=[_completion("PONG"), _completion('{"probe":"schema_ok"}')]
    )
    translator = _routed(create)

    assert create.call_count == 0
    translator._probe_verdict()

    route, schema = create.call_args_list
    assert route.kwargs["model"] == "gpt-a"
    assert "max_tokens" not in route.kwargs
    assert "response_format" not in route.kwargs
    # ...and the capability probe still runs on its own terms afterwards
    assert schema.kwargs["response_format"]["type"] == "json_schema"


def test_the_route_is_checked_once_per_run():
    create = Mock(
        side_effect=[_completion("PONG"), _completion('{"probe":"schema_ok"}')]
    )
    translator = _routed(create)

    for _ in range(4):
        translator._probe_verdict()

    # one route probe plus one capability probe, neither repeated per request
    assert create.call_count == 2


def test_a_worker_clone_does_not_buy_the_check_again():
    # parallel chapters translate through shallow copies of the translator
    # (loader._clone_translator_for_context). Rebinding the state on one
    # clone would leave every other worker to pay for the same probes.
    from copy import copy

    create = Mock(
        side_effect=[
            _completion("PONG"),
            _completion('{"probe":"schema_ok"}'),
            _completion('{"probe":"schema_ok"}'),
        ]
    )
    translator = _routed(create)

    first, second = copy(translator), copy(translator)
    first._probe_verdict()
    second._probe_verdict()

    route_probes = [
        c
        for c in create.call_args_list
        if c.kwargs["messages"][0]["content"] == ROUTE_PROBE_PROMPT
    ]
    assert len(route_probes) == 1
    # and the original knows too, so a later paragraph on it pays nothing
    assert translator._route_state["pending"] is None


def test_a_refusal_reaches_a_clone_that_never_probed():
    from copy import copy

    create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
    translator = _routed(create, models=["ghost"])
    first, second = copy(translator), copy(translator)

    with pytest.raises(ModelUnavailable):
        first._probe_verdict()
    with pytest.raises(ModelUnavailable):
        second._probe_verdict()

    # the second worker is told, not re-probed
    assert create.call_count == 1


def test_a_model_the_endpoint_will_not_serve_stops_the_run():
    create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
    translator = _routed(create, models=["ghost"])

    with pytest.raises(ModelUnavailable, match="ghost"):
        translator._probe_verdict()


def test_the_refusal_is_the_message_a_reader_gets_not_a_traceback():
    create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
    translator = _routed(create, models=["ghost"])

    with pytest.raises(ModelUnavailable) as caught:
        translator._probe_verdict()

    assert caught.value.user_facing is True


def test_a_route_refusal_is_not_re_probed_by_every_retry():
    # get_translation is wrapped in three tenacity attempts; a fatal answer
    # about the model must cost one request, not one per attempt
    create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
    translator = _routed(create, models=["ghost"])

    for _ in range(3):
        with pytest.raises(ModelUnavailable):
            translator._probe_verdict()

    assert create.call_count == 1


def test_the_check_narrows_the_list_to_the_models_that_answer():
    create = Mock(
        side_effect=[
            _completion("PONG"),
            _api_error(NotFoundError, 404, "no such model"),
            _completion("PONG"),
            _completion('{"probe":"schema_ok"}'),
        ]
    )
    translator = _routed(create, models=["gpt-a", "ghost", "gpt-c"])

    translator._probe_verdict()

    assert translator._model_names == ["gpt-a", "gpt-c"]
    # rotation keeps the order the user typed, minus what the endpoint refused
    assert [next(translator.model_list) for _ in range(3)] == [
        "gpt-a",
        "gpt-c",
        "gpt-a",
    ]


def test_a_refused_current_model_is_replaced_by_one_that_answers():
    create = Mock(
        side_effect=[
            _api_error(NotFoundError, 404, "no such model"),
            _completion("PONG"),
            _completion('{"probe":"schema_ok"}'),
        ]
    )
    translator = _routed(create, models=["ghost", "gpt-c"])
    assert translator.model == "ghost"

    translator._probe_verdict()

    assert translator.model == "gpt-c"
    # the capability probe asked about the survivor, not the refused model
    assert create.call_args_list[-1].kwargs["model"] == "gpt-c"


def test_a_model_the_endpoint_serves_but_does_not_list_is_kept():
    # the listing is the wrong authority: gateways serve models they do not
    # list, and the old gate refused them before they were ever tried
    listing = SimpleNamespace(model_dump=lambda: {"data": [{"id": "something-else"}]})
    create = Mock(
        side_effect=[_completion("PONG"), _completion('{"probe":"schema_ok"}')]
    )
    translator = _routed(
        create, models=["unlisted"], listing=SimpleNamespace(list=lambda: listing)
    )

    translator._probe_verdict()

    assert translator._model_names == ["unlisted"]


def test_the_batch_path_checks_the_route_too():
    create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
    translator = _routed(create, models=["ghost"])

    with pytest.raises(ModelUnavailable):
        translator.translate_list(["one", "two"])


def test_the_classification_path_checks_the_route_too():
    create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
    translator = _routed(create, models=["ghost"])

    with pytest.raises(ModelUnavailable):
        translator.structured_json("classify", {"schema": {}})


def test_a_transport_failure_never_reads_as_a_missing_model(capsys):
    create = Mock(
        side_effect=[
            APIConnectionError(request=httpx.Request("POST", "https://x/v1")),
            _completion('{"probe":"schema_ok"}'),
        ]
    )
    translator = _routed(create, models=["gpt-a"])

    assert translator._probe_verdict() == "strict"
    out = capsys.readouterr().out
    assert "does not serve" not in out


# --------------------------------------------------------------------------
# Usage meter: in/out/cached on the progress bar, replacing the cache guard
# --------------------------------------------------------------------------


def test_the_meter_sums_what_each_request_billed():
    from types import SimpleNamespace
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    t = ChatGPTAPI("k", "zh-hans")
    assert t.usage_postfix() is None  # nothing reported yet: nothing shown
    t._note_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1200,
                completion_tokens=300,
                prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
            )
        )
    )
    t._note_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=800, completion_tokens=100, prompt_tokens_details=None
            )
        )
    )
    t._note_usage(SimpleNamespace(usage=None))  # an answer without usage
    t._note_usage(None)  # a truncated answer that carried no completion
    assert t.usage_postfix() == {"in": "2.0k", "out": "400", "cached": "1.0k"}
    assert t.usage_summary() == "tokens: in 2.0k, out 400, cached 1.0k (2 requests)"


def test_short_counts_fit_a_progress_bar():
    from book_maker.translator.base_translator import short_count

    assert short_count(0) == "0"
    assert short_count(999) == "999"
    assert short_count(12345) == "12.3k"
    assert short_count(1_234_567) == "1.23M"


def test_the_claude_meter_counts_cache_reads_inside_the_prompt_total():
    from types import SimpleNamespace
    from book_maker.translator.claude_translator import Claude

    t = Claude("k", "zh-hans")
    t._note_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=900,
                cache_creation_input_tokens=0,
            )
        )
    )
    assert t.usage_postfix() == {"in": "1.0k", "out": "50", "cached": "900"}


def test_the_price_table_finds_a_model_by_id_router_tail_or_prefix():
    from book_maker.translator.base_translator import PriceTable

    table = PriceTable(
        {"gpt-5.6-luna": {"input": 0.2, "output": 1.2, "cached_input": 0.02}}
    )
    assert table.price_for("gpt-5.6-luna")["output"] == 1.2
    assert table.price_for("openai/gpt-5.6-luna")["output"] == 1.2
    assert table.price_for("gpt-5.6-luna-2026-07-30")["output"] == 1.2
    assert table.price_for("gpt-5.6-terra") is None
    assert table.price_for(None) is None


def test_a_request_is_costed_with_cache_reads_at_their_own_rate():
    from book_maker.translator.base_translator import PriceTable

    table = PriceTable(
        {"luna": {"input": 0.2, "output": 1.2, "cached_input": 0.02}}, "USD"
    )
    # 1M prompt of which 500k cached, 100k completion
    assert table.cost("luna", 1_000_000, 100_000, 500_000) == pytest.approx(
        0.5 * 0.2 + 0.5 * 0.02 + 0.1 * 1.2
    )
    # no cached_input: cache reads cost the input price, the conservative reading
    flat = PriceTable({"m": {"input": 1.0, "output": 2.0}})
    assert flat.cost("m", 1_000_000, 0, 1_000_000) == pytest.approx(1.0)
    assert table.money(0.00123) == "$0.0012"
    assert table.money(0.123) == "$0.123"
    assert table.money(12.3456) == "$12.35"
    assert PriceTable({}, "CNY").money(0.5) == "¥0.500"
    assert PriceTable({}, "CHF").money(0.5) == "0.500 CHF"


def test_with_prices_the_bar_shows_spent_instead_of_tokens():
    from types import SimpleNamespace
    from book_maker.translator.base_translator import PriceTable
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    t = ChatGPTAPI("k", "zh-hans")
    t.model = "gpt-5.6-luna"
    t.usage.prices = PriceTable(
        {"gpt-5.6-luna": {"input": 0.2, "output": 1.2, "cached_input": 0.02}}
    )
    t._note_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1_000_000,
                completion_tokens=100_000,
                prompt_tokens_details=SimpleNamespace(cached_tokens=500_000),
            )
        )
    )
    assert t.usage_postfix() == {"spent": "$0.230"}
    assert t.usage_summary().startswith("spent $0.230 — tokens: in 1.00M, out 100.0k")

    # the classifier asked another model: priced by the id *it* asked for
    t._note_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=1, prompt_tokens_details=None
            )
        ),
        "gpt-5.6-terra",
    )
    assert t.usage_postfix() == {"in": "1.00M", "out": "100.0k", "cached": "500.0k"}
    assert t.usage_summary().endswith(
        "; no price for gpt-5.6-terra in the provider entry, so spent is not shown"
    )


def test_requests_have_a_timeout_shorter_than_the_sdk_default():
    # router test 260902: a gateway that accepts a request and never answers
    # held a run for the SDK's 600 s × 3 tries with nothing printed
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI, REQUEST_LIMITS

    t = ChatGPTAPI("k", "zh-hans")
    assert t.openai_client.timeout == REQUEST_LIMITS["timeout"] == 300.0
    assert t.openai_client.max_retries == REQUEST_LIMITS["max_retries"] == 1
    client = t._create_async_client("k")
    assert client.timeout == 300.0 and client.max_retries == 1


def test_an_odd_usage_record_never_stops_a_request():
    """The meter is a readout, not a gate: a gateway that reports tokens as
    strings, as None, or through an object whose attributes raise must cost
    the operator the number on the bar and nothing else."""
    from types import SimpleNamespace
    from book_maker.translator.base_translator import UsageMeter
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI
    from book_maker.translator.claude_translator import Claude

    meter = UsageMeter()
    meter.note("12", None, object())  # strings and junk are not summed, not fatal
    meter.note(3.0, 2, 1)
    assert (meter.prompt, meter.completion, meter.cached, meter.requests) == (
        15,
        2,
        1,
        2,
    )

    class Exploding:
        @property
        def usage(self):
            raise RuntimeError("gateway shaped this one strangely")

    for t in (ChatGPTAPI("k", "zh-hans"), Claude("k", "zh-hans")):
        t._note_usage(Exploding())
        assert t.usage_postfix() is None
        t._note_usage(SimpleNamespace(usage={"prompt_tokens": 5}))  # a dict
        assert t.usage_postfix() == {"in": "0", "out": "0", "cached": "0"}


class TestRequestExtras:
    """`--extra_body` / `--extra_headers` on the OpenAI request path."""

    def _translator(self):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        return ChatGPTAPI("sk-test", "Chinese")

    def test_headers_go_on_the_client_not_the_call(self):
        # so the capability probe, the route check and the model listing
        # carry them too, without every call site knowing about them
        t = self._translator()
        t.set_request_extras(extra_headers={"X-Title": "bbm"})

        assert t.openai_client.default_headers["X-Title"] == "bbm"

    def test_cached_async_clones_are_dropped_so_they_are_rebuilt(self):
        t = self._translator()
        t._async_clients[("base", "k")] = object()
        t.set_request_extras(extra_headers={"X-Title": "bbm"})

        assert t._async_clients == {}

    def test_an_async_client_is_built_with_the_headers(self):
        t = self._translator()
        t.set_request_extras(extra_headers={"X-Title": "bbm"})

        assert t._create_async_client("k").default_headers["X-Title"] == "bbm"

    def test_every_rung_and_the_schema_probe_send_the_body(self):
        # _completion_text is the one door the rungs and the probe share
        t = self._translator()
        t.set_request_extras(extra_body={"enable_thinking": False})
        with patch.object(t, "openai_client") as client:
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=None,
                model="m",
            )
            t._completion_text("m", "hello")

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"] == {"enable_thinking": False}

    def test_a_caller_that_built_its_own_body_keeps_it(self):
        t = self._translator()
        t.set_request_extras(extra_body={"enable_thinking": False})
        with patch.object(t, "openai_client") as client:
            client.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=None,
                model="m",
            )
            t._completion_text("m", "hello", extra_body={"mine": 1})

        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"] == {"mine": 1}

    def test_a_refusal_while_extras_are_set_shows_what_the_endpoint_said(self, capsys):
        # the ladder answers a refusal by demoting, so without this the run
        # degrades quietly and the endpoint's own words are never seen
        t = self._translator()
        t.set_request_extras(extra_body={"enable_thinking": False})
        t.warn_if_extras_refused(Exception("unknown field 'enable_thinking'"))
        out = " ".join(capsys.readouterr().out.split())

        assert "unknown field 'enable_thinking'" in out
        assert "--extra_body" in out

    def test_a_refusal_with_no_extras_set_says_nothing(self):
        # every endpoint that does not do schemas refuses a rung; that is
        # the ladder working, not a problem to report
        t = self._translator()
        t.warn_if_extras_refused(Exception("no json_schema here"))


def test_a_refusal_never_repeats_a_header_value_back(capsys):
    """An endpoint that rejects a header routinely quotes it back.

    The CLI takes care never to print a header value; the endpoint's own
    words would put it on stdout anyway.
    """
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    t = ChatGPTAPI("sk-test", "Chinese")
    t.set_request_extras(extra_headers={"Authorization": "Bearer sk-SECRET"})
    t.warn_if_extras_refused(
        Exception("invalid header value 'Bearer sk-SECRET' for Authorization")
    )
    out = capsys.readouterr().out

    assert "sk-SECRET" not in out
    assert "<redacted>" in out
    assert "Authorization" in out  # the endpoint's own words otherwise intact
