"""`--no-thinking`: the field is negotiated, then remembered.

The flag cannot know what a given endpoint calls "do not reason" — every
vendor spells it differently and rejects the others — so the mechanism under
test is a conversation: send a spelling, read the endpoint's own validation
error, move on only when that error is about the field just sent. These pin
both halves of that: what makes the negotiation advance, and what must never
make it advance, since every wrong advance is a paid request spent on a
field the endpoint was never going to take.

Hermetic throughout. The endpoint is a dummy `create` that records the
request body and answers from a script, in the style of the `--extra_body`
tests next door; nothing here opens a socket.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import BadRequestError, UnprocessableEntityError

from book_maker.translator import reasoning
from book_maker.translator.capabilities import ModelUnavailable
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude
from book_maker.translator.reasoning import (
    NO_THINKING_CONTROLS,
    ThinkingOff,
    rejected_parameter,
)

BASE = "https://gateway.test/v1"
REQUEST = httpx.Request("POST", f"{BASE}/chat/completions")

# The ladder, spelled out here rather than imported into the assertions: a
# reordering of the module's tuple is a change in what every run sends, and
# it should have to be made twice.
LADDER = [
    {"reasoning_effort": "none"},
    {"reasoning": {"effort": "none"}},
    {"reasoning": {"enabled": False}},
    {"enable_thinking": False},
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"thinking": {"type": "disabled"}},
    {},
]


def test_the_ladder_is_what_the_tests_below_assume():
    assert list(NO_THINKING_CONTROLS) == LADDER


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """A process-wide cache is the point; a shared one between tests is not."""
    monkeypatch.setattr(reasoning, "CONTROLS", ThinkingOff())


def _completion(content="ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
        model="test-model",
    )


def _rejects(field, status=400, code="unknown_parameter", message="Rejected"):
    """The endpoint refusing the field named, the way a provider spells it."""
    cls = BadRequestError if status == 400 else UnprocessableEntityError
    return cls(
        f"Error code: {status}",
        response=httpx.Response(status, request=REQUEST),
        body={"message": message, "param": field, "code": code},
    )


def _error(status, body):
    cls = BadRequestError if status == 400 else UnprocessableEntityError
    return cls(
        f"Error code: {status}",
        response=httpx.Response(status, request=REQUEST),
        body=body,
    )


class Endpoint:
    """A `create` that records each body and answers from a script."""

    def __init__(self, *script):
        self.script = list(script)
        self.bodies = []

    def __call__(self, **kwargs):
        self.bodies.append(kwargs.get("extra_body"))
        answer = self.script.pop(0) if self.script else _completion()
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def controls(self):
        """The reasoning control on each request, `{}` where there was none."""
        return [body or {} for body in self.bodies]


def _translator(endpoint, no_thinking=True, extra_body=None):
    t = ChatGPTAPI("sk-test", "Chinese", api_base=BASE)
    t.model = "test-model"
    t.no_thinking = no_thinking
    if extra_body:
        t.set_request_extras(extra_body=extra_body)
    t.openai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=endpoint))
    )
    return t


# ------------------------------------------------------- the negotiation


class TestNegotiation:
    def test_the_first_spelling_accepted_is_the_one_every_request_uses(self):
        # nothing to negotiate: one request, one control, and the next
        # request repeats it without re-asking
        endpoint = Endpoint()
        t = _translator(endpoint)

        t.create_chat_completion("hello")
        t.create_chat_completion("again")

        assert endpoint.controls == [LADDER[0], LADDER[0]]

    def test_a_rejection_naming_the_field_moves_to_the_next_spelling(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t.create_chat_completion("hello")

        assert endpoint.controls == [LADDER[0], LADDER[1]]

    def test_the_accepted_spelling_is_cached_so_the_retry_happens_once(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t.create_chat_completion("hello")
        t.create_chat_completion("again")
        t.create_chat_completion("and again")

        # one retry in total, not one per request
        assert endpoint.controls == [LADDER[0], LADDER[1], LADDER[1], LADDER[1]]

    def test_the_cache_is_per_endpoint_and_model(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)
        t.create_chat_completion("hello")

        # a gateway serves generations of models behind one address; what the
        # last one took says nothing about this one
        t.model = "other-model"
        t.create_chat_completion("hello")

        assert endpoint.controls[-1] == LADDER[0]

    def test_a_spelling_rejected_later_moves_on_again(self):
        # the endpoint took it once and refuses it now (a model swap behind
        # the gateway, a deployment change); the ladder carries on from where
        # it was rather than starting over
        endpoint = Endpoint(_rejects("reasoning_effort"), None, _rejects("reasoning"))
        endpoint.script[1] = _completion()
        t = _translator(endpoint)

        t.create_chat_completion("hello")
        t.create_chat_completion("again")

        assert endpoint.controls == [LADDER[0], LADDER[1], LADDER[1], LADDER[2]]

    def test_every_spelling_rejected_warns_once_and_carries_on(self, capsys):
        endpoint = Endpoint(*(_rejects(next(iter(c))) for c in LADDER[:-1]))
        t = _translator(endpoint)

        t.create_chat_completion("hello")
        out = capsys.readouterr().out
        t.create_chat_completion("again")
        t.create_chat_completion("and again")
        rest = capsys.readouterr().out

        # every rung tried once, then nothing, and no further retries
        assert endpoint.controls == LADDER + [{}, {}]
        assert out.count("rejected every known reasoning control") == 1
        assert rest == ""

    def test_the_warning_is_the_sentence_the_operator_gets(self, capsys):
        endpoint = Endpoint(*(_rejects(next(iter(c))) for c in LADDER[:-1]))
        t = _translator(endpoint)

        t.create_chat_completion("hello")

        # flattened: rich wraps a long line to the terminal, and the
        # sentence is the last thing said either way
        out = " ".join(capsys.readouterr().out.split())
        assert out.endswith(
            "--no-thinking: this endpoint rejected every known reasoning "
            "control; requests continue without one."
        )

    def test_each_move_says_which_field_it_moved_to(self, capsys):
        # a negotiation nobody can see is one nobody can debug: a run that
        # paid for a refusal must not look like one that took the first field
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t.create_chat_completion("hello")

        out = " ".join(capsys.readouterr().out.split())
        # the whole control, not the field name: two rungs write `reasoning`
        # and differ only inside it
        assert 'rejected {"reasoning_effort": "none"}' in out
        assert 'asking with {"reasoning": {"effort": "none"}} instead' in out

    def test_a_settled_endpoint_says_nothing_at_all(self, capsys):
        t = _translator(Endpoint())

        t.create_chat_completion("hello")

        assert capsys.readouterr().out == ""

    def test_a_400_about_something_else_propagates_untouched(self):
        # the flag must not turn an unrelated refusal into a silent retry on
        # the operator's bill, nor swallow what the endpoint actually said
        refusal = _rejects("temperature")
        endpoint = Endpoint(refusal)
        t = _translator(endpoint)

        with pytest.raises(BadRequestError) as raised:
            t.create_chat_completion("hello")

        assert raised.value is refusal
        assert endpoint.controls == [LADDER[0]]

    def test_a_message_naming_a_neighbouring_field_is_not_a_rejection(self):
        # "reasoning_content" is another field entirely, and a control that
        # was never refused must not be spent on it
        endpoint = Endpoint(
            _error(
                400,
                {
                    "message": "Unknown parameter: 'reasoning_content'.",
                    "param": None,
                    "code": None,
                },
            )
        )
        t = _translator(endpoint)

        with pytest.raises(BadRequestError):
            t.create_chat_completion("hello")

        assert endpoint.controls == [LADDER[0]]

    def test_the_flag_off_sends_no_control_and_negotiates_nothing(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint, no_thinking=False)

        with pytest.raises(BadRequestError):
            t.create_chat_completion("hello")

        assert endpoint.controls == [{}]


class TestOperatorPrecedence:
    def test_a_field_set_in_extra_body_wins_over_the_flag(self):
        endpoint = Endpoint()
        t = _translator(endpoint, extra_body={"reasoning_effort": "high"})

        t.create_chat_completion("hello")

        assert endpoint.controls == [{"reasoning_effort": "high"}]

    def test_the_operators_field_is_not_negotiated_away(self):
        # their field, their refusal to read: the ladder must not answer a
        # rejection of something the flag did not send
        refusal = _rejects("reasoning_effort")
        endpoint = Endpoint(refusal)
        t = _translator(endpoint, extra_body={"reasoning_effort": "high"})

        with pytest.raises(BadRequestError) as raised:
            t.create_chat_completion("hello")

        assert raised.value is refusal
        assert endpoint.controls == [{"reasoning_effort": "high"}]

    def test_an_unrelated_extra_body_field_rides_along(self):
        endpoint = Endpoint()
        t = _translator(endpoint, extra_body={"seed": 7})

        t.create_chat_completion("hello")

        assert endpoint.controls == [{"reasoning_effort": "none", "seed": 7}]


class TestEveryRequestCarriesIt:
    """A probe graded on a request the run never makes grades another run."""

    def test_the_route_probe_carries_the_control(self):
        endpoint = Endpoint()
        t = _translator(endpoint)

        t._route_probe(t.openai_client, "test-model")

        assert endpoint.controls == [LADDER[0]]

    def test_the_route_probe_negotiates_before_the_run_spends(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t._route_probe(t.openai_client, "test-model")
        t._completion_text("test-model", "hello")

        assert endpoint.controls == [LADDER[0], LADDER[1], LADDER[1]]

    def test_a_route_probe_rejection_about_the_model_still_refuses(self):
        endpoint = Endpoint(
            _error(404, {"message": "model_not_found", "code": "model_not_found"})
        )
        t = _translator(endpoint)

        with pytest.raises(ModelUnavailable):
            t._route_probe(t.openai_client, "nope")

    def test_the_schema_probe_carries_the_control(self):
        endpoint = Endpoint(_completion('{"probe": "schema_ok"}'))
        t = _translator(endpoint)

        t._probe("test-model")

        assert endpoint.controls == [LADDER[0]]

    def test_a_translate_call_carries_the_control(self):
        endpoint = Endpoint()
        t = _translator(endpoint)

        t.create_chat_completion("hello")

        assert endpoint.controls == [LADDER[0]]

    def test_a_translate_call_negotiates_too(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t.create_chat_completion("hello")

        assert endpoint.controls == [LADDER[0], LADDER[1]]

    # Port 260923 (port/260920-batch): the image path (`_probe_vision`,
    # `_vision_completion_text`) arrived on the PDF feature branch after
    # --no-thinking was built, reading `self.extra_body` directly; these pin
    # that it is one of "every request" too.
    def test_the_image_probe_carries_the_control(self):
        pytest.importorskip("PIL")  # the probe draws its challenge with Pillow
        endpoint = Endpoint()
        t = _translator(endpoint)

        t._probe_vision("test-model")

        assert endpoint.controls == [LADDER[0]]

    def test_the_image_probe_negotiates_rather_than_grading_the_refusal(self):
        pytest.importorskip("PIL")  # the probe draws its challenge with Pillow
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t._probe_vision("test-model")

        assert endpoint.controls == [LADDER[0], LADDER[1]]

    def test_an_image_request_carries_the_control(self):
        endpoint = Endpoint()
        t = _translator(endpoint)

        t._vision_completion_text("test-model", [{"type": "text", "text": "q"}])

        assert endpoint.controls == [LADDER[0]]

    def test_an_image_request_negotiates_too(self):
        endpoint = Endpoint(_rejects("reasoning_effort"))
        t = _translator(endpoint)

        t._vision_completion_text("test-model", [{"type": "text", "text": "q"}])

        assert endpoint.controls == [LADDER[0], LADDER[1]]

    def test_an_image_request_keeps_the_operator_body_over_the_flag(self):
        endpoint = Endpoint()
        t = _translator(endpoint, extra_body={"reasoning_effort": "low"})

        t._vision_completion_text("test-model", [{"type": "text", "text": "q"}])

        assert endpoint.controls == [{"reasoning_effort": "low"}]


class TestTheAnthropicRoute:
    """One spelling, part of the wire format; nothing to negotiate."""

    def _claude(self, no_thinking=True, extra_body=None):
        c = Claude.__new__(Claude)
        c.extra_body = dict(extra_body or {})
        c.no_thinking = no_thinking
        return c

    def test_every_request_carries_thinking_disabled(self):
        assert self._claude().request_extra_body() == {"thinking": {"type": "disabled"}}

    def test_the_operators_thinking_field_wins(self):
        body = {"thinking": {"type": "enabled", "budget_tokens": 1024}}
        assert self._claude(extra_body=body).request_extra_body() == body

    def test_the_flag_off_changes_nothing(self):
        assert self._claude(no_thinking=False).request_extra_body() is None

    def test_the_request_sends_it(self):
        c = self._claude()
        c.model = "claude-haiku-latest"
        c.client = SimpleNamespace(
            messages=SimpleNamespace(
                create=Mock(
                    return_value=SimpleNamespace(
                        content=[SimpleNamespace(type="text", text="ok")],
                        usage=None,
                    )
                )
            )
        )
        c._note_usage = lambda *a, **k: None

        c._chat_completion("hello")

        kwargs = c.client.messages.create.call_args.kwargs
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


# --------------------------------------------------------- the matcher


class TestRejectionMatching:
    """What counts as "the endpoint refused this field", and what does not.

    Ported from jev-ultrafast, whose four commits on this are the reason the
    rule is field-specific: a matcher that reads any 400 as a rejection
    spends the whole ladder on one unrelated complaint and then omits the
    control for a run that would have taken the first spelling.
    """

    CONTROL = {"reasoning": {"effort": "none"}}

    @pytest.mark.parametrize(
        "detail",
        [
            {"message": "Unknown parameter: 'reasoning'."},
            {"message": "Unsupported value: 'reasoning.effort' does not support none."},
            {"message": "'reasoning' is not supported with this model."},
            {"param": "reasoning.effort", "code": "unsupported_value"},
            {"message": "Unrecognized request argument supplied: reasoning"},
            # an unquoted field at the end of a sentence: the period is not
            # part of the path
            {"message": "Unknown parameter: reasoning."},
        ],
    )
    def test_these_are_rejections_of_the_field(self, detail):
        assert rejected_parameter(_error(400, detail), self.CONTROL)

    @pytest.mark.parametrize(
        "status,body",
        [
            # right words, wrong status: an auth or server failure is not a
            # verdict about a request field
            (401, {"message": "Unknown parameter: 'reasoning'."}),
            (500, {"param": "reasoning", "code": "unknown_parameter"}),
            # blames another field
            (
                400,
                {"param": "max_tokens", "message": "Unknown parameter: 'reasoning'."},
            ),
            # merely mentions it
            (400, {"message": "max_tokens is too small for reasoning"}),
            # a neighbouring field's name must not be swallowed
            (400, {"message": "Unknown parameter: 'reasoning_content'."}),
            (
                400,
                {"message": "Unrecognized request argument supplied: reasoning_effort"},
            ),
            # names the field but does not reject it
            (400, {"param": "reasoning", "message": "Internal processing failed"}),
            # nothing readable
            (400, {"message": None}),
            (400, {"error": "Invalid request"}),
            (400, []),
            (400, None),
            (400, "<html>Bad request</html>"),
            # a provider that puts something that is not a string in `code`
            # must not crash the matcher
            (400, {"param": "reasoning", "code": ["unknown_parameter"]}),
            (400, {"param": "reasoning", "code": {"kind": "unknown_parameter"}}),
        ],
    )
    def test_these_are_not(self, status, body):
        assert not rejected_parameter(_error(status, body), self.CONTROL)

    def test_the_envelope_form_is_read_too(self):
        # a gateway that hands the SDK `{"error": {...}}` unopened
        error = _error(400, {"error": {"param": "reasoning", "code": "invalid_value"}})
        assert rejected_parameter(error, self.CONTROL)

    def test_the_omission_rung_can_never_be_rejected(self):
        # it sends nothing, so nothing can be refused on its behalf — this is
        # what stops the ladder looping once it has run out
        assert not rejected_parameter(_error(400, {"param": "reasoning"}), {})

    def test_a_422_counts(self):
        assert rejected_parameter(
            _error(422, {"param": "reasoning", "code": "invalid_parameter"}),
            self.CONTROL,
        )
