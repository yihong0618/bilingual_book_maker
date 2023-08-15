# Command Line Options

## Test translate
`--test` <br>

Use this option to preview the result if you haven't paid for the service or just want to test. Note that there is a limit and it may take some time.

```sh
bbook_maker --book_name test_books/Lex_Fridman_episode_322.srt --openai_key ${openai_key}  --test
```

```sh
bbook_maker --book_name test_books/animal_farm.epub --openai_key ${openai_key}  --test --language zh-hans
```

`--test_num <TEST_NUM>`<br>

Use this option to set how many paragraph you want to translate for testing. Default is 10.

## Resume
`--resume` <br>

Use this option to manually resume the process after an interruption.

## Retranslate (epub only)
`--retranslate <translated_filepath, file_name_in_epub, start_str [, end_str]>`<br>

If a file in epub is not translated well, it supports to re-translate part of epub separately.

This option take 4 arguments: `translated_filepath`, `file_name_in_epub`, `start_str`, `end_str`. `end_str` is optional.

- Retranslate from start_str to end_str's tag:

        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'

- Retranslate start_str's tag:
        
        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which'

- Retranslate start_str's tag, auto find filename:
        
        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' '' 'in spite of the present book shortage which'

**Warning:**

**It deletes from the tag at start_str of the finished book to the next tag at end_str, and then re-translates.**

**Therefore, please make sure that the next tag of end_str is the translated content. (If end_str is not provided, the next label of start_str is guaranteed to be the translated content.) There can be missing translations between the two strings, but if end_str is not translated, there will be problems.**




## Customize output style (epub only)
`--translation_style <TRANSLATION_STYLE>`<br>

Support changing the output style of epub files.

    bbook_maker --book_name test_books/animal_farm.epub --translation_style "color: #4a4a4a; font-style: normal; background-color: #f7f7f7; padding: 5px; margin: 10px 0; border-radius: 5px;"

![output_style](https://user-images.githubusercontent.com/89069008/226104545-7c029bb1-5325-46d4-a1eb-ec4e7bbaee97.png)
## Proxy
`--proxy <PROXY>` <br>

Use this option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890` .

## API base
`--api_base <API_BASE_URL>`<br>

If you want to change api_base like using Cloudflare Workers, use this option to support it.<br>

    bbook_maker --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'
**Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**

## Microsoft Azure Endpoints
`--api_base <API_BASE_URL>` `--deployment_id <DEPLOYMENT_ID>`<br>

You can use the api endpoint provided from Microsoft.


    bbook_maker --book_name 'animal_farm.epub' --openai_key XXXXX --api_base 'https://example-endpoint.openai.azure.com' --deployment_id 'deployment-name'

**Note : Current only support chatgptapi model for deployment_id. And `api_base` must be provided when using `deployment_id`. You can check [here](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/create-resource?pivots=web-portal) for more information about `deployment_id`.**

## Batch size (txt only)
`--batch_size`<br>

Use this parameter to specify the number of lines for batch translation. Default is 10. (Currently only effective for txt files).
```sh
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20
```

## Accumulated Num
`--accumulated_num <ACCUMULATED_NUM>`<br>

Wait for how many tokens have been accumulated before starting the translation. gpt3.5 limits the total_token to 4090. 

For example, if you use --accumulated_num 1600, maybe openai will
output 2200 tokens and maybe 200 tokens for other messages in the system messages user messages. 1600+2200+200=4000, so you are close to the limit. 

You have to choose your own
value, there is no way to tell if the limit is reached before sending request.
