# Model and Languages
## Models
`-m, --model <Model>` <br>

Currently `bbook_maker` supports these models: `chatgptapi` , `gpt3` , `google` , `caiyun` , `deepl` , `deeplfree` , `gpt4` , `claude` , `customapi`.
Default model is `chatgptapi` . 

### OPENAI models

There are three models you can choose from.

* gpt3

    

        bbook_maker --book_name test_books/animal_farm.epub --model gpt3 --openai_key ${openai_key}

    

* chatgpiapi


    `chatgptapi` is [GPT-3.5-turbo](https://openai.com/blog/introducing-chatgpt-and-whisper-apis), which is used by ChatGPT currently.

        bbook_maker --book_name test_books/animal_farm.epub --model chatgptapi --openai_key ${openai_key}

* gpt4

    

        bbook_maker --book_name test_books/animal_farm.epub --model gpt4 --openai_key ${openai_key}

    If using `gpt4` , you can add `--use_context` to add a context paragraph to each passage sent to the model for translation.

  

            
        bbook_maker --book_name test_books/animal_farm.epub --model gpt4 --openai_key ${openai_key} --use_context

    The option `--use_context` prompts the GPT4 model to create a one-paragraph summary. 

    

    If it is the beginning of the translation, it will summarize the entire passage sent (the size depending on `--accumulated_num` ).

    

    If it has any proceeding passage, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work, which improves consistency of flow and tone of each translation.

**Note 1: Use `--openai_key` option to specify OpenAI API key. If you have multiple keys, separate them by commas (xxx, xxx, xxx) to reduce errors caused by API call limits.**

**Note 2: You can just set the environment variable `BBM_OPENAI_API_KEY` instead the openai_key. See [Environment setting](settings.md).**

### CAIYUN 

Using Caiyun model to translate. The api currently only support: 

        

1. Simplified Chinese <-> English
2. Simplified Chinese <-> Japanese

The official Caiyun has provided a test token (3975l6lr5pcbvidl6jl2). You can apply your own token by following this [tutorial].(https://bobtranslate.com/service/translate/caiyun.html)

            
    bbook_maker --model caiyun --caiyun_key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub

### DEEPL

There are two models you can choose from.

    

* deepl: [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator). <br>

    

    Need to pay to get the token. Use `--model deepl --deepl_key ${deepl_key}`

        

        bbook_maker --book_name test_books/animal_farm.epub --model deepl --deepl_key ${deepl_key}

        

* deeplfree: DeepL free model

        

        bbook_maker --book_name test_books/animal_farm.epub --model deeplfree

### Claude

Support [Claude](https://console.anthropic.com/docs) model. Use `--model claude --claude_key ${claude_key}` .

    bbook_maker --book_name test_books/animal_farm.epub --model claude --claude_key ${claude_key}
            

### Custom API
Support CustomAPI model. Use `--model customapi --custom_api ${custom_api}` .

    bbook_maker --book_name test_books/animal_farm.epub --model customapi --custom_api ${custom_api}  

### Google

Support google model. Use `--model google`

## Languages
`--language <LANGUAGE>` <br>

Set target languages. All models except for `caiyun` supports lots of languages. You can use `bbook_maker --help` to check available languages. Default target language is `"Simplified Chinese"` .

```sh
bbook_maker --book_name test_books/animal_farm.epub --model chatgptapi --openai_key ${openai_key} --language ja
```

```sh
bbook_maker --book_name test_books/animal_farm.epub --model chatgptapi --openai_key ${openai_key} --language "Simplified Chinese"
```