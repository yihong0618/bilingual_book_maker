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

    elif os.path.exists(prompt_arg):
        if prompt_arg.endswith(".txt"):
            # if it's a txt file, treat it as a template string
            with open(prompt_arg, encoding="utf-8") as f:
                prompt = {"user": f.read()}
        elif prompt_arg.endswith(".json"):
            # if it's a json file, treat it as a json object
            # eg: --prompt prompt_template_sample.json
            with open(prompt_arg, encoding="utf-8") as f:
                prompt = json.load(f)
    else:
        raise FileNotFoundError(f"{prompt_arg} not found")

    if prompt is None or any(c not in prompt["user"] for c in ["{text}", "{language}"]):
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
        help="you can apply deepl key from here (https://rapidapi.com/splintPRO/api/dpl-translator",
    )
    parser.add_argument(
        "--claude_key",
        dest="claude_key",
        type=str,
        help="you can find claude key from here (https://console.anthropic.com/account/keys)",
    )

    parser.add_argument(
        "--custom_api",
        dest="custom_api",
        type=str,
        help="you should build your own translation api",
    )

    # for Google Gemini
    parser.add_argument(
        "--gemini_key",
        dest="gemini_key",
        type=str,
        help="You can get Gemini Key from  https://makersuite.google.com/app/apikey",
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
        + sorted([k.title() for k in TO_LANGUAGE_CODE]),
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
    parser.add_argument(
        "--deployment_id",
        dest="deployment_id",
        type=str,
        help="the deployment name you chose when you deployed the model",
    )
    # args to change api_base
    parser.add_argument(
        "--api_base",
        metavar="API_BASE_URL",
        dest="api_base",
        type=str,
        help="specify base url other than the OpenAI's official API address",
    )
    parser.add_argument(
        "--exclude_filelist",
        dest="exclude_filelist",
        type=str,
        default="",
        help="if you have more than one file to exclude, please use comma to split them, example: --exclude_filelist 'nav.xhtml,cover.xhtml'",
    )
    parser.add_argument(
        "--only_filelist",
        dest="only_filelist",
        type=str,
        default="",
        help="if you only have a few files with translations, please use comma to split them, example: --only_filelist 'nav.xhtml,cover.xhtml'",
    )
    parser.add_argument(
        "--translate-tags",
        dest="translate_tags",
        type=str,
        default="p",
        help="example --translate-tags p,blockquote",
    )
    parser.add_argument(
        "--exclude_translate-tags",
        dest="exclude_translate_tags",
        type=str,
        default="sup",
        help="example --exclude_translate-tags table,sup",
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
        "--translation_style",
        dest="translation_style",
        type=str,
        help="""ex: --translation_style "color: #808080; font-style: italic;" """,
    )
    parser.add_argument(
        "--batch_size",
        dest="batch_size",
        type=int,
        help="how many lines will be translated by aggregated translation(This options currently only applies to txt files)",
    )
    parser.add_argument(
        "--retranslate",
        dest="retranslate",
        nargs=4,
        type=str,
        help="""--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"(optional)
        Retranslate from start_str to end_str's tag:
        python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'
        Retranslate start_str's tag:
        python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which'
""",
    )
    parser.add_argument(
        "--single_translate",
        action="store_true",
        help="output translated book, no bilingual",
    )
    parser.add_argument(
        "--use_context",
        dest="context_flag",
        action="store_true",
        help="adds an additional paragraph for global, updating historical context of the story to the model's input, improving the narrative consistency for the AI model (this uses ~200 more tokens each time)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="temperature parameter for `chatgptapi`/`gpt4`/`claude`",
    )
    parser.add_argument(
        "--block_size",
        type=int,
        default=-1,
        help="merge multiple paragraphs into one block, may increase accuracy and speed up the process, but disturb the original format, must be used with `--single_translate`",
    )

    options = parser.parse_args()

    if not os.path.isfile(options.book_name):
        print(f"Error: {options.book_name} does not exist.")
        exit(1)

    PROXY = options.proxy
    if PROXY != "":
        os.environ["http_proxy"] = PROXY
        os.environ["https_proxy"] = PROXY

    translate_model = MODEL_DICT.get(options.model)
    assert translate_model is not None, "unsupported model"
    API_KEY = ""
    if options.model in ["chatgptapi", "gpt4"]:
        if OPENAI_API_KEY := (
            options.openai_key
            or env.get(
                "OPENAI_API_KEY",
            )  # XXX: for backward compatibility, deprecate soon
            or env.get(
                "BBM_OPENAI_API_KEY",
            )  # suggest adding `BBM_` prefix for all the bilingual_book_maker ENVs.
        ):
            API_KEY = OPENAI_API_KEY
            # patch
        else:
            raise Exception(
                "OpenAI API key not provided, please google how to obtain it",
            )
    elif options.model == "caiyun":
        API_KEY = options.caiyun_key or env.get("BBM_CAIYUN_API_KEY")
        if not API_KEY:
            raise Exception("Please provide caiyun key")
    elif options.model == "deepl":
        API_KEY = options.deepl_key or env.get("BBM_DEEPL_API_KEY")
        if not API_KEY:
            raise Exception("Please provide deepl key")
    elif options.model == "claude":
        API_KEY = options.claude_key or env.get("BBM_CLAUDE_API_KEY")
        if not API_KEY:
            raise Exception("Please provide claude key")
    elif options.model == "customapi":
        API_KEY = options.custom_api or env.get("BBM_CUSTOM_API")
        if not API_KEY:
            raise Exception("Please provide custom translate api")
    elif options.model == "gemini":
        API_KEY = options.gemini_key or env.get("BBM_GOOGLE_GEMINI_KEY")
    else:
        API_KEY = ""

    if options.book_from == "kobo":
        from book_maker import obok

        device_path = options.device_path
        if device_path is None:
            raise Exception(
                "Device path is not given, please specify the path by --device_path <DEVICE_PATH>",
            )
        options.book_name = obok.cli_main(device_path)

    book_type = options.book_name.split(".")[-1]
    support_type_list = list(BOOK_LOADER_DICT.keys())
    if book_type not in support_type_list:
        raise Exception(
            f"now only support files of these formats: {','.join(support_type_list)}",
        )

    if options.block_size > 0 and not options.single_translate:
        raise Exception(
            "block_size must be used with `--single_translate` because it disturbs the original format",
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
        prompt_config=parse_prompt_arg(options.prompt_arg),
        single_translate=options.single_translate,
        context_flag=options.context_flag,
        temperature=options.temperature,
    )
    # other options
    if options.allow_navigable_strings:
        e.allow_navigable_strings = True
    if options.translate_tags:
        e.translate_tags = options.translate_tags
    if options.exclude_translate_tags:
        e.exclude_translate_tags = options.exclude_translate_tags
    if options.exclude_filelist:
        e.exclude_filelist = options.exclude_filelist
    if options.only_filelist:
        e.only_filelist = options.only_filelist
    if options.accumulated_num > 1:
        e.accumulated_num = options.accumulated_num
    if options.translation_style:
        e.translation_style = options.translation_style
    if options.batch_size:
        e.batch_size = options.batch_size
    if options.retranslate:
        e.retranslate = options.retranslate
    if options.deployment_id:
        # only work for ChatGPT api for now
        # later maybe support others
        assert options.model in [
            "chatgptapi",
            "gpt4",
        ], "only support chatgptapi for deployment_id"
        if not options.api_base:
            raise ValueError("`api_base` must be provided when using `deployment_id`")
        e.translate_model.set_deployment_id(options.deployment_id)
    # TODO refactor, quick fix for gpt4 model
    if options.model == "chatgptapi":
        e.translate_model.set_gpt35_models()
    if options.model == "gpt4":
        e.translate_model.set_gpt4_models()
    if options.block_size > 0:
        e.block_size = options.block_size

    e.make_bilingual_book()


if __name__ == "__main__":
    main()
