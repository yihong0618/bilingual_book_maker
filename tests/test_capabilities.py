"""Unit tests for the extracted capability layer.

`tests/test_chatgptapi_translator.py` covers how the translator *uses* a
verdict; these cover the layer itself — what the probe grades, which errors it
is allowed to swallow, and what the ledger remembers. The boundary matters:
capability is a property of the endpoint, so it has to be establishable
without a translator at all.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from book_maker.translator.capabilities import (
    STRUCTURED_FAILURE_THRESHOLD,
    CapabilityLedger,
    ModelUnavailable,
    ProbeDeferred,
    classify_bad_request,
    grade_probe_response,
    probe_model_route,
    probe_structured_output,
    verify_model_routes,
)

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


@pytest.fixture(autouse=True)
def _no_backoff_naps(monkeypatch):
    """Sit out none of the retry waits.

    The probe retries on a patient exponential wait, so a Mock that raises
    made the test wait for real — `test_a_broken_listing_does_not_break_the_
    verdict` spent 4s in `tenacity.nap`. Every stop here counts attempts,
    not seconds, so the verdicts and the attempt counts are unchanged; what
    the waits are is pinned in tests/test_translate_with_backoff.py. The
    `time.sleep` the concurrency tests below use is their own, not this one.
    """
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


def _completion(content, finish_reason="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _api_error(cls, status_code, message="boom"):
    return cls(
        message, response=httpx.Response(status_code, request=REQUEST), body=None
    )


def _client(create):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


class TestGrading:
    """Four verdicts, graded from the body alone."""

    @pytest.mark.parametrize(
        "content,verdict",
        [
            ('{"probe":"schema_ok"}', "strict"),
            ('{"probe":"ignored"}', "shape"),  # shape honored, enum dropped
            ('{"answer":"x"}', "json"),  # json mode, schema dropped
            ('{"probe":"schema_ok","extra":1}', "json"),
            ('{"probe":42}', "json"),  # right key, wrong type
            ("[1,2,3]", "json"),  # JSON but not an object
            ("ignored", "unsupported"),  # prose: response_format dropped
            ("", "unsupported"),
        ],
    )
    def test_verdicts(self, content, verdict):
        assert grade_probe_response(_completion(content)) == verdict

    def test_truncation_is_never_graded_as_support(self):
        """A cut-off answer proves nothing; the fragment may even parse later."""
        truncated = _completion('{"probe":"schema', finish_reason="length")
        assert grade_probe_response(truncated) == "unsupported"


class TestProbeErrorTaxonomy:
    """Which failures are answers about the endpoint, and which are not."""

    @pytest.mark.parametrize(
        "error",
        [
            _api_error(AuthenticationError, 401),
            _api_error(PermissionDeniedError, 403),
            _api_error(NotFoundError, 404),
        ],
    )
    def test_permanent_errors_propagate(self, error):
        # A typo'd key must not read as "this endpoint has no schema support"
        # and pin the whole run to the delimiter method.
        with pytest.raises(type(error)):
            probe_structured_output(_client(Mock(side_effect=error)), "m")

    @pytest.mark.parametrize(
        "error",
        [
            APIConnectionError(request=REQUEST),
            APITimeoutError(request=REQUEST),
            _api_error(RateLimitError, 429),
        ],
    )
    def test_outages_defer_rather_than_answer(self, error):
        with pytest.raises(ProbeDeferred):
            probe_structured_output(_client(Mock(side_effect=error)), "m")

    def test_ambiguous_failures_grade_as_unusable(self):
        """A 500 from a quirky local server: not usable, but not fatal either."""
        verdict = probe_structured_output(
            _client(Mock(side_effect=RuntimeError("boom"))), "m"
        )
        assert verdict.startswith("request rejected")

    def test_bad_request_is_a_capability_answer(self):
        verdict = probe_structured_output(
            _client(Mock(side_effect=_api_error(BadRequestError, 400))), "m"
        )
        assert verdict.startswith("request rejected")


class TestProbeRequest:
    def test_prompt_fights_the_schema(self):
        create = Mock(return_value=_completion('{"probe":"schema_ok"}'))

        probe_structured_output(_client(create), "m")

        request = create.call_args.kwargs
        prompt = request["messages"][0]["content"]
        # The expected value must never appear in the prompt: a server that
        # echoes the prompt cannot accidentally pass.
        assert "schema_ok" not in prompt
        assert "json" in prompt.lower()
        assert request["response_format"]["json_schema"]["strict"] is True
        # Exactly one capability under test: no sampling, no cap.
        assert "temperature" not in request
        assert "max_tokens" not in request


class TestLedger:
    def test_probe_runs_once_per_model(self):
        ledger = CapabilityLedger()
        probe = Mock(return_value="strict")

        assert ledger.ensure_verdict("a", probe) == "strict"
        assert ledger.ensure_verdict("a", probe) == "strict"
        assert ledger.ensure_verdict("b", probe) == "strict"

        assert probe.call_count == 2  # once for "a", once for "b"

    def test_one_probe_under_parallel_workers(self):
        """N parallel workers must cost one probe, not N."""
        ledger = CapabilityLedger()
        verdicts = []

        def slow_probe(model):
            time.sleep(0.05)  # wide enough for the others to pile up on the lock
            return "strict"

        probe = Mock(side_effect=slow_probe)
        threads = [
            threading.Thread(
                target=lambda: verdicts.append(ledger.ensure_verdict("a", probe))
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
            assert not t.is_alive()

        assert probe.call_count == 1
        assert verdicts == ["strict"] * 4  # and every worker got the answer

    def test_no_probe_means_no_support_and_no_request(self):
        ledger = CapabilityLedger()
        assert ledger.ensure_verdict("a", None) is False
        assert ledger.verdicts["a"] is False

    def test_deferred_probe_caches_nothing(self):
        ledger = CapabilityLedger()
        probe = Mock(side_effect=ProbeDeferred("gateway down"))

        assert ledger.ensure_verdict("a", probe) is False
        assert ledger.verdicts == {}  # nothing learned

        probe.side_effect = None
        probe.return_value = "strict"
        assert ledger.ensure_verdict("a", probe) == "strict"

    @pytest.mark.parametrize(
        "verdict,stored",
        [
            ("strict", "strict"),
            ("shape", "shape"),
            ("json", "json"),
            ("unsupported", False),
            ("request rejected: boom", False),
        ],
    )
    def test_record_normalizes_unusable_verdicts_to_false(self, verdict, stored):
        ledger = CapabilityLedger()
        ledger.record("a", verdict)
        assert ledger.verdicts["a"] == stored

    def test_demotion_takes_a_streak_not_one_bad_reply(self):
        """One garbled proxy reply must not cost a multi-hour run its schema."""
        ledger = CapabilityLedger()
        ledger.record("a", "strict")

        for _ in range(STRUCTURED_FAILURE_THRESHOLD - 1):
            ledger.demote("a", "bad json")
            assert ledger.verdicts["a"] == "strict"

        ledger.demote("a", "bad json")
        assert ledger.verdicts["a"] is False

    def test_success_clears_the_streak(self):
        ledger = CapabilityLedger()
        ledger.record("a", "strict")

        ledger.demote("a", "blip")
        ledger.note_success("a")
        ledger.demote("a", "blip")

        # The streak restarted, so one more failure has not demoted it.
        assert ledger.verdicts["a"] == "strict"

    def test_streaks_are_per_model(self):
        ledger = CapabilityLedger()
        ledger.record("a", "strict")
        ledger.record("b", "strict")

        ledger.demote("a", "blip")
        ledger.demote("b", "blip")

        assert ledger.verdicts["a"] == "strict"
        assert ledger.verdicts["b"] == "strict"


class TestTemperature:
    def test_default_temperature_is_never_sent(self):
        """Sending the API's own default changes nothing and 400s on o-series."""
        ledger = CapabilityLedger()
        assert ledger.sampling_kwargs("m", 1.0) == {}
        assert ledger.sampling_kwargs("m", None) == {}

    def test_explicit_temperature_is_sent(self):
        ledger = CapabilityLedger()
        assert ledger.sampling_kwargs("m", 0.3) == {"temperature": 0.3}

    def test_a_rejection_is_remembered_and_announced_once(self):
        ledger = CapabilityLedger()

        assert ledger.note_temperature_rejected("m") is True
        assert ledger.note_temperature_rejected("m") is False  # no repeat log
        assert ledger.sampling_kwargs("m", 0.3) == {}
        # ...but only for the model that refused.
        assert ledger.sampling_kwargs("other", 0.3) == {"temperature": 0.3}


