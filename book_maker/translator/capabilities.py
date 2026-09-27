"""What an endpoint can actually do, established by asking it.

A model's name says nothing about whether the server behind it applies a JSON
Schema, accepts an explicit temperature, or serves the model at all. A proxy in
front of the same model routinely answers differently from the vendor, so each
is asked once per model and remembered here. Written against the OpenAI wire
format.
"""

import json
import re
from threading import RLock

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from rich import print
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..redaction import redact


class ModelUnavailable(Exception):
    """This endpoint will not serve the named model.

    The message names the model and, where the endpoint has a listing, what it
    does offer instead — the whole explanation, so callers print it rather
    than a traceback.
    """

    user_facing = True


class StructuredOutputUnsupported(Exception):
    """The endpoint does not really apply the JSON Schema we sent.

    Raised only for capability answers, never for model or transport errors, so
    callers can demote to the delimiter method instead of retrying.
    """


class StructuredRefusal(Exception):
    """The model declined to answer this particular text.

    Deliberately not a `StructuredOutputUnsupported`: a refusal says nothing
    about whether the endpoint honors the schema, and counting it as a
    capability failure would demote structured outputs for the whole run after
    two paragraphs. Callers retranslate the one paragraph without a schema.
    """

    def __init__(self, refusal):
        super().__init__(f"Model refused to translate: {refusal}")
        self.refusal = refusal


class ProbeDeferred(Exception):
    """The probe could not get an answer right now; ask again later."""


# The API's own default. Sending it explicitly changes nothing for models that
# accept it, and is a hard 400 for models that only allow their default.
DEFAULT_TEMPERATURE = 1.0

# Capability probe. The prompt asks for plain text and the schema pins a
# single-value enum, so the only way `PROBE_EXPECTED` can come back is if the
# server actually applied the schema to decoding. A proxy that accepts
# `response_format` and quietly drops it answers with the prompted text instead.
# Deliberately language-free: this asks whether the endpoint honors schemas at
# all, and a translation-shaped probe would confuse that with a bad translation.
PROBE_PROMPT = "Reply with the single word: ignored. Do not output JSON."
PROBE_KEY = "probe"
PROBE_EXPECTED = "schema_ok"
STRUCTURED_PROBE_SCHEMA = {
    "name": "structured_output_probe",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {PROBE_KEY: {"type": "string", "enum": [PROBE_EXPECTED]}},
        "required": [PROBE_KEY],
        "additionalProperties": False,
    },
}

# Route probe: one tiny chat request per model, answer unread. No schema,
# temperature or token cap — each is refused by some model somewhere, and a
# refusal of the question is not an answer about the model (gpt-5 rejects
# `max_tokens` outright, so a capped probe confirmed nothing).
ROUTE_PROBE_PROMPT = "Reply with the single word: PONG."

# How many listed model ids an error message may carry. A gateway lists
# hundreds; the listing is a hint under the refusal, not the finding.
LISTING_HINT_LIMIT = 12

# What an endpoint says when it will not route a model: a 404, or for the
# gateways that answer 400 instead, one of the phrases below. A 400 about a
# request field is never about the model, even when it names the model too.
PARAMETER_COMPLAINT_WORDS = (
    "parameter",
    "unsupported_parameter",
    "unsupported value",
    "invalid_request_error: unsupported",
)

MODEL_NOT_FOUND_PHRASES = (
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "model not found",
    "invalid model",
)


# A permanent answer about this endpoint: no key, no access, no such model.
# Nothing downstream recovers from these, and swallowing them would pin the whole
# run to the delimiter method because of a typo in the key.
PROBE_FATAL_ERRORS = (
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
)

# Router hiccups. These say nothing about schema support, but they also do not
# mean the run is over: API gateways go away and come back, and a book is
# expected to translate across hours of that. The probe therefore *defers* —
# records no verdict, uses the delimiter method for this one call, and probes
# again on the next paragraph. The real request behind it hits the same outage
# and gets tenacity's retries, which is where transient failures belong.
PROBE_TRANSIENT_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)

# A refusal of the *request shape*, which a simpler rung may not trigger: an
# unsupported `response_format`, a schema the endpoint will not compile, a
# payload it will not size. Distinct from PROBE_FATAL_ERRORS (no key, no model
# — descending cannot help) and from transport errors (retrying can).
RUNG_REFUSAL_ERRORS = (
    BadRequestError,
    UnprocessableEntityError,
)

