"""Classification through any translator, not just the OpenAI-shaped one.

The contract these tests pin down: a translator that can answer one prompt can
classify. The endpoint does not have to honor `response_format`, does not have
to compile a schema, and may wrap its answer in prose — the lint decides
whether the job was done, and the ladder and the divide-and-resend below it
keep asking in easier ways until it is, or fail loudly.
"""

import json
import pathlib
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from anthropic import BadRequestError as AnthropicBadRequest
from google.genai import errors as genai_errors

from book_maker.structured import (
    RungRejected,
    StructuredJSONFailed,
    extract_json_object,
    render_schema_for_prompt,
    run_rungs,
    unwrap_schema_echo,
)
from book_maker.translator.base_translator import Base
from book_maker.translator.claude_translator import Claude, _sdk_base_url
from book_maker.translator.gemini_translator import Gemini, _openapi_schema
from book_maker.translator.groq_translator import GroqClient
from book_maker.loader.classify.model import (
    PAGE_SIZE,
    PlanClassifyError,
    build_schema,
    classify_plan,
    lint_verdicts,
)
from book_maker.loader.ledger import Ledger

# ----------------------------------------------------------------- fixtures


def key_of(signature):
    """Ledger keys are scoped; the fixtures name bare signatures."""
    return signature if signature.startswith("block:") else f"block:{signature}"


def _ledger_with(signatures):
    """A ledger whose open questions are exactly `signatures`."""
    ledger = Ledger()
    for signature in signatures:
        for _ in range(4):
            ledger.add_occurrence("block", signature, 9, "GILGAMESH")
    return ledger.finalize(9 * 4 * max(1, len(signatures)))


def _verdicts_for(signatures, verdict="skip"):
    return {
        key_of(sig): {"content_type": "running head", "verdict": verdict}
        for sig in signatures
    }


def _asked_signatures(schema):
    return list(schema["schema"]["required"])


class FakeLLM(Base):
    """A translator with nothing but a prompt channel — the floor case.

    `replies` is a callable taking the prompt and returning raw text, exactly
    as a chat endpoint would: fenced, prose-wrapped, echoed schema and all.
    """

    def __init__(self, replies):
        super().__init__("key", "zh-hans")
        self.model = "fake-llm"
        self.replies = replies
        self.prompts = []

    def rotate_key(self):
        pass

    def translate(self, text):
        raise AssertionError("classification must never translate")

    def _chat_completion(self, prompt, model=None):
        self.prompts.append(prompt)
        return self.replies(prompt)


def _answers(signatures, verdict="skip"):
    """A reply function answering every signature the prompt asks about."""

    def reply(prompt):
        asked = [s for s in signatures if f'"{key_of(s)}"' in prompt]
        return json.dumps(_verdicts_for(asked, verdict))

    return reply


# ------------------------------------------------------- the shared plumbing


class TestJSONRecovery:
    @pytest.mark.parametrize(
        "reply,expected",
        [
            ('{"a": 1}', {"a": 1}),
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ("Sure!\n{" '"a": 1}\nHope that helps.', {"a": 1}),
            ("not json at all", None),
            ("", None),
        ],
    )
    def test_extraction_survives_fences_and_prose(self, reply, expected):
        assert extract_json_object(reply) == expected

    def test_schema_echo_is_unwrapped(self):
        # measured on gpt-5.6-luna, 3 of 15 trials: the answers came back
        # nested under the schema envelope, which parsed fine and linted to a
        # full page of "unsure" without a word of complaint
        echoed = {
            "type": "object",
            "properties": {"p.header": {"content_type": "head", "verdict": "skip"}},
            "required": ["p.header"],
            "additionalProperties": False,
        }
        assert unwrap_schema_echo(echoed) == {
            "p.header": {"content_type": "head", "verdict": "skip"}
        }

    def test_the_whole_request_envelope_is_unwrapped_too(self):
        # build_schema returns {"name":…, "strict":…, "schema":{…}}; a model
        # echoing what it was *sent* hands back the envelope, not just its body
        answers = {"p.header": {"content_type": "head", "verdict": "skip"}}
        echoed = {
            "name": "plan_signature_classification",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": answers,
                "required": ["p.header"],
                "additionalProperties": False,
            },
        }
        assert unwrap_schema_echo(echoed) == answers

    def test_a_real_answer_is_never_unwrapped(self):
        answer = {"p.header": {"content_type": "head", "verdict": "skip"}}
        assert unwrap_schema_echo(answer) == answer

    def test_an_answer_whose_keys_look_schema_ish_is_left_alone(self):
        # signatures are tag names; one could collide with a schema keyword
        answer = {"title": {"content_type": "heading", "verdict": "translate"}}
        assert unwrap_schema_echo(answer) == answer

    def test_an_echoed_schema_definition_stays_rejectable(self):
        # echoing the *definitions* rather than answers must not become an
        # answer: unwrapping yields property specs, which the lint refuses
        echoed = {
            "type": "object",
            "properties": {"p.header": {"type": "object", "description": "..."}},
        }
        cands = [{"key": key_of("p.header"), "units": 1, "chars": 9, "samples": ["X"]}]
        _, answered = lint_verdicts(unwrap_schema_echo(echoed), cands)
        assert answered == set()


