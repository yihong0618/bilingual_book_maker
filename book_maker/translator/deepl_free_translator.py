from .deepl_translator import DeepL


class DeepLFree(DeepL):
    """
    DeepL Free translator using official DeepL Free API
    Inherits from DeepL translator and only changes the API endpoint
    """

    def __init__(self, key, language, **kwargs) -> None:
        # Validate API key
        if not key or key == "no-key-required":
            raise Exception("DeepL Free requires an API key. Get one from https://www.deepl.com/pro-api")

        # Handle zh-cn mapping before calling parent
        if language == "zh-cn":
            language = "zh"

        # Initialize parent class
        super().__init__(key, language, **kwargs)

        # Override only the API endpoint for free tier
        self.api_url = "https://api-free.deepl.com/v2/translate"
