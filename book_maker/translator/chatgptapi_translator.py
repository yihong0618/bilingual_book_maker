import re
import time
import os
import shutil
from os import environ
from itertools import cycle
from types import SimpleNamespace
import json
from functools import lru_cache
from pathlib import Path
from threading import Lock

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    LengthFinishReasonError,
    OpenAI,
    RateLimitError,
)
from pydantic import ConfigDict, Field, ValidationError, create_model
from rich import print
from rich.markup import escape
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_not_exception_type,
)

from .base_translator import (
    AsyncTranslationUnsupported,
    Base,
    BatchMismatch,
    TranslationContext,
    TranslationResult,
)
from .capabilities import (
    ENTRY_RUNG,
    RUNG_REFUSAL_ERRORS,
    CapabilityLedger,
    ModelUnavailable,
    StructuredOutputUnsupported,
    StructuredRefusal,
    classify_bad_request,
    describe_listing,
    probe_structured_output,
    verify_model_routes,
)
from ..redaction import redact, remember
from ..structured import (
    RungRejected,
    extract_json_object,
    prompt_with_schema,
    schema_required_keys,
    unwrap_schema_echo,
)
from ..config import config
from ..glossary import Glossary
from ..session_context import (
    HandoffReport,
    SessionHistory,
    compact_budget_for,
    handoff_prompt,
    strip_handoff_glossary,
)

CHATGPT_CONFIG = config["translator"]["chatgptapi"]

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}


# The schema is the last thing the model reads before it decodes, and a bare
# `translated`/`paragraphs` field says nothing about *which* language to produce
# — the target language would otherwise live only in the middle of the prompt.
# So the language is baked into the field name, its description and the schema
# name: `simplified chinese` -> `simplified_chinese_translation`. Separator is
# `_` rather than `-`; hyphens are legal JSON keys, but this file exists because
# OpenAI-compatible proxies mishandle things, and `_` is the conservative shape.
def _language_slug(language):
    """Field-name token for `language`, or "" when there is nothing usable."""
    return re.sub(r"[^a-z0-9]+", "_", (language or "").strip().lower()).strip("_")


def single_field_name(language):
    """Name of the single-translation field for `language`.

    Shared by the SDK path and the hand-built Batch API schema so the two cannot
    drift; the Batch API reader keys off this too.
    """
    slug = _language_slug(language)
    return f"{slug}_translation" if slug else "translated"


def batch_field_name(language):
    """Name of the batch-translation field for `language`."""
    slug = _language_slug(language)
    return f"{slug}_paragraphs" if slug else "paragraphs"


# The schema name is sent to the model, which never has to tell the single
# schema from the batch one -- a request carries exactly one. So name each
# schema after the field it wraps rather than after our own call sites.
def single_translation_model(language, source_language=None, field_language=None):
    """Structured single translation output, pinned to `language`."""
    # The cache is keyed positionally, so the default has to be filled in
    # here: `f(lang)` and `f(lang, None)` are two entries otherwise, and two
    # entries mean two distinct classes for one schema — an identity check
    # on `response_format` then fails for no visible reason.
    return _single_translation_model(language, source_language, field_language)


@lru_cache(maxsize=None)
def _single_translation_model(language, source_language, field_language):
    field = single_field_name(field_language or language)
    return create_model(
        field,
        __config__=ConfigDict(extra="forbid"),
        **{
            field: (
                str,
                Field(description=_single_field_description(language, source_language)),
            )
        },
    )


@lru_cache(maxsize=None)
def batch_item_model(language, field_language=None):
    """One reply item: the id that was sent back, and its translation.

    Nothing else — no notes, no confidence, no echo of the source. Every
    extra property is a place for the model to spend output tokens, and
    strict mode forbids adding one later without a schema change anyway.
    """
    field = single_field_name(field_language or language)
    return create_model(
        f"{field}_item",
        __config__=ConfigDict(extra="forbid"),
        id=(
            int,
            Field(
                description=(
                    "The id of the input paragraph this translates, copied "
                    "exactly from the request."
                )
            ),
        ),
        **{
            field: (
                str,
                Field(description=_single_field_description(language)),
            )
        },
    )


def batch_translation_model(language, n, source_language=None, field_language=None):
    """Structured batch translation output for `n` paragraphs.

    Per-(language, n) because the count is part of what the model is being
    told: the prose tail says EXACTLY n, and the schema name carries it too.
    Strict mode does not honour `minItems`/`maxItems`, so the count is *not*
    a decode-time constraint — it is checked client-side, and a miscount
    raises `BatchMismatch` for the loader's ladder to divide.

    Items echo the id they were sent with. Alignment is by id, never by
    array position: a model that reorders its answers, or drops one and
    keeps the rest, is silently misaligned under positional reading.
    """
    # positional cache key; see `single_translation_model`
    return _batch_translation_model(language, n, source_language, field_language)


@lru_cache(maxsize=None)
def _batch_translation_model(language, n, source_language, field_language):
    field = batch_field_name(field_language or language)
    return create_model(
        field,
        __config__=ConfigDict(extra="forbid"),
        **{
            field: (
                list[batch_item_model(language, field_language)],
                Field(
                    description=_batch_field_description(language, n, source_language)
                ),
            )
        },
    )


def _single_field_description(language, source_language=None):
    target = language or "the target language"
    if source_language:
        return f"The source text translated from {source_language} into {target}."
    return f"The source text translated into {target}."


def _batch_field_description(language, n=None, source_language=None):
    target = language or "the target language"
    count = f"exactly {n}" if n is not None else "one"
    source = f" from {source_language}" if source_language else ""
    return (
        f"The source paragraphs translated{source} into {target}: {count} "
        f"item(s), one per input paragraph, each carrying back the id it "
        f"was given."
    )


@lru_cache(maxsize=None)
def single_translation_schema(language, field_language=None):
    """Mirror of `single_translation_model` for the Batch API.

    Batch JSONL bodies are built by hand and so cannot use the SDK's Pydantic
    support; both sides take their field name from `single_field_name`.
    """
    field = single_field_name(field_language or language)
    return {
        "name": field,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                field: {
                    "type": "string",
                    "description": _single_field_description(language),
                }
            },
            "required": [field],
            "additionalProperties": False,
        },
    }


@lru_cache(maxsize=None)
def batch_translation_schema(language, n, source_language=None, field_language=None):
    """Mirror of `batch_translation_model` as a plain JSON Schema dict.

    The json_object degree cannot be handed a schema at all — the endpoint
    guarantees only that *some* JSON comes back — so the shape is described
    in the prompt instead (`prompt_with_schema`). This is the dict that
    description is rendered from, and the source of the one top-level key
    the reply is checked for before anything is read out of it.
    """
    field = batch_field_name(field_language or language)
    item_field = single_field_name(field_language or language)
    return {
        "name": field,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                field: {
                    "type": "array",
                    "description": _batch_field_description(
                        language, n, source_language
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "integer",
                                "description": (
                                    "The id of the input paragraph this "
                                    "translates, copied exactly from the "
                                    "request."
                                ),
                            },
                            item_field: {
                                "type": "string",
                                "description": _single_field_description(
                                    language, source_language
                                ),
                            },
                        },
                        "required": ["id", item_field],
                        "additionalProperties": False,
                    },
                }
            },
            "required": [field],
            "additionalProperties": False,
        },
    }


# Probe verdicts the id-echo batch contract may run at. "strict" and "shape"
# are sent a `json_schema` request: the batch schema pins no *values* (the
# target language rides in the field name and in the prose tail), so an
# endpoint that honours shape honours all of it. "json" is sent
# `json_object` plus the schema described in the prompt, and its reply is
# read out of whatever prose or fences came with it.
SCHEMA_BATCH_DEGREES = ("strict", "shape")
BATCH_STRUCTURED_DEGREES = SCHEMA_BATCH_DEGREES + ("json",)