def _route_client(create, ids=None):
    """A client whose chat route answers `create` and whose listing is `ids`.

    `ids=None` stands for the many OpenAI-compatible servers that implement
    `/chat/completions` and nothing else.
    """
    if ids is None:
        models = SimpleNamespace(list=Mock(side_effect=_api_error(NotFoundError, 404)))
    else:
        listing = SimpleNamespace(model_dump=lambda: {"data": [{"id": i} for i in ids]})
        models = SimpleNamespace(list=lambda: listing)
    return SimpleNamespace(
        models=models,
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )


class TestRouteProbe:
    """Whether an endpoint serves a model is asked of the route, not a list.

    The listing used to be the gate, and it is the wrong authority: gateways
    routinely serve a model they do not list, or list it under another id, so
    a model that works was refused before it was ever tried. These pin the
    replacement — one tiny request, and a listing kept only as a hint.
    """

    def test_the_probe_asks_one_thing_and_sets_nothing_else(self):
        create = Mock(return_value=_completion("PONG"))

        probe_model_route(_route_client(create), "test-model")

        request = create.call_args.kwargs
        assert request["model"] == "test-model"
        assert len(request["messages"]) == 1
        assert request["messages"][0]["role"] == "user"
        # This asks whether the endpoint routes a model and nothing else. A
        # schema, a temperature or a token cap would each let a second
        # question fail the first — the cap measurably so: OpenAI's gpt-5
        # family rejects max_tokens outright, so a capped probe confirmed
        # nothing about this fork's own default model.
        assert "response_format" not in request
        assert "temperature" not in request
        assert "max_tokens" not in request
        assert "max_completion_tokens" not in request

    def test_a_model_the_endpoint_answers_for_is_usable(self):
        # The answer's content is never read: what was asked is whether the
        # endpoint served this model at all.
        client = _route_client(Mock(return_value=_completion("something else")))
        assert probe_model_route(client, "test-model") is None

    def test_a_model_that_answers_but_is_not_listed_is_still_usable(self):
        """The whole point: the route is the authority, the listing is not."""
        client = _route_client(Mock(return_value=_completion("PONG")), ids=["other"])

        result = verify_model_routes(client, ["unlisted"])

        assert result["success"] is True
        assert result["available_models"] == ["unlisted"]

    def test_a_404_is_a_missing_model(self):
        create = Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))

        with pytest.raises(ModelUnavailable, match="ghost"):
            probe_model_route(_route_client(create), "ghost")

    def test_a_400_naming_the_model_is_a_missing_model(self):
        create = Mock(
            side_effect=_api_error(
                BadRequestError, 400, "The model `ghost` does not exist"
            )
        )

        with pytest.raises(ModelUnavailable, match="ghost"):
            probe_model_route(_route_client(create), "ghost")

    def test_a_parameter_400_that_says_does_not_exist_is_still_not_the_model(self):
        # "Parameter 'max_tokens' does not exist for model 'gpt-5'" names the
        # model *and* carries a not-found phrase, and is about neither: the
        # field the endpoint blames settles it, and where it does not say,
        # the word "parameter" does.
        create = Mock(
            side_effect=_api_error(
                BadRequestError,
                400,
                "Parameter 'max_tokens' does not exist for model 'gpt-5'",
            )
        )

        with pytest.raises(BadRequestError):
            probe_model_route(_route_client(create), "gpt-5")

    def test_the_field_the_endpoint_blames_outranks_the_prose(self):
        # an OpenAI-shaped error carries `'param': 'max_tokens'` beside the
        # message; reading the prose instead is how a parameter complaint
        # gets mistaken for a missing model
        create = Mock(
            side_effect=_api_error(
                BadRequestError,
                400,
                "{'message': \"no such model 'gpt-5'\", 'param': 'max_tokens'}",
            )
        )

        with pytest.raises(BadRequestError):
            probe_model_route(_route_client(create), "gpt-5")

    def test_a_param_of_model_is_the_model(self):
        create = Mock(
            side_effect=_api_error(
                BadRequestError,
                400,
                "{'message': 'unsupported', 'param': 'model'}",
            )
        )

        with pytest.raises(ModelUnavailable):
            probe_model_route(_route_client(create), "ghost")

    def test_a_400_about_a_parameter_is_not_a_missing_model(self):
        # "'max_tokens' is not supported with gpt-5" names the model too, and
        # condemning it for that sends the user hunting for a typo that is
        # not there.
        create = Mock(
            side_effect=_api_error(
                BadRequestError,
                400,
                "Unsupported parameter: 'max_tokens' is not supported with gpt-5",
            )
        )

        with pytest.raises(BadRequestError):
            probe_model_route(_route_client(create), "gpt-5")

    @pytest.mark.parametrize(
        "error",
        [
            _api_error(AuthenticationError, 401, "bad key"),
            _api_error(PermissionDeniedError, 403, "no access"),
            _api_error(RateLimitError, 429, "slow down"),
            APIConnectionError(request=REQUEST),
        ],
    )
    def test_other_failures_are_never_reported_as_a_missing_model(self, error):
        create = Mock(side_effect=error)

        with pytest.raises(type(error)):
            probe_model_route(_route_client(create), "test-model")


