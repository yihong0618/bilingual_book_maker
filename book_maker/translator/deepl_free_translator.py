import time
import requests

from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE
from .base_translator import Base
from rich import print


class DeepLFree(Base):
    """
    DeepL Free translator using official DeepL Free API
    """

    def __init__(self, key, language, **kwargs) -> None:
        # Validate API key
        if not key or key == "no-key-required":
            raise Exception("DeepL Free requires an API key. Get one from https://www.deepl.com/pro-api")

        super().__init__(key, language)
        self.api_url = "https://api-free.deepl.com/v2/translate"

        # Map language codes to DeepL format
        if language == "zh-cn":
            language = "ZH"
        elif language in LANGUAGES:
            language = language.upper()
        elif language in TO_LANGUAGE_CODE:
            language = TO_LANGUAGE_CODE[language].upper()
        else:
            language = language.upper()

        # Validate supported languages for DeepL
        supported_languages = [
            "BG", "CS", "DA", "DE", "EL", "EN", "ES", "ET", "FI", "FR",
            "HU", "ID", "IT", "JA", "KO", "LT", "LV", "NB", "NL", "PL",
            "PT", "RO", "RU", "SK", "SL", "SV", "TR", "UK", "ZH"
        ]

        if language not in supported_languages:
            raise Exception(f"DeepL does not support language: {language}")

        self.language = language

        # Test API key validity immediately
        self._test_api_key()

    def rotate_key(self):
        """Rotate to next API key in the cycle"""
        pass  # Single key usage - no rotation needed for now

    def _test_api_key(self):
        """Test if the API key is valid by making a simple request"""
        headers = {
            "Authorization": f"DeepL-Auth-Key {next(self.keys)}",
            "Content-Type": "application/json"
        }

        payload = {
            "text": ["Hello"],
            "target_lang": self.language
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=10
            )

            if response.status_code == 403:
                raise Exception(f"Invalid DeepL API key. Please check your API key. Response: {response.text}")
            elif response.status_code == 456:
                raise Exception(f"DeepL quota exceeded. Please check your usage limits. Response: {response.text}")
            elif response.status_code != 200:
                raise Exception(f"DeepL API error: {response.status_code} - {response.text}")

        except requests.exceptions.RequestException as e:
            raise Exception(f"Failed to connect to DeepL API: {str(e)}")

    def translate(self, text):
        """Translate text using DeepL Free API"""
        headers = {
            "Authorization": f"DeepL-Auth-Key {next(self.keys)}",
            "Content-Type": "application/json"
        }

        payload = {
            "text": [text],
            "target_lang": self.language
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=30
            )

            if response.status_code == 403:
                raise Exception("Invalid DeepL API key")
            elif response.status_code == 456:
                raise Exception("DeepL quota exceeded")
            elif response.status_code != 200:
                raise Exception(f"DeepL API error: {response.status_code} - {response.text}")

            result = response.json()
            translated_text = result.get("translations", [{}])[0].get("text", "")

            if not translated_text:
                raise Exception("DeepL returned empty translation")

            print(f"[bold blue]Original:[/bold blue] {text}")
            print(f"[bold green]Translated:[/bold green] {translated_text}")

            return translated_text

        except requests.exceptions.RequestException as e:
            print(f"DeepL API request failed: {e}")
            time.sleep(5)
            raise Exception(f"DeepL translation failed: {str(e)}")
        except Exception as e:
            print(f"DeepL translation error: {e}")
            raise
