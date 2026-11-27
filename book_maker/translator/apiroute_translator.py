from .chatgptapi_translator import ChatGPTAPI

APIROUTE_API_BASE = "https://global.api-route.com/v1"
APIROUTE_MODEL_LIST = [
    "claude-3-7-sonnet-20250219",
]


class ApiRouteTranslator(ChatGPTAPI):
    """API Route (https://www.api-route.com) gateway: an OpenAI-compatible

    endpoint routing requests across leading LLM providers.
    """

    DEFAULT_API_BASE = APIROUTE_API_BASE

    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(
            key,
            language,
            api_base=str(api_base) if api_base else APIROUTE_API_BASE,
            **kwargs,
        )
        self.model_list = APIROUTE_MODEL_LIST
        self._model_names = tuple(APIROUTE_MODEL_LIST)
        self._configured_model_names = tuple(APIROUTE_MODEL_LIST)
        self.model = APIROUTE_MODEL_LIST[0]

    def rotate_model(self):
        self.model = self.model_list[0]
