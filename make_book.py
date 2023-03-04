import os
import argparse

from os import environ as env
from file_engine import BEPUB, BText
from translate_engine import GPT3, ChatGPT
from utils import LANGUAGES, TO_LANGUAGE_CODE

def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="your epub book file path",
    )
    parser.add_argument(
        "--openai_key",
        dest="openai_key",
        type=str,
        default="",
        help="openai api key,if you have more than one key,you can use comma"
        " to split them and you can break through the limitation",
    )
    parser.add_argument(
        "--no_limit",
        dest="no_limit",
        action="store_true",
        default=False,
        help="If you are a paying customer you can add it",
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        default=False,
        help="if test we only translat 10 contents you can easily check",
    )
    parser.add_argument(
        "--test_num",
        dest="test_num",
        type=int,
        default=10,
        help="test num for the test",
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default="chatgpt",
        choices=["chatgpt", "gpt3"],  # support DeepL later
        help="Which model to use",
    )
    parser.add_argument(
        "--language",
        type=str,
        choices=sorted(LANGUAGES.keys())
        + sorted([k.title() for k in TO_LANGUAGE_CODE.keys()]),
        default="zh-hans",
        help="language to translate to",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=False,
        help="if program accidentally stop you can use this to resume",
    )
    parser.add_argument(
        "-p",
        "--proxy",
        dest="proxy",
        type=str,
        default="",
        help="use proxy like http://127.0.0.1:7890",
    )
    options = parser.parse_args()
    return options

if __name__ == "__main__":
    options = get_parser()

    no_limit = options.no_limit
    is_test = options.test
    test_number = options.test_num
    book_name = options.book_name
    resume = options.resume
    proxy = options.proxy
    lang = options.language
    open_ai_api_key = options.openai_key or env.get("OPENAI_API_KEY")

    if proxy != "":
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy

    if not open_ai_api_key:
        raise Exception("Need openai API key, please google how to")

    if book_name.endswith(".epub"):
        FileEngine = BEPUB
    elif book_name.endswith(".txt"):
        FileEngine = BText
    else:
        raise Exception("Only support epub and txt file")

    if options.model == "gpt3":
        translate_engine_class = GPT3
    else:
        translate_engine_class = ChatGPT

    translate_engine = translate_engine_class(open_ai_api_key, lang, no_limit)
    book = FileEngine(translate_engine, book_name,
                      resume, is_test, test_number)
    book.make_bilingual_book()
