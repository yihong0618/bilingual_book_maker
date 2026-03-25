from openai import OpenAI
from .chatgptapi_translator import ChatGPTAPI

XAI_MODEL_LIST = [
    "grok-beta",
]


class XAIClient(ChatGPTAPI):
    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(key, language)
        self.model_list = XAI_MODEL_LIST
        self.api_url = str(api_base) if api_base else "https://api.x.ai/v1"
        self.openai_client = OpenAI(api_key=key, base_url=self.api_url, timeout=60.0, max_retries=3)

    def rotate_model(self):
        self.model = self.model_list[0]
