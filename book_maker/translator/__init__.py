from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.google_translator import Google
from book_maker.translator.gpt3_translator import GPT3
from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.deepl_translator import DeepL

MODEL_DICT = {
    "chatgptapi": ChatGPTAPI,
    "gpt3": GPT3,
    "google": Google,
    "caiyun": Caiyun,
    "deepl": DeepL,
    # add more here
}
