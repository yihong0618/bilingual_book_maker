import re
import time
import os
import shutil
from os import environ
from itertools import cycle
import json
from functools import lru_cache
from threading import Lock, RLock

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    AzureOpenAI,
    BadRequestError,
    InternalServerError,
    LengthFinishReasonError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import ConfigDict, Field, ValidationError, create_model
from rich import print
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_not_exception_type,
)

from .base_translator import Base, TranslationContext, TranslationResult
from ..structured import (
    RungRejected,
    extract_json_object,
    prompt_with_schema,
    unwrap_schema_echo,
)
from ..config import config

CHATGPT_CONFIG = config["translator"]["chatgptapi"]

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}


class StructuredOutputUnsupported(Exception):
    """The endpoint does not really apply the JSON Schema we sent.

    Raised only for capability answers, never for model or transport errors, so
    callers can demote to the delimiter method instead of retrying.
    """


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
@lru_cache(maxsize=None)
def single_translation_model(language):
    """Structured single translation output, pinned to `language`."""
    field = single_field_name(language)
    return create_model(
        field,
        __config__=ConfigDict(extra="forbid"),
        **{
            field: (
                str,
                Field(description=_single_field_description(language)),
            )
        },
    )


@lru_cache(maxsize=None)
def batch_translation_model(language):
    """Structured batch translation output, pinned to `language`."""
    field = batch_field_name(language)
    return create_model(
        field,
        __config__=ConfigDict(extra="forbid"),
        **{
            field: (
                list[str],
                Field(description=_batch_field_description(language)),
            )
        },
    )


def _single_field_description(language):
    target = language or "the target language"
    return f"The source text translated into {target}."


def _batch_field_description(language):
    target = language or "the target language"
    return (
        f"The source paragraphs translated into {target}, one per input "
        f"paragraph and in the same order."
    )


@lru_cache(maxsize=None)
def single_translation_schema(language):
    """Mirror of `single_translation_model` for the Batch API.

    Batch JSONL bodies are built by hand and so cannot use the SDK's Pydantic
    support; both sides take their field name from `single_field_name`.
    """
    field = single_field_name(language)
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


# Capability probe. The prompt asks for plain text and the schema pins a
# single-value enum, so the only way `PROBE_EXPECTED` can come back is if the
# server actually applied the schema to decoding. A proxy that accepts
# `response_format` and quietly drops it answers with the prompted text instead.
# Deliberately language-free: this asks whether the endpoint honors schemas at
# all, and a translation-shaped probe would confuse that with a bad translation.

# The API's own default. Sending it explicitly changes nothing for models that
# accept it, and is a hard 400 for models that only allow their default.
DEFAULT_TEMPERATURE = 1.0

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

# One garbled response from a proxy must not cost the whole book its structured
# mode. A genuinely unsupported endpoint still pays at most this many attempts.
STRUCTURED_FAILURE_THRESHOLD = 2

GPT35_MODEL_LIST = [
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-1106",
    "gpt-3.5-turbo-16k",
    "gpt-3.5-turbo-0613",
    "gpt-3.5-turbo-16k-0613",
    "gpt-3.5-turbo-0301",
    "gpt-3.5-turbo-0125",
]
GPT4_MODEL_LIST = [
    "gpt-4-1106-preview",
    "gpt-4",
    "gpt-4-32k",
    "gpt-4o-2024-05-13",
    "gpt-4-0613",
    "gpt-4-32k-0613",
]

GPT4oMINI_MODEL_LIST = [
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
]
GPT4o_MODEL_LIST = [
    "gpt-4o",
    "gpt-4o-2024-05-13",
    "gpt-4o-2024-08-06",
    "chatgpt-4o-latest",
]
GPT5MINI_MODEL_LIST = [
    "gpt-5-mini",
    "gpt-5.4-mini",
]
O1PREVIEW_MODEL_LIST = [
    "o1-preview",
    "o1-preview-2024-09-12",
]
O1_MODEL_LIST = [
    "o1",
    "o1-2024-12-17",
]
O1MINI_MODEL_LIST = [
    "o1-mini",
    "o1-mini-2024-09-12",
]
O3MINI_MODEL_LIST = [
    "o3-mini",
]