class TestRouteVerification:
    """The list-level answer: which models survive, in the order given."""

    def test_available_models_keep_the_requested_order(self):
        """Rotation follows what the user typed, not set/hash order."""
        client = _route_client(
            Mock(return_value=_completion("PONG")), ids=["c", "b", "a"]
        )

        result = verify_model_routes(client, ["a", "b", "c"])

        assert result["success"] is True
        assert result["available_models"] == ["a", "b", "c"]

    def test_every_named_model_is_probed(self):
        create = Mock(return_value=_completion("PONG"))

        verify_model_routes(_route_client(create, ids=[]), ["a", "b", "c"])

        assert [c.kwargs["model"] for c in create.call_args_list] == ["a", "b", "c"]

    def test_a_partial_list_narrows_to_what_answers(self):
        client = _route_client(
            Mock(
                side_effect=[
                    _completion("PONG"),
                    _api_error(NotFoundError, 404, "no such model"),
                    _completion("PONG"),
                ]
            ),
            ids=["a", "c"],
        )

        result = verify_model_routes(client, ["a", "ghost", "c"])

        assert result["success"] is True
        assert result["available_models"] == ["a", "c"]
        assert result["unavailable_models"] == ["ghost"]

    def test_a_model_only_a_transport_error_stood_between_is_kept(self, capsys):
        """Not an answer about the model, so it is not evidence against it."""
        client = _route_client(Mock(side_effect=APIConnectionError(request=REQUEST)))

        result = verify_model_routes(client, ["m"])

        assert result["success"] is True
        assert result["available_models"] == ["m"]
        assert "could not confirm" in capsys.readouterr().out

    def test_nothing_usable_fails_loud_and_quotes_the_listing(self, capsys):
        client = _route_client(
            Mock(side_effect=_api_error(NotFoundError, 404, "no such model")),
            ids=["real-model"],
        )

        result = verify_model_routes(client, ["a", "b"])

        assert result["success"] is False
        assert result["available_models"] == []
        assert result["unavailable_models"] == ["a", "b"]
        # the one place the listing is still useful: a hint in the refusal
        assert result["api_models"] == ["real-model"]
        assert "real-model" in capsys.readouterr().out

    def test_an_endpoint_with_no_listing_still_reports_the_refusal(self, capsys):
        """A server with only /chat/completions must still say what happened."""
        client = _route_client(
            Mock(side_effect=_api_error(NotFoundError, 404, "no such model"))
        )

        result = verify_model_routes(client, ["ghost"])

        assert result["success"] is False
        assert result["api_models"] is None
        assert "ghost" in capsys.readouterr().out

    def test_a_broken_listing_does_not_break_the_verdict(self):
        """The hint is best effort; it must not turn a refusal into a crash."""
        client = SimpleNamespace(
            models=SimpleNamespace(list=Mock(side_effect=RuntimeError("boom"))),
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=Mock(side_effect=_api_error(NotFoundError, 404, "nope"))
                )
            ),
        )

        result = verify_model_routes(client, ["ghost"])

        assert result["success"] is False
        assert result["api_models"] is None


