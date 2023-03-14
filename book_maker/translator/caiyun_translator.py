import json

import requests

from .base_translator import Base


class Caiyun(Base):
    """
    caiyun translator
    """

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language)
        self.api_url = "http://api.interpreter.caiyunai.com/v1/translator"
        self.headers = {
            "content-type": "application/json",
            "x-authorization": "token " + key,
        }
        # caiyun api only supports: zh2en, zh2ja, en2zh, ja2zh
        self.translate_type = "auto2zh"
        if self.language == "english":
            self.translate_type = "auto2en"
        elif self.language == "japanese":
            self.translate_type = "auto2ja"

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        payload = {
            "source": text,
            "trans_type": self.translate_type,
            "request_id": "demo",
            "detect": True,
        }
        response = requests.request(
            "POST", self.api_url, data=json.dumps(payload), headers=self.headers
        )
        t_text = json.loads(response.text)["target"]
        print(t_text)
        return t_text
