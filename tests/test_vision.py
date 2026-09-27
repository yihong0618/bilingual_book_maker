"""The image capability and the image-carrying structured request.

The probe must be graded by what came back, not by the request being
accepted: an endpoint in front of a text-only model can drop the image part
and answer the text alone. And an image refusal must never be read as a
verdict about schemas: the two capabilities share a ledger, not a counter.
"""

import base64
import io
import random
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from book_maker.structured import StructuredJSONFailed
from book_maker.translator.base_translator import UsageMeter
from book_maker.translator.capabilities import CapabilityLedger, ProbeDeferred
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.vision import (
    CHALLENGE_SIZE,
    IMAGE_PROBE_PROMPT,
    VisionRequestFailed,
    challenge_png,
    image_part,
    probe_image,
    QuestionTimedOut,
)

Image = pytest.importorskip("PIL.Image")  # Pillow is a pdf-extra dependency

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
SCHEMA = {
    "name": "digits",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"digits": {"type": "string"}},
        "required": ["digits"],
        "additionalProperties": False,
    },
}
IMAGE_400 = "Invalid content type. image_url is only supported by certain models."


@pytest.fixture(autouse=True)
def _no_backoff_naps(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


def _completion(content, prompt_tokens=10, completion_tokens=2):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt_tokens_details=None,
        ),
    )


def _api_error(cls, status_code, message="boom"):
    return cls(
        message, response=httpx.Response(status_code, request=REQUEST), body=None
    )


def _client(create):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


def _answer_for(seed):
    """What `probe_image(rng=random.Random(seed))` will draw."""
    return challenge_png(random.Random(seed))[1]


def _decode(content):
    """The PNG an image part carries, decoded back to a PIL image."""
    (part,) = [p for p in content if p["type"] == "image_url"]
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw)), raw


class TestChallenge:
    def test_different_seeds_draw_different_pictures(self):
        (png_a, a), (png_b, b) = challenge_png(random.Random(1)), challenge_png(
            random.Random(2)
        )
        assert a != b and png_a != png_b
        assert len(a) == 4 and a.isalnum() and not set(a) & set("0O1I")

    def test_the_picture_carries_ink(self):
        png, _ = challenge_png(random.Random(3))
        image = Image.open(io.BytesIO(png)).convert("L")
        assert image.size == CHALLENGE_SIZE
        # text drawn, not a blank white card
        assert image.getextrema()[0] < 64


class TestProbeGrading:
    def test_the_right_characters_verify(self):
        answer = _answer_for(7)
        create = Mock(return_value=_completion(f" {answer.lower()}.\n"))
        assert probe_image(_client(create), "m", rng=random.Random(7)) == "verified"

    @pytest.mark.parametrize("reply", ["NONE", "", None, "ABCD", "I see an image"])
    def test_anything_else_is_unsupported(self, reply):
        # "ABCD" cannot be seed 7's answer: pinned so the test means something
        assert _answer_for(7) != "ABCD"
        create = Mock(return_value=_completion(reply))
        assert probe_image(_client(create), "m", rng=random.Random(7)) == "unsupported"

    def test_a_400_naming_the_image_is_an_answer(self):
        create = Mock(side_effect=_api_error(BadRequestError, 400, IMAGE_400))
        assert probe_image(_client(create), "m") == "unsupported"

    def test_a_400_about_something_else_propagates(self):
        create = Mock(side_effect=_api_error(BadRequestError, 400, "context length"))
        with pytest.raises(BadRequestError):
            probe_image(_client(create), "m")

    def test_a_401_is_fatal(self):
        create = Mock(side_effect=_api_error(AuthenticationError, 401, "bad key"))
        with pytest.raises(AuthenticationError):
            probe_image(_client(create), "m")

    @pytest.mark.parametrize(
        "error",
        [_api_error(RateLimitError, 429), APIConnectionError(request=REQUEST)],
    )
    def test_weather_defers(self, error):
        with pytest.raises(ProbeDeferred):
            probe_image(_client(Mock(side_effect=error)), "m")

    def test_usage_is_metered(self):
        seen = []
        create = Mock(return_value=_completion("NONE"))
        probe_image(_client(create), "m", on_usage=seen.append)
        assert len(seen) == 1


