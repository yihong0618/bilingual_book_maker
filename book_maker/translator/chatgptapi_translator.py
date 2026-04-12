import re
import time
import os
import shutil
from os import environ
from itertools import cycle
import json
from threading import Lock

from openai import AzureOpenAI, NotFoundError, OpenAI, RateLimitError
from rich import print
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .base_translator import Base
from ..config import config

CHATGPT_CONFIG = config["translator"]["chatgptapi"]

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}

# JSON Schema for structured batch translation output (OpenAI compatible)
TRANSLATION_SCHEMA = {
    "name": "translation_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"paragraphs": {"type": "array", "items": {"type": "string"}}},
        "required": ["paragraphs"],
        "additionalProperties": False,
    },
}

# Simpler schema for single translations
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
        self.extra_body = extra_body or {}

        # Structured outputs: auto-detected on first translate_list() call
        # None means "not yet tested", will be set to True/False after test
        self._use_structured_outputs = None
        self.model = (
            None  # Will be set by rotate_model() after model_list is initialized
        )

    def _test_structured_outputs(self):
        """Test if the server supports structured outputs (strict json schema)"""
        try:
            test_messages = [{"role": "user", "content": "Say 'test'"}]
            self.openai_client.chat.completions.create(
                model=self.model,
                messages=test_messages,
                temperature=0.1,
                response_format={
                    "type": "json_schema",
                    "json_schema": SINGLE_TRANSLATION_SCHEMA,
                },
            )
            self._use_structured_outputs = True
        except Exception:
            self._use_structured_outputs = False
            print(
                "[yellow]ℹ Server doesn't support JSON schema, using delimiter method[/yellow]"
            )

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

    def create_chat_completion(self, text):
        messages = self.create_messages(text, self.create_context_messages())

        if self._use_structured_outputs:
            completion = self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                response_format={
                    "type": "json_schema",
                    "json_schema": SINGLE_TRANSLATION_SCHEMA,
                },
                extra_body=self.extra_body if self.extra_body else None,
            )
        else:
            completion = self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                extra_body=self.extra_body if self.extra_body else None,
            )
        return completion

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((RateLimitError, Exception)),
        reraise=True,
    )
    def get_translation(self, text):
        self.rotate_key()
        self.rotate_model()  # rotate all the model to avoid the limit

        # Auto-detect if not yet tested
        if self._use_structured_outputs is None:
            self._test_structured_outputs()

        completion = self.create_chat_completion(text)

        # TODO work well or exception finish by length limit
        # Check if content is not None before encoding
        if completion.choices[0].message.content is not None:
            t_text = completion.choices[0].message.content.encode("utf8").decode() or ""
        else:
            t_text = ""

        # Parse structured output if enabled
        if self._use_structured_outputs and t_text:
            try:
                parsed = json.loads(t_text)
                t_text = parsed.get("translated", t_text)
            except json.JSONDecodeError as e:
                print(
                    f"[yellow]Warning: Failed to parse structured output: {e}[/yellow]"
                )

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
        # Auto-detect output mode on first use
        if self._use_structured_outputs is None:
            self._test_structured_outputs()

        # Use structured outputs if available
        if self._use_structured_outputs:
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
        except Exception as e:
            print(
                f"[yellow]Structured batch translation failed after retries: {e}. "
                f"Falling back to one-by-one translation.[/yellow]"
            )
            return [self.translate(t, False) for t in text_list]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((RateLimitError, Exception)),
        reraise=True,
    )
    def _execute_structured_batch_translate(self, text_list, plist_len):
        """Execute the actual structured batch translation with tenacity retry"""
        self.rotate_key()
        self.rotate_model()

        messages = self._create_structured_batch_messages(text_list)

        completion = self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            response_format={
                "type": "json_schema",
                "json_schema": TRANSLATION_SCHEMA,
            },
            extra_body=self.extra_body if self.extra_body else None,
        )

        t_text = completion.choices[0].message.content.encode("utf8").decode() or ""

        if not t_text:
            raise ValueError("Structured output returned empty response")

        try:
            parsed = json.loads(t_text)
            paragraphs = parsed.get("paragraphs", [])
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse structured batch output: {e}") from e

        if len(paragraphs) != plist_len:
            raise ValueError(
                f"Expected {plist_len} translations, got {len(paragraphs)}"
            )

        if self.context_flag:
            for orig, trans in zip(text_list, paragraphs):
                self.save_context(orig, trans)

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
        except NotFoundError:
            # 404 — models endpoint not supported by this API provider
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
                temperature=0.1,
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
                    content = result["response"]["body"]["choices"][0]["message"][
                        "content"
                    ]

                    # Parse JSON response if using structured outputs
                    if self._use_structured_outputs:
                        try:
                            parsed = json.loads(content)
                            if "translated" in parsed:
                                return parsed["translated"]
                            elif "paragraphs" in parsed:
                                return (
                                    parsed["paragraphs"][0]
                                    if parsed["paragraphs"]
                                    else content
                                )
                        except json.JSONDecodeError:
                            return content  # Return as-is if parsing fails

                    return content

        raise ValueError(f"No result found for custom_id {custom_id}")

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
            "temperature": self.temperature,
        }

        # Add response format for batch requests if using structured outputs
        if self._use_structured_outputs:
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
