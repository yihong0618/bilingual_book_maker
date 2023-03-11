This forked added Google Translate support, only supported translate to `zh-CN`.
Usage: make sure to add `--model google` in the command.


**[中文](./README-CN.md) | English**

# bilingual_book_maker
The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt files and books. This tool is exclusively designed for translating epub books that have entered the public domain and is not intended for copyrighted works. Before using this tool, please review the project's **[disclaimer](./disclaimer.md)**.

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)


## Preparation

1. ChatGPT or OpenAI token [^token]
2. epub/txt books
3. Environment with internet access or proxy
4. Python 3.8+

## Use

1. `pip install -r requirements.txt`
2. Use `--openai_key` option to specify OpenAI API key. If you have multiple keys, separate them by commas (xxx,xxx,xxx) to reduce errors caused by API call limits.  
   Or, just set environment variable `OPENAI_API_KEY` to ignore this option.
3. A sample book, `test_books/animal_farm.epub`, is provided for testing purposes.
4. The default underlying model is [GPT-3.5-turbo](https://openai.com/blog/introducing-chatgpt-and-whisper-apis), which is used by ChatGPT currently. Use `--model gpt3` to change the underlying model to `GPT3`
5. Use `--test` option to preview the result if you haven't paid for the service. Note that there is a limit and it may take some time.
6. Set the target language like `--language "Simplified Chinese"`. Default target language is `"Simplified Chinese"`.  
   Read available languages by helper message: `python make_book.py --help`
7. Use `--proxy` option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890`.
8. Use `--resume` option to manually resume the process after an interruption.
9. epub is made of html files. By default, we only translate contents in `<p>`.
   Use `--translate-tags` to specify tags need for translation. Use comma to seperate multiple tags. For example:
   `--translate-tags h1,h2,h3,p,div`
10. Use `--book_from` option to specify e-reader type (Now only `kobo` is available), and use `--device_path` to specify the mounting point.
11. If you want to change api_base like using Cloudflare Workers, use `--api_base <URL>` to support it.  
   **Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**
12. Once the translation is complete, a bilingual book named `${book_name}_bilingual.epub` would be generated.
13. If there are any errors or you wish to interrupt the translation by pressing `CTRL+C`. A book named `${book_name}_bilingual_temp.epub` would be generated. You can simply rename it to any desired name.
14. If you want to translate strings in an e-book that aren't labeled with any tags, you can use the `--allow_navigable_strings` parameter. This will add the strings to the translation queue. **Note that it's best to look for e-books that are more standardized if possible.**

### Eamples

```shell
# Test quickly
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key}  --test --language zh-hans

# Or translate the whole book
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --language zh-hans

# Set env OPENAI_API_KEY to ignore option --openai_key
export OPENAI_API_KEY=${your_api_key}

# Use the GPT-3 model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt3 --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# Translate books download from Rakuten Kobo on kobo e-reader
python3 make_book.py --book_from kobo --device_path /tmp/kobo

# translate txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --language zh-hans
```

More understandable example
```shell
python3 make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'
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
3. The DeepL model will be updated later.

# Thanks

- @[yetone](https://github.com/yetone)

# Contribution

- Any issues or PRs are welcome.
- TODOs in the issue can also be selected.
- Please run `black make_book.py`[^black] before submitting the code.

## Appreciation

Thank you, that's enough.

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: https://platform.openai.com/account/api-keys
[^black]: https://github.com/psf/black
