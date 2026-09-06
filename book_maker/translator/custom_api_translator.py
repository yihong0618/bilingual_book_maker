from .base_translator import Base, NO_PROMPT_SECTIONS
import json
import requests
import time


class CustomAPI(Base):
    """
    Custom API translator
    """

    # Handed text and nothing else: --prompt has no slot here.
    PROMPT_SECTION_SLOTS = NO_PROMPT_SECTIONS

    def __init__(
        self, key, language, api_base=None, source_lang="auto", **kwargs
    ) -> None:
        super().__init__(key, language)
        # The endpoint is the whole configuration here, so it arrives as
        # --api_base like every other route. `key` used to carry the URL
        # (via the removed --custom_api) and is still accepted as a fallback.
        self.custom_api = api_base or key
        if not self.custom_api:
            raise ValueError(
                "the customapi format needs the endpoint URL: "
                "--api_format customapi --api_base https://your.host/translate"
            )
        self.language = language
        self.source_lang = source_lang

    def rotate_key(self):
        pass

    def translate(self, text):
        data = {
            "text": text,
            "source_lang": self.source_lang,
            "target_lang": self.language,
        }
        post_data = json.dumps(data)
        r = requests.post(url=self.custom_api, data=post_data, timeout=10).text
        t_text = json.loads(r)["data"]
        time.sleep(5)
        return t_text