class TestPromptedSchema:
    def _rendered(self):
        cands = [
            {"key": key_of("p.header"), "units": 9, "chars": 81, "samples": ["G"]},
            {"key": key_of("td.no"), "units": 4, "chars": 8, "samples": ["No"]},
        ]
        return render_schema_for_prompt(build_schema(cands))

    def test_the_schema_itself_is_never_serialized(self):
        # handing a model a JSON Schema and asking for "this exact shape" is
        # what produced the echo above
        rendered = self._rendered()
        assert "additionalProperties" not in rendered
        assert '"type": "object"' not in rendered

    def test_an_example_instance_carries_the_shape(self):
        rendered = self._rendered()
        assert "content_type" in rendered and "verdict" in rendered
        assert '"block:p.header"' in rendered and '"block:td.no"' in rendered

    def test_the_enum_is_stated_but_not_demonstrated(self):
        # showing one enum value filled in would anchor every verdict on it
        rendered = self._rendered()
        assert "translate" in rendered and "skip" in rendered
        assert '"verdict": "translate"' not in rendered


class TestLadderDescent:
    def _rungs(self, *behaviors):
        calls = []

        def make(name, behavior):
            def rung():
                calls.append(name)
                if isinstance(behavior, Exception):
                    raise behavior
                return behavior

            return (name, rung)

        return [make(f"r{i}", b) for i, b in enumerate(behaviors)], calls

    def test_a_refused_rung_descends(self):
        rungs, calls = self._rungs(RungRejected("400"), {"ok": 1})
        assert run_rungs(rungs) == {"ok": 1}
        assert calls == ["r0", "r1"]

    def test_an_unusable_answer_descends(self):
        # the lint, not the transport, is what says the page was not answered
        rungs, calls = self._rungs({"wrong": 1}, {"ok": 1})
        assert run_rungs(rungs, accept=lambda o: "ok" in o) == {"ok": 1}
        assert calls == ["r0", "r1"]

    def test_the_last_parsed_answer_survives_an_exhausted_ladder(self):
        # a partial answer is worth more than a discarded page: the caller
        # lints it and re-asks only what is missing
        rungs, _ = self._rungs({"partial": 1}, {"also_partial": 2})
        assert run_rungs(rungs, accept=lambda o: False) == {"also_partial": 2}

    def test_nothing_parsed_anywhere_raises_with_every_reason(self):
        rungs, _ = self._rungs(RungRejected("400 no response_format"), None)
        with pytest.raises(StructuredJSONFailed) as excinfo:
            run_rungs(rungs)
        assert "no response_format" in str(excinfo.value)
        assert "no JSON object" in str(excinfo.value)


class TestProviderNeutralTranslator:
    def test_a_prompt_only_translator_classifies(self):
        llm = FakeLLM(_answers(["p.header"]))
        actions, cands = classify_plan(_ledger_with(["p.header"]), llm)

        assert actions == {key_of("p.header"): ("skip", "running head")}
        assert len(cands) == 1
        # the schema had to travel in the prompt, as an example instance
        assert "Shaped like this example" in llm.prompts[0]

    def test_a_fenced_prose_wrapped_answer_still_classifies(self):
        def reply(prompt):
            body = json.dumps(_verdicts_for(["p.header"]))
            return (
                f"Sure — here are my verdicts:\n```json\n{body}\n```\nHope that helps!"
            )

        actions, _ = classify_plan(_ledger_with(["p.header"]), FakeLLM(reply))
        assert actions == {key_of("p.header"): ("skip", "running head")}

    def test_an_echoed_schema_is_recovered_not_silently_unsure(self):
        def reply(prompt):
            return json.dumps(
                {
                    "type": "object",
                    "properties": _verdicts_for(["p.header"]),
                    "required": ["p.header"],
                }
            )

        actions, _ = classify_plan(_ledger_with(["p.header"]), FakeLLM(reply))
        assert actions == {key_of("p.header"): ("skip", "running head")}

    def test_a_translator_without_a_prompt_channel_is_refused(self):
        class MTOnly(Base):
            def rotate_key(self):
                pass

            def translate(self, text):
                return text

        assert MTOnly("k", "zh-hans").supports_structured_json() is False
        with pytest.raises(PlanClassifyError, match="structured-output"):
            classify_plan(_ledger_with(["p.header"]), MTOnly("k", "zh-hans"))