class TestBadRequestClassification:
    @pytest.mark.parametrize(
        "message,kind",
        [
            ("Unsupported value: 'temperature' does not support 0.3", "temperature"),
            ("Invalid parameter: 'response_format'", "schema"),
            ("json_schema is not supported", "schema"),
            ("context length exceeded", "other"),
        ],
    )
    def test_classification(self, message, kind):
        # Misreading a temperature 400 as "no schema support" demotes the model
        # for the rest of the run and hides the real cause from the user.
        assert classify_bad_request(_api_error(BadRequestError, 400, message)) == kind


class TestProbesCarryTheRunsExtraBody:
    """A verdict earned on a request shape the run never sends is a verdict
    about a different endpoint. Both probes take `--extra_body`."""

    def test_the_schema_probe_sends_it(self):
        create = Mock(return_value=_completion('{"answer": "ignored"}'))
        probe_structured_output(
            _client(create), "m", extra_body={"enable_thinking": False}
        )

        assert create.call_args.kwargs["extra_body"] == {"enable_thinking": False}

    def test_the_route_probe_sends_it(self):
        create = Mock(return_value=_completion("PONG"))
        probe_model_route(
            _route_client(create), "m", extra_body={"enable_thinking": False}
        )

        assert create.call_args.kwargs["extra_body"] == {"enable_thinking": False}

    def test_no_extra_body_sends_none_not_an_empty_object(self):
        # an endpoint that rejects unknown keys should see the request it
        # would have seen before the flag existed
        create = Mock(return_value=_completion("PONG"))
        probe_model_route(_route_client(create), "m")

        assert create.call_args.kwargs["extra_body"] is None
