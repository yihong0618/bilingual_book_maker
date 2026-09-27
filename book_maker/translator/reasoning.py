"""`--no-thinking`: turning reasoning off in whatever the endpoint calls it.

Thinking buys nothing on a paragraph of prose and costs tokens and wall
clock, so the flag exists. What it cannot do is know the field name: every
vendor and every gateway spells "do not reason" differently, the spellings
are mutually exclusive — an endpoint that takes one 400s on the next — and a
model id says nothing about which one is in front of it today. Nothing is
looked up from a name here. The endpoint is asked, and its own validation
error is read for whether the field just sent is one it knows.

Two rules keep that from turning into guesswork on the user's bill:

* **Only an explicit rejection of the field just sent moves the negotiation
  on.** A 400 that blames another parameter, a 500, an auth failure and a
  message that merely *mentions* the field ("max_tokens is too small for
  reasoning") all propagate untouched, because none of them is an answer
  about this field.
* **The answer is cached per endpoint and model**, in the process, so a book
  pays for the negotiation once rather than once a paragraph. In-process
  only: an endpoint's answer is a fact about today's deployment behind that
  address, and a file of them would outlive the truth.

When every spelling has been refused the run continues without a control and
says so once. A reasoning model that will not be told to stop reasoning is a
slower, costlier run, never a broken one, so it is a warning and not a stop.

Ported from the sibling `jev-ultrafast` project, which negotiates the same
field against the same class of endpoints; the matcher below is its
`rejected_parameter`, adapted to the OpenAI SDK's exception shape.
"""

import json
import re

from rich import print

# The spellings, in the order they are tried. Each is a real field of a real
# OpenAI-compatible endpoint: `reasoning_effort` is OpenAI's own and the one
# most gateways copied, `reasoning` as an object is the OpenRouter/Responses
# shape (with a boolean form of its own), `enable_thinking` is DashScope and
# vLLM, `chat_template_kwargs.enable_thinking` is how vLLM and SGLang reach a
# Qwen-style chat template, and the `thinking` object is what a gateway that
# fronts Anthropic over the OpenAI shape wants. The empty control at the end
# is not a spelling: it is the omission the warning announces.
NO_THINKING_CONTROLS = (
    {"reasoning_effort": "none"},
    {"reasoning": {"effort": "none"}},
    {"reasoning": {"enabled": False}},
    {"enable_thinking": False},
    {"chat_template_kwargs": {"enable_thinking": False}},
    {"thinking": {"type": "disabled"}},
    {},
)

# The top-level fields the ladder above may write. A run whose `--extra_body`
# already sets one of these has said what it wants sent, and the flag does
# not argue with it — see `ChatGPTAPI._no_thinking_control`.
NO_THINKING_FIELDS = frozenset(
    field for control in NO_THINKING_CONTROLS for field in control
)

# The anthropic wire format has exactly one spelling and every endpoint that
# speaks it takes the field, so that route sends this and negotiates nothing.
ANTHROPIC_NO_THINKING = {"thinking": {"type": "disabled"}}

NO_CONTROL_WARNING = (
    "[yellow]--no-thinking: this endpoint rejected every known reasoning "
    "control; requests continue without one.[/yellow]"
)


# `error.code` values that are an endpoint saying "not this field". Only read
# alongside a `param` that names the field we sent: a code on its own says
# what kind of complaint it is, never what it is about.
REJECTION_CODES = frozenset(
    {
        "unknown_parameter",
        "unsupported_parameter",
        "unsupported_value",
        "invalid_parameter",
        "invalid_value",
    }
)

# Rejection wording, for the endpoints that fill in neither `param` nor
# `code`. Two shapes: "<verb> parameter: <field>" and "<field> is not
# supported". `(?<![\w.])` and `(?!\w)(?!\.[a-z_])` keep the field boundary
# honest — "reasoning" must not match a message about `reasoning_content`,
# nor may a rejection of `reasoning_effort` be read as one of `reasoning` —
# while a trailing sentence period is not part of the field path.
_REJECTION_WORDING = (
    r"\b(?:unknown|unrecognized|unrecognised|unexpected|unsupported|invalid)\s+"
    r"(?:request\s+)?(?:parameter|argument|field|key|value)(?:\s+supplied)?"
    r"\s*:?\s*[`'\"]?(?<![\w.]){path}(?!\w)(?!\.[a-z_])"
    r"|(?<![\w.]){path}[`'\"]?\s+(?:is\s+)?"
    r"(?:not supported|not allowed|not permitted|unsupported)\b"
)


