import json
import time

import requests

from book_maker.utils import TO_LANGUAGE_CODE, LANGUAGES
from .base_translator import Base


class DeepL(Base):
    """
    caiyun translator
    """

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language)
        self.api_url = "https://deepl-translator.p.rapidapi.com/translate"
        self.headers = {
            "content-type": "application/json",
            "X-RapidAPI-Key": "",
            "X-RapidAPI-Host": "deepl-translator.p.rapidapi.com",
        }
        l = None
        if language in LANGUAGES:
            l = language
        else:
            l = TO_LANGUAGE_CODE.get(language)
        if l not in [
            "bg",
            "zh",
            "cs",
            "da",
            "nl",
            "en-US",
            "en-GB",
            "et",
            "fi",
            "fr",
            "de",
            "el",
            "hu",
            "id",
            "it",
            "ja",
            "lv",
            "lt",
            "pl",
            "pt-PT",
            "pt-BR",
            "ro",
            "ru",
            "sk",
            "sl",
            "es",
            "sv",
            "tr",
            "uk",
            "ko",
            "nb",
        ]:
            raise Exception(f"DeepL do not support {l}")
        self.language = l

    def rotate_key(self):
        self.headers["X-RapidAPI-Key"] = f"{next(self.keys)}"

    def translate(self, text):
        self.rotate_key()
        print(text)
        payload = {"text": text, "source": "EN", "target": self.language}
        try:
            response = requests.request(
                "POST", self.api_url, data=json.dumps(payload), headers=self.headers
            )
        except Exception as e:
            print(str(e))
            time.sleep(30)
            response = requests.request(
                "POST", self.api_url, data=json.dumps(payload), headers=self.headers
            )
        t_text = response.json().get("text", "")
        print(t_text)
        return t_text
