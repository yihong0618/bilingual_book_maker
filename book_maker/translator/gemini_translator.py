import json
import re
import time
import typing
from os import environ
from itertools import cycle

from google import genai
from google.genai import types, errors
from rich import print

from .base_translator import Base

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
    INITIAL_RETRY_DELAY = 1
    EXPONENTIAL_BACKOFF_BASE = 2
    MAX_RETRY_ATTEMPTS = 7
    HISTORY_TRIM_THRESHOLD = 10
    HISTORY_KEEP_SIZE = 8

    # Regex patterns
    TAG_PATTERN = r"<step3_refined_translation>(.*?)</step3_refined_translation>"
    PARAGRAPH_NUMBER_PATTERN = r"^\s*[\[\(]\d+[\]\)]\s*"

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

    def _remove_paragraph_number(self, text: str) -> str:
        """Remove leading paragraph numbers like '[1]' or '(1)' from text."""
        return re.sub(self.PARAGRAPH_NUMBER_PATTERN, "", text)

    def _extract_translation_text(self, response_text: str) -> str:
        """Extract translation from response, handling custom tags if present."""
        text = response_text.strip()
        tag_match = re.search(self.TAG_PATTERN, text, re.DOTALL)
        if tag_match:
            text = tag_match.group(1).strip()
        return self._remove_paragraph_number(text)

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

    def translate(self, text: str) -> str | None:
        """Translate a single text string."""
        delay = self.INITIAL_RETRY_DELAY
        attempt_count = 0

        text_list = text.splitlines()
        paragraph_num = None
        if len(text_list) > 1 and text_list[0].isdigit():
            paragraph_num = text_list[0]

        while attempt_count < self.MAX_RETRY_ATTEMPTS:
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
                self._handle_api_error(e, delay, attempt_count)
                delay *= self.EXPONENTIAL_BACKOFF_BASE
            except Exception as e:
                self._handle_general_error(e, delay, attempt_count)
                delay *= self.EXPONENTIAL_BACKOFF_BASE

            attempt_count += 1

        print(f"Translation failed after {self.MAX_RETRY_ATTEMPTS} attempts.")
        return None

    def _handle_api_error(
        self, error: errors.APIError, delay: float, attempt: int
    ) -> None:
        """Handle API errors with appropriate retry strategy."""
        error_msg = str(error).lower()
        if "blocked" in error_msg or "stop" in error_msg:
            print(f"Translation failed due to API error: {error}. Switching model...")
            self.rotate_model()
        else:
            print(
                f"Translation failed due to API error: {error}. Retrying in {delay}s..."
            )
            time.sleep(delay)
            self.rotate_key()
            if attempt >= 1:
                self.rotate_model()

    def _handle_general_error(
        self, error: Exception, delay: float, attempt: int
    ) -> None:
        """Handle general errors with exponential backoff."""
        print(
            f"Translation failed due to {type(error).__name__}: {error}. Retrying in {delay}s..."
        )
        time.sleep(delay)
        self.rotate_key()
        if attempt >= 1:
            self.rotate_model()

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
            return [result] if result else []

        return self._batch_translate(text_list, plist_len)

    def _batch_translate(self, text_list: list[str], batch_size: int) -> list[str]:
        """Attempt batch translation with retries and fallback."""
        stripped_texts = [str(t).strip() for t in text_list]
        batch_text = "\n\n".join(
            f"[{i+1}] {text}" for i, text in enumerate(stripped_texts)
        )

        prompt = self.prompt.format(text=batch_text, language=self.language)
        if "translated_paragraphs" not in prompt.lower():
            prompt += (
                f"\n\nReturn the translations as a JSON object with a 'translated_paragraphs' "
                f"field containing exactly {batch_size} translated texts in order."
            )

        delay = self.INITIAL_RETRY_DELAY
        attempt_count = 0

        while attempt_count < self.MAX_RETRY_ATTEMPTS:
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

            except errors.APIError as e:
                self._handle_api_error(e, delay, attempt_count)
                delay *= self.EXPONENTIAL_BACKOFF_BASE
            except Exception as e:
                self._handle_general_error(e, delay, attempt_count)
                delay *= self.EXPONENTIAL_BACKOFF_BASE

            attempt_count += 1

        # Fallback to one-by-one translation
        print(
            f"Batch translation failed after {self.MAX_RETRY_ATTEMPTS} attempts. "
            f"Falling back to one-by-one translation."
        )
        return [t for t in (self.translate(text) for text in text_list) if t]

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

            # Remove leading paragraph numbers
            return [self._remove_paragraph_number(str(t)) for t in translated]

        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON response: {e}. Retrying...")
            print(f"Response text: {response_text[:200]}")
            return None
