from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.gpt3_translator import GPT3

MODEL_DICT = {
    "chatgptapi": ChatGPTAPI,
    "gpt3": GPT3,
    # add more here
}