# How an endpoint is asked to cap a reply's length, in the order they are
# tried. The two are not interchangeable: the gpt-5 and o-series families
# reject `max_tokens` outright and name `max_completion_tokens` in the
# refusal, while plenty of OpenAI-compatible servers know only the older
# spelling. An endpoint that refuses both is asked for no cap at all — the
# only caller (the session compact turn) truncates on this side anyway.
COMPACT_CAP_FIELDS = ("max_tokens", "max_completion_tokens")

# Headroom added to the cap under the `max_completion_tokens` spelling, and
# only under it. That spelling is what the reasoning families answer to, and
# on those the budget covers the reasoning *before* the reply: a cap sized for
# the report alone is spent thinking, and the request comes back with an empty
# message. Measured live (260913 smoke matrix, gpt-5.6-luna): every session
# cell lost its first compaction to exactly this — a billed request answering
# nothing, the window kept, the report retried on the next unit.
#
# CHOSEN, not measured: 1500 covers the reasoning budgets observed there with
# room over. It cannot make the seed bigger — the client truncates at
# SEED_CAP_TOKENS whatever arrives — so the whole cost of being generous here
# is some completion tokens on one request per window, against a wasted
# request per run if it is too small.
REASONING_ALLOWANCE_TOKENS = 1500

# How an endpoint says it does not know a field, as opposed to not liking the
# value in it. The difference decides whether a cap is given up for the rest
# of the run: "max_tokens is too large: 500 > limit" and "max_tokens exceeds
# the context length" both name the field while confirming the endpoint
# understands it, and treating either as a refusal of the parameter would
# strip the cap permanently over one bad request.
PARAMETER_REFUSAL_PHRASES = (
    "unsupported parameter",
    "unsupported_parameter",
    "unknown parameter",
    "unrecognized",
    "unrecognised",
    "is not supported",
    "not supported with",
    "no longer supported",
)

# ...except when the endpoint is refusing the VALUE in the field, which it
# can only do by understanding the field. These overlap in wording — a real
# 400 reads "Unsupported value: max_tokens=0 is not supported; must be
# greater than zero" — so a value complaint wins over a refusal phrase
# outright, and the cap survives to be sent again with a sane number.
PARAMETER_VALUE_COMPLAINTS = re.compile(
    r"unsupported value|invalid value|must be (greater|less|at least|no more)"
    r"|too large|too small|maximum|minimum|greater than|less than",
    re.IGNORECASE,
)

# One garbled response from a proxy must not cost the whole book its structured
# mode. A genuinely unsupported endpoint still pays at most this many attempts.
STRUCTURED_FAILURE_THRESHOLD = 2

# Probe verdict -> the cheapest rung worth *starting* at. Advisory only:
# descent is failure-driven, so a wrong guess costs one request.
ENTRY_RUNG = {
    "strict": "json_schema",
    "shape": "json_schema",
    "json": "json_object",
}


def grade_probe_response(completion):
    """Grade a probe completion: 'strict', 'shape', 'json', 'unsupported'.

    The prompt asks for plain text, so anything JSON-shaped that comes
    back is evidence of *some* structuring. The four verdicts map onto the
    four entry rungs, which is all a verdict is used for in
    classification — a wrong guess costs one request, not the run.
    """
    choice = completion.choices[0]
    if getattr(choice, "finish_reason", "stop") != "stop":
        return "unsupported"

    content = getattr(choice.message, "content", None)
    if not content:
        return "unsupported"

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return "unsupported"

    # Right JSON, wrong keys: json mode is on, the schema was not applied.
    # Worth knowing — such an endpoint should enter at the json_object
    # rung rather than being lumped in with prose-only ones.
    if not isinstance(parsed, dict) or set(parsed) != {PROBE_KEY}:
        return "json"
    if not isinstance(parsed[PROBE_KEY], str):
        return "json"

    # Some backends honor the structure but ignore `enum`. Still usable: our
    # real schemas constrain shape only, never values.
    return "strict" if parsed[PROBE_KEY] == PROBE_EXPECTED else "shape"


