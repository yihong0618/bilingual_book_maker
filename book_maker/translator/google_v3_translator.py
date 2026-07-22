import os
import time

from book_maker.utils import TO_LANGUAGE_CODE
from .base_translator import Base

# translate_text accepts up to 1024 contents / 30k codepoints per request;
# stay well under both so a single oversized paragraph cannot break a chunk.
BATCH_MAX_SEGMENTS = 128
BATCH_MAX_CODEPOINTS = 20000

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

    def _request(self, contents):
        """Translate a list of strings; returns translations in order."""
        self._ensure_client()
        request = {
            "parent": self.parent,
            "contents": contents,
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
                    translations = response.glossary_translations
                else:
                    translations = response.translations
                return [t.translated_text for t in translations]
            except Exception as e:
                last_error = e
                time.sleep(2**attempt)
        raise Exception(
            f"Google Cloud Translation failed after retries: {last_error}"
        ) from last_error

    def translate(self, text):
        return self._request([text])[0]

    def translate_list(self, text_list):
        """
        Batch translation: pack the block's paragraphs into as few
        contents[] requests as the API limits allow. One request replaces
        up to BATCH_MAX_SEGMENTS per-paragraph round trips, which is where
        the speedup comes from; each paragraph is still translated as its
        own segment, so quality and alignment match single requests.
        Pair with a large --block_size (e.g. 200) to feed big blocks.
        """
        results = [None] * len(text_list)
        chunk, chunk_size = [], 0
        chunks = []
        for i, text in enumerate(text_list):
            if not text.strip():
                results[i] = text
                continue
            if chunk and (
                len(chunk) >= BATCH_MAX_SEGMENTS
                or chunk_size + len(text) > BATCH_MAX_CODEPOINTS
            ):
                chunks.append(chunk)
                chunk, chunk_size = [], 0
            chunk.append((i, text))
            chunk_size += len(text)
        if chunk:
            chunks.append(chunk)

        for chunk in chunks:
            translated = self._request([text for _, text in chunk])
            for (i, _), t_text in zip(chunk, translated):
                results[i] = t_text
        return results
