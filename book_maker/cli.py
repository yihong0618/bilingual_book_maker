import argparse
import json
import os
from os import environ as env

from book_maker.loader import BOOK_LOADER_DICT
from book_maker.translator import MODEL_DICT
from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE


def parse_prompt_arg(prompt_arg):
    prompt = None
    if prompt_arg is None:
        return prompt

    if not any(prompt_arg.endswith(ext) for ext in [".json", ".txt"]):
        try:
            # user can define prompt by passing a json string
            # eg: --prompt '{"system": "You are a professional translator who translates computer technology books", "user": "Translate \`{text}\` to {language}"}'
            prompt = json.loads(prompt_arg)
        except json.JSONDecodeError:
            # if not a json string, treat it as a template string
            prompt = {"user": prompt_arg}

    else:
        if os.path.exists(prompt_arg):
            if prompt_arg.endswith(".txt"):
                # if it's a txt file, treat it as a template string
                with open(prompt_arg, "r") as f:
                    prompt = {"user": f.read()}
            elif prompt_arg.endswith(".json"):
                # if it's a json file, treat it as a json object
                # eg: --prompt prompt_template_sample.json
                with open(prompt_arg, "r") as f:
                    prompt = json.load(f)
        else:
            raise FileNotFoundError(f"{prompt_arg} not found")

    if prompt is None or not (
        all(c in prompt["user"] for c in ["{text}", "{language}"])
    ):
        raise ValueError("prompt must contain `{text}` and `{language}`")

    if "user" not in prompt:
        raise ValueError("prompt must contain the key of `user`")

    if (prompt.keys() - {"user", "system"}) != set():
        raise ValueError("prompt can only contain the keys of `user` and `system`")

    print("prompt config:", prompt)
    return prompt


def main():
    translate_model_list = list(MODEL_DICT.keys())
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="path of the epub file to be translated",
    )
    parser.add_argument(
        "--book_from",
        dest="book_from",
        type=str,
        choices=["kobo"],  # support kindle later
        metavar="E-READER",
        help="e-reader type, available: {%(choices)s}",
    )
    parser.add_argument(
        "--device_path",
        dest="device_path",
        type=str,
        help="Path of e-reader device",
    )
    ########## KEYS ##########
    parser.add_argument(
        "--openai_key",
        dest="openai_key",
        type=str,
        default="",
        help="OpenAI api key,if you have more than one key, please use comma"
        " to split them to go beyond the rate limits",
    )
    parser.add_argument(
        "--caiyun_key",
        dest="caiyun_key",
        type=str,
        help="you can apply caiyun key from here (https://dashboard.caiyunapp.com/user/sign_in/)",
    )
    parser.add_argument(
        "--deepl_key",
        dest="deepl_key",
        type=str,
        help="you can apply deepl key from here (https://rapidapi.com/splintPRO/api/deepl-translator",
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
        choices=translate_model_list,  # support DeepL later
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
        "--prompt",
        dest="prompt_arg",
        type=str,
        metavar="PROMPT_ARG",
        help="used for customizing the prompt. It can be the prompt template string, or a path to the template file. The valid placeholders are `{text}` and `{language}`.",
    )
    parser.add_argument(
        "--accumulated_num",
        dest="accumulated_num",
        type=int,
        default=1,
        help="""Wait for how many tokens have been accumulated before starting the translation.
gpt3.5 limits the total_token to 4090.
For example, if you use --accumulated_num 1600, maybe openai will output 2200 tokens
and maybe 200 tokens for other messages in the system messages user messages, 1600+2200+200=4000,
So you are close to reaching the limit. You have to choose your own value, there is no way to know if the limit is reached before sending
""",
    )
    parser.add_argument(
        "--batch_size",
        dest="batch_size",
        type=int,
        default=10,
        help="how many lines will be translated by aggregated translation(This options currently only applies to txt files)",
    )

    options = parser.parse_args()
    PROXY = options.proxy
    if PROXY != "":
        os.environ["http_proxy"] = PROXY
        os.environ["https_proxy"] = PROXY

    translate_model = MODEL_DICT.get(options.model)
    assert translate_model is not None, "unsupported model"
    if options.model in ["gpt3", "chatgptapi"]:
        OPENAI_API_KEY = (
            options.openai_key
            or env.get(
                "OPENAI_API_KEY"
            )  # XXX: for backward compatability, deprecate soon
            or env.get(
                "BBM_OPENAI_API_KEY"
            )  # suggest adding `BBM_` prefix for all the bilingual_book_maker ENVs.
        )
        if not OPENAI_API_KEY:
            raise Exception(
                "OpenAI API key not provided, please google how to obtain it"
            )
        API_KEY = OPENAI_API_KEY
    elif options.model == "caiyun":
        API_KEY = options.caiyun_key or env.get("BBM_CAIYUN_API_KEY")
        if not API_KEY:
            raise Exception("Please provid caiyun key")
    elif options.model == "deepl":
        API_KEY = options.deepl_key or env.get("BBM_DEEPL_API_KEY")
        if not API_KEY:
            raise Exception("Please provid deepl key")
    else:
        API_KEY = ""

    if options.book_from == "kobo":
        import book_maker.obok as obok

        device_path = options.device_path
        if device_path is None:
            raise Exception(
                "Device path is not given, please specify the path by --device_path <DEVICE_PATH>"
            )
        options.book_name = obok.cli_main(device_path)

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
        API_KEY,
        options.resume,
        language=language,
        model_api_base=model_api_base,
        is_test=options.test,
        test_num=options.test_num,
        translate_tags=options.translate_tags,
        allow_navigable_strings=options.allow_navigable_strings,
        accumulated_num=options.accumulated_num,
        prompt_config=parse_prompt_arg(options.prompt_arg),
        batch_size=options.batch_size,
    )
    e.make_bilingual_book()


if __name__ == "__main__":
    main()
