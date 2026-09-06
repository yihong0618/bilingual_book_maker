import json
import re
import time

import requests
from rich import print

from .base_translator import Base, NO_PROMPT_SECTIONS


class Caiyun(Base):
    """
    caiyun translator
    """

    # Handed text and nothing else: --prompt has no slot here.
    PROMPT_SECTION_SLOTS = NO_PROMPT_SECTIONS

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)
        self.api_url = "https://api.interpreter.caiyunai.com/v1/translator"
        self.headers = {
            "content-type": "application/json",
            # One key per request, set by rotate_key: `key` may be the whole
            # comma-separated list, which is not a credential.
            "x-authorization": f"token {next(self.keys)}",
        }
        # caiyun api only supports: zh2en, zh2ja, en2zh, ja2zh
        self.translate_type = "auto2zh"
        if self.language == "english":
            self.translate_type = "auto2en"
        elif self.language == "japanese":
            self.translate_type = "auto2ja"

    def rotate_key(self):
        self.headers["x-authorization"] = f"token {next(self.keys)}"

    def translate(self, text):
        self.rotate_key()
        # for caiyun translate src issue #279
        text_list = text.splitlines()
        num = None
        if len(text_list) > 1:
            if text_list[0].isdigit():
                num = text_list[0]
        payload = {
            "source": text,
            "trans_type": self.translate_type,
            "request_id": "demo",
            "detect": True,
        }
        response = requests.request(
            "POST",
            self.api_url,
            data=json.dumps(payload),
            headers=self.headers,
        )
        try:
            t_text = response.json()["target"]
        except Exception as e:
            print(str(e), response.text, "will sleep 60s for the time limit")
            if "limit" in response.json()["message"]:
                print("will sleep 60s for the time limit")
            time.sleep(60)
            response = requests.request(
                "POST",
                self.api_url,
                data=json.dumps(payload),
                headers=self.headers,
            )
            t_text = response.json()["target"]

        # for issue #279
        if num:
            t_text = str(num) + "\n" + t_text
        return t_text
