import json
import re
import time
import typing
from os import environ
from itertools import cycle

from google import genai
from google.genai import types, errors
from rich import print

from .base_translator import Base, BATCH_DELIMITER

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
        self.interval = 3
        self.client = genai.Client(api_key=next(self.keys))
        generation_config.temperature = temperature

    def create_convo(self):
        self.convo = self.client.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(
                temperature=generation_config.temperature,
                top_p=generation_config.top_p,
                top_k=generation_config.top_k,
                max_output_tokens=generation_config.max_output_tokens,
                safety_settings=safety_settings,
                system_instruction=self.prompt_sys_msg,
            ),
        )

    def rotate_model(self):
        self.model = next(self.model_list)
        self.create_convo()

    def rotate_key(self):
        self.client = genai.Client(api_key=next(self.keys))
        self.create_convo()

    def translate(self, text):
        delay = 1
        exponential_base = 2
        attempt_count = 0
        max_attempts = 7

        t_text = ""
        # same for caiyun translate src issue #279 gemini for #374
        text_list = text.splitlines()
        num = None
        if len(text_list) > 1:
            if text_list[0].isdigit():
                num = text_list[0]

        while attempt_count < max_attempts:
            try:
                response = self.convo.send_message(
                    self.prompt.format(text=text, language=self.language)
                )
                t_text = response.text.strip()
                # 检查是否包含特定标签,如果有则只返回标签内的内容
                tag_pattern = (
                    r"<step3_refined_translation>(.*?)</step3_refined_translation>"
                )
                tag_match = re.search(tag_pattern, t_text, re.DOTALL)
                if tag_match:
                    t_text = tag_match.group(1).strip()
                break
            except errors.APIError as e:
                # Check if it's a blocked prompt or stop candidate issue
                error_msg = str(e).lower()
                if "blocked" in error_msg or "stop" in error_msg:
                    print(
                        f"Translation failed due to API error: {e} Attempting to switch model..."
                    )
                    self.rotate_model()
                else:
                    print(
                        f"Translation failed due to API error: {e} Will sleep {delay} seconds"
                    )
                    time.sleep(delay)
                    delay *= exponential_base
                    self.rotate_key()
                    if attempt_count >= 1:
                        self.rotate_model()
            except Exception as e:
                print(
                    f"Translation failed due to {type(e).__name__}: {e} Will sleep {delay} seconds"
                )
                time.sleep(delay)
                delay *= exponential_base

                self.rotate_key()
                if attempt_count >= 1:
                    self.rotate_model()

            attempt_count += 1

        if attempt_count == max_attempts:
            print(f"Translation failed after {max_attempts} attempts.")
            return

        if self.context_flag:
            if len(self.convo.get_history()) > 10:
                # Trim history to keep only recent messages
                history = self.convo.get_history()
                self.convo = self.client.chats.create(
                    model=self.model,
                    config=types.GenerateContentConfig(
                        temperature=generation_config.temperature,
                        top_p=generation_config.top_p,
                        top_k=generation_config.top_k,
                        max_output_tokens=generation_config.max_output_tokens,
                        safety_settings=safety_settings,
                        system_instruction=self.prompt_sys_msg,
                    ),
                    history=history[-8:],  # Keep last 8 messages (4 exchanges)
                )
        else:
            # Clear history by creating new chat
            self.create_convo()

        # for rate limit (RPM)
        time.sleep(self.interval)
        if num:
            t_text = str(num) + "\n" + t_text
        return t_text

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
            return [self.translate(str(text_list[0]).strip())]

        # Build prompt for batch translation
        stripped_texts = [str(t).strip() for t in text_list]
        batch_text = "\n\n".join(
            f"[{i+1}] {text}" for i, text in enumerate(stripped_texts)
        )

        # Use user's prompt template if available, otherwise use default batch instruction
        if self.prompt and self.prompt != self.DEFAULT_PROMPT:
            # User has defined a custom prompt, use it for batch translation
            prompt = self.prompt.format(text=batch_text, language=self.language)
            # Add JSON schema instruction if not already present
            if "translated_paragraphs" not in prompt.lower():
                prompt += (
                    f"\n\nReturn the translations as a JSON object with a 'translated_paragraphs' "
                    f"field containing exactly {plist_len} translated texts in order."
                )
        else:
            # Default batch translation prompt
            prompt = (
                f"Translate these {plist_len} numbered paragraphs into {self.language}. "
                f"Return ONLY a valid JSON object with a 'translated_paragraphs' field containing "
                f"a list of exactly {plist_len} translated texts in the same order. "
                f"Do not include the original text, only the translations. "
                f"Return ONLY the JSON object, no other text.\n\n"
                f"{batch_text}"
            )

        delay = 1
        exponential_base = 2
        attempt_count = 0
        max_attempts = 7

        while attempt_count < max_attempts:
            try:
                # Use JSON schema enforcement for reliable parsing
                response = self.convo.send_message(
                    prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=TranslationResponse,
                        temperature=generation_config.temperature,
                        max_output_tokens=generation_config.max_output_tokens,
                        system_instruction=self.prompt_sys_msg,
                    ),
                )

                # Parse JSON response (should always be valid due to schema enforcement)
                try:
                    result = json.loads(response.text)
                    translated = result.get("translated_paragraphs", [])

                    if len(translated) == plist_len:
                        # for rate limit (RPM)
                        time.sleep(self.interval)
                        return translated
                    else:
                        print(
                            f"Warning: Expected {plist_len} translations, got {len(translated)}. "
                            f"Retrying..."
                        )
                except json.JSONDecodeError as e:
                    print(f"Failed to parse JSON response: {e}. Retrying...")
                    print(f"Response text: {response.text[:200]}")

            except errors.APIError as e:
                error_msg = str(e).lower()
                if "blocked" in error_msg or "stop" in error_msg:
                    print(
                        f"Batch translation failed due to API error: {e} "
                        f"Attempting to switch model..."
                    )
                    self.rotate_model()
                else:
                    print(
                        f"Batch translation failed due to API error: {e} "
                        f"Will sleep {delay} seconds"
                    )
                    time.sleep(delay)
                    delay *= exponential_base
                    self.rotate_key()
                    if attempt_count >= 1:
                        self.rotate_model()
            except Exception as e:
                print(
                    f"Batch translation failed due to {type(e).__name__}: {e} "
                    f"Will sleep {delay} seconds"
                )
                time.sleep(delay)
                delay *= exponential_base
                self.rotate_key()
                if attempt_count >= 1:
                    self.rotate_model()

            attempt_count += 1

        # Fallback to one-by-one translation if batch fails
        print(
            f"Batch translation failed after {max_attempts} attempts. "
            f"Falling back to one-by-one translation."
        )
        return [self.translate(t) for t in stripped_texts]
