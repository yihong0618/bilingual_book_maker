**[中文](./README-CN.md) | English**
[![litellm](https://img.shields.io/badge/%20%F0%9F%9A%85%20liteLLM-OpenAI%7CAzure%7CAnthropic%7CPalm%7CCohere%7CReplicate%7CHugging%20Face-blue?color=green)](https://github.com/BerriAI/litellm)

# bilingual_book_maker
The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt/srt files and books. This tool is exclusively designed for translating epub books that have entered the public domain and is not intended for copyrighted works. Before using this tool, please review the project's **[disclaimer](./disclaimer.md)**.

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## Supported Models
gpt-4, gpt-3.5-turbo, claude-2, palm, llama-2, azure-openai, command-nightly, gemini
For using Non-OpenAI models, use class `liteLLM()` - liteLLM supports all models above.
Find more info here for using liteLLM: https://github.com/BerriAI/litellm/blob/main/setup.py

## Preparation

1. ChatGPT or OpenAI token [^token]
2. epub/txt books
3. Environment with internet access or proxy
4. Python 3.8+

## Use

- `pip install -r requirements.txt` or `pip install -U bbook_maker`(you can use)
- Use `--openai_key` option to specify OpenAI API key. If you have multiple keys, separate them by commas (xxx,xxx,xxx) to reduce errors caused by API call limits.
   Or, just set environment variable `BBM_OPENAI_API_KEY` instead.
- A sample book, `test_books/animal_farm.epub`, is provided for testing purposes.
- The default underlying model is [GPT-3.5-turbo](https://openai.com/blog/introducing-chatgpt-and-whisper-apis), which is used by ChatGPT currently. Use `--model gpt4` to change the underlying model to `GPT4`.
   If using `GPT4`, you can add `--use_context` to add a context paragraph to each passage sent to the model for translation (see below)
- support DeepL model [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) need pay to get the token use `--model deepl --deepl_key ${deepl_key}`
- support DeepL free model `--model deeplfree`
- support Google [Gemini](https://makersuite.google.com/app/apikey) model `--model gemini --gemini_key ${gemini_key}`
- Support [Claude](https://console.anthropic.com/docs) model, use `--model claude --claude_key ${claude_key}`
- Support [Tencent TranSmart](https://transmart.qq.com) model (Free), use `--model tencentransmart`
- Use `--test` option to preview the result if you haven't paid for the service. Note that there is a limit and it may take some time.
- Set the target language like `--language "Simplified Chinese"`. Default target language is `"Simplified Chinese"`.
   Read available languages by helper message: `python make_book.py --help`
- Use `--proxy` option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890`.
- Use `--resume` option to manually resume the process after an interruption.
- epub is made of html files. By default, we only translate contents in `<p>`.
   Use `--translate-tags` to specify tags need for translation. Use comma to separate multiple tags. For example:
   `--translate-tags h1,h2,h3,p,div`
- Use `--book_from` option to specify e-reader type (Now only `kobo` is available), and use `--device_path` to specify the mounting point.
- If you want to change api_base like using Cloudflare Workers, use `--api_base <URL>` to support it.
   **Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**
- Once the translation is complete, a bilingual book named `${book_name}_bilingual.epub` would be generated.
- If there are any errors or you wish to interrupt the translation by pressing `CTRL+C`. A book named `${book_name}_bilingual_temp.epub` would be generated. You can simply rename it to any desired name.
- If you want to translate strings in an e-book that aren't labeled with any tags, you can use the `--allow_navigable_strings` parameter. This will add the strings to the translation queue. **Note that it's best to look for e-books that are more standardized if possible.**
- To tweak the prompt, use the `--prompt` parameter. Valid placeholders for the `user` role template include `{text}` and `{language}`. It supports a few ways to configure the prompt:
   If you don't need to set the `system` role content, you can simply set it up like this: `--prompt "Translate {text} to {language}."` or `--prompt prompt_template_sample.txt` (example of a text file can be found at [./prompt_template_sample.txt](./prompt_template_sample.txt)).
   If you need to set the `system` role content, you can use the following format: `--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'` or `--prompt prompt_template_sample.json` (example of a JSON file can be found at [./prompt_template_sample.json](./prompt_template_sample.json)).
   You can also set the `user` and `system` role prompt by setting environment variables: `BBM_CHATGPTAPI_USER_MSG_TEMPLATE` and `BBM_CHATGPTAPI_SYS_MSG`.
- Use the `--batch_size` parameter to specify the number of lines for batch translation (default is 10, currently only effective for txt files).
- `--accumulated_num` Wait for how many tokens have been accumulated before starting the translation. gpt3.5 limits the total_token to 4090. For example, if you use --accumulated_num 1600, maybe openai will
output 2200 tokens and maybe 200 tokens for other messages in the system messages user messages, 1600+2200+200=4000, So you are close to reaching the limit. You have to choose your own
value, there is no way to know if the limit is reached before sending
- `--use_context` prompts the GPT4 model to create a one-paragraph summary. If it's the beginning of the translation, it will summarize the entire passage sent (the size depending on `--accumulated_num`), but if it's any proceeding passage, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work, which improves consistency of flow and tone of each translation.
- `--translation_style` example: `--translation_style "color: #808080; font-style: italic;"`
- `--retranslate` `--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"(optional)`<br>
Retranslate from start_str to end_str's tag:
`python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'`<br>
Retranslate start_str's tag:
`python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which'`
### Examples

**Note if use `pip install bbook_maker` all commands can change to `bbook_maker args`**

```shell
# Test quickly
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key}  --test --language zh-hans

# Test quickly for src
python3 make_book.py --book_name test_books/Lex_Fridman_episode_322.srt --openai_key ${openai_key}  --test

# Or translate the whole book
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --language zh-hans

# Or translate the whole book using Gemini
python3 make_book.py --book_name test_books/animal_farm.epub --gemini_key ${gemini_key} --model gemini

# Set env OPENAI_API_KEY to ignore option --openai_key
export OPENAI_API_KEY=${your_api_key}

# Use the GPT-4 model with context to Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt4 --use_context --language ja

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model deepl --deepl_key ${deepl_key} --language ja


# Use the Claude model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model claude --claude_key ${claude_key} --language ja

# Use the CustomAPI model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model customapi --custom_api ${custom_api} --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# Tweaking the prompt
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.json
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"

# Translate books download from Rakuten Kobo on kobo e-reader
python3 make_book.py --book_from kobo --device_path /tmp/kobo

# translate txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --language zh-hans
# aggregated translation txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20

# Using Caiyun model to translate
# (the api currently only support: simplified chinese <-> english, simplified chinese <-> japanese)
# the official Caiyun has provided a test token (3975l6lr5pcbvidl6jl2)
# you can apply your own token by following this tutorial(https://bobtranslate.com/service/translate/caiyun.html)
python3 make_book.py --model caiyun --caiyun_key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub


# Set env BBM_CAIYUN_API_KEY to ignore option --openai_key
export BBM_CAIYUN_API_KEY=${your_api_key}

```

More understandable example
```shell
python3 make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'
```

Microsoft Azure Endpoints
```shell
python3 make_book.py --book_name 'animal_farm.epub' --openai_key XXXXX --api_base 'https://example-endpoint.openai.azure.com' --deployment_id 'deployment-name'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --openai_key XXXXX --api_base 'https://example-endpoint.openai.azure.com' --deployment_id 'deployment-name'
```

## Docker

You can use [Docker](https://www.docker.com/) if you don't want to deal with setting up the environment.

```shell
# Build image
docker build --tag bilingual_book_maker .

# Run container
# "$folder_path" represents the folder where your book file locates. Also, it is where the processed file will be stored.

# Windows PowerShell
$folder_path=your_folder_path # $folder_path="C:\Users\user\mybook\"
$book_name=your_book_name # $book_name="animal_farm.epub"
$openai_key=your_api_key # $openai_key="sk-xxx"
$language=your_language # see utils.py

docker run --rm --name bilingual_book_maker --mount type=bind,source=$folder_path,target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/$book_name" --openai_key $openai_key --language $language

# Linux
export folder_path=${your_folder_path}
export book_name=${your_book_name}
export openai_key=${your_api_key}
export language=${your_language}

docker run --rm --name bilingual_book_maker --mount type=bind,source=${folder_path},target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/${book_name}" --openai_key ${openai_key} --language "${language}"
```

For example:

```shell
# Linux
docker run --rm --name bilingual_book_maker --mount type=bind,source=/home/user/my_books,target='/app/test_books' bilingual_book_maker --book_name /app/test_books/animal_farm.epub --openai_key sk-XXX --test --test_num 1 --language zh-hant
```

## Notes

1. API token from free trial has limit. If you want to speed up the process, consider paying for the service or use multiple OpenAI tokens
2. PR is welcome

# Thanks

- @[yetone](https://github.com/yetone)

# Contribution

- Any issues or PRs are welcome.
- TODOs in the issue can also be selected.
- Please run `black make_book.py`[^black] before submitting the code.

# Others better

- 书译 iOS -> [AI 全书翻译工具](https://apps.apple.com/cn/app/%E4%B9%A6%E8%AF%91-ai-%E5%85%A8%E4%B9%A6%E7%BF%BB%E8%AF%91%E5%B7%A5%E5%85%B7/id6447665417)

## Appreciation

Thank you, that's enough.

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: https://platform.openai.com/account/api-keys
[^black]: https://github.com/psf/black
