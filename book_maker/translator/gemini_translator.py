import re
import time
from os import environ
from itertools import cycle

import google.generativeai as genai
from google.generativeai.types.generation_types import (
    StopCandidateException,
    BlockedPromptException,
)
from rich import print

from .base_translator import Base

generation_config = {
    "temperature": 1.0,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 8192,
}

safety_settings = {
    "HATE": "BLOCK_NONE",
    "HARASSMENT": "BLOCK_NONE",
    "SEXUAL": "BLOCK_NONE",
    "DANGEROUS": "BLOCK_NONE",
}

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
]


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
        interval=0.01,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.context_flag = context_flag
        self.interval = interval
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

        genai.configure(api_key=next(self.keys))
        generation_config["temperature"] = temperature

    def create_convo(self):
        model = genai.GenerativeModel(
            model_name=self.model,
            generation_config=generation_config,
            safety_settings=safety_settings,
            system_instruction=self.prompt_sys_msg,
        )
        self.convo = model.start_chat()
        # print(model)  # Uncomment to debug and inspect the model details. 

    def rotate_model(self):
        self.model = next(self.model_list)
        self.create_convo()
        print(f"Using model {self.model}")

    def rotate_key(self):
        genai.configure(api_key=next(self.keys))

    def translate(self, text):
        t_text = ""
        print(text)
        # same for caiyun translate src issue #279 gemini for #374
        text_list = text.splitlines()
        num = None
        if len(text_list) > 1:
            if text_list[0].isdigit():
                num = text_list[0]
        try:
            self.convo.send_message(
                self.DEFAULT_PROMPT.format(text=text, language=self.language)
            )
            print(text)
            t_text = self.convo.last.text.strip()
        except StopCandidateException as e:
            match = re.search(r'content\s*{\s*parts\s*{\s*text:\s*"([^"]+)"', str(e))
            if match:
                t_text = match.group(1)
                t_text = re.sub(r"\\n", "\n", t_text)
            else:
                t_text = "Can not translate"
        except BlockedPromptException as e:
            print(str(e))
            t_text = "Can not translate by SAFETY reason.(因安全问题不能翻译)"
        except Exception as e:
            print(str(e))
            t_text = "Can not translate by other reason.(因安全问题不能翻译)"


        if self.context_flag:
            if len(self.convo.history) > 10:
                self.convo.history = self.convo.history[2:]
        else:
            self.convo.history = []

        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        # for rate limit(RPM)
        time.sleep(self.interval)
        if num:
            t_text = str(num) + "\n" + t_text
        return t_text

    def set_geminipro_models(self):
        self.set_models(GEMINIPRO_MODEL_LIST)

    def set_geminiflash_models(self):
        self.set_models(GEMINIFLASH_MODEL_LIST)

    def set_models(self, allowed_models):
        available_models = [
            re.sub(r"^models/", "", i.name) for i in genai.list_models()
        ]
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
