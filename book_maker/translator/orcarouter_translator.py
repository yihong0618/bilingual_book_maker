from .chatgptapi_translator import ChatGPTAPI

# orcarouter/auto is OrcaRouter's smart routing endpoint: it picks the best
# model for each request instead of pinning one. Specific model IDs can be
# used through `--api_format openai --api_base https://api.orcarouter.ai/v1`.
ORCAROUTER_MODEL_LIST = [
    "orcarouter/auto",
]


class OrcaRouterTranslator(ChatGPTAPI):
    # An OpenAI-shaped gateway: everything ChatGPTAPI can do — context,
    # session history, structured output, batching — it can do here, so every
    # argument is forwarded. Only the default address and model differ.
    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(
            key,
            language,
            api_base=str(api_base) if api_base else "https://api.orcarouter.ai/v1",
            **kwargs,
        )
        self.model_list = ORCAROUTER_MODEL_LIST
        self.api_url = self.api_base
        self._model_names = tuple(ORCAROUTER_MODEL_LIST)
        self._configured_model_names = tuple(ORCAROUTER_MODEL_LIST)
        self.model = ORCAROUTER_MODEL_LIST[0]

    def rotate_model(self):
        self.model = self.model_list[0]
