import re
import time

import google.generativeai as genai
from google.generativeai.types.generation_types import (
    StopCandidateException,
    BlockedPromptException,
)
from rich import print

from .base_translator import Base

generation_config = {
    "temperature": 0.7,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 2048,
}

safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_MEDIUM_AND_ABOVE",
    },
]


class Gemini(Base):
    """
    Google gemini translator
    """

    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    def __init__(self, key, language, **kwargs) -> None:
        genai.configure(api_key=key)
        super().__init__(key, language)
        model = genai.GenerativeModel(
            model_name="gemini-pro",
            generation_config=generation_config,
            safety_settings=safety_settings,
        )
        self.convo = model.start_chat()

    def rotate_key(self):
        pass

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

        if len(self.convo.history) > 10:
            self.convo.history = self.convo.history[2:]

        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        # for limit
        time.sleep(0.5)
        if num:
            t_text = str(num) + "\n" + t_text
        return t_text