# -------------------------------------------------------- divide and resend


class Scripted(Base):
    """A translator whose answer for a page depends on the page's size."""

    def __init__(self, answer):
        super().__init__("key", "zh-hans")
        self.model = "scripted"
        self.answer = answer
        self.pages = []

    def rotate_key(self):
        pass

    def translate(self, text):
        raise AssertionError("classification must never translate")

    def structured_json(self, prompt, schema, model=None, accept=None):
        asked = _asked_signatures(schema)
        self.pages.append(asked)
        result = self.answer(asked)
        if result is None:
            raise StructuredJSONFailed("every rung failed")
        return result


class TestDivideAndResend:
    def test_a_page_that_fails_once_splits_and_succeeds(self):
        signatures = [f"p.h{i}" for i in range(8)]

        def answer(asked):
            return None if len(asked) == 8 else _verdicts_for(asked)

        clf = Scripted(answer)
        actions, _ = classify_plan(_ledger_with(signatures), clf)

        assert len(actions) == 8
        assert [len(p) for p in clf.pages] == [8, 4, 4]

    def test_only_the_unanswered_signatures_are_re_asked(self):
        # answering eleven of twelve must cost one more request, not seven
        signatures = [f"p.h{i}" for i in range(12)]

        def answer(asked):
            answered = (
                [s for s in asked if s != key_of("p.h7")] if len(asked) > 1 else asked
            )
            return _verdicts_for(answered)

        clf = Scripted(answer)
        actions, _ = classify_plan(_ledger_with(signatures), clf)

        assert len(actions) == 12
        assert clf.pages[1] == [key_of("p.h7")]
        assert len(clf.pages) == 2

    def test_verdicts_from_split_pages_merge_like_an_unsplit_one(self):
        signatures = [f"p.h{i}" for i in range(6)]
        whole = Scripted(lambda asked: _verdicts_for(asked))
        split = Scripted(
            lambda asked: None if len(asked) == 6 else _verdicts_for(asked)
        )

        assert (
            classify_plan(_ledger_with(signatures), whole)[0]
            == classify_plan(_ledger_with(signatures), split)[0]
        )

    def test_a_single_signature_that_never_answers_is_terminal(self):
        # the easiest question we can ask is one property described in prose;
        # if that fails the endpoint cannot do the job, and a plan of silent
        # "unsure" would look exactly like a considered one
        signatures = [f"p.h{i}" for i in range(4)]

        def answer(asked):
            return _verdicts_for([s for s in asked if s != key_of("p.h2")])

        with pytest.raises(PlanClassifyError, match="p.h2"):
            classify_plan(_ledger_with(signatures), Scripted(answer))

    def test_the_failure_names_what_the_rungs_did(self):
        with pytest.raises(PlanClassifyError, match="every rung failed"):
            classify_plan(_ledger_with(["p.header"]), Scripted(lambda asked: None))

    def test_request_count_stays_bounded(self):
        signatures = [f"p.h{i}" for i in range(PAGE_SIZE * 2)]
        clf = Scripted(lambda asked: {})  # answers nothing, ever

        with pytest.raises(PlanClassifyError):
            classify_plan(_ledger_with(signatures), clf)
        assert len(clf.pages) <= 4 * len(signatures) + 8

    def test_a_transport_failure_is_not_divided_into_many(self):
        # auth, quota, a model that does not exist: dividing multiplies the
        # failure instead of recovering from it
        class Explodes(Scripted):
            def structured_json(self, prompt, schema, model=None, accept=None):
                self.pages.append(_asked_signatures(schema))
                raise RuntimeError("401 invalid api key")

        clf = Explodes(lambda asked: {})
        with pytest.raises(PlanClassifyError, match="invalid api key"):
            classify_plan(_ledger_with([f"p.h{i}" for i in range(6)]), clf)
        assert len(clf.pages) == 1

    def test_splitting_is_reported(self, capsys):
        signatures = [f"p.h{i}" for i in range(4)]
        clf = Scripted(lambda asked: None if len(asked) == 4 else _verdicts_for(asked))
        classify_plan(_ledger_with(signatures), clf)

        # a run that limped through must not read like one that did not
        assert "smaller pieces" in capsys.readouterr().out


