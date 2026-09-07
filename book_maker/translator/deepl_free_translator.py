import time
import random

from .base_translator import Base, NO_PROMPT_SECTIONS
from .deepl_translator import deepl_target
from PyDeepLX import PyDeepLX


class DeepLFree(Base):
    """
    DeepL free translator
    """

    # Handed text and nothing else: --prompt has no slot here.
    PROMPT_SECTION_SLOTS = NO_PROMPT_SECTIONS

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)
        l = deepl_target(language)
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
        t_text = str(PyDeepLX.translate(text, "EN", self.language))
        # spider rule
        time.sleep(random.choice(self.time_random))
        return t_text
