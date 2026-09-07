import requests

from book_maker.utils import TO_LANGUAGE_CODE
from .base_translator import Base, NO_PROMPT_SECTIONS

# Google's endpoint spells a handful of targets its own way, and knows no
# regional variant of the big languages at all. The shared table in
# `book_maker.utils` stays correct BCP-47 — `zh-hans` is the tag a book is
# stamped with — and the translation this route asks for is spelled here, on
# the way into the request. Anything not listed travels as the table has it.
GOOGLE_TARGETS = {
    "zh": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-sg": "zh-CN",
    "zh-hant": "zh-TW",
    "zh-tw": "zh-TW",
    "zh-hk": "zh-TW",
    # Google has one of each of these, not the regional split.
    "en-gb": "en",
    "en-us": "en",
    "en-au": "en",
    "en-ca": "en",
    "en-in": "en",
    "pt-br": "pt",
    "pt-pt": "pt",
    "es-419": "es",
    "es-mx": "es",
    "es-es": "es",
    "es-ar": "es",
    "fr-ca": "fr",
    "de-at": "de",
    "de-ch": "de",
    "nl-be": "nl",
    "sr-latn": "sr",
    "sr-cyrl": "sr",
    # Google's Norwegian is the macro-language code.
    "nb": "no",
}


def google_target(language):
    """The `tl=` Google is asked for, from a `--language` name or tag."""
    code = TO_LANGUAGE_CODE.get(language.lower(), language)
    return GOOGLE_TARGETS.get(code.lower(), code)


class Google(Base):
    """
    google translate
    """

    # Handed text and nothing else: --prompt has no slot here.
    PROMPT_SECTION_SLOTS = NO_PROMPT_SECTIONS

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)

        # Convert language name to code if needed, otherwise use as-is
        language_code = google_target(language)

        self.api_url = f"https://translate.google.com/translate_a/single?client=it&dt=qca&dt=t&dt=rmt&dt=bd&dt=rms&dt=sos&dt=md&dt=gt&dt=ld&dt=ss&dt=ex&otf=2&dj=1&hl=en&ie=UTF-8&oe=UTF-8&sl=auto&tl={language_code}"
        self.headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "GoogleTranslate/6.29.59279 (iPhone; iOS 15.4; en; iPhone14,2)",
        }
        # TODO support more models here
        self.session = requests.session()
        self.language = language

    def rotate_key(self):
        pass

    def translate(self, text):
        """r = self.session.post(
            self.api_url,
            headers=self.headers,
            data=f"q={requests.utils.quote(text)}",
        )
        if not r.ok:
            return text
        t_text = "".join(
            [sentence.get("trans", "") for sentence in r.json()["sentences"]],
        )"""
        t_text = self._retry_translate(text)
        return t_text

    def _retry_translate(self, text, timeout=3):
        time = 0
        while time <= timeout:
            time += 1
            r = self.session.post(
                self.api_url,
                headers=self.headers,
                data=f"q={requests.utils.quote(text)}",
                timeout=3,
            )
            if r.ok:
                t_text = "".join(
                    [sentence.get("trans", "") for sentence in r.json()["sentences"]],
                )
                return t_text
        return text