class TestLintAuthority:
    CANDS = [
        {"key": key_of("p.header"), "units": 9, "chars": 81, "samples": ["GILGAMESH"]},
        {"key": key_of("td.no"), "units": 4, "chars": 8, "samples": ["No"]},
    ]

    def test_a_deliberate_unsure_is_not_a_coerced_one(self):
        # a deliberate unsure still names what it looked at; a reply that
        # skipped the naming did not do the reasoning the schema asked for
        deliberate = {
            key_of("p.header"): {"verdict": "unsure", "content_type": "running head"},
            key_of("td.no"): {"verdict": "unsure", "content_type": "table cell"},
        }
        coerced = {
            key_of("p.header"): {"verdict": "banana", "content_type": "x"},
            key_of("td.no"): "skip",
        }

        assert lint_verdicts(deliberate, self.CANDS)[1] == {
            key_of("p.header"),
            key_of("td.no"),
        }
        assert lint_verdicts(coerced, self.CANDS)[1] == set()
        # both leave the row undecided, which is why the second value exists:
        # only `answered` tells a considered "unsure" from a garbled reply
        assert all(
            v[0] == "unsure" for v in lint_verdicts(deliberate, self.CANDS)[0].values()
        )
        assert all(
            v[0] == "unsure" for v in lint_verdicts(coerced, self.CANDS)[0].values()
        )

    def test_a_coerced_page_is_re_asked_rather_than_believed(self):
        # a whole page of coercion used to pass as a whole page of verdicts
        signatures = [f"p.h{i}" for i in range(4)]
        seen = []

        def answer(asked):
            seen.append(len(asked))
            if len(seen) == 1:
                return {s: {"verdict": "banana"} for s in asked}
            return _verdicts_for(asked, "translate")

        actions, cands = classify_plan(_ledger_with(signatures), Scripted(answer))
        # every verdict is recorded now, agreement included
        assert actions == {key_of(s): ("translate", "running head") for s in signatures}
        assert len(cands) == 4
        assert seen[0] == 4 and len(seen) > 1


# -------------------------------------------------------- provider wiring
# One test per provider for how its rungs are built and how its errors map.
# The ladder itself is exercised once, above, against fake rungs — these do
# not re-test it.


def _http_error(cls, status):
    request = httpx.Request("POST", "https://example.invalid/v1/messages")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


class TestClaudeWiring:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("https://api.b.ai/v1", "https://api.b.ai"),
            ("https://api.b.ai/v1/", "https://api.b.ai"),
            ("https://api.b.ai", "https://api.b.ai"),
            (None, None),
        ],
    )
    def test_a_trailing_v1_is_not_sent_twice(self, given, expected):
        # the SDK appends /v1/messages itself; /v1/v1/messages is a 403 whose
        # text says nothing about the cause
        assert _sdk_base_url(given) == expected

    def _claude(self, create):
        claude = Claude.__new__(Claude)
        Base.__init__(claude, "k", "zh-hans")
        claude.model = "claude-haiku-4-5-20251001"
        claude.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        return claude

    def test_classification_asks_the_plain_question(self):
        create = Mock(
            return_value=SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text='{"p.h": {"verdict": "skip"}}')
                ]
            )
        )
        claude = self._claude(create)

        assert (
            claude._chat_completion("classify this") == '{"p.h": {"verdict": "skip"}}'
        )
        request = create.call_args.kwargs
        # no prompt template, no system message, no context pairs
        assert request["messages"] == [{"role": "user", "content": "classify this"}]
        assert "system" not in request

    def test_claude_has_one_rung_and_it_is_the_prompt(self):
        # anthropic-native structured outputs are deliberately out of scope:
        # untestable here, and the gateways drop schema fields anyway
        claude = self._claude(Mock())
        assert [name for name, _ in claude.structured_rungs("q", {})] == ["prompt"]

    def test_a_refused_request_is_a_rung_rejection(self):
        claude = self._claude(Mock(side_effect=_http_error(AnthropicBadRequest, 400)))
        with pytest.raises(RungRejected):
            claude._chat_completion("classify this")