def probe_structured_output(client, model, extra_body=None):
    """Ask `model` whether the endpoint really applies a strict JSON Schema.

    Grades the response body: accepting the request proves nothing, because
    OpenAI-compatible proxies routinely accept `response_format` and drop it.
    No temperature and no token cap — the probe must test exactly one
    capability, and a cap would be rejected by o-series/gpt-5 models or eaten
    by reasoning tokens, producing a false negative.

    `extra_body` is the run's own `--extra_body`, for the reason
    `probe_model_route` gives: a verdict earned on a request shape the run
    never sends is a verdict about a different endpoint.

    Returns a verdict string. Fatal errors propagate; a transient outage
    raises `ProbeDeferred` so no verdict is cached.
    """
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROBE_PROMPT}],
            response_format={
                "type": "json_schema",
                "json_schema": STRUCTURED_PROBE_SCHEMA,
            },
            extra_body=extra_body or None,
        )
    except PROBE_FATAL_ERRORS:
        raise
    except PROBE_TRANSIENT_ERRORS as e:
        raise ProbeDeferred(str(e)) from e
    except Exception as e:
        # Ambiguous (400 for an unknown param, 500 from a local server, ...):
        # not a usable endpoint for schemas either way, so degrade loudly.
        # redact: this request carries the client's --extra_headers, and a
        # refusal routinely quotes what it objected to.
        return f"request rejected: {redact(e)}"

    return grade_probe_response(completion)


def classify_bad_request(error):
    """What a 400 was about: 'temperature', 'schema', 'max_tokens' or 'other'.

    Without this, a temperature rejection is misread as "no schema support":
    the model gets demoted for the rest of the run and the real cause never
    reaches the user.
    """
    text = str(error).lower()
    if "temperature" in text:
        return "temperature"
    if "response_format" in text or "json_schema" in text:
        return "schema"
    if (
        any(field in text for field in COMPACT_CAP_FIELDS)
        and any(phrase in text for phrase in PARAMETER_REFUSAL_PHRASES)
        and not PARAMETER_VALUE_COMPLAINTS.search(text)
    ):
        return "max_tokens"
    return "other"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_endpoint_models(client):
    """Model ids the endpoint admits to, or None when it has no such endpoint.

    None is not an error: plenty of OpenAI-compatible servers implement
    `/chat/completions` and nothing else, so the caller falls back to asking
    each named model directly.
    """
    try:
        return [i["id"] for i in client.models.list().model_dump()["data"]]
    except (NotFoundError, BadRequestError):
        print(
            "[yellow]This endpoint has no model listing to compare a name "
            "against.[/yellow]"
        )
        return None
    except Exception as e:
        print(
            f"[yellow]Error checking model availability: {redact(e)}. "
            f"Retrying...[/yellow]"
        )
        raise


def names_missing_model(error, model):
    """Whether `error` is the endpoint refusing to route `model` at all.

    A 404 is unambiguous. A 400 counts only when the field it blames is the
    model, or, absent a named field, when it names the model and says it
    does not exist without complaining about a parameter.
    """
    if isinstance(error, NotFoundError) or getattr(error, "status_code", None) == 404:
        return True
    if not isinstance(error, BadRequestError):
        return False
    text = str(error).lower()
    if "model_not_found" in text:
        return True
    param = _named_param(text)
    if param is not None:
        return param == "model"
    if any(word in text for word in PARAMETER_COMPLAINT_WORDS):
        return False
    named = bool(model) and model.lower() in text
    return named and any(phrase in text for phrase in MODEL_NOT_FOUND_PHRASES)


def _named_param(text):
    """The field an error blames, when it says so: `'param': 'max_tokens'`."""
    match = re.search(r"['\"]param['\"]:\s*['\"]([\w.\-\[\]]+)['\"]", text)
    return match.group(1) if match else None


def probe_model_route(client, model, extra_body=None):
    """Ask this endpoint to serve `model` once, as cheaply as a request can be.

    Gateways routinely serve models they do not list, so the route is asked,
    not the listing. Raises `ModelUnavailable` when the endpoint says there
    is no such model; any other failure is re-raised untouched, since it is
    not an answer about the model.

    `extra_body` is the run's own `--extra_body`. Asking without it would
    check a request the run never makes: an endpoint that needs a field to
    answer at all would be judged on a shape it is not given.
    """
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": ROUTE_PROBE_PROMPT}],
            extra_body=extra_body or None,
        )
    except Exception as e:
        if names_missing_model(e, model):
            raise ModelUnavailable(
                f"This endpoint does not serve the model {model!r} " f"({redact(e)})."
            ) from e
        raise
    return None


def _listing_hint(client):
    """What the endpoint admits to serving, for an error message. Best effort."""
    try:
        return fetch_endpoint_models(client)
    except Exception:
        return None


def describe_listing(api_models, limit=LISTING_HINT_LIMIT):
    """A listing short enough to read under an error message."""
    names = list(api_models or [])
    if len(names) <= limit:
        return f"{names}"
    return f"{names[:limit]} and {len(names) - limit} more"


