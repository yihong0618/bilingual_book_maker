import json
import time

import requests

from book_maker.utils import TO_LANGUAGE_CODE

from .base_translator import Base, NO_PROMPT_SECTIONS
from rich import print

# DeepL's own target codes, which are not the tags the shared table stamps
# books with: it has one `zh`, and its regional codes are cased its way. The
# table stays correct BCP-47; the spelling DeepL is asked for is made here.
# Anything not listed reaches the allowlist below as the table has it, and
# an unsupported language is refused there by name.
DEEPL_TARGETS = {
    "zh-hans": "zh",
    "zh-cn": "zh",
    "zh-sg": "zh",
    "en-us": "en-US",
    "en-gb": "en-GB",
    "pt-pt": "pt-PT",
    "pt-br": "pt-BR",
    "no": "nb",
}


def deepl_target(language):
    """The `target` DeepL is asked for, from a `--language` name or tag."""
    code = TO_LANGUAGE_CODE.get(language.lower(), language)
    return DEEPL_TARGETS.get(code.lower(), code)


# Every target DeepL serves. Both routes ask the same engine, so one list.
DEEPL_SUPPORTED = (
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
)


def deepl_supported_target(language):
    """`deepl_target`, refusing by name what DeepL does not translate into."""
    l = deepl_target(language)
    if l not in DEEPL_SUPPORTED:
        raise Exception(f"DeepL do not support {l}")
    return l


class DeepL(Base):
    """
    DeepL translator
    """

    # Handed text and nothing else: --prompt has no slot here.
    PROMPT_SECTION_SLOTS = NO_PROMPT_SECTIONS

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)
        self.api_url = "https://dpl-translator.p.rapidapi.com/translate"
        self.headers = {
            "content-type": "application/json",
            "X-RapidAPI-Key": "",
            "X-RapidAPI-Host": "dpl-translator.p.rapidapi.com",
        }
        self.language = deepl_supported_target(language)

    def rotate_key(self):
        self.headers["X-RapidAPI-Key"] = f"{next(self.keys)}"

    def translate(self, text):
        self.rotate_key()
        payload = {"text": text, "source": "EN", "target": self.language}
        try:
            response = requests.request(
                "POST",
                self.api_url,
                data=json.dumps(payload),
                headers=self.headers,
            )
        except Exception as e:
            print(e)
            time.sleep(30)
            response = requests.request(
                "POST",
                self.api_url,
                data=json.dumps(payload),
                headers=self.headers,
            )
        t_text = response.json().get("text", "")
        return t_text