class ChatGPTAPI(Base):
    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    # Subclasses that do not route through `self.openai_client` must opt out:
    # probing them would send the capability request to the wrong endpoint.
    SUPPORTS_STRUCTURED_OUTPUTS = True

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
        extra_body=None,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.key_len = len(key.split(","))
        self.openai_client = OpenAI(api_key=next(self.keys), base_url=api_base)
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
        self.deployment_id = None
        self.temperature = temperature
        self.model_list = None
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
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
        # Reentrant: the probe records its verdict while still holding the lock.
        self._structured_lock = RLock()
        self.extra_body = extra_body or {}

        # Both keyed by model, because --model_list rotates across models of
        # differing capability. Structured support is probed on first use;
        # temperature support is learned from the first rejection.
        self._structured_support = {}
        self._temperature_unsupported = {}
        # Consecutive capability failures per model, and models whose probe was
        # postponed by an outage (tracked only to keep the log to one line).
        self._structured_failures = {}
        self._probe_deferred = set()
        self.model = (
            None  # Will be set by rotate_model() after model_list is initialized
        )

    def _probe_verdict(self, model=None):
        """The endpoint's graded schema support, probed once per model.

        One of "strict", "shape", "json" or False. The probe runs while
        holding the lock so that N parallel workers issue one probe per model,
        not N.
        """
        model = model or self.model
        with self._structured_lock:
            if model not in self._structured_support:
                if self.SUPPORTS_STRUCTURED_OUTPUTS:
                    self._test_structured_outputs(model)
                else:
                    self._structured_support[model] = False
            return self._structured_support.get(model, False)

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

    def _structured_enabled(self):
        return self._structured_support.get(self.model, False) == "strict"

    def _defer_probe(self, model, error):
        """Postpone the verdict: record nothing so the next call probes again."""
        with self._structured_lock:
            first_time = model not in self._probe_deferred
            self._probe_deferred.add(model)
        if first_time:
            print(
                f"[yellow]ℹ could not probe '{model}' right now ({error}); "
                f"using the delimiter method until the endpoint answers[/yellow]"
            )

    def _note_structured_success(self):
        """A working structured call clears the model's failure streak."""
        if self._structured_failures.get(self.model):
            with self._structured_lock:
                self._structured_failures.pop(self.model, None)

    def _demote_structured_outputs(self, reason):
        """Count a capability failure and, on a streak, stop paying for it.

        The caller falls back for the current paragraph or batch either way. The
        streak is what keeps a single garbled proxy response from disabling
        structured outputs for the rest of a multi-hour run, while an endpoint
        that really ignores the schema still costs only
        `STRUCTURED_FAILURE_THRESHOLD` attempts instead of three tenacity
        retries per batch, forever.
        """
        with self._structured_lock:
            failures = self._structured_failures.get(self.model, 0) + 1
            self._structured_failures[self.model] = failures
            demote = failures >= STRUCTURED_FAILURE_THRESHOLD
            already_demoted = self._structured_support.get(self.model) is False
            if demote:
                self._structured_support[self.model] = False

        if demote:
            if not already_demoted:
                print(
                    f"[yellow]ℹ '{self.model}' did not honor the JSON schema "
                    f"({reason}); switching to the delimiter method[/yellow]"
                )
        else:
            print(
                f"[yellow]ℹ '{self.model}' did not honor the JSON schema "
                f"({reason}); falling back for this one and trying structured "
                f"outputs once more[/yellow]"
            )

    def _test_structured_outputs(self, model=None):
        """Probe whether the endpoint really applies a strict JSON Schema.

        Grades the response body: accepting the request proves nothing, because
        OpenAI-compatible proxies routinely accept `response_format` and drop it.
        No temperature and no token cap — the probe must test exactly one
        capability, and a cap would be rejected by o-series/gpt-5 models or eaten
        by reasoning tokens, producing a false negative.
        """
        model = model or self.model
        try:
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROBE_PROMPT}],
                response_format={
                    "type": "json_schema",
                    "json_schema": STRUCTURED_PROBE_SCHEMA,
                },
            )
        except PROBE_FATAL_ERRORS:
            raise
        except PROBE_TRANSIENT_ERRORS as e:
            self._defer_probe(model, e)
            return
        except Exception as e:
            # Ambiguous (400 for an unknown param, 500 from a local server, ...):
            # not a usable endpoint for schemas either way, so degrade loudly.
            self._record_probe_result(model, f"request rejected: {e}")
            return

        self._record_probe_result(model, self._grade_probe_response(completion))

    @staticmethod
    def _grade_probe_response(completion):
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

    def _record_probe_result(self, model, verdict):
        """Store the verdict string; False means no schema support at all."""
        stored = verdict if verdict in ("strict", "shape", "json") else False
        with self._structured_lock:
            self._structured_support[model] = stored
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

    # Hoisted to `structured.py` — every provider's bottom rung needs it.
    _extract_json_object = staticmethod(extract_json_object)

    # Probe verdict -> the cheapest rung worth *starting* at. Advisory only:
    # descent is failure-driven, so a wrong guess costs one request.
    ENTRY_RUNG = {
        "strict": "json_schema",
        "shape": "json_schema",
        "json": "json_object",
    }

    def structured_rungs(self, prompt, schema, model=None):
        """json_schema -> json_object + described schema -> plain prompt.

        A real ladder now: `run_rungs` descends whenever a rung is refused or
        answers unusably, so an endpoint that accepts the one-key probe schema
        and then rejects a twelve-property one still classifies, and a
        `strict` endpoint that returns prose falls through instead of aborting
        the run.
        """
        target = model or self.model
        ladder = [
            ("json_schema", lambda: self._json_schema_rung(prompt, schema, target)),
            ("json_object", lambda: self._json_object_rung(prompt, schema, target)),
            ("prompt", lambda: self._prompt_rung(prompt, schema, target)),
        ]
        entry = self.ENTRY_RUNG.get(self._probe_verdict(target), "prompt")
        start = next(i for i, (name, _) in enumerate(ladder) if name == entry)
        return ladder[start:]

    def _completion_text(self, model, content, **kwargs):
        """One single-turn request, with shape refusals marked as such."""
        try:
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
        except RUNG_REFUSAL_ERRORS as e:
            raise RungRejected(e) from e
        return completion.choices[0].message.content

    def _json_schema_rung(self, prompt, schema, model):
        text = self._completion_text(
            model,
            prompt,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        return unwrap_schema_echo(extract_json_object(text))

    def _json_object_rung(self, prompt, schema, model):
        text = self._completion_text(
            model,
            prompt_with_schema(prompt, schema),
            response_format={"type": "json_object"},
        )
        return unwrap_schema_echo(extract_json_object(text))

    def _chat_completion(self, prompt, model=None):
        return self._completion_text(model or self.model, prompt)

    def rotate_key(self):
        with self._api_lock:
            self.openai_client.api_key = next(self.keys)

    def rotate_model(self):
        with self._api_lock:
            if self.model_list:
                self.model = next(self.model_list)

    def create_messages(self, text, intermediate_messages=None):
        content = self.prompt_template.format(
            text=text, language=self.language, crlf="\n"
        )

        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")
        messages = [
            {"role": "system", "content": sys_content},
        ]

        if intermediate_messages:
            messages.extend(intermediate_messages)

        messages.append({"role": "user", "content": content})
        return messages

    def create_context_messages(self, context: TranslationContext | None = None):
        messages = []
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
        if self.deployment_id:
            return AsyncAzureOpenAI(
                api_key=key,
                azure_endpoint=self.api_base,
                api_version="2023-07-01-preview",
                azure_deployment=self.deployment_id,
            )
        return AsyncOpenAI(api_key=key, base_url=self.api_base)

    def _get_async_client(self, key):
        cache_key = (self.api_base, self.deployment_id, key)
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
            if self._classify_bad_request(e) != "temperature":
                raise
            with self._structured_lock:
                first_time = not self._temperature_unsupported.get(model)
                self._temperature_unsupported[model] = True
            if first_time:
                print(
                    f"[yellow]ℹ '{model}' rejected temperature={self.temperature}; "
                    f"retrying with the model default[/yellow]"
                )
            completion = await create({})

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
        """Sampling parameters to send, or nothing when the model owns them.

        `DEFAULT_TEMPERATURE` is the API's own default, so sending it changes no
        output — but gpt-5.x and the o-series reject *any* explicit temperature,
        so an unrequested default is pure downside. A model that turned one down
        is remembered and never asked again.
        """
        model = model or self.model
        if self._temperature_unsupported.get(model):
            return {}
        if self.temperature is None or self.temperature == DEFAULT_TEMPERATURE:
            return {}
        return {"temperature": self.temperature}

    @staticmethod
    def _classify_bad_request(error):
        """Say what a 400 was actually about: 'temperature', 'schema' or 'other'.

        Without this, a temperature rejection is misread as "no schema support":
        the model gets demoted for the rest of the run and the real cause never
        reaches the user.
        """
        text = str(error).lower()
        if "temperature" in text:
            return "temperature"
        if "response_format" in text or "json_schema" in text:
            return "schema"
        return "other"

    def _request(self, call, model=None):
        """Issue an API call, retrying once without temperature if refused."""
        model = model or self.model
        try:
            return call(self._sampling_kwargs(model))
        except BadRequestError as e:
            if self._classify_bad_request(e) != "temperature":
                raise
            with self._structured_lock:
                first_time = not self._temperature_unsupported.get(model)
                self._temperature_unsupported[model] = True
            if first_time:
                print(
                    f"[yellow]ℹ '{model}' rejected temperature={self.temperature}; "
                    f"retrying with the model default[/yellow]"
                )
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
        (never returns the truncated JSON fragment), and `ValueError` on refusal.
        """
        messages = self.create_messages(text, self.create_context_messages())
        field = single_field_name(self.language)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=single_translation_model(self.language),
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

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"Model refused to translate: {message.refusal}")
        if message.parsed is None:
            raise StructuredOutputUnsupported("no parsed content in response")

        self._note_structured_success()
        return getattr(message.parsed, field)

    def _plain_translation(self, text):
        completion = self.create_chat_completion(text)
        content = completion.choices[0].message.content
        return content.encode("utf8").decode() if content else ""

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
            except LengthFinishReasonError:
                # The answer was cut off mid-JSON. Nothing partial may be used,
                # but the plain path has no JSON to truncate — retranslate there
                # rather than ending a multi-hour run over one paragraph.
                print(
                    "[yellow]ℹ structured answer was truncated; retranslating "
                    "this paragraph without a schema[/yellow]"
                )
                t_text = self._plain_translation(text)
        else:
            t_text = self._plain_translation(text)

        if self.context_flag:
            self.save_context(text, t_text)

        return t_text

    def save_context(self, text, t_text):
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
            print(f"Translation failed after retries: {e}")
            raise

    def translate_and_split_lines(self, text):
        result_str = self.translate(text, False)
        lines = result_str.splitlines()
        lines = [line.strip() for line in lines if line.strip() != ""]
        return lines

    def log_retry(self, state, retry_count, elapsed_time, log_path="log/buglog.txt"):
        if retry_count == 0:
            return
        print(f"retry {state}")
        with open(log_path, "a", encoding="utf-8") as f:
            print(
                f"retry {state}, count = {retry_count}, time = {elapsed_time:.1f}s",
                file=f,
            )

    def log_translation_mismatch(
        self,
        plist_len,
        result_list,
        new_str,
        sep,
        log_path="log/buglog.txt",
    ):
        if len(result_list) == plist_len:
            return
        newlist = new_str.split(sep)
        with open(log_path, "a", encoding="utf-8") as f:
            print(f"problem size: {plist_len - len(result_list)}", file=f)
            for i in range(len(newlist)):
                print(newlist[i], file=f)
                print(file=f)
                if i < len(result_list):
                    print("............................................", file=f)
                    print(result_list[i], file=f)
                    print(file=f)
                print("=============================", file=f)

        print(
            f"bug: {plist_len} paragraphs of text translated into {len(result_list)} paragraphs",
        )
        print("continue")

    def join_lines(self, text):
        lines = text.splitlines()
        new_lines = []
        temp_line = []

        # join
        for line in lines:
            if line.strip():
                temp_line.append(line.strip())
            else:
                if temp_line:
                    new_lines.append(" ".join(temp_line))
                    temp_line = []
                new_lines.append(line)

        if temp_line:
            new_lines.append(" ".join(temp_line))

        text = "\n".join(new_lines)
        # try to fix #372
        if not text:
            return ""

        # del ^M
        text = text.replace("^M", "\r")
        lines = text.splitlines()
        filtered_lines = [line for line in lines if line.strip() != "\r"]
        new_text = "\n".join(filtered_lines)

        return new_text

    def translate_list(self, text_list):
        """
        Translate multiple texts using the best available method.
        Priority: 1. Structured Outputs (strict) -> 2. Delimiter-based
        Returns a list of translated texts.
        """
        # Use structured outputs if available (probed once per model)
        if self._ensure_structured_support():
            return self._do_structured_batch_translate(text_list)

        # Fallback to delimiter-based method
        return self._do_batch_translate(
            text_list,
            self.prompt_template,
            self.system_content,
            self.DEFAULT_PROMPT,
            lambda text: self.translate(text, False),
        )

    def _create_structured_batch_messages(self, text_list):
        """Create messages for structured batch translation"""
        plist_len = len(text_list)

        # Build the user message with all texts, incorporating user's prompt template
        texts_json = json.dumps(text_list, ensure_ascii=False)

        # Format user's prompt template with the JSON array as {text}
        user_prompt = self.prompt_template.format(
            text=texts_json, language=self.language, crlf="\n"
        )

        # Add structured format instruction. The target language goes last: this
        # is the final thing the model reads before decoding, and a shape-only
        # tail leaves `{language}` buried behind the source JSON blob above.
        field = batch_field_name(self.language)
        content = (
            f"{user_prompt}\n\n"
            f"Return a JSON object whose '{field}' array contains EXACTLY "
            f"{plist_len} strings, one per input paragraph and in the same "
            f"order, each written in {self.language}."
        )

        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")

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

        try:
            result = self._execute_structured_batch_translate(text_list, plist_len)
            return result
        except StructuredOutputUnsupported as e:
            # Capability answer, not a transient failure: stop paying for it.
            self._demote_structured_outputs(e)
            return [self.translate(t, False) for t in text_list]
        except Exception as e:
            print(
                f"[yellow]Structured batch translation failed after retries: {e}. "
                f"Falling back to one-by-one translation.[/yellow]"
            )
            return [self.translate(t, False) for t in text_list]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_not_exception_type(StructuredOutputUnsupported),
        reraise=True,
    )
    def _execute_structured_batch_translate(self, text_list, plist_len):
        """Execute the actual structured batch translation with tenacity retry"""
        self.rotate_key()
        self.rotate_model()
        if not self._ensure_structured_support(self.model):
            # eligibility was decided for the model current at call time, but
            # rotation may have moved us to a different one: a model that
            # never passed the probe must not be handed a schema
            raise StructuredOutputUnsupported(
                f"'{self.model}' has no strict structured-output support"
            )

        messages = self._create_structured_batch_messages(text_list)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=batch_translation_model(self.language),
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

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"Model refused to translate: {message.refusal}")
        if message.parsed is None:
            raise StructuredOutputUnsupported("no parsed content in response")

        paragraphs = getattr(message.parsed, batch_field_name(self.language))

        # A wrong count is a model error, not a capability answer: retry it.
        if len(paragraphs) != plist_len:
            raise ValueError(
                f"Expected {plist_len} translations, got {len(paragraphs)}"
            )

        # Count is not alignment. A model that merges two source lines into
        # one slot (routine on verse: one sentence spans two pādas) keeps
        # the count by shifting the rest and padding a slot with "" — the
        # only unambiguous symptom of the shift. An empty slot for a
        # non-empty input is therefore a misaligned window, never a valid
        # translation: retry it.
        empty_slots = [
            i
            for i, (src, out) in enumerate(zip(text_list, paragraphs))
            if not out.strip() and src.strip()
        ]
        if empty_slots:
            raise ValueError(
                f"Empty translation for non-empty paragraph(s) {empty_slots}: "
                f"batch alignment lost"
            )

        if self.context_flag:
            for orig, trans in zip(text_list, paragraphs):
                self.save_context(orig, trans)

        self._note_structured_success()
        return paragraphs

    def set_deployment_id(self, deployment_id):
        self.deployment_id = deployment_id
        self.openai_client = AzureOpenAI(
            api_key=next(self.keys),
            azure_endpoint=self.api_base,
            api_version="2023-07-01-preview",
            azure_deployment=self.deployment_id,
        )

    def _check_model_availability(self, model_list, model_family_name):
        """Check if any models from the model_list are available from the API.
        Returns True if at least one model is available, False otherwise.
        """
        if not model_list:
            print(
                f"[red]Error: No {model_family_name} models are available from the API.[/red]"
            )
            print(
                "[yellow]Please check your API key, endpoint, and model permissions.[/yellow]"
            )
            return False
        return True

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _fetch_api_models_with_retry(self):
        """Fetch available models from API with retry logic.
        Returns list of model IDs, or None if the models API is not available (e.g., 404).
        """
        try:
            return [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
        except (NotFoundError, BadRequestError):
            # 404 or 400 — models endpoint not supported by this API provider
            print(
                "[yellow]Model availability check skipped: API does not support models endpoint.[/yellow]"
            )
            return None
        except Exception as e:
            print(
                f"[yellow]Error checking model availability: {e}. Retrying...[/yellow]"
            )
            raise

    def _validate_custom_models(self, custom_model_list):
        """Validate that custom models exist in the API's model list.
        Returns a dict with 'success', 'available_models', and 'unavailable_models' keys.
        """
        api_models = self._fetch_api_models_with_retry()

        # If models API is not available, validate by testing each model directly
        if api_models is None:
            available_models = []
            unavailable_models = []

            for model_name in custom_model_list:
                try:
                    self._validate_model_with_test(model_name, "custom")
                    available_models.append(model_name)
                except Exception as e:
                    print(f"[red]{e}[/red]")
                    unavailable_models.append(model_name)

            if not available_models:
                return {
                    "success": False,
                    "available_models": [],
                    "unavailable_models": custom_model_list,
                    "api_models": [],
                }

            if unavailable_models:
                print(
                    f"[yellow]Warning: {unavailable_models} not accessible, using {available_models}[/yellow]"
                )

            return {
                "success": True,
                "available_models": available_models,
                "unavailable_models": unavailable_models,
                "api_models": [],
            }

        available_models = list(set(custom_model_list) & set(api_models))
        unavailable_models = list(set(custom_model_list) - set(api_models))

        if not available_models:
            print(
                f"[red]Error: None of the custom models {custom_model_list} are available in the API.[/red]"
            )
            print(f"[yellow]Available models: {api_models}[/yellow]")
            print(
                "[yellow]Please check your model name, API key, endpoint, and model permissions.[/yellow]"
            )
            return {
                "success": False,
                "available_models": [],
                "unavailable_models": custom_model_list,
                "api_models": api_models,
            }

        # If some models are not available, warn but continue with available ones
        if unavailable_models:
            print(
                f"[yellow]Warning: Models {unavailable_models} not found in API, using available models: {available_models}[/yellow]"
            )

        return {
            "success": True,
            "available_models": available_models,
            "unavailable_models": unavailable_models,
            "api_models": api_models,
        }

    def _set_models(
        self, model_family_name: str, default_azure_model: str, allowed_models: set
    ):
        """Generic method to set available models based on model family.

        Args:
            model_family_name: Human-readable name for error messages (e.g., "GPT-3.5")
            default_azure_model: Default model name to use for Azure deployments
            allowed_models: Set of allowed model IDs to intersect with API models
        """
        # For Azure deployments, use the default model directly
        if self.deployment_id:
            self.model_list = cycle([default_azure_model])
            self.model = default_azure_model
            return

        # For regular OpenAI client, fetch and filter available models
        my_model_list = self._fetch_api_models_with_retry()

        # If models API is not available, validate by testing each model directly
        if my_model_list is None:
            available_models = []
            unavailable_models = []

            for model_name in allowed_models:
                try:
                    self._validate_model_with_test(model_name, model_family_name)
                    available_models.append(model_name)
                except Exception as e:
                    print(f"[red]{e}[/red]")
                    unavailable_models.append(model_name)

            if not available_models:
                raise Exception(
                    f"No {model_family_name} models are accessible. "
                    f"Please check the model names and your API permissions."
                )

            if unavailable_models:
                print(
                    f"[yellow]Warning: {unavailable_models} not accessible, using {available_models}[/yellow]"
                )

            print(
                f"[yellow]Using {model_family_name} models without API validation: {available_models}[/yellow]"
            )
            model_list = available_models
        else:
            model_list = list(set(my_model_list) & allowed_models)
            if not self._check_model_availability(model_list, model_family_name):
                raise Exception(
                    f"No {model_family_name} models available. Available models: {my_model_list}"
                )
        print(f"Using model list {model_list}")
        self.model_list = cycle(model_list)
        self.model = model_list[0]

    def _validate_model_with_test(self, model_name: str, model_family_name: str):
        """Validate a model by making a test request when models API is unavailable.
        Raises Exception if the model is not accessible.

        NOTE: This makes a real API call (~10 tokens) to verify the model works.
        This adds a small delay on startup but provides early error detection.
        """
        print(
            f"[yellow]Model validation: Making a test API call to verify '{model_name}' is accessible. "
            f"This uses ~10 tokens.[/yellow]"
        )
        try:
            # Make a minimal test request
            test_messages = [{"role": "user", "content": "Say 'ok'"}]
            self.openai_client.chat.completions.create(
                model=model_name,
                messages=test_messages,
                max_tokens=10,
            )
            print(f"[green]Model '{model_name}' is accessible and working.[/green]")
        except Exception as e:
            raise Exception(
                f"Model '{model_name}' from family '{model_family_name}' is not accessible. "
                f"Error: {e}. "
                f"Please check the model name and your API permissions."
            )

    def set_gpt35_models(self, ollama_model=""):
        if ollama_model:
            self.model_list = cycle([ollama_model])
            self.model = ollama_model
            return
        self._set_models("GPT-3.5", "gpt-35-turbo", set(GPT35_MODEL_LIST))

    def set_gpt4_models(self):
        self._set_models("GPT-4", "gpt-4", set(GPT4_MODEL_LIST))

    def set_gpt4omini_models(self):
        self._set_models("GPT-4o-mini", "gpt-4o-mini", set(GPT4oMINI_MODEL_LIST))

    def set_gpt4o_models(self):
        self._set_models("GPT-4o", "gpt-4o", set(GPT4o_MODEL_LIST))

    def set_gpt5mini_models(self):
        self._set_models("GPT-5-mini", "gpt-5-mini", set(GPT5MINI_MODEL_LIST))

    def set_o1preview_models(self):
        self._set_models("O1-preview", "o1-preview", set(O1PREVIEW_MODEL_LIST))

    def set_o1_models(self):
        self._set_models("O1", "o1", set(O1_MODEL_LIST))

    def set_o1mini_models(self):
        self._set_models("O1-mini", "o1-mini", set(O1MINI_MODEL_LIST))

    def set_o3mini_models(self):
        self._set_models("O3-mini", "o3-mini", set(O3MINI_MODEL_LIST))

    def set_model_list(self, model_list):
        model_list = list(set(model_list))
        if not model_list:
            raise Exception(
                "Empty model list provided. Use --model_list with at least one model name."
            )

        # Validate custom models against API
        if not self.deployment_id:  # Skip for Azure deployments
            validation_result = self._validate_custom_models(model_list)
            if not validation_result["success"]:
                raise Exception(
                    f"Custom model validation failed. "
                    f"Requested: {model_list}. "
                    f"Unavailable: {validation_result['unavailable_models']}. "
                    f"Available models in API: {validation_result['api_models']}. "
                    f"Check your model name, API key, and permissions."
                )
            # If some models were partially available, use only the available ones
            if validation_result["unavailable_models"]:
                model_list = validation_result["available_models"]

        print(f"Using model list {model_list}")
        self.model_list = cycle(model_list)
        self.model = model_list[
            0
        ]  # Set initial model so it's available before rotate_model() is called

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
                        self.language,
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
                "json_schema": single_translation_schema(self.language),
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
