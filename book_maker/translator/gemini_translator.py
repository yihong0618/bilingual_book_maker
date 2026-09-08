import json
import re
import time
import typing
from os import environ
from itertools import cycle

from google import genai
from google.genai import types, errors
from rich import print
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    RetryCallState,
)

from .base_translator import Base, BatchMismatch
from ..structured import RungRejected, extract_json_object, unwrap_schema_echo


def _openapi_schema(schema):
    """JSON Schema -> the OpenAPI subset `response_schema` accepts.

    Gemini's schema dialect has no `additionalProperties` and no `strict`, and
    spells its types in upper case. Anything it does not understand is a 400,
    which only costs a rung — but converting is cheap and keeps the strongest
    rung reachable for the classify schema, whose properties are built at run
    time from signature names and so can never be a TypedDict.
    """
    body = schema.get("schema", schema)
    return _openapi_node(body)


def _openapi_node(node):
    if not isinstance(node, dict):
        return node
    out = {}
    if node.get("type"):
        out["type"] = str(node["type"]).upper()
    for key in ("description", "enum", "required"):
        if key in node:
            out[key] = node[key]
    if isinstance(node.get("properties"), dict):
        out["properties"] = {
            name: _openapi_node(spec) for name, spec in node["properties"].items()
        }
    if "items" in node:
        out["items"] = _openapi_node(node["items"])
    return out


def _print_retry_details(retry_state: RetryCallState) -> None:
    """Print retry attempt information."""
    exception = retry_state.outcome.exception()
    attempt_number = retry_state.attempt_number
    error_msg = str(exception).lower()

    if isinstance(exception, ValueError):
        print(
            f"Retry attempt {attempt_number} due to {type(exception).__name__}: {exception}"
        )
    elif "blocked" in error_msg or "stop" in error_msg:
        print(
            f"Retry attempt {attempt_number} due to {type(exception).__name__}: {exception} (switching model)"
        )
    else:
        print(
            f"Retry attempt {attempt_number} due to {type(exception).__name__}: {exception} (rotating key)"
        )


def _should_retry(exception: Exception) -> bool:
    """Determine if we should retry based on exception type."""
    # Never retry on user interrupt
    if isinstance(exception, KeyboardInterrupt):
        return False
    # A miscounted batch is not a transient failure: the same group asked
    # again usually comes back miscounted again, and each attempt re-pays
    # the whole group. The loader's ladder halves it instead.
    if isinstance(exception, BatchMismatch):
        return False
    # Don't retry geo-restriction errors
    if isinstance(exception, errors.APIError):
        if exception.status == "FAILED_PRECONDITION":
            return False
    return True


generation_config = types.GenerateContentConfig(
    temperature=1.0,
    top_p=1,
    top_k=1,
    max_output_tokens=8192,
)

safety_settings = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

PROMPT_ENV_MAP = {
    "user": "BBM_GEMINIAPI_USER_MSG_TEMPLATE",
    "system": "BBM_GEMINIAPI_SYS_MSG",
}


class TranslationResponse(typing.TypedDict):
    """Schema for batch translation response."""

    translated_paragraphs: list[str]


