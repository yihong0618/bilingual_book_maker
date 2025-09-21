import time
import random
import re
import requests

from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE

from .base_translator import Base
from rich import print


class DeepLFree(Base):
    """
    DeepL free translator using official DeepL Free API
    Requires API key from https://www.deepl.com/pro-api
    """

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)

        # Validate API key
        if not key or key == "no-key-required":
            raise Exception("DeepL Free requires an API key. Get one from https://www.deepl.com/pro-api")

        # Set up API endpoint for free tier
        self.api_url = "https://api-free.deepl.com/v2/translate"
        self.headers = {
            "Authorization": f"DeepL-Auth-Key {key}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        # Handle common language variants manually first
        if language == "zh-cn":
            l = "zh"
        else:
            # Use same language mapping as regular DeepL translator
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
            raise Exception(f"DeepL does not support {l}")

        self.language = l
        self.time_random = [0.5, 0.8, 1.0, 1.2, 1.5]  # Reduced delays for API

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)

        # Prepare request data
        data = {
            "text": text,
            "target_lang": self.language,
            "source_lang": "auto"  # Auto-detect source language
        }

        try:
            # Make API request
            response = requests.post(
                self.api_url,
                headers=self.headers,
                data=data,
                timeout=30
            )

            # Check for errors
            if response.status_code == 403:
                raise Exception("DeepL API key is invalid or quota exceeded")
            elif response.status_code == 456:
                raise Exception("DeepL quota exceeded")
            elif response.status_code != 200:
                raise Exception(f"DeepL API error: {response.status_code} - {response.text}")

            # Parse response
            result = response.json()

            if "translations" not in result or not result["translations"]:
                raise Exception("No translation returned from DeepL")

            t_text = result["translations"][0]["text"]

            # Rate limiting
            time.sleep(random.choice(self.time_random))

            print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
            return t_text

        except requests.exceptions.RequestException as e:
            raise Exception(f"DeepL API request failed: {str(e)}")
        except Exception as e:
            # If it's already our custom exception, re-raise
            if "DeepL" in str(e):
                raise
            else:
                raise Exception(f"DeepL translation failed: {str(e)}")
