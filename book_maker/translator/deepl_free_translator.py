import time
import random
import re

from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE

from .base_translator import Base
from rich import print
from PyDeepLX import PyDeepLX


class DeepLFree(Base):
    """
    DeepL free translator
    """

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)
        l = None
        l = language if language in LANGUAGES else TO_LANGUAGE_CODE.get(language)
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
        self.time_random = [0.3, 0.5, 1, 1.3, 1.5, 2]

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        t_text = str(PyDeepLX.translate(text, "EN", self.language))
        # spider rule
        time.sleep(random.choice(self.time_random))
        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        return t_text
