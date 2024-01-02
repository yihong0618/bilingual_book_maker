from .base_translator import Base
import re
import json
import requests
import time
from rich import print


class CustomAPI(Base):
    """
    Custom API translator
    """

    def __init__(self, custom_api, language, **kwargs) -> None:
        super().__init__(custom_api, language)
        self.language = language
        self.custom_api = custom_api

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        custom_api = self.custom_api
        data = {"text": text, "source_lang": "auto", "target_lang": self.language}
        post_data = json.dumps(data)
        r = requests.post(url=custom_api, data=post_data, timeout=10).text
        t_text = json.loads(r)["data"]
        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        time.sleep(5)
        return t_text
