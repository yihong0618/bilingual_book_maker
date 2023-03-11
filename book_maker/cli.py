import argparse
import os
from os import environ as env

from book_maker.loader import BOOK_LOADER_DICT
from book_maker.translator import MODEL_DICT
from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="path of the epub file to be translated",
    )
    parser.add_argument(
        "--openai_key",
        dest="openai_key",
        type=str,
        default="",
        help="OpenAI api key,if you have more than one key, please use comma"
        " to split them to go beyond the rate limits",
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        help="only the first 10 paragraphs will be translated, for testing",
    )
    parser.add_argument(
        "--test_num",
        dest="test_num",
        type=int,
        default=10,
        help="how many paragraphs will be translated for testing",
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default="chatgptapi",
        choices=["chatgptapi", "gpt3", "google"],  # support DeepL later
        metavar="MODEL",
        help="model to use, available: {%(choices)s}",
    )
    parser.add_argument(
        "--language",
        type=str,
        choices=sorted(LANGUAGES.keys())
        + sorted([k.title() for k in TO_LANGUAGE_CODE.keys()]),
        default="zh-hans",
        metavar="LANGUAGE",
        help="language to translate to, available: {%(choices)s}",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="if program stop unexpected you can use this to resume",
    )
    parser.add_argument(
        "-p",
        "--proxy",
        dest="proxy",
        type=str,
        default="",
        help="use proxy like http://127.0.0.1:7890",
    )
    # args to change api_base
    parser.add_argument(
        "--api_base",
        dest="api_base",
        type=str,
        help="specify base url other than the OpenAI's official API address",
    )
    parser.add_argument(
        "--translate-tags",
        dest="translate_tags",
        type=str,
        default="p",
        help="example --translate-tags p,blockquote",
    )
    parser.add_argument(
        "--allow_navigable_strings",
        dest="allow_navigable_strings",
        action="store_true",
        default=False,
        help="allow NavigableStrings to be translated",
    )
    parser.add_argument(
        "--accumulated_num",
        dest="accumulated_num",
        type=int,
        default=1,
        help="Wait for how many characters have been accumulated before starting the translation",
    )

    options = parser.parse_args()
    PROXY = options.proxy
    if PROXY != "":
        os.environ["http_proxy"] = PROXY
        os.environ["https_proxy"] = PROXY

    translate_model = MODEL_DICT.get(options.model)
    assert translate_model is not None, "unsupported model"
    if options.model in ["gpt3", "chatgptapi"]:
        OPENAI_API_KEY = options.openai_key or env.get("OPENAI_API_KEY")
        if not OPENAI_API_KEY:
            raise Exception(
                "OpenAI API key not provided, please google how to obtain it"
            )
    else:
        OPENAI_API_KEY = ""

    book_type = options.book_name.split(".")[-1]
    support_type_list = list(BOOK_LOADER_DICT.keys())
    if book_type not in support_type_list:
        raise Exception(
            f"now only support files of these formats: {','.join(support_type_list)}"
        )

    book_loader = BOOK_LOADER_DICT.get(book_type)
    assert book_loader is not None, "unsupported loader"
    language = options.language
    if options.language in LANGUAGES:
        # use the value for prompt
        language = LANGUAGES.get(language, language)

    # change api_base for issue #42
    model_api_base = options.api_base

    e = book_loader(
        options.book_name,
        translate_model,
        OPENAI_API_KEY,
        options.resume,
        language=language,
        model_api_base=model_api_base,
        is_test=options.test,
        test_num=options.test_num,
        translate_tags=options.translate_tags,
        allow_navigable_strings=options.allow_navigable_strings,
        accumulated_num=options.accumulated_num,
    )
    e.make_bilingual_book()


if __name__ == "__main__":
    main()
