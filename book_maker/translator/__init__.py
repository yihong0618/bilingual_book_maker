from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.deepl_translator import DeepL
from book_maker.translator.deepl_free_translator import DeepLFree
from book_maker.translator.google_translator import Google
from book_maker.translator.claude_translator import Claude
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.groq_translator import GroqClient
from book_maker.translator.tencent_transmart_translator import TencentTranSmart
from book_maker.translator.custom_api_translator import CustomAPI
from book_maker.translator.xai_translator import XAIClient
from book_maker.translator.qwen_translator import QwenTranslator

MODEL_DICT = {
    "openai": ChatGPTAPI,
    "chatgptapi": ChatGPTAPI,
    "gpt4": ChatGPTAPI,
    "gpt4omini": ChatGPTAPI,
    "gpt4o": ChatGPTAPI,
    "gpt5mini": ChatGPTAPI,
    "o1preview": ChatGPTAPI,
    "o1": ChatGPTAPI,
    "o1mini": ChatGPTAPI,
    "o3mini": ChatGPTAPI,
    "google": Google,
    "caiyun": Caiyun,
    "deepl": DeepL,
    "deeplfree": DeepLFree,
    "claude": Claude,
    "claude-sonnet-4-6": Claude,
    "claude-opus-4-6": Claude,
    "claude-opus-4-5-20251101": Claude,
    "claude-haiku-4-5-20251001": Claude,
    "claude-sonnet-4-5-20250929": Claude,
    "claude-opus-4-1-20250805": Claude,
    "claude-opus-4-20250514": Claude,
    "claude-sonnet-4-20250514": Claude,
    "gemini": Gemini,
    "geminipro": Gemini,
    "groq": GroqClient,
    "tencentransmart": TencentTranSmart,
    "customapi": CustomAPI,
    "xai": XAIClient,
    "qwen": QwenTranslator,
    "qwen-mt-turbo": QwenTranslator,
    "qwen-mt-plus": QwenTranslator,
    # add more here
}