class Gemini(Base):
    """
    Google gemini translator
    """

    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    # Configuration constants
    DEFAULT_INTERVAL = 3
    MAX_RETRY_ATTEMPTS = 7
    HISTORY_TRIM_THRESHOLD = 10
    HISTORY_KEEP_SIZE = 8

    # Error marker for failed translations
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"

    # This route's context is the chat object, and
    # `_clone_translator_for_context` gives each worker a fresh one — that
    # branch was written for this class.
    SUPPORTS_PARALLEL_CONTEXT = True

    # Regex patterns
    TAG_PATTERN = r"<step3_refined_translation>(.*?)</step3_refined_translation>"

    # `--prompt`'s style section. Class-level so an instance built without
    # __init__ — a subclass, a test double — still answers.
    style_note = None

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        context_flag=False,
        temperature=1.0,
        style_note=None,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        # `api_base` used to be swallowed by **kwargs and discarded, so
        # --api_base was a silent no-op for gemini and every request went to
        # generativelanguage.googleapis.com regardless.
        self.api_base = api_base
        self.context_flag = context_flag
        self.prompt = (
            prompt_template
            or environ.get(PROMPT_ENV_MAP["user"])
            or self.DEFAULT_PROMPT
        )
        self.prompt_sys_msg = (
            prompt_sys_msg
            or environ.get(PROMPT_ENV_MAP["system"])
            or None  # Allow None, but not empty string
        )
        # `--prompt`'s style section. It used to fall into **kwargs and be
        # discarded here, so a style this route was given was never sent.
        self.style_note = style_note
        self.interval = self.DEFAULT_INTERVAL
        self.client = self._new_client()
        generation_config.temperature = temperature

    def _new_client(self):
        http_options = (
            types.HttpOptions(base_url=self.api_base) if self.api_base else None
        )
        return genai.Client(api_key=next(self.keys), http_options=http_options)

    def _system_instruction(self):
        """The system slot's value, or None.

        `--prompt`'s system section fills it, with `{language}`/`{crlf}`
        resolved the way the other routes resolve them. None rather than "":
        this SDK takes an absent instruction, and an empty one is not the same
        request.
        """
        return self._augment_system_content(
            self.fill_optional(self.prompt_sys_msg) or None
        )

    def _build_config_kwargs(
        self, response_mime_type: str | None = None, response_schema: type | None = None
    ) -> dict:
        """Build configuration kwargs for API calls."""
        config_kwargs = {
            "temperature": generation_config.temperature,
            "top_p": generation_config.top_p,
            "top_k": generation_config.top_k,
            "max_output_tokens": generation_config.max_output_tokens,
            "safety_settings": safety_settings,
            # `--source_lang` names the source; the note is fixed for a
            # run, so it belongs with the run's standing instructions.
            "system_instruction": self._system_instruction(),
        }

        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type

        if response_schema:
            config_kwargs["response_schema"] = response_schema

        return config_kwargs

    def _note_usage(self, response, model=None) -> None:
        """Record one response's tokens on the shared meter.

        Nothing else reads Gemini's `usage_metadata`, so without this a run
        reports no tokens and a provider entry's prices never apply. A
        response without the field (or with None counts, which the SDK does
        return) is skipped rather than counted as zero-cost.
        """
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return
        self.usage.note(
            prompt=getattr(meta, "prompt_token_count", 0) or 0,
            completion=getattr(meta, "candidates_token_count", 0) or 0,
            cached=getattr(meta, "cached_content_token_count", 0) or 0,
            # a classification request may name --plan-classify-model, and
            # pricing it as the translation model would be wrong
            model=model or getattr(self, "model", None),
        )

    def _user_content(self, text: str) -> str:
        """The turn's text: the user template, then the style section.

        Gemini's system slot is `system_instruction`, so `system` is native
        here. There is no style slot, so `--prompt`'s style rides at the end
        of the turn, in the wording every other route uses. `{crlf}` is filled
        too — it is documented for `--prompt` and used to raise KeyError here.
        """
        return (
            self.prompt.format(text=text, language=self.language, crlf="\n")
            + self.style_suffix()
        )

    def _extract_translation_text(self, response_text: str) -> str:
        """Extract translation from response, handling custom tags if present."""
        text = response_text.strip()
        tag_match = re.search(self.TAG_PATTERN, text, re.DOTALL)
        if tag_match:
            text = tag_match.group(1).strip()
        return text

    def create_convo(self):
        """Create a new chat conversation with configured model."""
        config_kwargs = self._build_config_kwargs()
        self.convo = self.client.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(**config_kwargs),
        )

    def rotate_model(self):
        self.model = next(self.model_list)
        self.create_convo()

    def rotate_key(self):
        self.client = self._new_client()
        self.create_convo()

    def _manage_conversation_history(self) -> None:
        """Manage conversation history to prevent memory bloat."""
        if self.context_flag:
            history = self.convo.get_history()
            if len(history) > self.HISTORY_TRIM_THRESHOLD:
                config_kwargs = self._build_config_kwargs()
                self.convo = self.client.chats.create(
                    model=self.model,
                    config=types.GenerateContentConfig(**config_kwargs),
                    history=history[-self.HISTORY_KEEP_SIZE :],
                )
        else:
            # Clear history by creating new chat
            self.create_convo()

    def _is_fatal_error(self, exception: Exception) -> bool:
        """Check if error should not be retried."""
        if isinstance(exception, errors.APIError):
            return exception.status == "FAILED_PRECONDITION"
        return False

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception(_should_retry),
        before_sleep=_print_retry_details,
        reraise=True,
    )
    def _translate_with_retry(self, text: str, paragraph_num: str | None) -> str:
        """Internal translation method with tenacity retry logic."""
        try:
            response = self.convo.send_message(self._user_content(text))
            self._note_usage(response)
            t_text = self._extract_translation_text(response.text)

            # Restore paragraph number if present
            if paragraph_num:
                t_text = f"{paragraph_num}\n{t_text}"

            # Manage history after successful translation
            self._manage_conversation_history()
            time.sleep(self.interval)
            return t_text

        except errors.APIError as e:
            error_msg = str(e).lower()
            if "blocked" in error_msg or "stop" in error_msg:
                self.rotate_model()
            else:
                self.rotate_key()
            raise
        except Exception:
            self.rotate_key()
            raise

    def translate(self, text: str) -> str | None:
        """Translate a single text string."""
        # Skip if fatal error already detected
        if self._fatal_error_detected:
            return self.TRANSLATION_ERROR_MARKER

        text_list = text.splitlines()
        paragraph_num = None
        if len(text_list) > 1 and text_list[0].isdigit():
            paragraph_num = text_list[0]

        try:
            return self._translate_with_retry(text, paragraph_num)
        except Exception as e:
            if self._is_fatal_error(e):
                self._fatal_error_detected = True
                print(f"Translation disabled due to fatal error: {e}")
            else:
                print(f"Translation failed after all retry attempts: {e}")
            return self.TRANSLATION_ERROR_MARKER

    # ------------------------------------------------- plan classification

    def _generate(self, prompt: str, model=None, **config_kwargs) -> str:
        """One stateless request. Never `self.convo`.

        Classification must not enter the translation conversation: its
        question and answer would become context for the paragraphs that
        follow it.
        """
        try:
            response = self.client.models.generate_content(
                model=model or getattr(self, "model", None),
                contents=prompt,
                config=types.GenerateContentConfig(
                    safety_settings=safety_settings, **config_kwargs
                ),
            )
        except errors.ClientError as e:
            # 400/422 is "I will not take this request shape"; 401/403/404 are
            # permanent answers about the endpoint, which a lower rung cannot
            # fix, so those propagate.
            if getattr(e, "code", None) in (400, 422):
                raise RungRejected(e) from e
            raise
        self._note_usage(response, model=model or getattr(self, "model", None))
        return response.text

    def _chat_completion(self, prompt, model=None):
        return self._generate(prompt, model)

    def _response_schema_rung(self, prompt, schema, model):
        text = self._generate(
            prompt,
            model,
            response_mime_type="application/json",
            response_schema=_openapi_schema(schema),
        )
        return unwrap_schema_echo(extract_json_object(text))

    def structured_rungs(self, prompt, schema, model=None):
        """Native constrained decoding first, a described schema underneath.

        No capability probe: with failure-driven descent there is nothing a
        verdict could decide that the first real request does not decide
        better, and a probe would cost a request per model to learn it.
        """
        target = model or getattr(self, "model", None)
        return [
            (
                "response_schema",
                lambda: self._response_schema_rung(prompt, schema, target),
            ),
            ("prompt", lambda: self._prompt_rung(prompt, schema, target)),
        ]

    def set_interval(self, interval):
        self.interval = interval

    def set_model_list(self, model_list):
        # keep the order of input
        model_list = sorted(list(set(model_list)), key=model_list.index)
        print(f"Using model list {model_list}")
        # `model_list` becomes an endless cycle, which the output file's
        # translation note cannot iterate; `_model_names` is the readable
        # copy it reads, and a rotation mid-book means every name here may
        # have translated part of the book.
        self._model_names = tuple(model_list)
        self._configured_model_names = tuple(model_list)
        self.model_list = cycle(model_list)
        self.rotate_model()

    def translate_list(self, text_list: list[str]) -> list[str]:
        """
        Translate multiple texts using JSON Schema for structured output.

        This method sends all paragraphs in a single batch request with JSON schema
        enforcement to ensure reliable parsing. It respects custom prompt templates
        and system messages if provided.

        Args:
            text_list: List of text strings to translate.

        Returns:
            List of translated text strings in the same order as input.

        Note:
            Falls back to one-by-one translation if batch translation fails
            after all retry attempts.
        """
        plist_len = len(text_list)

        if plist_len == 0:
            return []

        if plist_len == 1:
            result = self.translate(str(text_list[0]).strip())
            return [result if result else self.TRANSLATION_ERROR_MARKER]

        return self._batch_translate(text_list, plist_len)

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception(_should_retry),
        before_sleep=_print_retry_details,
        reraise=True,
    )
    def _batch_translate_with_retry(
        self, prompt: str, batch_size: int
    ) -> list[str] | None:
        """Internal batch translation with tenacity retry."""
        try:
            response = self.convo.send_message(
                prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TranslationResponse,
                    temperature=generation_config.temperature,
                    system_instruction=self._system_instruction(),
                ),
            )

            self._note_usage(response)
            result = self._parse_batch_response(response.text, batch_size)
            if result:
                self._manage_conversation_history()
                time.sleep(self.interval)
                return result
            # If result is None, raise an exception to trigger retry
            # Don't rotate key for parsing/response issues
            raise ValueError("Invalid batch translation response")

        except errors.APIError as e:
            if self._is_fatal_error(e):
                self._fatal_error_detected = True
            error_msg = str(e).lower()
            if "blocked" in error_msg or "stop" in error_msg:
                self.rotate_model()
            else:
                self.rotate_key()
            raise
        except BatchMismatch:
            # Not retried and not repaired here: the loader divides. Rotating
            # the key would blame the credential for a counting mistake.
            raise
        except ValueError:
            # Parsing/response mismatch - retry without rotating key
            raise
        except Exception:
            self.rotate_key()
            raise

    def _batch_translate(self, text_list: list[str], batch_size: int) -> list[str]:
        """Attempt batch translation with retries and fallback."""
        # Check if fatal error was already detected during batch attempt
        if self._fatal_error_detected:
            return [self.TRANSLATION_ERROR_MARKER] * batch_size

        stripped_texts = [str(t).strip() for t in text_list]

        # The model silently drops empty/whitespace paragraphs, causing a bogus
        # "expected N, got N-1" mismatch. Send only the non-empty ones.
        non_empty_indices = [i for i, s in enumerate(stripped_texts) if s]
        non_empty_texts = [stripped_texts[i] for i in non_empty_indices]

        if not non_empty_texts:
            # Nothing to translate — return the originals unchanged.
            return list(text_list)

        expected_count = len(non_empty_texts)
        batch_text = "\n\n".join(non_empty_texts)

        prompt = self._user_content(batch_text)
        if "translated_paragraphs" not in prompt.lower():
            prompt += (
                f"\n\nReturn the translations as a JSON object with a 'translated_paragraphs' "
                f"field containing exactly {expected_count} translated texts in order."
            )

        result = self._batch_translate_with_retry(prompt, expected_count)

        # Check again after retry attempt (error may have been detected during retries)
        if self._fatal_error_detected:
            print("Batch translation aborted: fatal error detected.")
            return [self.TRANSLATION_ERROR_MARKER] * batch_size

        if result:
            # Put each translation back at its original index; empty paragraphs stay as-is.
            merged = list(text_list)
            for idx, value in zip(non_empty_indices, result):
                merged[idx] = value
            # count is not alignment — an empty slot for a non-empty source
            # is the one symptom of a shifted batch
            self._check_batch(text_list, merged)
            return merged

        # Fallback to one-by-one translation (only for non-fatal errors)
        print(
            "Batch translation failed after all retry attempts. "
            "Falling back to one-by-one translation."
        )

        # Always return the expected number of items
        translations = []
        for text in text_list:
            t = self.translate(str(text).strip())
            if self._fatal_error_detected:
                # Complete remaining items with error markers
                remaining = len(text_list) - len(translations)
                translations.extend([self.TRANSLATION_ERROR_MARKER] * remaining)
                break
            translations.append(t if t else self.TRANSLATION_ERROR_MARKER)

        return translations

    def _parse_batch_response(
        self, response_text: str, expected_count: int
    ) -> list[str] | None:
        """Parse and validate batch translation response.

        Raises `BatchMismatch` when the reply cannot be aligned — not a
        retry, and not a per-line fallback: the loader's ladder halves the
        chunk, which costs about twice the batch instead of N singles.
        """
        try:
            result = json.loads(response_text)
            translated = result.get("translated_paragraphs", [])

            if len(translated) != expected_count:
                raise BatchMismatch(
                    f"expected {expected_count} translations, " f"got {len(translated)}"
                )

            return [str(t) for t in translated]

        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON response: {e}. Retrying...")
            print(f"Response text: {response_text[:200]}")
            return None