def verify_model_routes(client, model_list, extra_body=None, probe=None):
    """Which of `model_list` this endpoint actually serves, in the order given.

    One route probe per model. A model that answers is usable; a model the
    endpoint has no route for is dropped and named; anything else that goes
    wrong is not evidence about the model, so the model is kept and the run
    finds out from the real request, where transport failures belong.

    Returns success plus the split, in the order the caller asked for, so
    rotation order stays the order the user typed.

    `probe` replaces `probe_model_route` for a caller that has something to
    settle on this request — `--no-thinking` negotiates its field here,
    because this is the cheapest request the run makes.
    """
    probe = probe or probe_model_route
    model_list = list(model_list)
    # silent when every model answers: only a refusal is news
    available, unavailable = [], []
    for model_name in model_list:
        try:
            probe(client, model_name, extra_body=extra_body)
        except ModelUnavailable as e:
            print(f"[red]{redact(e)}[/red]")
            unavailable.append(model_name)
            continue
        except Exception as e:
            print(
                f"[yellow]ℹ could not confirm {model_name!r} ({redact(e)}); "
                f"that is no answer about the model, so the run keeps it"
                f"[/yellow]"
            )
        available.append(model_name)

    # a listing is a hint under a refusal, never a gate in front of one
    api_models = _listing_hint(client) if unavailable else None
    if unavailable and api_models:
        print(f"[yellow]This endpoint lists: {describe_listing(api_models)}[/yellow]")

    if not available:
        print(
            f"[red]Error: this endpoint served none of the models "
            f"{model_list}.[/red]"
        )
        print(
            "[yellow]Check the model id, the API base, and your key's model "
            "permissions.[/yellow]"
        )
        return {
            "success": False,
            "available_models": [],
            "unavailable_models": model_list,
            "api_models": api_models,
        }

    if unavailable:
        print(
            f"[yellow]Warning: {unavailable} not served by this endpoint, "
            f"using {available}[/yellow]"
        )

    return {
        "success": True,
        "available_models": available,
        "unavailable_models": unavailable,
        "api_models": api_models,
    }


