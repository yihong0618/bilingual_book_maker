import os
import time

from book_maker.utils import TO_LANGUAGE_CODE
from .base_translator import Base

# Cloud Translation v3 expects BCP-47 codes and rejects some of the
# internal codes used by this repo's language table.
V3_LANGUAGE_CODE_FIX = {
    "zh-hans": "zh-CN",
    "zh": "zh-CN",
    "zh-hant": "zh-TW",
    "zh-yue": "yue",
}

ADC_HELP = """Google Cloud Application Default Credentials (ADC) not found.
Set up one of the following, then retry:
  1. Service account (recommended):
       export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
     The account needs the role `roles/cloudtranslate.user`.
  2. Personal account (quick start):
       gcloud auth application-default login
       gcloud config set project <your-project-id>
Also make sure the Cloud Translation API is enabled for your project:
  https://console.cloud.google.com/apis/library/translate.googleapis.com"""

PROJECT_HELP = (
    "Google Cloud project id not found. Provide it via --google_project_id, "
    "the BBM_GOOGLE_PROJECT_ID environment variable, "
    "or `gcloud config set project <your-project-id>`."
)


class GoogleV3(Base):
    """
    Official Google Cloud Translation API v3, with optional glossary
    (terminology) support. Authenticates via Application Default Credentials.
    """

    def __init__(self, key, language, source_lang="auto", **kwargs) -> None:
        super().__init__(key or "adc", language)
        code = TO_LANGUAGE_CODE.get(language.lower(), language)
        self.target_lang = V3_LANGUAGE_CODE_FIX.get(code.lower(), code)
        source_lang = source_lang or "auto"
        self.source_lang = V3_LANGUAGE_CODE_FIX.get(
            source_lang.lower(), source_lang
        )
        self.project_id = None
        self.location = "us-central1"
        self.glossary_id = None
        self.client = None
        self.parent = None
        self.glossary_config = None

    def rotate_key(self):
        pass

    def set_config(self, project_id=None, location=None, glossary_id=None):
        self.project_id = project_id
        if location:
            self.location = location
        self.glossary_id = glossary_id
        if glossary_id and self.source_lang == "auto":
            raise Exception(
                "Glossary translation requires an explicit source language, "
                "e.g. --source_lang en"
            )

    def _ensure_client(self):
        if self.client is not None:
            return
        try:
            from google.cloud import translate_v3
        except ImportError as e:
            raise Exception(
                "google-cloud-translate is required for `--model googlev3`. "
                "Install it with: pip install google-cloud-translate"
            ) from e
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError

        try:
            credentials, adc_project = google.auth.default()
        except DefaultCredentialsError as e:
            raise Exception(ADC_HELP) from e
        if not self.project_id:
            self.project_id = adc_project
        if not self.project_id:
            raise Exception(PROJECT_HELP)
        # REST by default: gRPC bypasses SOCKS/HTTP proxies and often
        # fails behind them, while REST honors standard proxy env vars.
        transport = os.environ.get("BBM_GOOGLE_TRANSPORT", "rest")
        self.client = translate_v3.TranslationServiceClient(
            credentials=credentials, transport=transport
        )
        self.parent = f"projects/{self.project_id}/locations/{self.location}"
        if self.glossary_id:
            glossary_path = self.client.glossary_path(
                self.project_id, self.location, self.glossary_id
            )
            self.glossary_config = translate_v3.TranslateTextGlossaryConfig(
                glossary=glossary_path
            )

    def translate(self, text):
        self._ensure_client()
        request = {
            "parent": self.parent,
            "contents": [text],
            "mime_type": "text/plain",
            "target_language_code": self.target_lang,
        }
        if self.source_lang != "auto":
            request["source_language_code"] = self.source_lang
        if self.glossary_config is not None:
            request["glossary_config"] = self.glossary_config

        last_error = None
        for attempt in range(3):
            try:
                response = self.client.translate_text(request=request)
                if (
                    self.glossary_config is not None
                    and response.glossary_translations
                ):
                    return response.glossary_translations[0].translated_text
                return response.translations[0].translated_text
            except Exception as e:
                last_error = e
                time.sleep(2**attempt)
        raise Exception(
            f"Google Cloud Translation failed after retries: {last_error}"
        ) from last_error