class TestGeminiWiring:
    def test_the_schema_is_converted_to_geminis_dialect(self):
        cands = [{"key": key_of("p.header"), "units": 9, "chars": 81, "samples": ["G"]}]
        converted = _openapi_schema(build_schema(cands))

        assert converted["type"] == "OBJECT"
        # gemini's dialect has neither of these and 400s on both
        assert "additionalProperties" not in json.dumps(converted)
        assert "strict" not in converted
        verdict = converted["properties"][key_of("p.header")]["properties"]["verdict"]
        assert verdict["type"] == "STRING"
        assert verdict["enum"] == ["translate", "skip", "unsure"]

    def _gemini(self, generate_content):
        gemini = Gemini.__new__(Gemini)
        Base.__init__(gemini, "k", "zh-hans")
        gemini.model = "gemini-3-flash"
        gemini.convo = SimpleNamespace(
            send_message=Mock(side_effect=AssertionError("used the translation chat"))
        )
        gemini.client = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        )
        return gemini

    def test_classification_never_enters_the_translation_conversation(self):
        # self.convo carries context for the paragraphs still to come; a
        # classification question and its answer must not become part of it
        generate = Mock(
            return_value=SimpleNamespace(text='{"p.h": {"verdict": "skip"}}')
        )
        gemini = self._gemini(generate)

        gemini._chat_completion("classify this")

        assert generate.call_args.kwargs["contents"] == "classify this"

    def test_the_native_rung_comes_first_and_the_prompt_rung_backs_it(self):
        gemini = self._gemini(Mock())
        names = [name for name, _ in gemini.structured_rungs("q", {"schema": {}})]
        assert names == ["response_schema", "prompt"]

    def test_a_rejected_schema_descends_but_a_dead_key_does_not(self):
        class _Err(genai_errors.ClientError):
            def __init__(self, code):
                Exception.__init__(self, f"{code}")
                self.code = code

        gemini = self._gemini(Mock(side_effect=_Err(400)))
        with pytest.raises(RungRejected):
            gemini._chat_completion("q")

        gemini = self._gemini(Mock(side_effect=_Err(403)))
        with pytest.raises(genai_errors.ClientError):
            gemini._chat_completion("q")


class TestGroqWiring:
    def test_classification_does_not_go_to_openai_with_a_groq_key(self):
        # inherited from ChatGPTAPI, structured_json would have posted to
        # self.openai_client — which for groq is api.openai.com
        groq = GroqClient.__new__(GroqClient)
        Base.__init__(groq, "groq-key", "zh-hans")
        groq.model = "llama3-8b-8192"
        groq.openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=Mock(side_effect=AssertionError("wrong client"))
                )
            )
        )
        groq._structured_support = {"llama3-8b-8192": False}
        groq._structured_lock = threading.RLock()

        with patch("book_maker.translator.groq_translator.Groq") as client:
            client.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"a": 1}'))]
            )
            assert groq.structured_json("classify", {"schema": {}}) == {"a": 1}

        # and no probe was sent either: SUPPORTS_STRUCTURED_OUTPUTS is False
        assert [name for name, _ in groq.structured_rungs("q", {})] == ["prompt"]


class TestProviderModelList:
    """`--provider` calls set_model_list on every api_style (cli.py:881-890).

    Two of the four did not have one, so a provider config with
    `api_style: "claude"` or `"qwen"` died on an AttributeError — which is
    also the only route by which a gateway's own model id can reach those
    classes, since `--model` is limited to MODEL_DICT keys.
    """

    def test_every_supported_api_style_accepts_a_model_list(self):
        from book_maker.provider_loader import SUPPORTED_API_STYLES

        missing = [
            style
            for style, cls in SUPPORTED_API_STYLES.items()
            if not hasattr(cls, "set_model_list")
        ]
        assert missing == []

    def test_claude_takes_the_first_entry_and_says_so(self, capsys):
        claude = Claude.__new__(Claude)
        Base.__init__(claude, "k", "zh-hans")

        claude.set_model_list(["claude-haiku-4.5", "claude-sonnet-4.6"])

        assert claude.model == "claude-haiku-4.5"
        assert "ignoring 1 more" in capsys.readouterr().out

    def test_an_empty_model_list_is_an_error_not_a_default(self):
        claude = Claude.__new__(Claude)
        Base.__init__(claude, "k", "zh-hans")
        with pytest.raises(ValueError):
            claude.set_model_list([])