class CapabilityLedger:
    """What each model turned out to support, and how far it has been trusted.

    Every entry is keyed by model because `--model_list` rotates across models
    of differing capability. All state changes take the lock: N parallel
    workers must issue one probe per model, not N. The lock is reentrant
    because recording a verdict happens while the probe still holds it.
    """

    def __init__(self):
        self.lock = RLock()
        # Probe verdicts: "strict" | "shape" | "json" | False.
        self.verdicts = {}
        # Learned from the first rejection, never asked again.
        self.temperature_unsupported = {}
        # How far down COMPACT_CAP_FIELDS each model has pushed us: 0 is the
        # first spelling, len(COMPACT_CAP_FIELDS) means it takes no cap.
        self.compact_cap_field = {}
        # Consecutive capability failures, and models whose probe was
        # postponed by an outage (tracked only to keep the log to one line).
        self.failures = {}
        self.deferred = set()
        # Image support, a separate question: "verified" | "unsupported".
        # Its own dict and no streak — a schema demotion says nothing about
        # images, and an image refusal never touches `verdicts`.
        self.vision = {}
        # Optional image-request fields (`vision.VISION_REQUEST_PARAMS`) a
        # model refused once, so later requests stop sending them.
        self.vision_unsent = {}

    def ensure_verdict(self, model, probe=None):
        """The model's graded schema support, probed at most once.

        `probe` is a callable taking the model name; passing None records "no
        support" without a request, for translators that do not route through
        an OpenAI-compatible client.
        """
        with self.lock:
            if model not in self.verdicts:
                if probe is None:
                    self.verdicts[model] = False
                else:
                    try:
                        self.record(model, probe(model))
                    except ProbeDeferred as e:
                        self.defer(model, e)
            return self.verdicts.get(model, False)

    def ensure_vision(self, model, probe=None):
        """Whether the model reads images here: 'verified', 'unsupported', 'deferred'.

        Probed at most once per model, under the same lock as the schema
        verdict so parallel workers issue one probe. `probe` takes the model
        name (`vision.probe_image` bound to a client); None records
        'unsupported' without a request. An outage records nothing and
        answers 'deferred', so the next call asks again.
        """
        with self.lock:
            if model not in self.vision:
                if probe is None:
                    self.vision[model] = "unsupported"
                else:
                    try:
                        verdict = probe(model)
                    except ProbeDeferred as e:
                        print(
                            f"[yellow]ℹ could not ask '{model}' about images "
                            f"right now ({e}); asking again next time[/yellow]"
                        )
                        return "deferred"
                    self.vision[model] = (
                        "verified" if verdict == "verified" else "unsupported"
                    )
            return self.vision[model]

    def record(self, model, verdict):
        """Store the verdict string; False means no schema support at all."""
        stored = verdict if verdict in ("strict", "shape", "json") else False
        with self.lock:
            self.verdicts[model] = stored
        if stored == "shape":
            print(
                f"[yellow]ℹ '{model}' honors JSON schema shape but not value "
                f"constraints; using the delimiter method for translation, "
                f"schema kept for classification[/yellow]"
            )
        elif stored == "json":
            print(
                f"[yellow]ℹ '{model}' returns JSON but does not apply the "
                f"schema; using the delimiter method for translation, "
                f"classification asks in the prompt[/yellow]"
            )
        elif not stored:
            print(
                f"[yellow]ℹ '{model}' doesn't apply JSON schema ({verdict}), "
                f"using delimiter method[/yellow]"
            )

    def defer(self, model, error):
        """Postpone the verdict: record nothing so the next call probes again."""
        with self.lock:
            first_time = model not in self.deferred
            self.deferred.add(model)
        if first_time:
            print(
                f"[yellow]ℹ could not probe '{model}' right now ({error}); "
                f"using the delimiter method until the endpoint answers[/yellow]"
            )

    def note_success(self, model):
        """A working structured call clears the model's failure streak."""
        if self.failures.get(model):
            with self.lock:
                self.failures.pop(model, None)

    def demote(self, model, reason):
        """Count a capability failure and, on a streak, stop paying for it.

        The caller falls back for the current paragraph or batch either way. The
        streak is what keeps a single garbled proxy response from disabling
        structured outputs for the rest of a multi-hour run, while an endpoint
        that really ignores the schema still costs only
        `STRUCTURED_FAILURE_THRESHOLD` attempts instead of three tenacity
        retries per batch, forever.
        """
        with self.lock:
            failures = self.failures.get(model, 0) + 1
            self.failures[model] = failures
            demote = failures >= STRUCTURED_FAILURE_THRESHOLD
            already_demoted = self.verdicts.get(model) is False
            if demote:
                self.verdicts[model] = False

        if demote:
            if not already_demoted:
                print(
                    f"[yellow]ℹ '{model}' did not honor the JSON schema "
                    f"({reason}); switching to the delimiter method[/yellow]"
                )
        else:
            print(
                f"[yellow]ℹ '{model}' did not honor the JSON schema "
                f"({reason}); falling back for this one and trying structured "
                f"outputs once more[/yellow]"
            )

    def note_temperature_rejected(self, model):
        """Remember that `model` owns its sampling; True the first time only."""
        with self.lock:
            first_time = not self.temperature_unsupported.get(model)
            self.temperature_unsupported[model] = True
        return first_time

    def compact_cap_kwargs(self, model, cap):
        """How to ask `model` for a reply no longer than `cap`, or nothing.

        Empty once the endpoint has turned down every spelling there is —
        which is not a failure: the caller's own truncation is what the seed
        bound actually rests on, and a cap the endpoint applies only saves
        paying for output that would be cut anyway.

        The `max_completion_tokens` spelling gets REASONING_ALLOWANCE_TOKENS
        on top: the endpoints that insist on it spend that budget on hidden
        reasoning before the reply, so a cap sized for the report alone buys
        thinking and an empty message.
        """
        with self.lock:
            index = self.compact_cap_field.get(model, 0)
        if index >= len(COMPACT_CAP_FIELDS):
            return {}
        field = COMPACT_CAP_FIELDS[index]
        if field == "max_completion_tokens":
            return {field: cap + REASONING_ALLOWANCE_TOKENS}
        return {field: cap}

    def note_compact_cap_rejected(self, model):
        """Remember that `model` refused this spelling; name the next, if any."""
        with self.lock:
            index = self.compact_cap_field.get(model, 0) + 1
            self.compact_cap_field[model] = index
        return COMPACT_CAP_FIELDS[index] if index < len(COMPACT_CAP_FIELDS) else None

    def sampling_kwargs(self, model, temperature):
        """Sampling parameters to send, or nothing when the model owns them.

        `DEFAULT_TEMPERATURE` is the API's own default, so sending it changes no
        output — but gpt-5.x and the o-series reject *any* explicit temperature,
        so an unrequested default is pure downside. A model that turned one down
        is remembered and never asked again.
        """
        if self.temperature_unsupported.get(model):
            return {}
        if temperature is None or temperature == DEFAULT_TEMPERATURE:
            return {}
        return {"temperature": temperature}
