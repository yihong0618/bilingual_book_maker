import re
import time
import os
import shutil
from os import environ
from itertools import cycle
import json
from threading import Lock, RLock

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    AzureOpenAI,
    BadRequestError,
    LengthFinishReasonError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, ValidationError
from rich import print
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_not_exception_type,
)

from .base_translator import Base
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


class BatchTranslation(BaseModel):
    """Structured batch translation output (OpenAI Structured Outputs)."""

    model_config = ConfigDict(extra="forbid")

    paragraphs: list[str]


class SingleTranslation(BaseModel):
    """Structured single translation output."""

    model_config = ConfigDict(extra="forbid")

    translated: str


# Capability probe. The prompt asks for plain text and the schema pins a
# single-value enum, so the only way `PROBE_EXPECTED` can come back is if the
# server actually applied the schema to decoding. A proxy that accepts
# `response_format` and quietly drops it answers with the prompted text instead.
# Mirror of SingleTranslation for the Batch API, whose JSONL bodies are built by
# hand and therefore cannot use the SDK's Pydantic support.
SINGLE_TRANSLATION_SCHEMA = {
    "name": "single_translation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"translated": {"type": "string"}},
        "required": ["translated"],
        "additionalProperties": False,
    },
}

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

    def _ensure_structured_support(self, model=None, purpose="translate"):
        """Resolve (once per model) whether structured outputs can be used.

        The stored value is the probe verdict itself ("strict" / "shape" /
        False), because the two positive verdicts are not interchangeable:

        - `translate` requires "strict". Our translation schema pins the
          target language as a *value* constraint (#544), so an endpoint that
          honors shape but ignores values gives us a schema that cannot do
          the one job we added it for — worse than the delimiter method,
          which at least states the language in the prompt.
        - `classify` accepts "shape" too. Every verdict is linted locally
          against its enum, so an ignored value constraint costs one "unsure",
          never a silently wrong translation.

        The probe runs while holding the lock so that N parallel workers issue
        one probe per model, not N.
        """
        model = model or self.model
        with self._structured_lock:
            if model not in self._structured_support:
                if self.SUPPORTS_STRUCTURED_OUTPUTS:
                    self._test_structured_outputs(model)
                else:
                    self._structured_support[model] = False
            verdict = self._structured_support.get(model, False)
        return self._verdict_allows(verdict, purpose)

    @staticmethod
    def _verdict_allows(verdict, purpose):
        if not verdict:
            return False
        if purpose == "classify":
            return verdict in ("strict", "shape")
        return verdict == "strict"

    def _structured_enabled(self):
        return self._verdict_allows(
            self._structured_support.get(self.model, False), "translate"
        )

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
        """Return 'strict', 'shape' or 'unsupported' for a probe completion."""
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

        # Exact key set, so a json-mode-only server (right JSON, arbitrary keys)
        # and a server ignoring additionalProperties both fail here.
        if not isinstance(parsed, dict) or set(parsed) != {PROBE_KEY}:
            return "unsupported"
        if not isinstance(parsed[PROBE_KEY], str):
            return "unsupported"

        # Some backends honor the structure but ignore `enum`. Still usable: our
        # real schemas constrain shape only, never values.
        return "strict" if parsed[PROBE_KEY] == PROBE_EXPECTED else "shape"

    def _record_probe_result(self, model, verdict):
        """Store the verdict string; False means no schema support at all."""
        stored = verdict if verdict in ("strict", "shape") else False
        with self._structured_lock:
            self._structured_support[model] = stored
        if stored == "shape":
            print(
                f"[yellow]ℹ '{model}' honors JSON schema shape but not value "
                f"constraints; using the delimiter method for translation, "
                f"schema kept for classification[/yellow]"
            )
        elif not stored:
            print(
                f"[yellow]ℹ '{model}' doesn't apply JSON schema ({verdict}), "
                f"using delimiter method[/yellow]"
            )

    @staticmethod
    def _extract_json_object(text):
        """Pull the first balanced JSON object out of a model reply.

        Endpoints below strict decoding wrap answers in prose or ``` fences
        no matter how firmly the prompt says not to. Returns None when there
        is no parseable object — the caller decides what that means.
        """
        if not text:
            return None
        start = text.find("{")
        while start != -1:
            depth, in_string, escaped = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except json.JSONDecodeError:
                            break  # try the next opening brace
            start = text.find("{", start + 1)
        return None

    def structured_json(self, prompt, schema, model=None):
        """One-off JSON request outside the translation flow (plan
        classification), over a three-rung ladder:

        1. probe says strict/shape -> a real json_schema response_format
        2. probe says unsupported  -> json_object mode, schema inlined in the
           prompt (many proxies and local servers support this and nothing
           more)
        3. that too rejected       -> a plain completion, schema in the prompt

        Returns the parsed object, or None when no rung produced JSON. Value
        constraints are NOT guaranteed below rung 1 — callers lint verdicts
        locally, which is why classification tolerates a shape-only endpoint
        while translation does not.
        """
        model = model or self.model
        if self._ensure_structured_support(model, purpose="classify"):
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_schema", "json_schema": schema},
            )
            return self._extract_json_object(completion.choices[0].message.content)

        inlined = (
            f"{prompt}\n\nAnswer with a single JSON object of this exact "
            f"shape:\n{json.dumps(schema.get('schema', schema), ensure_ascii=False)}"
        )
        try:
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": inlined}],
                response_format={"type": "json_object"},
            )
        except BadRequestError as e:
            if "response_format" not in str(e):
                raise
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{inlined}\n\nOutput raw JSON only — no prose, "
                            f"no markdown fences."
                        ),
                    }
                ],
            )
        return self._extract_json_object(completion.choices[0].message.content)

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

    def create_context_messages(self):
        messages = []
        if self.context_flag:
            messages.append({"role": "user", "content": "\n".join(self.context_list)})
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(self.context_translated_list),
                }
            )
        return messages

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

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=SingleTranslation,
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
        return message.parsed.translated

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

        # Add structured format instruction
        content = (
            f"{user_prompt}\n\n"
            f"Return a JSON object with a 'paragraphs' array containing EXACTLY {plist_len} translated strings."
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

        messages = self._create_structured_batch_messages(text_list)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=BatchTranslation,
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

        paragraphs = message.parsed.paragraphs

        # A wrong count is a model error, not a capability answer: retry it.
        if len(paragraphs) != plist_len:
            raise ValueError(
                f"Expected {plist_len} translations, got {len(paragraphs)}"
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
                        result["response"]["body"]["choices"][0], custom_id
                    )

        raise ValueError(f"No result found for custom_id {custom_id}")

    @staticmethod
    def _read_batch_choice(choice, custom_id):
        """Unwrap one Batch API choice.

        Results are often fetched by a later process that never probed the
        model, so this decides from the payload itself rather than from cached
        capability state — and refuses to hand back a truncated JSON fragment.
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

        if isinstance(parsed, dict) and isinstance(parsed.get("translated"), str):
            return parsed["translated"]
        return content

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
        # the SDK here; SINGLE_TRANSLATION_SCHEMA mirrors SingleTranslation.
        if self._ensure_structured_support(self.batch_model):
            batch_body["response_format"] = {
                "type": "json_schema",
                "json_schema": SINGLE_TRANSLATION_SCHEMA,
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