def _echoed_id(value):
    """An id from an unconstrained reply, as the integer it was sent as.

    Nothing constrained the type here, and an id echoed as "3" is the id 3.
    A boolean is not an id at all, and is deliberately taken out of the
    integer space it would otherwise share (`True == 1`) so that a reply
    carrying one fails alignment instead of passing it.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value)
    return value


# Per-request timeout and SDK retries. The SDK defaults (600 s, 3 tries) let
# a gateway that accepts a request and never answers hold a run for half an
# hour with nothing printed.
REQUEST_LIMITS = {"timeout": 300.0, "max_retries": 1}


class ClassifierSession:
    """Plan classification over one append-only message list.

    The same prefix discipline `session_context` keeps for translation, for
    the same reason: every turn is the previous request plus its reply plus
    the new signatures, so an endpoint with prompt caching re-reads the
    trunk at its cache rate instead of buying it again three signatures at
    a time. Nothing already sent is ever rewritten — the classifier
    restarts a session rather than editing one.

    Kept apart from `self.session`, which is the *translation* history:
    `--use_context` decides whether that exists, and this one is planning
    machinery that runs before the first paragraph either way.
    """

    def __init__(self, translator, model=None):
        self.translator = translator
        self.model = model or translator.model
        self._messages = []

    def budget(self):
        """The window the classifier works to: `--context-compact-at`, else
        this session's *own* model's default.

        `--plan-classify-model` is exactly the case where those differ: the
        classifier's conversation is held with that model and rolls over
        against that model's window, so deriving the budget from whatever
        the translation runs on described a different session.
        """
        if self.translator.context_compact_at is None:
            return compact_budget_for(self.model)
        return self.translator.context_compact_at

    def start(self, trunk):
        """Open a fresh conversation. The trunk rides in the system message,
        where it is the stable head of every later request in this session.

        Behind it, one demonstrated exchange: the example turn as a user
        message and the reply it should have got as an assistant one. The
        first *real* turn is then never the first turn of the conversation,
        which is what a weak endpoint needs to answer it in the format rather
        than in prose about it. The seeded reply is text this side wrote; it
        is never read back as a verdict, because `ask` returns only what the
        endpoint says.

        Every restart re-seeds it: the classifier restarts rather than
        compacts, and a fresh session with no demonstration is a fresh
        first-turn problem.
        """
        # Imported here, not at module scope: the loader package imports this
        # one back, and the classifier text is wanted only once a session
        # actually opens.
        from ..loader.classify.session import EXAMPLE_REPLY, build_example_turn

        self._messages = [
            {"role": "system", "content": trunk},
            {"role": "user", "content": build_example_turn()},
            {"role": "assistant", "content": EXAMPLE_REPLY},
        ]

    def ask(self, text):
        messages = [*self._messages, {"role": "user", "content": text}]
        reply = self.translator._classify_turn(messages, self.model)
        self._messages = [*messages, {"role": "assistant", "content": reply or ""}]
        return reply

    def messages(self):
        """What the next request will replay. Callers must not mutate it."""
        return list(self._messages)


class ChatGPTAPI(Base):
    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    # Subclasses that do not route through `self.openai_client` must opt out:
    # probing them would send the capability request to the wrong endpoint.
    SUPPORTS_STRUCTURED_OUTPUTS = True

    # The address this route calls when the command names none. None means
    # the OpenAI SDK's own default, api.openai.com. A subclass that stands
    # for one vendor's endpoint sets it; the CLI reads the same value off
    # FORMAT_DEFAULT_BASES so that --api_format alone is a complete route.
    DEFAULT_API_BASE = None

    SUPPORTS_SESSION_CONTEXT = True
    SUPPORTS_PARALLEL_CONTEXT = True
    SUPPORTS_BATCH_API = True
    SUPPORTS_REQUEST_EXTRAS = True
    SUPPORTS_GLOSSARY = True
    # Session-mode state, declared here so the window-mode path is well
    # defined on any instance — including the subclasses and test fixtures
    # that build one without running __init__. `session is None` means window
    # mode everywhere in this class.
    session = None
    # `pinned` is the operator's --glossary file, `learned` what this run's
    # compacts established, `glossary` the two combined. None here rather than
    # an empty Glossary so an instance built without __init__ still answers
    # "no glossary" without constructing one.
    glossary = None
    pinned = None
    learned = None
    # Tri-state, from `--glossary-auto {on,off}`: None is "unsaid", and
    # `glossary_auto_on` below turns that into the default for this run.
    glossary_auto = None
    handoff_path = None
    context_compact_at = None
    no_context_compact = False
    # Every model --model_list rotates through, not just the current one.
    _model_names = ()
    # Models not yet confirmed served, and the refusal if one was. One dict,
    # not two attributes: parallel workers translate through shallow copies,
    # and a shared dict settles the question for all of them at once.
    _route_state = None
    context_mode = "window"
    # Units one request may carry when the endpoint is below strict decoding.
    # Mirrors `book_maker.loader.plan.SUBSTRICT_GROUP_MAX_UNITS` (pinned by a
    # test) but is spelled here as a plain class attribute: importing the
    # loader from the translator only works lazily, and an instance built
    # without __init__ — a subclass, a test double — still needs the value.
    # The CLI lowers it alongside `--max-batch-units`.
    substrict_batch_cap = 16

    # Set by the CLI from --quiet. Suppresses this class's own echoes.
    quiet = False
    style_note = None

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        temperature=1.0,
        context_flag=False,
        context_paragraph_limit=0,
        context_mode="window",
        context_compact_at=None,
        no_context_compact=False,
        glossary=None,
        glossary_auto=None,
        style_note=None,
        handoff_path=None,
        extra_body=None,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        api_base = api_base or self.DEFAULT_API_BASE
        self.openai_client = OpenAI(
            api_key=next(self.keys), base_url=api_base, **REQUEST_LIMITS
        )
        self.api_base = api_base

        self.prompt_template = (
            prompt_template
            or environ.get(PROMPT_ENV_MAP["user"])
            or self.DEFAULT_PROMPT
        )
        self.prompt_sys_msg = (
            prompt_sys_msg
            or environ.get(
                "OPENAI_API_SYS_MSG",
            )  # XXX: for backward compatibility, deprecate soon
            or environ.get(PROMPT_ENV_MAP["system"])
            or ""
        )
        self.system_content = environ.get("OPENAI_API_SYS_MSG") or ""
        self.temperature = temperature
        self.model_list = None
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        # Session mode replaces the window entirely; `session is None` is the
        # single test for "are we in window mode" everywhere below.
        self.context_mode = context_mode or "window"
        self.session = (
            SessionHistory()
            if context_flag and self.context_mode == "session"
            else None
        )
        self.context_compact_at = context_compact_at
        self.no_context_compact = no_context_compact
        # `pinned` is the operator's --glossary file and never changes.
        # `learned` accumulates what compacts establish. `glossary` is the two
        # combined, pins on top, and is what gets injected per unit. Keeping
        # `pinned` separate is what lets anything downstream record the pinned
        # file alone: nothing merges back into it.
        self.pinned = glossary or Glossary()
        self.learned = Glossary()
        self.glossary = self.pinned
        self.glossary_auto = glossary_auto
        self.style_note = style_note
        self.handoff_path = Path(handoff_path) if handoff_path else None
        self._compact_failures = 0
        if context_paragraph_limit > 0:
            # not set by user, use default
            self.context_paragraph_limit = context_paragraph_limit
        else:
            # set by user, use user's value
            self.context_paragraph_limit = CHATGPT_CONFIG["context_paragraph_limit"]
        self.batch_text_list = []
        self.batch_info_cache = None
        self.result_content_cache = {}
        self._api_lock = Lock()
        self._async_clients = {}
        self.extra_body = extra_body or {}
        # Set by --extra_headers, after construction; on the client rather
        # than the call so the capability probe, the route check and the
        # model listing carry them too.
        self.extra_headers = {}

        # What this endpoint turned out to support, learned at runtime and
        # keyed by model because --model_list rotates across models of
        # differing capability.
        self.capabilities = CapabilityLedger()
        self.model = (
            None  # Will be set by rotate_model() after model_list is initialized
        )

    def _ensure_models_routable(self):
        """Confirm the endpoint serves the models this run was given. Once.

        Not part of `set_model_list`: a run that only writes a plan must not
        pay for a probe. Settled under the API lock, so parallel workers issue
        one probe per model, and a refusal is re-raised rather than re-probed
        across `get_translation`'s retries.
        """
        state = self._route_state
        if state is None:
            if not self._model_names:
                return
            # a subclass that named its models in __init__ (OrcaRouterTranslator)
            state = self._route_state = {
                "pending": list(self._model_names),
                "failure": None,
            }
        with self._api_lock:
            if state["failure"] is not None:
                raise state["failure"]
            pending = state["pending"]
            if pending is None:
                return
            # Cleared before the probe, not after: the endpoint is asked once
            # per run whatever it answers.
            state["pending"] = None

            result = verify_model_routes(
                self.openai_client, pending, extra_body=self.extra_body or None
            )
            if not result["success"]:
                listed = result["api_models"]
                state["failure"] = ModelUnavailable(
                    f"This endpoint served none of the models {pending}."
                    + (f" It lists {describe_listing(listed)}." if listed else "")
                    + " Check the model id, the API base, and your key's "
                    "model permissions."
                )
                raise state["failure"]

            available = result["available_models"]
            if available == pending:
                return
            # A partially available list narrows to what works, in the order
            # the user gave. The model in hand may be one of the refused ones,
            # in which case rotation restarts on the first that answered.
            self._model_names = available
            self.model_list = cycle(available)
            if self.model not in available:
                self.model = available[0]

    def _probe_verdict(self, model=None):
        """The endpoint's graded schema support, probed once per model.

        One of "strict", "shape", "json" or False. Every paid path asks for
        a verdict before it spends, so this is also where the route check
        runs — before the model name is read, since the check may drop the
        one in hand.
        """
        self._ensure_models_routable()
        model = model or self.model
        probe = self._probe if self.SUPPORTS_STRUCTURED_OUTPUTS else None
        return self.capabilities.ensure_verdict(model, probe)

    def _probe(self, model):
        return probe_structured_output(
            self.openai_client, model, extra_body=self.extra_body or None
        )

    def _ensure_structured_support(self, model=None):
        """Whether *translation* may use a schema. Only "strict" qualifies.

        Our translation schema pins the target language as a value constraint
        (#544), so an endpoint that honors shape but ignores values gives us a
        schema that cannot do the one job we added it for — worse than the
        delimiter method, which at least states the language in the prompt.

        Classification does not come through here at all: it needs a JSON
        object with legal values, not an endpoint that applied our schema, so
        the verdict only picks its entry rung (`structured_rungs`).
        """
        return self._probe_verdict(model) == "strict"

    def _structured_enabled(self, model=None):
        """The degree the id-echo batch contract may run at, or False.

        Batching asks a different question from single-paragraph
        translation. A single translation is pinned to its target language
        by a schema *value* (#544), which only a "strict" endpoint applies —
        but a batch's schema pins no values at all, and the thing batching
        actually needs is that ids come back attached to their translations.
        The 260905 off-OpenAI eval measured that contract holding at the
        json_object degree on every endpoint tried, so "shape" and "json"
        batch too; they are simply carried on a looser wire format, and the
        alignment checks that catch a bad strict reply catch a bad loose one
        the same way.
        """
        verdict = self._probe_verdict(model)
        return verdict if verdict in BATCH_STRUCTURED_DEGREES else False

    def _note_structured_success(self):
        """A working structured call clears the model's failure streak."""
        self.capabilities.note_success(self.model)

    def _demote_structured_outputs(self, reason):
        """Count a capability failure and, on a streak, stop paying for it."""
        self.capabilities.demote(self.model, reason)

    # Hoisted to `structured.py` — every provider's bottom rung needs it.
    _extract_json_object = staticmethod(extract_json_object)

    def structured_rungs(self, prompt, schema, model=None):
        """json_schema -> json_object + described schema -> plain prompt.

        A real ladder now: `run_rungs` descends whenever a rung is refused or
        answers unusably, so an endpoint that accepts the one-key probe schema
        and then rejects a twelve-property one still classifies, and a
        `strict` endpoint that returns prose falls through instead of aborting
        the run.
        """
        # The verdict first: asking for it is what checks the route, and the
        # check may narrow `--model_list` out from under `self.model`.
        entry = ENTRY_RUNG.get(self._probe_verdict(model), "prompt")
        target = model or self.model
        ladder = [
            ("json_schema", lambda: self._json_schema_rung(prompt, schema, target)),
            ("json_object", lambda: self._json_object_rung(prompt, schema, target)),
            ("prompt", lambda: self._prompt_rung(prompt, schema, target)),
        ]
        start = next(i for i, (name, _) in enumerate(ladder) if name == entry)
        return ladder[start:]

    def _completion_text(self, model, content, **kwargs):
        """One single-turn request, with shape refusals marked as such."""
        try:
            # Every rung and the schema probe come through here, so this is
            # where --extra_body reaches them. `setdefault`: a caller that
            # already built one owns it.
            kwargs.setdefault("extra_body", self.extra_body or None)
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
        except RUNG_REFUSAL_ERRORS as e:
            self.warn_if_extras_refused(e)
            raise RungRejected(e) from e
        self._note_usage(completion, model)
        return completion.choices[0].message.content

    def _json_schema_rung(self, prompt, schema, model):
        text = self._completion_text(
            model,
            prompt,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        return unwrap_schema_echo(
            extract_json_object(text, schema_required_keys(schema))
        )

    def _json_object_rung(self, prompt, schema, model):
        text = self._completion_text(
            model,
            prompt_with_schema(prompt, schema),
            response_format={"type": "json_object"},
        )
        return unwrap_schema_echo(
            extract_json_object(text, schema_required_keys(schema))
        )

    def _chat_completion(self, prompt, model=None):
        return self._completion_text(model or self.model, prompt)

    def classify_session(self, model=None):
        """See `Base.classify_session`. One conversation, held in messages."""
        return ClassifierSession(self, model)

    def _classify_turn(self, messages, model):
        """One turn of a classifier session: the whole history, one reply.

        Not `_completion_text`, which sends a single user message — the
        point here is that the prefix is re-sent byte for byte. Billed like
        any other request, so the meter is told about it.
        """
        completion = self._request(
            lambda sampling: self.openai_client.chat.completions.create(
                model=model,
                messages=messages,
                extra_body=self.extra_body if self.extra_body else None,
                **sampling,
            ),
            model=model,
        )
        self._note_usage(completion, model)
        return completion.choices[0].message.content or ""

    def set_request_extras(self, extra_body=None, extra_headers=None):
        """See `Base.set_request_extras`.

        Headers go on the client rather than on each call, so they reach the
        capability probe, the route check and the model listing as well as
        the translate calls. Cached async clones are dropped so the next one
        is built with them.
        """
        self.extra_body = extra_body or {}
        self.extra_headers = extra_headers or {}
        remember(*self.extra_headers.values())
        if self.extra_headers:
            with self._api_lock:
                self.openai_client = self.openai_client.with_options(
                    default_headers=self.extra_headers
                )
                self._async_clients.clear()

    def rotate_key(self):
        with self._api_lock:
            self.openai_client.api_key = next(self.keys)

    def rotate_model(self):
        with self._api_lock:
            if self.model_list:
                self.model = next(self.model_list)

    # Both of these turn off exactly what session mode cannot afford, and both
    # are the same question — is a byte-stable prefix being maintained? — so
    # they answer it the same way, as they do on `Claude`. Window mode is
    # untouched by either.

    @property
    def BATCH_SYS_MSG_PER_REQUEST(self):
        """False while a session is open: the system message is part of the prefix.

        Borrowing it for the length of one grouped request moves the prefix
        for that request and leaves the next one no longer extending it —
        one full-price re-read of the accumulated history per batch, which is
        the one expense session mode exists to avoid. The batch contract is
        not lost: `_build_batch_prompt` also puts it at the head of the user
        prompt, and that rides with the request.
        """
        return self.session is None

    @property
    def BATCH_CONTEXT_PER_LINE(self):
        """False while a session is open: the history replays what was sent.

        A window keeps paragraphs, so it wants one pair per line. A session
        keeps requests, and a grouped request was a single exchange — split
        into per-line pairs, the history stops matching what the endpoint
        saw.
        """
        return self.session is None

    @property
    def glossary_auto_on(self):
        """Whether this run learns renderings from its own handoff reports.

        The derived glossary is a by-product of session compaction, so it can
        only exist where a session does. The default is therefore "on wherever
        a session runs": `--glossary-auto off` is the way to decline it, and
        `--glossary-auto on` cannot conjure one on the windowed path, where
        there is no compact turn to learn from (the CLI says so).
        """
        if self.session is None:
            return False
        return self.glossary_auto is not False

    def _user_content(self, text):
        """The user message for one unit.

        Deterministic for a given (text, glossary), which is what lets session
        mode store exactly what it sent without threading the string around —
        the marker preamble included, since it is a function of the text too.

        Pinned terms belong to *this* unit, so they go in the fresh tail
        message rather than the system prompt: a block that varies per unit
        sitting in a fixed position would invalidate the cached prefix on
        every request. Once this message is frozen into the history it stops
        varying, so it is stable there.
        """
        content = (
            self._marker_preamble(text)
            + self.prompt_template.format(text=text, language=self.language, crlf="\n")
            # No endpoint has a slot for `--prompt`'s style section, so it
            # rides here. Last, after the user's own template: it is the
            # standing instruction the run must not lose, and a fixed string
            # keeps every request's tail comparable.
            + self.style_suffix()
        )
        block = self.glossary.prompt_block(text) if self.glossary else ""
        return f"{block}\n\n{content}" if block else content

    def create_messages(self, text, intermediate_messages=None):
        content = self._user_content(text)

        sys_content = self._augment_system_content(self._system_message())
        messages = [
            {"role": "system", "content": sys_content},
        ]

        if intermediate_messages:
            messages.extend(intermediate_messages)

        messages.append({"role": "user", "content": content})
        return messages

    def create_context_messages(self, context: TranslationContext | None = None):
        messages = []
        if self.session is not None:
            # Session mode: the whole append-only history is the prefix. The
            # per-unit window below does not apply — that is the mode this
            # one replaces.
            return self.session.messages()
        if self.context_flag:
            if context is None:
                source_texts = self.context_list
                translated_texts = self.context_translated_list
            else:
                source_texts = context.source_texts
                translated_texts = context.translated_texts
                if not source_texts:
                    return messages
            messages.append({"role": "user", "content": "\n".join(source_texts)})
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(translated_texts),
                }
            )
        return messages

    def _create_async_client(self, key):
        return AsyncOpenAI(
            api_key=key,
            base_url=self.api_base,
            default_headers=self.extra_headers or None,
            **REQUEST_LIMITS,
        )

    def _get_async_client(self, key):
        cache_key = (self.api_base, key)
        with self._api_lock:
            if cache_key not in self._async_clients:
                self._async_clients[cache_key] = self._create_async_client(key)
            return self._async_clients[cache_key]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
        ),
        reraise=True,
    )
    async def translate_async(
        self, text: str, *, context: TranslationContext | None = None
    ) -> TranslationResult:
        if self.session is not None:
            # This path threads an immutable per-call TranslationContext,
            # which is the opposite of one shared append-only history: the
            # session would never be appended to, and every request would be
            # billed as an uncached fresh prefix. No loader uses this path
            # today; failing here keeps that from becoming a silent cost
            # regression the moment one does.
            raise AsyncTranslationUnsupported(
                "session context is not supported on the async path; "
                "use --use_context (window mode) there"
            )
        if type(self).create_chat_completion is not ChatGPTAPI.create_chat_completion:
            return await super().translate_async(text, context=context)

        with self._api_lock:
            key = next(self.keys)
            if self.model_list:
                model = (
                    next(self.model_list)
                    if hasattr(self.model_list, "__next__")
                    else self.model_list[0]
                )
            else:
                model = self.model

        current_context = context or TranslationContext()
        messages = self.create_messages(
            text, self.create_context_messages(current_context)
        )
        client = self._get_async_client(key)

        async def create(sampling):
            return await client.chat.completions.create(
                model=model,
                messages=messages,
                extra_body=self.extra_body if self.extra_body else None,
                **sampling,
            )

        try:
            completion = await create(self._sampling_kwargs(model))
        except BadRequestError as e:
            if classify_bad_request(e) != "temperature":
                raise
            self._note_temperature_rejected(model)
            completion = await create({})

        self._note_usage(completion, model)
        translated = completion.choices[0].message.content or ""
        if self.context_flag:
            current_context = current_context.append(
                text, translated, self.context_paragraph_limit
            )
        return TranslationResult(translated, current_context)

    async def close_async(self) -> None:
        clients = list(self._async_clients.values())
        self._async_clients.clear()
        for client in clients:
            await client.close()

    def _sampling_kwargs(self, model=None):
        """Sampling parameters to send, or nothing when the model owns them."""
        return self.capabilities.sampling_kwargs(model or self.model, self.temperature)

    _classify_bad_request = staticmethod(classify_bad_request)

    def _note_temperature_rejected(self, model):
        if self.capabilities.note_temperature_rejected(model):
            print(
                f"[yellow]ℹ '{model}' rejected temperature={self.temperature}; "
                f"retrying with the model default[/yellow]"
            )

    def _request(self, call, model=None):
        """Issue an API call, retrying once without temperature if refused."""
        model = model or self.model
        try:
            return call(self._sampling_kwargs(model))
        except BadRequestError as e:
            if classify_bad_request(e) != "temperature":
                raise
            self._note_temperature_rejected(model)
            return call({})

    def create_chat_completion(self, text):
        """Plain (delimiter-mode) completion. Overridden by some subclasses."""
        messages = self.create_messages(text, self.create_context_messages())

        return self._request(
            lambda sampling: self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                extra_body=self.extra_body if self.extra_body else None,
                **sampling,
            )
        )

    def _structured_single_translation(self, text):
        """Translate one paragraph via Structured Outputs.

        Raises `StructuredOutputUnsupported` when the endpoint turns out not to
        honor the schema, `LengthFinishReasonError` when the answer was cut off
        (never returns the truncated JSON fragment), and `StructuredRefusal`
        when the model declined this text.
        """
        messages = self.create_messages(text, self.create_context_messages())
        field = single_field_name(self.field_language)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=single_translation_model(
                        self.language, field_language=self.language_field_tag
                    ),
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if self._classify_bad_request(e) != "schema":
                raise  # not a capability answer — do not blame the schema
            raise StructuredOutputUnsupported(str(e)) from e
        except (ValidationError, json.JSONDecodeError) as e:
            # Answered with something that is not the schema.
            raise StructuredOutputUnsupported(str(e)) from e

        # Meter before ruling on the message: the bill is the same whatever
        # the answer says.
        self._note_usage(completion)

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise StructuredRefusal(message.refusal)
        if message.parsed is None:
            raise StructuredOutputUnsupported("no parsed content in response")

        self._note_structured_success()
        return getattr(message.parsed, field)

    def _plain_translation(self, text):
        completion = self.create_chat_completion(text)
        self._note_usage(completion)
        content = completion.choices[0].message.content
        return content.encode("utf8").decode() if content else ""

    # ---- session mode -----------------------------------------------------

    # Compact attempts before giving up on a summary and starting clean. More
    # than one so a transient error does not cost the accumulated context;
    # bounded so a broken endpoint cannot grow the history forever.
    COMPACT_ATTEMPTS = 3

    def _note_usage(self, completion, model=None):
        """Add what the endpoint billed for this request to the meter.

        `cached_tokens` is what session mode is watched by: without
        pass-through caching the history is re-read at full price every
        request, and only this number says so. `model` is the id the
        request asked for — the one a provider entry prices.
        """
        try:
            usage = getattr(completion, "usage", None)
            if usage is None:
                return
            details = getattr(usage, "prompt_tokens_details", None)
            self.usage.note(
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
                getattr(details, "cached_tokens", 0),
                model=model or self.model,
            )
        except Exception:
            # a readout, not a gate: a usage record shaped strangely by a
            # gateway is not a reason to stop a paid run
            return

    def _session_budget(self):
        """How large a window may grow before it rolls over."""
        if self.context_compact_at is None:
            return compact_budget_for(self.model)
        return self.context_compact_at

    def _compact_session(self):
        """Ask for a handoff report, then start the next window seeded with it.

        The report is requested on top of the existing history — that is the
        one turn where the whole window is worth re-reading, because it is
        being condensed into what replaces it.
        """
        budget = self._session_budget()
        prompt = handoff_prompt(
            with_glossary=self.glossary_auto_on, with_style=not self.style_note
        )
        messages = [
            *self.session.messages(),
            {"role": "user", "content": prompt},
        ]
        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
            # The handoff turn is billed like any other request — it carries
            # the whole window and answers with a report. Without this line
            # the meter understates a session run's cost worst exactly where
            # it looks best: at a short compact budget, where compaction is
            # frequent (up to 38% of real cost invisible, 260905 eval).
            self._note_usage(completion)
            report_text = completion.choices[0].message.content or ""
        except Exception as e:
            # Keep the window. One rate-limited or dropped request is not a
            # reason to throw away a book's worth of accumulated context — the
            # budget stays exceeded, so the next unit simply tries again.
            self._compact_failures += 1
            # Give up on attempts, or as soon as the window has outgrown its
            # budget badly enough that retrying is the wrong bet: a compact
            # that fails because the history is too long will keep failing,
            # and the translation requests carrying that history fail with it.
            give_up = (
                self._compact_failures >= self.COMPACT_ATTEMPTS
                or self.session.estimated_tokens() > 2 * budget
            )
            print(
                f"[yellow]ℹ handoff report failed ({e}); "
                + (
                    "starting the next context window without a summary"
                    if give_up
                    else "keeping the current context and retrying on the next paragraph"
                )
                + "[/yellow]"
            )
            if give_up:
                # Bounded: without this the history would grow past the
                # budget forever on a persistently failing endpoint.
                self._compact_failures = 0
                self.session.reset(seed="")
            return
        self._compact_failures = 0

        glossary_lines = self._learn_from_handoff(report_text)

        report = HandoffReport(
            window=self.session.windows,
            # A style the user fixed is handed on verbatim, so it cannot be
            # eroded window by window by a model re-describing it.
            style_note=self.style_note,
            # The renderings block is parsed into `glossary_lines`, so it is
            # stripped from the prose rather than stored and re-seeded twice.
            summary=(
                strip_handoff_glossary(report_text)
                if self.glossary_auto_on
                else report_text.strip()
            ),
            glossary_lines=glossary_lines,
        )
        self._show_handoff(report)
        if self.handoff_path:
            try:
                report.append_to(self.handoff_path)
            except OSError as e:
                # The paragraph is already translated and billed. Failing here
                # would send get_translation's retry policy round again and
                # pay for the same paragraph up to three more times.
                print(
                    f"[yellow]ℹ could not write {self.handoff_path} ({e}); "
                    f"the run continues without a saved handoff[/yellow]"
                )
        self.session.reset(seed=report.seed_text())

    def _show_handoff(self, report):
        """Print the report the next window will inherit.

        `escape` is not optional: rich reads square brackets as markup, and
        these reports genuinely contain things like "[PGA]", which would be
        swallowed or raise on an unclosed tag.
        """
        if self.quiet:
            # --quiet suppresses echoes like this one; warnings and errors
            # still print.
            return
        print(
            f"[bold cyan]— handoff report, window {report.window} —[/bold cyan]\n"
            + escape(report.render())
        )

    def _save_session_context(self, text, t_text):
        # Store what was *sent*, not the bare source. The next request replays
        # this message verbatim, so any difference — the prompt template, say —
        # would make the newest pair a cache miss, and the run would re-read a
        # paragraph at full input price every request.
        self._record_session_exchange(self._user_content(text), t_text)

    def _record_session_exchange(self, user_content, reply_text):
        """Append one exchange, given the strings the wire actually carried.

        The structured batch path builds its user message itself, so it
        cannot go through `_save_session_context` — and a batch recorded as N
        synthetic pairs is a history that no longer matches what the endpoint
        cached, which costs a full-price re-read of the whole prefix every
        request.
        """
        self.session.append(user_content, reply_text)
        if not self.session.should_compact(self._session_budget()):
            return
        if self.no_context_compact:
            self._start_empty_window()
        else:
            self._compact_session()

    def _start_empty_window(self):
        """Roll over with no handoff report, because the user asked for none.

        Continuity across the seam is what the report buys, and
        `--no-context-compact` declines to buy it — so this is a plain reset,
        not a cheaper summary.
        """
        self.session.reset(seed="")
        if self.quiet:
            return
        print(
            f"[bold cyan]— context window {self.session.windows}, started "
            f"empty (--no-context-compact) —[/bold cyan]"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((RateLimitError, Exception)),
        reraise=True,
    )
    def get_translation(self, text):
        self.rotate_key()
        self.rotate_model()  # rotate all the model to avoid the limit

        if self._ensure_structured_support():
            try:
                t_text = self._structured_single_translation(text)
            except StructuredOutputUnsupported as e:
                self._demote_structured_outputs(e)
                t_text = self._plain_translation(text)
            except LengthFinishReasonError as e:
                # The answer was cut off mid-JSON. Nothing partial may be used,
                # but the plain path has no JSON to truncate — retranslate there
                # rather than ending a multi-hour run over one paragraph.
                # The truncated request was still billed, so it still counts.
                self._note_usage(getattr(e, "completion", None))
                print(
                    "[yellow]ℹ structured answer was truncated; retranslating "
                    "this paragraph without a schema[/yellow]"
                )
                t_text = self._plain_translation(text)
            except StructuredRefusal as e:
                # Seen live with the refusal field holding a complete, correct
                # translation. Whatever the reason, one paragraph the schema
                # path will not answer must not end the run.
                print(
                    "[yellow]ℹ the model refused this paragraph under the "
                    f"schema ({escape(str(e.refusal))}); retranslating it "
                    "without one[/yellow]"
                )
                t_text = self._plain_translation(text)
        else:
            t_text = self._plain_translation(text)

        if self.context_flag:
            self.save_context(text, t_text)

        return t_text

    def save_context(self, text, t_text):
        if self.session is not None:
            self._save_session_context(text, t_text)
            return
        if self.context_paragraph_limit > 0:
            self.context_list.append(text)
            self.context_translated_list.append(t_text)
            # Remove the oldest context
            if len(self.context_list) > self.context_paragraph_limit:
                self.context_list.pop(0)
                self.context_translated_list.pop(0)

    def translate(self, text, needprint=True):
        try:
            t_text = self.get_translation(text)
            return t_text
        except Exception as e:
            print(f"Translation failed after retries: {redact(e)}")
            raise

    def translate_list(self, text_list):
        """
        Translate multiple texts using the best available method.
        Priority: 1. id-echo JSON (strict, shape or json) -> 2. Delimiter-based
        Returns a list of translated texts.
        """
        # Use structured outputs if available (probed once per model)
        if self._structured_enabled():
            return self._do_structured_batch_translate(text_list)

        # Fallback to delimiter-based method. The *effective* system message,
        # not `system_content`: that attribute only ever holds
        # `$OPENAI_API_SYS_MSG`, so passing it dropped a `--prompt` system
        # message for the whole group — `_build_batch_prompt` then wrapped the
        # empty string and installed "Professional translator. …" over the top
        # of it, and the operator's own instruction never left the process.
        return self._do_batch_translate(
            text_list,
            self.prompt_template,
            self._system_message(),
            self.DEFAULT_PROMPT,
            lambda text: self.translate(text, False),
        )

    # A short, unremarkable stand-in for a paragraph: the overhead wanted is
    # everything a request carries *besides* the text, so what the text says
    # must not matter.
    _OVERHEAD_PROBE_TEXT = "The quick brown fox jumps over the lazy dog."

    def prompt_overhead_tokens(self):
        """Tokens one request spends on the prompt rather than on the book.

        Assembled from the real batch messages, because the prompts are
        user-customisable: a fat `BBM_CHATGPTAPI_USER_MSG_TEMPLATE` is paid
        for on every request, and the grouping budget has to know about it
        (see `session_token_budget`). The JSON wrapper counts too — it is
        per-request overhead like the rest of it.

        A sizing hint, never a gate: anything that goes wrong here answers
        None and the caller falls back to its floor.
        """
        try:
            from ..utils import num_tokens_from_text

            # `num_tokens_from_text` adds 7 tokens of chat framing per call;
            # subtract it per message so only the content is counted.
            def content_tokens(text):
                return num_tokens_from_text(text) - 7

            messages = self._create_structured_batch_messages(
                [self._OVERHEAD_PROBE_TEXT]
            )
            total = sum(content_tokens(m.get("content") or "") for m in messages)
            return max(0, total - content_tokens(self._OVERHEAD_PROBE_TEXT))
        except Exception:
            return None

    def _create_structured_batch_messages(self, text_list, degree="strict"):
        """Create messages for structured batch translation.

        The source travels as `{"paragraphs": [{"id": n, "text": ...}, ...]}`
        and the reply carries the same ids back. Ids rather than positions,
        because position is not something a reply can be *checked* against:
        a model that answers two paragraphs in the other order, or drops one
        and keeps the count by merging, is silently misaligned under
        positional reading and obvious under id reading.
        """
        plist_len = len(text_list)

        payload = {
            "paragraphs": [
                {"id": i, "text": str(text)} for i, text in enumerate(text_list)
            ]
        }
        texts_json = json.dumps(payload, ensure_ascii=False)

        # Format user's prompt template with the JSON payload as {text}.
        # `--prompt`'s style section has no slot of its own anywhere, so it is
        # appended here — before the shape instruction below, which has to
        # stay the last thing the model reads.
        user_prompt = (
            self.prompt_template.format(
                text=texts_json, language=self.language, crlf="\n"
            )
            + self.style_suffix()
        )

        # Add structured format instruction. The target language goes last: this
        # is the final thing the model reads before decoding, and a shape-only
        # tail leaves `{language}` buried behind the source JSON blob above.
        field = batch_field_name(self.field_language)
        item_field = single_field_name(self.field_language)
        # Pinned terms for this group, on the same rule as a single unit: only
        # the ones that occur in the batch, and first, so the shape and the
        # target language stay the last thing the model reads.
        glossary_block = self.glossary.prompt_block(texts_json) if self.glossary else ""
        content = (f"{glossary_block}\n\n" if glossary_block else "") + (
            f"{self._marker_preamble(texts_json)}{user_prompt}\n\n"
            f"Return a JSON object whose '{field}' array contains EXACTLY "
            f"{plist_len} objects, one per input paragraph. Each object has "
            f"exactly two fields: 'id', copied unchanged from the paragraph "
            f"it translates — use every id once and invent none — and "
            f"'{item_field}', holding that paragraph's translation. Return "
            f"the {plist_len} translations, each written in {self.language}."
        )

        if degree == "json":
            # No schema reaches this endpoint, so the shape has to be said
            # out loud. The language sentence is repeated after it for the
            # reason the tail exists at all: the last thing the model reads
            # before decoding must be what language to write in, and
            # `prompt_with_schema` appends its description after the prompt.
            schema = batch_translation_schema(
                self.language,
                plist_len,
                self.source_language,
                self.language_field_tag,
            )
            content = (
                f"{prompt_with_schema(content, schema)}\n\n"
                f"Every translation must be written in {self.language}."
            )

        sys_content = self._augment_system_content(self._system_message())

        messages = [
            {"role": "system", "content": sys_content},
        ]

        if self.context_flag:
            messages.extend(self.create_context_messages())

        messages.append({"role": "user", "content": content})
        return messages

    def _do_structured_batch_translate(self, text_list):
        """Batch translate using structured outputs"""
        plist_len = len(text_list)

        if plist_len == 0:
            return []

        if plist_len == 1:
            return [self.get_translation(text_list[0])]

        degree = self._structured_enabled()
        if degree and degree not in SCHEMA_BATCH_DEGREES:
            cap = self.substrict_batch_cap
            if plist_len > cap:
                # Tag mode sizes batches by characters and plan mode may have
                # sized this one for a schema-degree model before rotation.
                # Either way the json-degree cap is a per-*request* bound, so
                # it is honoured by making more requests — refusing instead
                # would send tag mode's fallback into an N-singles sweep.
                out = []
                for i in range(0, plist_len, cap):
                    out.extend(
                        self._do_structured_batch_translate(text_list[i : i + cap])
                    )
                return out

        try:
            items, user_content, raw_reply = self._execute_structured_batch_translate(
                text_list, plist_len
            )
        except StructuredOutputUnsupported as e:
            # Capability answer, not a transient failure: stop paying for it.
            self._demote_structured_outputs(e)
            return [self.translate(t, False) for t in text_list]
        except BatchMismatch:
            # The reply arrived and did not answer the request. That is the
            # loader's ladder's business — it halves the chunk — so it must
            # not be swallowed into a per-paragraph sweep here. Raised from
            # inside the request only at the json degree, where the parse is
            # ours rather than the SDK's.
            raise
        except Exception as e:
            # A refusal, or a transport failure that outlived its retries.
            # Neither says the batch came back misaligned, so this is not the
            # loader's ladder's business: translate the paragraphs one by one
            # and let the run continue.
            print(
                f"[yellow]Structured batch translation failed after retries: {e}. "
                f"Falling back to one-by-one translation.[/yellow]"
            )
            return [self.translate(t, False) for t in text_list]

        # Outside the retry on purpose. tenacity re-sends transport failures;
        # a miscounted or misaligned answer is a model error, and asking the
        # same model the same question again mostly buys the same answer at
        # the same price. The loader's ladder halves the chunk instead.
        paragraphs = self._align_batch_items(text_list, items)

        if self.context_flag:
            if self.session is not None:
                # One exchange, exactly what the endpoint saw: N synthetic
                # pairs that were never sent make the next request's prefix
                # diverge from the cached one.
                self._record_session_exchange(user_content, raw_reply)
            else:
                for orig, trans in zip(text_list, paragraphs):
                    self.save_context(orig, trans)
        return paragraphs

    def _align_batch_items(self, text_list, items):
        """Reply items in source order, or `BatchMismatch`.

        By id, never by position. The id set has to match the request's
        exactly — no duplicate, no stranger, none missing — and no non-empty
        source may come back empty (see `Base._check_batch`).
        """
        field = single_field_name(self.field_language)
        if len(items) != len(text_list):
            raise BatchMismatch(
                f"expected {len(text_list)} translations, got {len(items)}"
            )
        by_id = {}
        for item in items:
            item_id = getattr(item, "id", None)
            if item_id in by_id:
                raise BatchMismatch(f"duplicate id {item_id!r} in the reply")
            by_id[item_id] = getattr(item, field, "")
        expected = set(range(len(text_list)))
        if set(by_id) != expected:
            raise BatchMismatch(
                f"reply ids {sorted(map(str, by_id))} do not match the "
                f"{len(text_list)} ids that were sent"
            )
        paragraphs = [by_id[i] for i in range(len(text_list))]
        self._check_batch(text_list, paragraphs)
        self._note_structured_success()
        return paragraphs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_not_exception_type(
            (StructuredOutputUnsupported, StructuredRefusal, BatchMismatch)
        ),
        reraise=True,
    )
    def _execute_structured_batch_translate(self, text_list, plist_len):
        """One structured batch request, retried on transport failures only.

        Returns `(items, user_content, raw_reply)`: the parsed reply items,
        the user message exactly as it was sent, and the raw assistant text.
        The last two are what session mode records — and only after the
        caller has found the items usable.

        A reply that came back and did not answer the request is a
        `BatchMismatch`, never a retry: re-asking the same model the same
        question buys the same answer at the same price, and the loader
        halves the chunk instead.
        """
        self.rotate_key()
        self.rotate_model()
        degree = self._structured_enabled()
        if not degree:
            # eligibility was decided for the model current at call time, but
            # rotation may have moved us to a different one: a model that
            # never passed the probe must not be handed a schema
            raise StructuredOutputUnsupported(
                f"'{self.model}' has no structured-output support"
            )

        if degree not in SCHEMA_BATCH_DEGREES:
            # Backstop for the rotation race: the caller chunked to this cap
            # against the degree it saw, but `rotate_model()` above may have
            # just moved execution to a json-degree model. Refusing before
            # the request costs nothing — the loader's ladder divides the
            # batch — where sending it re-opens the oversized-batch
            # corruption the cap bounds.
            cap = self.substrict_batch_cap
            if plist_len > cap:
                raise BatchMismatch(
                    f"batch of {plist_len} exceeds the json-degree cap of "
                    f"{cap} units for '{self.model}'"
                )

        messages = self._create_structured_batch_messages(text_list, degree=degree)
        if degree not in SCHEMA_BATCH_DEGREES:
            return self._execute_json_object_batch(messages, plist_len)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=batch_translation_model(
                        self.language,
                        plist_len,
                        self.source_language,
                        self.language_field_tag,
                    ),
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if self._classify_bad_request(e) != "schema":
                raise  # not a capability answer — do not blame the schema
            raise StructuredOutputUnsupported(str(e)) from e
        except (ValidationError, json.JSONDecodeError) as e:
            raise StructuredOutputUnsupported(str(e)) from e

        self._note_usage(completion)

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise StructuredRefusal(message.refusal)
        if message.parsed is None:
            raise StructuredOutputUnsupported("no parsed content in response")

        items = getattr(message.parsed, batch_field_name(self.field_language))
        raw_reply = getattr(message, "content", None)
        if not raw_reply:
            # Some gateways return only the parsed object. A history has to
            # hold *something* the next request can extend, and the parsed
            # form is what the endpoint produced.
            raw_reply = json.dumps(
                {batch_field_name(self.field_language): [str(i) for i in items]},
                ensure_ascii=False,
            )
        return items, messages[-1]["content"], raw_reply

    def _execute_json_object_batch(self, messages, plist_len):
        """One id-echo batch at the json_object degree.

        The endpoint guarantees only that *some* JSON comes back, so
        everything the SDK's parse mode would have guaranteed is checked
        here instead: the object is dug out of whatever fences or prose came
        with it, it must carry the batch key, and every row must be an
        object. Each failure is a `BatchMismatch` — the same signal a
        misaligned strict reply raises, and the loader answers it the same
        way, by halving the chunk.
        """
        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if self._classify_bad_request(e) != "schema":
                raise  # not a capability answer — do not blame the schema
            raise StructuredOutputUnsupported(str(e)) from e

        self._note_usage(completion)

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise StructuredRefusal(message.refusal)
        raw_reply = getattr(message, "content", None) or ""
        return (
            self._parse_json_object_batch(raw_reply),
            messages[-1]["content"],
            raw_reply,
        )

    def _parse_json_object_batch(self, raw_reply):
        """The reply's items, or `BatchMismatch` saying what was wrong.

        The required top key is the whole point (260905 amendment 1):
        `extract_json_object` on its own hands back the first object it can
        parse, which on a reply with one unescaped quote is a fragment of
        the answer rather than the answer. A missing key is a mismatch, not
        a fall-through.
        """
        field = batch_field_name(self.field_language)
        item_field = single_field_name(self.field_language)
        obj = extract_json_object(raw_reply, (field,))
        if isinstance(obj, dict):
            obj = unwrap_schema_echo(obj)
        if not isinstance(obj, dict) or field not in obj:
            raise BatchMismatch(f"no JSON object carrying '{field}' in the reply")
        rows = obj[field]
        if not isinstance(rows, list):
            raise BatchMismatch(f"the reply's '{field}' is not a list")
        items = []
        for row in rows:
            if not isinstance(row, dict):
                raise BatchMismatch(f"a '{field}' entry is not an object")
            text = row.get(item_field)
            items.append(
                SimpleNamespace(
                    **{
                        # An id echoed as "3" is the same id as 3: nothing
                        # constrained its type here, and the alignment check
                        # below compares against the integers we sent.
                        "id": _echoed_id(row.get("id")),
                        # Anything that is not a string is not a translation.
                        # Left empty on purpose: an empty slot for a
                        # non-empty source is exactly what `_check_batch`
                        # exists to catch.
                        item_field: text if isinstance(text, str) else "",
                    }
                )
            )
        return items

    def set_model_list(self, model_list):
        """The only way models get set: whatever the user named, in that order.

        No name is special. Rotation follows the order given — the old
        `set()` pass made it depend on hash order, so the same command could
        start on a different model between runs.

        Nothing is asked of the endpoint here. Whether it will serve these
        models is settled at the first paid call, by
        `_ensure_models_routable`, so a run that writes a plan and exits pays
        for nothing.
        """
        seen = {}
        for name in model_list:
            name = (name or "").strip()
            if name:
                seen.setdefault(name, None)
        model_list = list(seen)
        if not model_list:
            raise ValueError(
                "Empty model list provided. Use --model_list with at least one model name."
            )

        print(f"Using model list {model_list}")
        # Remembered as a list too: `cycle` cannot be read back, and an auto
        # budget has to size the shared history for the smallest window among
        # *all* of them, not just whichever is current.
        self._model_names = model_list
        # What the command configured, as distinct from `_model_names`,
        # which `_ensure_models_routable` narrows to what the endpoint
        # serves — availability must not move a checkpoint fingerprint.
        self._configured_model_names = tuple(model_list)
        self.model_list = cycle(model_list)
        # Set the initial model so it is available before rotate_model() runs.
        self.model = model_list[0]
        # Shared by every clone made from here on (see _route_state).
        self._route_state = {"pending": model_list, "failure": None}

    def batch_init(self, book_name):
        self.book_name = self.sanitize_book_name(book_name)

    def add_to_batch_translate_queue(self, book_index, text):
        self.batch_text_list.append({"book_index": book_index, "text": text})

    def sanitize_book_name(self, book_name):
        # Replace any characters that are not alphanumeric, underscore, hyphen, or dot with an underscore
        sanitized_book_name = re.sub(r"[^\w\-_\.]", "_", book_name)
        # Remove leading and trailing underscores and dots
        sanitized_book_name = sanitized_book_name.strip("._")
        return sanitized_book_name

    def batch_metadata_file_path(self):
        return os.path.join(os.getcwd(), "batch_files", f"{self.book_name}_info.json")

    def batch_dir(self):
        return os.path.join(os.getcwd(), "batch_files", self.book_name)

    def custom_id(self, book_index):
        return f"{self.book_name}-{book_index}"

    def is_completed_batch(self):
        batch_metadata_file_path = self.batch_metadata_file_path()

        if not os.path.exists(batch_metadata_file_path):
            print("Batch result file does not exist")
            raise Exception("Batch result file does not exist")

        with open(batch_metadata_file_path, "r", encoding="utf-8") as f:
            batch_info = json.load(f)

        for batch_file in batch_info["batch_files"]:
            batch_status = self.check_batch_status(batch_file["batch_id"])
            if batch_status.status != "completed":
                return False

        return True

    def batch_translate(self, book_index):
        if self.batch_info_cache is None:
            batch_metadata_file_path = self.batch_metadata_file_path()
            with open(batch_metadata_file_path, "r", encoding="utf-8") as f:
                self.batch_info_cache = json.load(f)

        batch_info = self.batch_info_cache
        target_batch = None
        for batch in batch_info["batch_files"]:
            if batch["start_index"] <= book_index < batch["end_index"]:
                target_batch = batch
                break

        if not target_batch:
            raise ValueError(f"No batch found for book_index {book_index}")

        if target_batch["batch_id"] in self.result_content_cache:
            result_content = self.result_content_cache[target_batch["batch_id"]]
        else:
            batch_status = self.check_batch_status(target_batch["batch_id"])
            if batch_status.output_file_id is None:
                raise ValueError(f"Batch {target_batch['batch_id']} is not completed")
            result_content = self.get_batch_result(batch_status.output_file_id)
            self.result_content_cache[target_batch["batch_id"]] = result_content

        result_lines = result_content.text.split("\n")
        custom_id = self.custom_id(book_index)
        for line in result_lines:
            if line.strip():
                result = json.loads(line)
                if result["custom_id"] == custom_id:
                    return self._read_batch_choice(
                        result["response"]["body"]["choices"][0],
                        custom_id,
                        self.field_language,
                    )

        raise ValueError(f"No result found for custom_id {custom_id}")

    @staticmethod
    def _read_batch_choice(choice, custom_id, language):
        """Unwrap one Batch API choice.

        Results are often fetched by a later process that never probed the
        model, so this decides from the payload itself rather than from cached
        capability state — and refuses to hand back a truncated JSON fragment.
        `language` only names the field to look for; nothing here inspects the
        text itself.
        """
        message = choice.get("message", {})
        if message.get("refusal"):
            raise ValueError(
                f"Model refused to translate {custom_id}: {message['refusal']}"
            )
        if choice.get("finish_reason") == "length":
            raise ValueError(
                f"Batch result for {custom_id} was truncated by the token limit"
            )

        content = message.get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content  # delimiter-mode batch, plain text is expected

        if not isinstance(parsed, dict):
            return content  # delimiter-mode batch, plain text is expected

        # A structured answer whose key we cannot find is not text: returning
        # `content` would paste the raw JSON into the book. The usual cause is a
        # result file produced under a different --language than this run.
        field = single_field_name(language)
        value = parsed.get(field)
        if not isinstance(value, str):
            raise ValueError(
                f"Batch result for {custom_id} has no '{field}' string; "
                f"got keys {sorted(parsed)}. A result file from a run with a "
                f"different --language cannot be resumed under this one."
            )
        return value

    def create_batch_context_messages(self, index):
        messages = []
        if self.context_flag:
            if index % CHATGPT_CONFIG[
                "batch_context_update_interval"
            ] == 0 or not hasattr(self, "cached_context_messages"):
                context_messages = []
                for i in range(index - 1, -1, -1):
                    item = self.batch_text_list[i]
                    if len(item["text"].split()) >= 100:
                        context_messages.append(item["text"])
                        if len(context_messages) == self.context_paragraph_limit:
                            break

                if len(context_messages) == self.context_paragraph_limit:
                    print("Creating cached context messages")
                    self.cached_context_messages = [
                        {"role": "user", "content": "\n".join(context_messages)},
                        {
                            "role": "assistant",
                            "content": self.get_translation(
                                "\n".join(context_messages)
                            ),
                        },
                    ]

            if hasattr(self, "cached_context_messages"):
                messages.extend(self.cached_context_messages)

        return messages

    def make_batch_request(self, book_index, text):
        messages = self.create_messages(
            text, self.create_batch_context_messages(book_index)
        )

        batch_body = {
            "model": self.batch_model,
            "messages": messages,
            **self._sampling_kwargs(self.batch_model),
        }

        # The Batch API takes hand-built bodies, so the schema cannot come from
        # the SDK here; single_translation_schema mirrors the Pydantic model.
        if self._ensure_structured_support(self.batch_model):
            batch_body["response_format"] = {
                "type": "json_schema",
                "json_schema": single_translation_schema(
                    self.language, self.language_field_tag
                ),
            }

        return {
            "custom_id": self.custom_id(book_index),
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": batch_body,
        }

    def create_batch_files(self, dest_file_path):
        file_paths = []
        # max request 50,000 and max size 100MB
        lines_per_file = 40000
        current_file = 0

        for i in range(0, len(self.batch_text_list), lines_per_file):
            current_file += 1
            file_path = os.path.join(dest_file_path, f"{current_file}.jsonl")
            start_index = i
            end_index = i + lines_per_file

            # TODO: Split the file if it exceeds 100MB
            with open(file_path, "w", encoding="utf-8") as f:
                for text in self.batch_text_list[i : i + lines_per_file]:
                    batch_req = self.make_batch_request(
                        text["book_index"], text["text"]
                    )
                    json.dump(batch_req, f, ensure_ascii=False)
                    f.write("\n")
            file_paths.append(
                {
                    "file_path": file_path,
                    "start_index": start_index,
                    "end_index": end_index,
                }
            )

        return file_paths

    def batch(self):
        # The whole submission is billed to one model, picked here and frozen
        # into every request in the file, so the list it is picked from has to
        # be settled before the pick rather than at the first request built.
        self._ensure_models_routable()
        self.rotate_model()
        self.batch_model = self.model
        # current working directory
        batch_dir = self.batch_dir()
        batch_metadata_file_path = self.batch_metadata_file_path()
        # cleanup batch dir and result file
        if os.path.exists(batch_dir):
            shutil.rmtree(batch_dir)
        if os.path.exists(batch_metadata_file_path):
            os.remove(batch_metadata_file_path)
        os.makedirs(batch_dir, exist_ok=True)
        # batch execute
        batch_files = self.create_batch_files(batch_dir)
        batch_info = []
        for batch_file in batch_files:
            file_id = self.upload_batch_file(batch_file["file_path"])
            batch = self.batch_execute(file_id)
            batch_info.append(
                self.create_batch_info(
                    file_id, batch, batch_file["start_index"], batch_file["end_index"]
                )
            )
        # save batch info
        batch_info_json = {
            "book_id": self.book_name,
            "batch_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch_files": batch_info,
        }
        with open(batch_metadata_file_path, "w", encoding="utf-8") as f:
            json.dump(batch_info_json, f, ensure_ascii=False, indent=2)

    def create_batch_info(self, file_id, batch, start_index, end_index):
        return {
            "input_file_id": file_id,
            "batch_id": batch.id,
            "start_index": start_index,
            "end_index": end_index,
            "prefix": self.book_name,
        }

    def upload_batch_file(self, file_path):
        batch_input_file = self.openai_client.files.create(
            file=open(file_path, "rb"), purpose="batch"
        )
        return batch_input_file.id

    def batch_execute(self, file_id):
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        res = self.openai_client.batches.create(
            input_file_id=file_id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={
                "description": f"Batch job for {self.book_name} at {current_time}"
            },
        )
        if res.errors:
            print(res.errors)
            raise Exception(f"Batch execution failed: {res.errors}")
        return res

    def check_batch_status(self, batch_id):
        return self.openai_client.batches.retrieve(batch_id)

    def get_batch_result(self, output_file_id):
        return self.openai_client.files.content(output_file_id)
