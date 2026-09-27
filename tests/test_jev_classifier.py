"""The jev backend: TypeSafe's System One classifier behind `Classifier`.

Packet F (260923, owner 22:50 "build the Jev backend now"; 23:10: selected
by the classify flags only, no --jev-* flag). Request and answer shapes are
docs.typesafe.ai/api as read 260923. No network: the transport is injected.
"""

import pytest

from book_maker.classifier import (
    JEV_MIN_CONFIDENCE,
    JEV_PATH,
    Classifier,
    JevBackend,
    JevFatal,
    NoBackend,
    Question,
)
from book_maker.endpoints import (
    EndpointChoice,
    build_classifier,
    resolve_classify_endpoint,
    run_choice,
)


class Response:
    def __init__(self, status, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def _answer(choice, probabilities):
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": 0.5,
    }


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, url, json, headers, timeout):
        self.sent.append((url, json, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _question(**kw):
    kw.setdefault("prompt", "PAGE PROMPT")
    kw.setdefault(
        "candidates",
        {"a": ("translate", "skip", "unsure"), "b": ("translate", "skip", "unsure")},
    )
    kw.setdefault("abstain", "unsure")
    kw.setdefault("per_candidate", {"a": "PROMPT A", "b": "PROMPT B"})
    return Question(**kw)


def _ok(answers, usage=None):
    return Response(
        200,
        {
            "model": "jev-1.13.0",
            "answers": answers,
            "usage": usage or {"input_tokens": 120, "output_tokens": 8},
        },
    )


def _backend(transport, sleeps=None, lines=None):
    return JevBackend(
        "jev-latest",
        "jev-test-key-000",
        "https://api.typesafe.ai",
        post=transport,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        log=(lines.append if lines is not None else lambda line: None),
    )


class TestTheRequest:
    def test_one_choice_per_candidate_with_the_caller_s_text(self):
        transport = Transport(
            _ok(
                {
                    "a": _answer("skip", {"translate": 0.03, "skip": 0.97}),
                    "b": _answer("translate", {"translate": 0.97, "skip": 0.03}),
                }
            )
        )
        c = Classifier(None, "jev-latest", backends=[_backend(transport)])
        answer = c.ask(_question())
        ((url, body, headers),) = transport.sent
        assert url == "https://api.typesafe.ai" + JEV_PATH
        assert headers["Authorization"] == "Bearer jev-test-key-000"
        assert body["model"] == "jev-latest"
        assert body["state"] == "PAGE PROMPT"
        assert body["questions"]["a"] == {
            "type": "choice",
            "instructions": "PROMPT A",
            # no abstain option: abstaining is what a flat distribution means
            "criteria": {"translate": None, "skip": None},
        }
        assert answer.backend == "jev"
        assert answer.values == {"a": "skip", "b": "translate"}
        assert answer.confidence == {"a": 0.97, "b": 0.97}

    def test_the_shared_context_is_the_state_and_criteria_describe_options(self):
        """Packet J (260924): the state is sent once per request, each
        question adds only its pointer and the caller's option wording."""
        transport = Transport(
            _ok(
                {
                    "a": _answer("skip", {"translate": 0.03, "skip": 0.97}),
                    "b": _answer("translate", {"translate": 0.9, "skip": 0.1}),
                }
            )
        )
        question = _question(
            context="1. sig a\n2. sig b",
            criteria={"translate": "book content", "skip": "apparatus"},
        )
        Classifier(None, "jev-latest", backends=[_backend(transport)]).ask(question)
        ((_url, body, _headers),) = transport.sent
        assert body["state"] == "1. sig a\n2. sig b"
        assert body["questions"]["b"] == {
            "type": "choice",
            "instructions": "PROMPT B",
            # abstain ("unsure") is never offered, described or not
            "criteria": {"translate": "book content", "skip": "apparatus"},
        }

    def test_without_a_fallback_a_low_confidence_answer_is_the_abstain(self):
        # a caller that sets no fallback (the role pass) keeps F's behaviour
        # PIN (owner 260924, eval EJ, docs/260924-eval-JEV_CONFIDENCE_THRESHOLD.md):
        # the gate is 0.95 on probabilities[choice], the cost minimum over 662
        # corpus signatures against gpt-5.6-luna at 10:1; on two options the
        # probability is never below 0.5, so a lower value would be inert.
        assert JEV_MIN_CONFIDENCE == 0.95
        transport = Transport(
            _ok(
                {
                    "a": _answer("skip", {"translate": 0.06, "skip": 0.94}),
                    "b": _answer("skip", {"translate": 0.05, "skip": 0.95}),
                }
            )
        )
        c = Classifier(None, "jev-latest", backends=[_backend(transport)])
        answer = c.ask(_question())
        assert answer.values["a"] == "unsure"  # 0.94 < 0.95
        assert answer.values["b"] == "skip"  # 0.95 is not below

    def test_usage_is_metered_as_jev(self):
        transport = Transport(
            _ok({"a": _answer("skip", {"skip": 1.0, "translate": 0.0})}),
        )
        backend = _backend(transport)
        c = Classifier(None, "jev-latest", backends=[backend])
        answer = c.ask(_question(candidates={"a": ("translate", "skip")}))
        assert backend.usage.requests == 1
        assert (backend.usage.prompt, backend.usage.completion) == (120, 8)
        assert answer.usage["prompt_tokens"] == 120
        assert c.usage is backend.usage

    def test_a_single_option_is_answered_without_asking(self):
        transport = Transport()
        c = Classifier(None, "jev-latest", backends=[_backend(transport)])
        answer = c.ask(_question(candidates={"a": ("keep", "unsure")}))
        assert answer.values == {"a": "keep"}
        assert transport.sent == []


class TestPatience:
    def test_a_429_is_waited_out_honouring_retry_after(self):
        sleeps, lines = [], []
        transport = Transport(
            Response(429, headers={"retry-after": "7"}, text="slow down"),
            Response(529, text="overloaded"),
            ConnectionError("reset"),
            _ok({"a": _answer("skip", {"skip": 0.97, "translate": 0.03})}),
        )
        backend = _backend(transport, sleeps, lines)
        answer = Classifier(None, "j", backends=[backend]).ask(
            _question(candidates={"a": ("translate", "skip")})
        )
        assert answer.values == {"a": "skip"}
        assert sleeps == [7.0, 4, 8]
        assert len(lines) == 3 and "HTTP 429" in lines[0]

    def test_no_attempt_cap(self):
        # PIN (owner ruling 260907, AGENTS.md "Retry philosophy"): patient
        # waits capped per wait, never in attempts.
        sleeps = []
        transport = Transport(
            *[Response(503, text="down")] * 40,
            _ok({"a": _answer("skip", {"skip": 0.97, "translate": 0.03})}),
        )
        backend = _backend(transport, sleeps)
        Classifier(None, "j", backends=[backend]).ask(
            _question(candidates={"a": ("translate", "skip")})
        )
        assert len(sleeps) == 40
        assert max(sleeps) == backend.wait_cap

    @pytest.mark.parametrize("status", [400, 401, 403, 422])
    def test_the_request_s_own_fault_is_fatal_at_once(self, status):
        sleeps = []
        transport = Transport(
            Response(status, text="Invalid API key jev-test-key-000"),
        )
        with pytest.raises(JevFatal) as err:
            Classifier(None, "j", backends=[_backend(transport, sleeps)]).ask(
                _question()
            )
        assert sleeps == []
        assert str(status) in str(err.value)
        assert "jev-test-key-000" not in str(err.value)


class TestCoverage:
    """Codex review 260923: jev takes a question only when every candidate
    has a prompt of its own; the page prompt is never substituted."""

    def test_a_partial_per_candidate_map_is_not_taken(self):
        backend = _backend(Transport())
        partial = _question(per_candidate={"a": "PROMPT A"})
        assert backend.can(partial) is False
        assert "1 candidate(s) have no prompt" in backend.why_not(partial)
        assert backend.can(_question(per_candidate=None)) is False
        assert backend.can(_question()) is True

    def test_the_classifier_says_why_rather_than_substituting(self):
        # a request, were one sent, is refused at once (no retry loop)
        backend = _backend(Transport(Response(401, text="unauthorised")))
        classifier = Classifier(None, "jev-latest", backends=[backend])
        with pytest.raises(NoBackend, match="no prompt of their own"):
            classifier.ask(_question(per_candidate={"a": "PROMPT A"}))


class TestDispatch:
    def test_an_image_question_is_no_backend(self):
        c = Classifier(None, "j", backends=[_backend(Transport())])
        with pytest.raises(NoBackend, match="jev reads text only"):
            c.ask(_question(image_png=b"png"))

    def test_classify_model_jev_builds_a_jev_only_classifier(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "jev-test-key-000")
        options = type(
            "O",
            (),
            {"classify_model": "jev", "classify_base_url": None, "classify_key": None},
        )()
        run = run_choice("gpt-run", "https://api.openai.com/v1", "sk", "openai")
        choice = resolve_classify_endpoint(options, run, None)
        c = build_classifier(choice, object(), options, "Simplified Chinese")
        assert list(c.backends) == ["jev"]
        assert c.separate and c.source == "cli"
        assert c.describe("jev") == (
            "jev-latest at https://api.typesafe.ai via jev (cli)"
        )

    def test_the_run_s_own_choice_asks_the_run_s_translator(self):
        class T:
            model = "m"

            def supports_structured_json(self):
                return True

            def structured_json(self, *a, **k):
                return {}

        run = run_choice("m", "", "sk", "openai")
        translator = T()
        c = build_classifier(run, translator, None, "English")
        assert c.translator is translator and not c.separate

    def test_a_named_model_gets_a_translator_of_its_own(self):
        choice = EndpointChoice(
            "gpt-5.6-luna", "https://api.openai.com/v1", "sk-x", "openai", "cli"
        )
        c = build_classifier(choice, object(), None, "English")
        assert c.separate
        assert c.translator.model == "gpt-5.6-luna"


class TestThePlanClassifierIsLean:
    """Packet J (260924): the plan classifier's jev request carries the
    signatures once as the state and a one-line pointer per signature, not
    the instruction paragraph per signature (F's mapping sent 14.4k prompt
    tokens for 31 signatures against the schema arm's 7.6k)."""

    PAGE = [
        {"key": f"block:p.c{i}", "units": 3, "chars": 90, "samples": [f"s{i}"]}
        for i in range(11)
    ] + [{"key": "inline:span.x", "units": 2, "chars": 9, "samples": ["Fig. 1"]}]

    def _body(self):
        from book_maker.loader.classify import model as model_entry

        question = model_entry.page_question(self.PAGE)
        body, settled = _backend(Transport()).request_body(question)
        return model_entry, question, body, settled

    def test_the_paragraph_is_not_sent_and_the_signatures_are_sent_once(self):
        import json

        model_entry, question, body, settled = self._body()
        wire = json.dumps(body, ensure_ascii=False)
        assert settled == {}
        assert body["state"] == model_entry.build_context(self.PAGE)
        assert "You are preparing a bilingual EPUB" not in wire
        assert wire.count("Sample: s3") == 1
        # F's mapping: the page prompt as the state plus the one-signature
        # prompt per question; on this page the lean request is well under
        # two thirds of it even with tiny samples (the paragraph dominates)
        old = len(model_entry.build_prompt(self.PAGE)) + sum(
            len(model_entry.build_prompt([c])) for c in self.PAGE
        )
        assert len(wire) < old * 0.6

    def test_each_question_points_at_its_numbered_signature(self):
        model_entry, _q, body, _s = self._body()
        first = body["questions"]["block:p.c0"]["instructions"]
        assert first.startswith('Signature 1 ("block:p.c0"):')
        assert '1. "block:p.c0"' in body["state"]
        assert "markup inside a sentence" not in first
        inline = body["questions"]["inline:span.x"]["instructions"]
        assert inline.startswith('Signature 12 ("inline:span.x"):')
        assert inline.endswith(model_entry.POINTER_INLINE)

    def test_the_criteria_are_the_audited_prompt_s_own_words(self):
        """The descriptions are copied from `build_prompt`, which stays what
        the schema and session backends send (the audited prompt)."""
        model_entry, _q, body, _s = self._body()
        prompt = model_entry.build_prompt(self.PAGE)
        for label, words in model_entry.CRITERIA.items():
            assert f'Answer "{label}" for {words}.' in prompt
        assert body["questions"]["block:p.c0"]["criteria"] == model_entry.CRITERIA


class TestTheAsymmetricGate:
    """Packet J (owner 260924, "we need a best guess for everything"): the
    fallback answer is accepted at any confidence; another answer below
    the gate becomes the fallback, its confidence kept for the audit."""

    def _ask(self, answers, **kw):
        transport = Transport(_ok(answers))
        classifier = Classifier(None, "jev-latest", backends=[_backend(transport)])
        return classifier.ask(_question(fallback="translate", **kw))

    def test_a_low_confidence_fallback_is_accepted(self):
        answer = self._ask(
            {
                "a": _answer("translate", {"translate": 0.3, "skip": 0.2, "x": 0.5}),
                "b": _answer("translate", {"translate": 0.9, "skip": 0.1}),
            }
        )
        assert answer.values == {"a": "translate", "b": "translate"}
        assert answer.confidence["a"] == 0.3
        assert answer.raw.fell_back == {}

    def test_a_low_confidence_skip_becomes_the_fallback_with_its_confidence(self):
        answer = self._ask(
            {
                "a": _answer("skip", {"translate": 0.3, "skip": 0.4, "x": 0.3}),
                "b": _answer("skip", {"translate": 0.04, "skip": 0.96}),
            }
        )
        assert answer.values == {"a": "translate", "b": "skip"}
        assert answer.confidence == {"a": 0.4, "b": 0.96}
        assert answer.raw.fell_back == {"a": "skip"}

    # Codex (packet J review, 260924): a row without a choice was scored 0
    # and gated into the fallback, so a malformed reply read as a plan verdict.
    def test_a_row_without_a_choice_stays_unanswered(self):
        answer = self._ask(
            {
                "a": {"type": "choice", "confidence": 0.9},
                "b": _answer("skip", {"translate": 0.04, "skip": 0.96}),
            }
        )
        assert answer.values == {"b": "skip"}
        assert "a" not in answer.confidence
        assert answer.raw.fell_back == {}

    def test_a_choice_never_offered_stays_unanswered(self):
        answer = self._ask(
            {
                # the abstain label is never offered to jev, so never accepted
                "a": _answer("unsure", {"translate": 0.1, "skip": 0.1, "unsure": 0.8}),
                "b": _answer("skip", {"translate": 0.04, "skip": 0.96}),
                # an id never asked is dropped whatever it says
                "c": _answer("skip", {"translate": 0.04, "skip": 0.96}),
            }
        )
        assert answer.values == {"b": "skip"}
        assert "a" not in answer.raw and "c" not in answer.raw
        assert answer.raw.fell_back == {}

    def test_a_bare_label_row_stays_unanswered(self):
        # a scalar "skip" carried neither a choice nor a probability; it used
        # to be copied into the reply ungated
        answer = self._ask(
            {"a": "skip", "b": _answer("skip", {"translate": 0.04, "skip": 0.96})}
        )
        assert answer.values == {"b": "skip"}
        assert "a" not in answer.raw
        assert "a" not in answer.confidence
        assert answer.raw.fell_back == {}

    def test_a_probability_container_that_is_not_an_object_stays_unanswered(self):
        answer = self._ask(
            {
                "a": {"type": "choice", "choice": "skip", "probabilities": [0.9]},
                "b": {"type": "choice", "choice": "skip", "probabilities": "0.9"},
            }
        )
        assert answer.values == {}
        assert answer.confidence == {}

    @pytest.mark.parametrize(
        "value",
        [-0.1, 1.7, float("nan"), float("inf"), -float("inf"), True, "0.9", 10**400],
    )
    def test_a_probability_outside_the_unit_interval_stays_unanswered(self, value):
        answer = self._ask(
            {
                "a": {
                    "type": "choice",
                    "choice": "skip",
                    "probabilities": {"skip": value},
                }
            }
        )
        assert answer.values == {}
        assert answer.confidence == {}

    @pytest.mark.parametrize("value", [0, 1, 0.0, 1.0])
    def test_the_unit_interval_s_ends_are_accepted(self, value):
        answer = self._ask(
            {
                "a": {
                    "type": "choice",
                    "choice": "translate",
                    "probabilities": {"translate": value},
                }
            }
        )
        assert answer.values == {"a": "translate"}
        assert answer.confidence == {"a": float(value)}

    def test_a_choice_without_its_probability_stays_unanswered(self):
        # the server's `confidence` is never read in its place: on the
        # official Jev it is not the chosen option's probability
        answer = self._ask(
            {
                "a": {"type": "choice", "choice": "skip", "confidence": 0.95},
                "b": {
                    "type": "choice",
                    "choice": "skip",
                    "confidence": 0.95,
                    "probabilities": {"translate": "0.1", "skip": "0.9"},
                },
            }
        )
        assert answer.values == {}
        assert answer.confidence == {}

    def test_the_role_pass_s_fallback_is_its_abstain_label(self):
        # the role pass sets `abstain` and no fallback (it never reaches jev
        # today: it asks with an image); a text question shaped like it
        # falls back to abstain, never to another role
        roles = ("text", "title", "abstain")
        transport = Transport(
            _ok(
                {
                    "0": _answer("title", {"text": 0.6, "title": 0.4}),
                    "1": _answer("title", {"text": 0.03, "title": 0.97}),
                }
            )
        )
        classifier = Classifier(None, "jev-latest", backends=[_backend(transport)])
        answer = classifier.ask(
            Question(
                prompt="P",
                candidates={"0": roles, "1": roles},
                abstain="abstain",
                per_candidate={"0": "zero", "1": "one"},
            )
        )
        assert answer.values == {"0": "abstain", "1": "title"}

    def test_the_environment_overrides_the_gate(self, monkeypatch):
        monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", "0.85")
        answer = self._ask(
            {
                "a": _answer("skip", {"translate": 0.2, "skip": 0.8}),
                "b": _answer("skip", {"translate": 0.1, "skip": 0.9}),
            }
        )
        assert answer.values == {"a": "translate", "b": "skip"}
        monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", " ")
        assert _backend(Transport()).min_confidence == JEV_MIN_CONFIDENCE

    @pytest.mark.parametrize("raw", ["abc", "1.5", "-0.1", "nan", "0,7"])
    def test_a_malformed_override_stops_when_the_backend_is_built(
        self, monkeypatch, raw
    ):
        monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", raw)
        with pytest.raises(SystemExit, match="BBM_JEV_MIN_CONFIDENCE must be"):
            _backend(Transport())

    def test_the_plan_classifier_records_what_jev_chose_below_the_gate(
        self, monkeypatch
    ):
        from book_maker.loader.classify import classify_plan
        from book_maker.loader.classify import model as model_entry

        monkeypatch.setattr(model_entry, "gather_candidates", lambda ledger: ledger)
        page = [
            {"key": "block:p.a", "units": 1, "chars": 9, "samples": ["one"]},
            {"key": "block:p.b", "units": 1, "chars": 9, "samples": ["12"]},
        ]
        assert model_entry.page_question(page).fallback == "translate"
        transport = Transport(
            _ok(
                {
                    "block:p.a": _answer("skip", {"translate": 0.45, "skip": 0.55}),
                    "block:p.b": _answer("skip", {"translate": 0.05, "skip": 0.95}),
                }
            )
        )
        monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", "0.6")
        classifier = Classifier(None, "jev-latest", backends=[_backend(transport)])
        decisions, _ = classify_plan(page, classifier)
        assert decisions == {
            "block:p.a": (
                "translate",
                "unnamed (jev verdict skip at confidence 0.55, below the gate: "
                "translate)",
            ),
            "block:p.b": ("skip", "unnamed (jev verdict skip, confidence 0.95)"),
        }


class TestTheTwoReplyShapes:
    """Packet J: a Simple-Jev-shaped reply (docs/260924-jev-alternative-format.md)
    and the Vercel AI Gateway's recorded reply (copied from jev-calculator's
    `test/fixtures/gateway-response.recorded.json`) parse alike; the
    gateway's `provider_metadata` is ignored."""

    def test_a_simple_jev_reply(self):
        payload = {
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": "billing",
                    "confidence": 0.9999,
                    "probabilities": {
                        "billing": 0.9999,
                        "technical": 0.00003,
                        "account": 0.0001,
                    },
                }
            },
            "usage": {"input_tokens": 434, "output_tokens": 3},
        }
        backend = _backend(Transport(Response(200, payload)))
        answer = Classifier(None, "m", backends=[backend]).ask(
            Question(
                prompt="P",
                candidates={"route": ("billing", "technical", "account")},
                per_candidate={"route": "Which team handles this?"},
            )
        )
        assert answer.values == {"route": "billing"}
        assert answer.confidence == {"route": 0.9999}
        assert answer.usage["prompt_tokens"] == 434
        assert (backend.usage.prompt, backend.usage.completion) == (434, 3)

    def test_the_recorded_gateway_reply(self):
        import json
        from pathlib import Path

        fixture = Path(__file__).parent / "fixtures"
        payload = json.loads(
            (fixture / "jev_gateway_response.recorded.json").read_text()
        )
        options = tuple(payload["answers"]["next_char"]["probabilities"])
        backend = _backend(Transport(Response(200, payload)))
        answer = Classifier(None, "typesafe-ai/jev", backends=[backend]).ask(
            Question(
                prompt="P",
                candidates={"next_char": options},
                per_candidate={"next_char": "Next character?"},
            )
        )
        assert answer.values == {"next_char": "5"}
        # the chosen option's probability (0.76), not the derived
        # `confidence` (0.73) nor provider_metadata's copy of it
        assert answer.confidence == {"next_char": 0.76}
        assert (backend.usage.prompt, backend.usage.completion) == (378, 102)
        assert answer.unknown_ids == ()