class TestProbeRequest:
    def test_the_request_carries_the_challenge_and_not_its_answer(self):
        answer = _answer_for(11)
        create = Mock(return_value=_completion(answer))
        probe_image(_client(create), "m", rng=random.Random(11))

        request = create.call_args.kwargs
        (message,) = request["messages"]
        content = message["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": IMAGE_PROBE_PROMPT}
        image, raw = _decode(content)
        assert image.size == CHALLENGE_SIZE
        assert raw == challenge_png(random.Random(11))[0]
        assert content[1]["image_url"]["detail"] == "high"
        # the answer is on the picture only
        assert answer not in IMAGE_PROBE_PROMPT
        assert answer not in repr({k: v for k, v in request.items() if k != "messages"})
        assert request["max_completion_tokens"] == 2000
        assert request["reasoning_effort"] == "low"

    def test_two_seeds_send_two_different_images(self):
        create = Mock(return_value=_completion("NONE"))
        probe_image(_client(create), "m", rng=random.Random(1))
        probe_image(_client(create), "m", rng=random.Random(2))
        first, second = (
            _decode(call.kwargs["messages"][0]["content"])[1]
            for call in create.call_args_list
        )
        assert first != second
        assert _answer_for(1) != _answer_for(2)

    def test_a_refused_reasoning_effort_is_dropped_once(self):
        answer = _answer_for(5)
        create = Mock(
            side_effect=[
                _api_error(
                    BadRequestError,
                    400,
                    "Unsupported parameter: 'reasoning_effort' is not supported "
                    "with this model.",
                ),
                _completion(answer),
            ]
        )
        assert probe_image(_client(create), "m", rng=random.Random(5)) == "verified"
        first, second = (c.kwargs for c in create.call_args_list)
        assert first["reasoning_effort"] == "low"
        assert "reasoning_effort" not in second
        assert second["max_completion_tokens"] == 2000

    def test_the_runs_extra_body_rides_along(self):
        create = Mock(return_value=_completion("NONE"))
        probe_image(_client(create), "m", extra_body={"provider": {"x": 1}})
        assert create.call_args.kwargs["extra_body"] == {"provider": {"x": 1}}


