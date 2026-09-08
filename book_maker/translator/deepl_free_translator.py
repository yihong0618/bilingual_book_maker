import time
import random

from ..vendor import deeplx
from .base_translator import Base, NO_PROMPT_SECTIONS
from .deepl_translator import deepl_supported_target


class DeepLFree(Base):
    """
    DeepL free translator
    """

    # Handed text and nothing else: --prompt has no slot here.
    PROMPT_SECTION_SLOTS = NO_PROMPT_SECTIONS

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)
        self.language = deepl_supported_target(language)
        self.time_random = [0.3, 0.5, 1, 1.3, 1.5, 2]

    def rotate_key(self):
        pass

    def translate(self, text):
        t_text = str(deeplx.translate(text, "EN", self.language))
        # spider rule
        time.sleep(random.choice(self.time_random))
        return t_text
