from openai import OpenAI
from .chatgptapi_translator import ChatGPTAPI

# orcarouter/auto is OrcaRouter's smart routing endpoint: it picks the best
# model for each request instead of pinning one. Specific model IDs can be
# used through `--model openai --api_base https://api.orcarouter.ai/v1`.
ORCAROUTER_MODEL_LIST = [
    "orcarouter/auto",
]


class OrcaRouterTranslator(ChatGPTAPI):
    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(key, language)
        self.model_list = ORCAROUTER_MODEL_LIST
        self.api_url = str(api_base) if api_base else "https://api.orcarouter.ai/v1"
        self.api_base = self.api_url
        self.openai_client = OpenAI(api_key=key, base_url=self.api_url)

    def rotate_model(self):
        self.model = self.model_list[0]