class TestLedger:
    def test_probes_once_per_model_and_caches(self):
        ledger = CapabilityLedger()
        probe = Mock(return_value="verified")
        assert ledger.ensure_vision("a", probe) == "verified"
        assert ledger.ensure_vision("a", probe) == "verified"
        assert ledger.ensure_vision("b", probe) == "verified"
        assert [c.args[0] for c in probe.call_args_list] == ["a", "b"]

    def test_one_probe_under_parallel_workers(self):
        ledger = CapabilityLedger()
        probe = Mock(return_value="verified")
        threads = [
            threading.Thread(target=ledger.ensure_vision, args=("a", probe))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert probe.call_count == 1

    def test_no_probe_is_unsupported_without_a_request(self):
        assert CapabilityLedger().ensure_vision("a") == "unsupported"

    def test_a_deferred_probe_leaves_no_entry(self):
        ledger = CapabilityLedger()
        probe = Mock(side_effect=[ProbeDeferred("429"), "verified"])
        assert ledger.ensure_vision("a", probe) == "deferred"
        assert "a" not in ledger.vision
        assert ledger.ensure_vision("a", probe) == "verified"
        assert probe.call_count == 2

    def test_schema_demotion_leaves_vision_alone(self):
        ledger = CapabilityLedger()
        ledger.record("a", "strict")
        ledger.ensure_vision("a", Mock(return_value="verified"))
        ledger.demote("a", "garbled")
        ledger.demote("a", "garbled")
        assert ledger.verdicts["a"] is False
        assert ledger.vision == {"a": "verified"}

    def test_an_unsupported_image_leaves_the_schema_verdict_alone(self):
        ledger = CapabilityLedger()
        ledger.record("a", "strict")
        ledger.ensure_vision("a", Mock(return_value="unsupported"))
        assert ledger.verdicts == {"a": "strict"}
        assert ledger.failures == {}


# --------------------------------------------------------------------------
# The request: structured_json's ladder with an image on every rung.
# --------------------------------------------------------------------------

PNG, _ = challenge_png(random.Random(42))
PART = image_part(PNG)


def _translator(create, verdict="strict"):
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "test-model"
    translator.extra_body = {}
    translator.capabilities = CapabilityLedger()
    if verdict is not None:
        translator.capabilities.verdicts["test-model"] = verdict
    translator._rung_refusals = {}
    translator.usage = UsageMeter()
    translator.openai_client = _client(create)
    return translator


def _assert_carries_image(content, *texts):
    assert isinstance(content, list)
    images = [p for p in content if p["type"] == "image_url"]
    assert images == [PART]
    text_parts = [p["text"] for p in content if p["type"] == "text"]
    for text in texts:
        assert any(text in t for t in text_parts), (text, text_parts)
    return text_parts


class TestRequestOnEachRung:
    def test_json_schema_rung(self):
        create = Mock(return_value=_completion('{"digits": "1234"}'))
        translator = _translator(create, verdict="strict")

        result = translator.structured_json_with_image("read it", SCHEMA, PNG)

        assert result == {"digits": "1234"}
        request = create.call_args.kwargs
        assert request["response_format"]["type"] == "json_schema"
        texts = _assert_carries_image(request["messages"][-1]["content"], "read it")
        assert texts == ["read it"]
        assert request["max_completion_tokens"] == 2000
        assert request["reasoning_effort"] == "low"

    def test_json_object_rung_adds_the_schema_as_another_part(self):
        create = Mock(return_value=_completion('{"digits": "1234"}'))
        translator = _translator(create, verdict="json")

        translator.structured_json_with_image("read it", SCHEMA, PNG)

        request = create.call_args.kwargs
        assert request["response_format"] == {"type": "json_object"}
        texts = _assert_carries_image(request["messages"][-1]["content"], "read it")
        assert texts[0] == "read it"
        assert len(texts) == 2 and "Answer with a single JSON object" in texts[1]
        assert '"digits"' in texts[1]

    def test_prompt_rung_adds_the_schema_as_another_part(self):
        create = Mock(return_value=_completion('Sure: {"digits": "1234"}'))
        translator = _translator(create, verdict=False)

        assert translator.structured_json_with_image("read it", SCHEMA, PNG) == {
            "digits": "1234"
        }
        request = create.call_args.kwargs
        assert "response_format" not in request
        texts = _assert_carries_image(request["messages"][-1]["content"], "read it")
        assert len(texts) == 2 and "Answer with a single JSON object" in texts[1]

    def test_descending_keeps_the_same_image(self):
        create = Mock(
            side_effect=[
                _api_error(BadRequestError, 400, "Invalid schema for response_format"),
                _completion("no json here"),
                _completion('{"digits": "1234"}'),
            ]
        )
        translator = _translator(create, verdict="strict")

        assert translator.structured_json_with_image("read it", SCHEMA, PNG) == {
            "digits": "1234"
        }
        for call in create.call_args_list:
            _assert_carries_image(call.kwargs["messages"][-1]["content"], "read it")


class TestRequestFailures:
    def test_an_image_400_raises_and_touches_no_schema_state(self):
        create = Mock(side_effect=_api_error(BadRequestError, 400, IMAGE_400))
        translator = _translator(create, verdict="strict")
        translator._rung_refusals = {"test-model": {"json_object": 1}}
        before = (
            dict(translator.capabilities.verdicts),
            dict(translator.capabilities.failures),
            {k: dict(v) for k, v in translator._rung_refusals.items()},
        )

        with pytest.raises(VisionRequestFailed):
            translator.structured_json_with_image("read it", SCHEMA, PNG)

        assert create.call_count == 1  # no descent: every rung carries the image
        after = (
            dict(translator.capabilities.verdicts),
            dict(translator.capabilities.failures),
            {k: dict(v) for k, v in translator._rung_refusals.items()},
        )
        assert after == before

    def test_a_schema_400_descends_as_today(self):
        create = Mock(
            side_effect=[
                _api_error(BadRequestError, 400, "Invalid schema for response_format"),
                _completion('{"digits": "1234"}'),
            ]
        )
        translator = _translator(create, verdict="strict")

        assert translator.structured_json_with_image("read it", SCHEMA, PNG) == {
            "digits": "1234"
        }
        assert create.call_args.kwargs["response_format"] == {"type": "json_object"}
        assert translator._rung_refusals == {"test-model": {"json_schema": 1}}

    def test_a_429_is_waited_out_and_metered_per_answer(self):
        create = Mock(
            side_effect=[
                _api_error(RateLimitError, 429),
                _api_error(RateLimitError, 429),
                _completion('{"digits": "1234"}', prompt_tokens=100),
            ]
        )
        translator = _translator(create, verdict="strict")

        assert translator.structured_json_with_image("read it", SCHEMA, PNG) == {
            "digits": "1234"
        }
        assert create.call_count == 3
        # the rung was retried, not descended
        assert all(
            c.kwargs["response_format"]["type"] == "json_schema"
            for c in create.call_args_list
        )
        assert translator.usage.requests == 1
        assert translator.usage.prompt == 100

    def test_auth_errors_are_fatal(self):
        create = Mock(side_effect=_api_error(AuthenticationError, 401, "bad key"))
        translator = _translator(create, verdict="strict")
        with pytest.raises(AuthenticationError):
            translator.structured_json_with_image("read it", SCHEMA, PNG)
        assert create.call_count == 1

    def test_a_deadline_ends_the_waiting_with_the_last_transport_error(
        self, monkeypatch
    ):
        # PIN (lead 260923, Codex review of E2): still no attempt cap (owner
        # ruling 260907), but a caller with a time budget gets its question
        # back at the deadline; no wait runs past it.
        clock = {"now": 500.0}
        naps = []

        def nap(seconds):
            naps.append(seconds)
            clock["now"] += seconds

        monkeypatch.setattr("time.monotonic", lambda: clock["now"])
        monkeypatch.setattr("tenacity.nap.time.sleep", nap)
        errors = []

        def create(**kwargs):
            errors.append(APIConnectionError(request=REQUEST))
            raise errors[-1]

        translator = _translator(create, verdict="strict")
        with pytest.raises(APIConnectionError) as raised:
            translator.structured_json_with_image(
                "read it", SCHEMA, PNG, deadline=560.0
            )
        assert raised.value is errors[-1]
        assert clock["now"] == 560.0 and sum(naps) == 60.0
        assert len(errors) > 3

    def test_without_a_deadline_weather_is_waited_out_uncapped(self):
        create = Mock(
            side_effect=[APIConnectionError(request=REQUEST)] * 30
            + [_completion('{"digits": "9"}')]
        )
        translator = _translator(create, verdict="strict")
        assert translator.structured_json_with_image("read it", SCHEMA, PNG) == {
            "digits": "9"
        }
        assert create.call_count == 31
        # the deadline is not a request parameter, and without one the
        # client's own timeout stands
        assert "deadline" not in create.call_args.kwargs
        assert "timeout" not in create.call_args.kwargs

    def test_a_deadline_bounds_the_request_in_flight(self, monkeypatch):
        clock = {"now": 500.0}
        monkeypatch.setattr("time.monotonic", lambda: clock["now"])
        monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
        timeouts, options = [], []

        def create(**kwargs):
            timeouts.append(kwargs["timeout"])
            clock["now"] += kwargs["timeout"]  # hangs as long as it may
            raise APITimeoutError(request=REQUEST)

        class Client:
            chat = SimpleNamespace(completions=SimpleNamespace(create=create))

            def with_options(self, **kwargs):
                options.append(kwargs)
                return self

        translator = _translator(create, verdict="strict")
        translator.openai_client = Client()
        with pytest.raises(APITimeoutError):
            translator.structured_json_with_image(
                "read it", SCHEMA, PNG, deadline=560.0
            )
        assert timeouts == [60.0] and clock["now"] == 560.0
        # the SDK's own retry would start a second request past the deadline
        assert options == [{"max_retries": 0}]

    def test_the_timeout_is_never_below_one_second(self, monkeypatch):
        monkeypatch.setattr("time.monotonic", lambda: 559.9)
        create = Mock(return_value=_completion('{"digits": "1"}'))
        translator = _translator(create, verdict="strict")
        translator.structured_json_with_image("read it", SCHEMA, PNG, deadline=560.0)
        assert create.call_args.kwargs["timeout"] == 1.0

    def test_auth_errors_are_fatal_before_any_deadline(self):
        create = Mock(side_effect=_api_error(AuthenticationError, 401, "bad key"))
        translator = _translator(create, verdict="strict")
        with pytest.raises(AuthenticationError):
            translator.structured_json_with_image(
                "read it", SCHEMA, PNG, deadline=10**12
            )
        assert create.call_count == 1

    def test_usage_is_metered_under_the_model_used(self):
        create = Mock(return_value=_completion('{"digits": "1"}'))
        translator = _translator(create, verdict="strict")
        translator.capabilities.verdicts["vision-model"] = "strict"
        noted = []
        translator.usage.note = lambda *a, **k: noted.append(k.get("model"))

        translator.structured_json_with_image("q", SCHEMA, PNG, model="vision-model")

        assert create.call_args.kwargs["model"] == "vision-model"
        assert noted == ["vision-model"]

    def test_a_refused_optional_field_is_not_sent_again(self):
        create = Mock(
            side_effect=[
                _api_error(
                    BadRequestError,
                    400,
                    "Unrecognized request argument supplied: reasoning_effort",
                ),
                _completion('{"digits": "1"}'),
                _completion('{"digits": "2"}'),
            ]
        )
        translator = _translator(create, verdict="strict")

        translator.structured_json_with_image("q", SCHEMA, PNG)
        translator.structured_json_with_image("q", SCHEMA, PNG)

        sent = [c.kwargs for c in create.call_args_list]
        assert "reasoning_effort" in sent[0]
        assert "reasoning_effort" not in sent[1] and "reasoning_effort" not in sent[2]
        assert translator._rung_refusals == {}

    def test_accept_rejection_descends_like_structured_json(self):
        create = Mock(
            side_effect=[
                _completion('{"digits": "wrong"}'),
                _completion('{"digits": "1234"}'),
            ]
        )
        translator = _translator(create, verdict="strict")

        result = translator.structured_json_with_image(
            "q", SCHEMA, PNG, accept=lambda obj: obj["digits"].isdigit()
        )
        assert result == {"digits": "1234"}

    def test_no_accepted_answer_returns_the_last_parsed_one(self):
        create = Mock(return_value=_completion('{"digits": "wrong"}'))
        translator = _translator(create, verdict="strict")
        result = translator.structured_json_with_image(
            "q", SCHEMA, PNG, accept=lambda obj: False
        )
        assert result == {"digits": "wrong"}
        assert create.call_count == 3

    def test_no_json_anywhere_raises_like_structured_json(self):
        create = Mock(return_value=_completion("prose"))
        translator = _translator(create, verdict="strict")
        with pytest.raises(StructuredJSONFailed, match="no JSON object"):
            translator.structured_json_with_image("q", SCHEMA, PNG)


class TestTranslatorVerdict:
    def test_vision_verdict_probes_once_and_meters(self):
        seen = {}

        def create(**kwargs):
            content = kwargs["messages"][0]["content"]
            image, _ = _decode(content)
            seen["size"] = image.size
            return _completion("NONE", prompt_tokens=300)

        translator = _translator(create, verdict="strict")
        assert translator.vision_verdict() == "unsupported"
        assert translator.vision_verdict() == "unsupported"
        assert seen["size"] == CHALLENGE_SIZE
        assert translator.usage.requests == 1
        assert translator.usage.prompt == 300
        # the schema verdict is untouched by the image question
        assert translator.capabilities.verdicts == {"test-model": "strict"}


class TestTheDeadlineBoundsTheLoopsOwnRetries:
    """Port 260923 (Codex finding on port/260920-batch): the two retries
    `_vision_completion_text` makes itself -- a refused optional field
    dropped, a refused --no-thinking spelling advanced -- used to `continue`
    without looking at the clock, so several rejected spellings could each
    start a request after the pass's deadline. Past it they raise
    `QuestionTimedOut`, which the structure pass records as a lost question.
    """

    @pytest.fixture(autouse=True)
    def _fresh_controls(self, monkeypatch):
        from book_maker.translator import reasoning
        from book_maker.translator.reasoning import ThinkingOff

        monkeypatch.setattr(reasoning, "CONTROLS", ThinkingOff())

    @staticmethod
    def _rejecting(field):
        return BadRequestError(
            "Error code: 400",
            response=httpx.Response(400, request=REQUEST),
            body={"message": "Rejected", "param": field, "code": "unknown_parameter"},
        )

    def _no_thinking_translator(self, create):
        translator = _translator(create, verdict="strict")
        translator.no_thinking = True
        translator.api_base = "https://api.openai.com/v1"
        return translator

    def test_a_reasoning_spelling_refused_at_the_deadline_ends_the_question(
        self, monkeypatch
    ):
        clock = {"now": 500.0}
        monkeypatch.setattr("time.monotonic", lambda: clock["now"])

        def create(**kwargs):
            clock["now"] = 600.0  # the refusal arrives after the deadline
            raise self._rejecting("reasoning_effort")

        create = Mock(side_effect=create)
        translator = self._no_thinking_translator(create)
        with pytest.raises(QuestionTimedOut, match="deadline had passed"):
            translator.structured_json_with_image(
                "read it", SCHEMA, PNG, deadline=560.0
            )
        # one request; the next spelling is not tried past the deadline
        assert create.call_count == 1

    def test_a_reasoning_spelling_refused_in_time_is_negotiated(self, monkeypatch):
        monkeypatch.setattr("time.monotonic", lambda: 500.0)
        create = Mock(
            side_effect=[
                self._rejecting("reasoning_effort"),
                _completion('{"digits": "1"}'),
            ]
        )
        translator = self._no_thinking_translator(create)
        translator.structured_json_with_image("read it", SCHEMA, PNG, deadline=560.0)
        bodies = [c.kwargs["extra_body"] for c in create.call_args_list]
        assert bodies == [
            {"reasoning_effort": "none"},
            {"reasoning": {"effort": "none"}},
        ]

    def test_an_optional_field_refused_at_the_deadline_ends_the_question(
        self, monkeypatch
    ):
        clock = {"now": 500.0}
        monkeypatch.setattr("time.monotonic", lambda: clock["now"])

        def create(**kwargs):
            clock["now"] = 600.0
            raise _api_error(
                BadRequestError, 400, "unknown parameter: reasoning_effort"
            )

        create = Mock(side_effect=create)
        translator = _translator(create, verdict="strict")
        with pytest.raises(QuestionTimedOut, match="reasoning_effort"):
            translator.structured_json_with_image(
                "read it", SCHEMA, PNG, deadline=560.0
            )
        assert create.call_count == 1
        # the refusal is still remembered for the next question
        assert translator.capabilities.vision_unsent["test-model"] == {
            "reasoning_effort"
        }

    def test_without_a_deadline_the_retries_are_unbounded_as_before(self):
        create = Mock(
            side_effect=[
                _api_error(BadRequestError, 400, "unknown parameter: reasoning_effort"),
                _completion('{"digits": "1"}'),
            ]
        )
        translator = _translator(create, verdict="strict")
        assert translator.structured_json_with_image("read it", SCHEMA, PNG) == {
            "digits": "1"
        }
        assert create.call_count == 2

    def test_no_thinking_drops_the_convenience_effort_setting(self):
        create = Mock(return_value=_completion('{"digits": "1"}'))
        translator = self._no_thinking_translator(create)
        translator.structured_json_with_image("read it", SCHEMA, PNG)
        sent = create.call_args.kwargs
        # one instruction about reasoning, the flag's; not `low` beside `none`
        assert "reasoning_effort" not in sent
        assert sent["extra_body"] == {"reasoning_effort": "none"}
        assert sent["max_completion_tokens"] == 2000

    def test_without_the_flag_the_effort_setting_is_sent(self):
        create = Mock(return_value=_completion('{"digits": "1"}'))
        translator = _translator(create, verdict="strict")
        translator.structured_json_with_image("read it", SCHEMA, PNG)
        assert create.call_args.kwargs["reasoning_effort"] == "low"