def _error_detail(error):
    """The provider's own error object, whichever way the SDK handed it over.

    The OpenAI SDK unwraps `{"error": {...}}` before it builds the exception,
    so `.body` is usually the inner object already; a gateway that answers
    with something else keeps its own shape, and a body that is not an object
    at all (HTML, a bare string, a list) is no evidence about any field.
    """
    detail = getattr(error, "body", None)
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        detail = detail["error"]
    return detail if isinstance(detail, dict) else None


def rejected_parameter(error, control):
    """Whether `error` is this endpoint refusing the field `control` sends.

    Deliberately narrow. Anything short of an explicit validation rejection
    of *this* field — a different parameter, a server error, a transport
    failure, a message that only mentions the field in passing — is not an
    answer, so it propagates and the negotiation stays where it is.
    """
    if not control:
        return False
    if getattr(error, "status_code", None) not in (400, 422):
        return False
    detail = _error_detail(error)
    if detail is None:
        return False

    field = next(iter(control))
    # A nested complaint is still about the field we sent: an endpoint that
    # takes `reasoning` but not `reasoning.effort` blames the path.
    path = rf"{re.escape(field)}(?:\.[a-z_]+)*"

    param = detail.get("param")
    if param is not None and (
        not isinstance(param, str) or not re.fullmatch(path, param)
    ):
        return False

    code = detail.get("code")
    if param is not None and isinstance(code, str) and code in REJECTION_CODES:
        return True

    message = detail.get("message")
    if not isinstance(message, str):
        return False
    return bool(
        re.search(_REJECTION_WORDING.replace("{path}", path), message, re.IGNORECASE)
    )


def _advance_note(rejected, accepted):
    """What the operator is told when the ladder moves on one rung.

    A negotiation nobody can see is a negotiation nobody can debug: a run
    that quietly paid for six refusals looks exactly like one that took the
    first field. One line per rung, at the rung, and never again for that
    endpoint and model.

    The whole control, not just its field name: two rungs write `reasoning`
    and differ only inside it, and "rejected reasoning, asking with
    reasoning" tells nobody anything.
    """
    return (
        f"[yellow]ℹ --no-thinking: this endpoint rejected "
        f"{json.dumps(rejected)}; asking with {json.dumps(accepted)} "
        f"instead[/yellow]"
    )


class ThinkingOff:
    """Which reasoning-off field an endpoint takes, learned by being told.

    One instance per process (`CONTROLS` below). Keyed by endpoint and model
    because a gateway routinely serves models of different generations behind
    one address, and the field that works is the model's property as much as
    the server's.
    """

    def __init__(self, controls=NO_THINKING_CONTROLS):
        self._controls = tuple(controls)
        self._accepted = {}
        self._warned = set()

    def control(self, endpoint, model):
        """The spelling to send to this endpoint and model right now.

        `{}` once every spelling has been refused; the run is told that here,
        at the moment the first request without a control goes out, and once.
        """
        key = (endpoint, model)
        control = self._controls[self._accepted.get(key, 0)]
        if not control and key not in self._warned:
            self._warned.add(key)
            print(NO_CONTROL_WARNING)
        return control

    def rejected(self, endpoint, model, error):
        """Advance to the next spelling if `error` refused the one just sent.

        Returns whether the caller should send the request again. False for
        every error that is not this endpoint rejecting this field, and False
        once the ladder has run out — at which point `control` sends nothing
        and nothing can be rejected on its behalf again.
        """
        key = (endpoint, model)
        index = self._accepted.get(key, 0)
        if not rejected_parameter(error, self._controls[index]):
            return False
        if index + 1 >= len(self._controls):
            return False
        self._accepted[key] = index + 1
        following = self._controls[index + 1]
        if following:
            # The omission rung announces itself in `control`, with the
            # warning that says the ladder is spent; this line is only for
            # the moves that still have a field to send.
            print(_advance_note(self._controls[index], following))
        return True


# The process's cache. Read through the module (`reasoning.CONTROLS`) rather
# than imported by value, so a test can put a fresh one in place.
CONTROLS = ThinkingOff()
