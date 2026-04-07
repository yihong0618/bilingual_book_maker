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

from .base_translator import Base


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

GEMINIPRO_MODEL_LIST = [
    "gemini-1.5-pro",
    "gemini-1.5-pro-latest",
    "gemini-1.5-pro-001",
    "gemini-1.5-pro-002",
]

GEMINIFLASH_MODEL_LIST = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-001",
    "gemini-1.5-flash-002",
    "gemini-2.0-flash-exp",
    "gemini-2.5-flash-preview-04-17",
]


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

    # Regex patterns
    TAG_PATTERN = r"<step3_refined_translation>(.*?)</step3_refined_translation>"

    def __init__(
        self,
        key,
        language,
        prompt_template=None,
        prompt_sys_msg=None,
        context_flag=False,
        temperature=1.0,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
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
        self.interval = self.DEFAULT_INTERVAL
        self.client = genai.Client(api_key=next(self.keys))
        generation_config.temperature = temperature

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
            "system_instruction": self.prompt_sys_msg,
        }

        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type

        if response_schema:
            config_kwargs["response_schema"] = response_schema

        return config_kwargs

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
        self.client = genai.Client(api_key=next(self.keys))
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
            response = self.convo.send_message(
                self.prompt.format(text=text, language=self.language)
            )
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
        except Exception as e:
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

    _available_models_cache = None

    def set_interval(self, interval):
        self.interval = interval

    def set_geminipro_models(self):
        self.set_models(GEMINIPRO_MODEL_LIST)

    def set_geminiflash_models(self):
        self.set_models(GEMINIFLASH_MODEL_LIST)

    def set_models(self, allowed_models):
        if Gemini._available_models_cache is None:
            available_models = [
                re.sub(r"^models/", "", m.name) for m in self.client.models.list()
            ]
            Gemini._available_models_cache = available_models
        else:
            available_models = Gemini._available_models_cache

        model_list = sorted(
            list(set(available_models) & set(allowed_models)),
            key=allowed_models.index,
        )
        print(f"Using model list {model_list}")
        self.model_list = cycle(model_list)
        self.rotate_model()

    def set_model_list(self, model_list):
        # keep the order of input
        model_list = sorted(list(set(model_list)), key=model_list.index)
        print(f"Using model list {model_list}")
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
                    system_instruction=self.prompt_sys_msg,
                ),
            )

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
        except ValueError:
            # Parsing/response mismatch - retry without rotating key
            raise
        except Exception as e:
            self.rotate_key()
            raise

    def _batch_translate(self, text_list: list[str], batch_size: int) -> list[str]:
        """Attempt batch translation with retries and fallback."""
        # Check if fatal error was already detected during batch attempt
        if self._fatal_error_detected:
            return [self.TRANSLATION_ERROR_MARKER] * batch_size

        stripped_texts = [str(t).strip() for t in text_list]
        batch_text = "\n\n".join(stripped_texts)

        prompt = self.prompt.format(text=batch_text, language=self.language)
        if "translated_paragraphs" not in prompt.lower():
            prompt += (
                f"\n\nReturn the translations as a JSON object with a 'translated_paragraphs' "
                f"field containing exactly {batch_size} translated texts in order."
            )

        result = self._batch_translate_with_retry(prompt, batch_size)

        # Check again after retry attempt (error may have been detected during retries)
        if self._fatal_error_detected:
            print(f"Batch translation aborted: fatal error detected.")
            return [self.TRANSLATION_ERROR_MARKER] * batch_size

        if result:
            return result

        # Fallback to one-by-one translation (only for non-fatal errors)
        print(
            f"Batch translation failed after all retry attempts. "
            f"Falling back to one-by-one translation."
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
        """Parse and validate batch translation response."""
        try:
            result = json.loads(response_text)
            translated = result.get("translated_paragraphs", [])

            if len(translated) != expected_count:
                print(
                    f"Warning: Expected {expected_count} translations, got {len(translated)}. "
                    f"Retrying..."
                )
                return None

            return [str(t) for t in translated]

        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON response: {e}. Retrying...")
            print(f"Response text: {response_text[:200]}")
            return None