class TestRungRetirement:
    """A 400 is as often about *this request* as about the request shape.

    Retiring a rung on the first refusal confiscates the very rung that
    divide-and-resend needs for the smaller page — and if the floor goes with
    it, the retry answers without making a single call.
    """

    class Sized(Base):
        """Refuses any prompt mentioning more than `limit` signatures."""

        def __init__(self, limit=2):
            super().__init__("k", "zh-hans")
            self.model = "sized"
            self.limit = limit
            self.calls = 0

        def rotate_key(self):
            pass

        def translate(self, text):
            raise AssertionError("classification must never translate")

        def _chat_completion(self, prompt, model=None):
            self.calls += 1
            asked = [s for s in self.signatures if f'"{key_of(s)}"' in prompt]
            if len(asked) > self.limit:
                raise RungRejected("400 context length exceeded")
            return json.dumps(_verdicts_for(asked))

    def test_a_size_refusal_leaves_the_rung_available_for_the_retry(self):
        llm = self.Sized(limit=2)
        llm.signatures = [f"p.h{i}" for i in range(5)]
        big = " ".join(f'"{key_of(s)}"' for s in llm.signatures)
        with pytest.raises(StructuredJSONFailed):
            llm.structured_json(big, build_schema([]))
        before = llm.calls

        # the floor is never retired, so a smaller ask still reaches the wire
        assert llm.structured_json(f'"{key_of("p.h0")}"', build_schema([]))
        assert llm.calls == before + 1

    def test_divide_and_resend_survives_a_page_too_big_for_the_endpoint(self):
        signatures = [f"p.h{i}" for i in range(4)]
        llm = self.Sized(limit=2)
        llm.signatures = signatures

        actions, cands = classify_plan(_ledger_with(signatures), llm)

        assert len(actions) == 4 and len(cands) == 4
        assert llm.calls > 1  # the whole page failed; the halves did not

    def test_a_rung_refused_repeatedly_is_retired(self):
        class Native(FakeLLM):
            def __init__(self):
                super().__init__(_answers(["p.h0"]))
                self.native_calls = 0

            def structured_rungs(self, prompt, schema, model=None):
                def native():
                    self.native_calls += 1
                    raise RungRejected("400 response_format unsupported")

                return [
                    ("native", native),
                    ("prompt", lambda: self._prompt_rung(prompt, schema, model)),
                ]

        llm = Native()
        for _ in range(4):
            llm.structured_json('"p.h0"', build_schema([]))

        # tried twice, then dropped — not once, and not every time
        assert llm.native_calls == Base.RUNG_REFUSAL_THRESHOLD

    def test_the_floor_rung_is_never_retired(self):
        llm = FakeLLM(lambda prompt: (_ for _ in ()).throw(RungRejected("400")))
        for _ in range(3):
            with pytest.raises(StructuredJSONFailed):
                llm.structured_json("q", build_schema([]))
        assert llm._rung_refusals.get("fake-llm", {}) == {}


class TestQwenConfiguration:
    """Qwen-MT cannot classify, but it must still be configured correctly."""

    def test_the_model_survives_construction(self):
        # `self.model = self.set_qwen_model(model)` assigned the setter's
        # return over the value it had just set; every request went out with
        # model=None
        from book_maker.translator.qwen_translator import QwenTranslator

        qwen = QwenTranslator(key="k", language="zh-hans", model="qwen-mt-plus")
        assert qwen.model == "qwen-mt-plus"
        assert qwen.terminology == [] and qwen.domain_hint == ""

    def test_an_unknown_model_is_refused_not_substituted(self):
        from book_maker.translator.qwen_translator import QwenTranslator

        qwen = QwenTranslator(key="k", language="zh-hans")
        with pytest.raises(ValueError, match="qwen-mt-turbo"):
            qwen.set_model_list(["qwen-mt-ultra"])
        # billing a whole book to a model the user did not choose is not a
        # recovery from a typo
        assert qwen.model == "qwen-mt-turbo"

    def test_the_qwen_alias_can_load_its_key(self):
        # cli.py matched only "qwen-", so the bare "qwen" choice — which is a
        # MODEL_DICT key — always got an empty API key
        import re

        from book_maker.translator import MODEL_DICT

        source = (
            pathlib.Path(__file__).parent.parent / "book_maker" / "cli.py"
        ).read_text()
        assert 'options.model.startswith("qwen")' in source
        assert "qwen" in MODEL_DICT
