from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.deepl_translator import DeepL
from book_maker.translator.deepl_free_translator import DeepLFree
from book_maker.translator.google_translator import Google
from book_maker.translator.claude_translator import Claude
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.tencent_transmart_translator import TencentTranSmart
from book_maker.translator.custom_api_translator import CustomAPI

MODEL_DICT = {
    "chatgptapi": ChatGPTAPI,
    "google": Google,
    "caiyun": Caiyun,
    "deepl": DeepL,
    "deeplfree": DeepLFree,
    "gpt4": ChatGPTAPI,
    "claude": Claude,
    "gemini": Gemini,
    "tencentransmart": TencentTranSmart,
    "customapi": CustomAPI,
    # add more here
}
