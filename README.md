**[中文](./README-CN.md) | English**

# bilingual_book_maker
The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub files and books. This tool is exclusively designed for translating epub books that have entered the public domain and is not intended for copyrighted works. Prior to usage, please review the project's **[disclaimer](./disclaimer.md)**.

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)


## Preparation

1. ChatGPT or OpenAI token
2. prepared epub books
3. Environment with internet access or proxy
4. python3.8+


## Use

1. pip install -r requirements.txt
2. OpenAI API key. If you have multiple keys, separate them by commas (xxx,xxx,xxx) to reduce errors caused by API call limits.
3. A sample book, test_books/animal_farm.epub, is provided for testing purposes.
4. The default underlying model is [GPT-3.5-turbo](https://openai.com/blog/introducing-chatgpt-and-whisper-apis) ，which is used by ChatGPT currently. Use `--model gpt3` to change the underlying model to `GPT3`
5. Use --test command to preview the result if you haven't paid for the service. Note that there is a limit and it may take some time.
6. Set the target language like `--language "Simplified Chinese"`.
   Support ` "Japanese" / "Traditional Chinese" / "German" / "French" / "Korean"`.
   Default target language is `"Simplified Chinese"`. Support language list please see the LANGUAGES at [utils.py](./utils.py).
7. Use the --proxy parameter to enable users in mainland China to use a proxy when testing locally. Enter a string such as http://127.0.0.1:7890.
8. Use the --resume command to manually resume the process after an interruption.
9. If you want to change api_base like using Cloudflare Workers Use --api_base ${url} to support it. **Note: the api url you input should be `https://xxxx/v1', and quotation marks are required. **
10. Once the translation is complete, a bilingual book named ${book_name}_bilingual.epub will be generated.
11. If there are any errors or you wish to interrupt the translation using CTRL+C and do not want to continue further, a book named ${book_name}_bilingual_temp.epub will be generated. You can simply rename it to the desired name.

e.g.
```shell
# Test quickly
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --no_limit --test --language "Simplified Chinese"
# or do it
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --language "Simplified Chinese"
# or use the GPT-3 model
export OPENAI_API_KEY=${your_api_key}
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt3 --no_limit --language "Simplified Chinese"
```
More understandable example
```shell
python3 make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'
# or
python make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'
```

## Docker
You can use [Docker](https://www.docker.com/) if you don't want to deal with setting up the environment.
```shell
# build image
docker build --tag bilingual_book_maker .

# run container
# "$folder_path" represents the folder where your book file is located. Also, it is where the processed file will be stored.

# Windows PowerShell
$folder_path=your_folder_path # $folder_path="C:\Users\user\mybook\"
$book_name=your_book_name # $book_name="animal_farm.epub"
$openai_key=your_api_key # $openai_key="sk-xxx"
$language=your_language # see utils.py

docker run --rm --name bilingual_book_maker --mount type=bind,source=$folder_path,target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/$book_name" --openai_key $openai_key --no_limit --language $language

# linux
export folder_path=${your_folder_path}
export book_name=${your_book_name}
export openai_key=${your_api_key}
export language=${your_language}

docker container run --rm --name bilingual_book_maker --mount type=bind,source=${folder_path},target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/${book_name}" --openai_key ${openai_key} --no_limit --language "${language}"
```
for example,
```shell
# linux
docker container run --rm --name bilingual_book_maker --mount type=bind,source=/home/user/my_books,target='/app/test_books' bilingual_book_maker --book_name /app/test_books/animal_farm.epub --openai_key sk-XXX --no_limit --test --test_num 1 --language zh-hant
```

## Notes

1. here is a limit. If you want to speed up the process, consider paying for the service or use multiple OpenAI tokens
2. PR welcome
3. The DeepL model will be updated later.


# Thanks

- @[yetone](https://github.com/yetone)

# Contribution

- Any issues or PRs are welcome.
- TODOs in the issue can also be selected.
- Please run black make_book.py before submitting the code.

## Appreciation

Thank you, that's enough.

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)
