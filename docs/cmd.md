# Command Line Options

`book_maker/cli.py` and `python3 make_book.py --help` are the runtime source of truth.
The inventory below is checked against every long option registered with `argparse`; the
sections after it provide additional notes for selected workflows.

## Complete option inventory

### Input, scope, and output

| Option | Purpose |
|---|---|
| `--book_name PATH` | Input EPUB, TXT, Markdown, SRT, or PDF path (required). |
| `--book_from kobo` | Import from a Kobo device instead of a normal source path. |
| `--device_path PATH` | Kobo mount path used with `--book_from`. |
| `--language LANGUAGE` | Target language; default `zh-hans`. |
| `--source_lang LANGUAGE` | Source language for models such as Qwen; default `auto`. |
| `--single_translate` | Output translation only instead of bilingual text. |
| `--translate-tags TAGS` | Comma-separated EPUB tags; default `p`, ignored in plan mode. |
| `--exclude-translate-tags TAGS` | EPUB ancestor tags to exclude; default `sup,code`; `""` clears it. |
| `--allow_navigable_strings` | Include otherwise untagged EPUB strings; redundant in plan mode. |
| `--only_filelist FILES` | Include only these comma-separated internal EPUB files. |
| `--exclude_filelist FILES` | Exclude these comma-separated internal EPUB files. |
| `--translation_style CSS` | CSS applied to translated EPUB entries. |
| `--translation_color COLOR` | Color-only shorthand; `--translation_style` takes precedence. |
| `--pdf_layout MODE` | Additional PDF output: `none`, `top-bottom`, `side-by-side`, or `all`. |
| `--retranslate OUT FILE START END` | Retranslate an EPUB range in an existing output. |

### EPUB plan mode

| Option | Purpose |
|---|---|
| `--plan-dry-run` | Build/report a free EPUB plan and exit. |
| `--plan-classify {none,most,model,agent}` | Select no plan, greedy plan, model triage, or coding-agent triage. |
| `--plan-classify-model MODEL` | Classification model; implies model mode and conflicts with `most`/`agent`. |
| `--plan-min-coverage FRACTION` | Fail if selected planned text is below this fraction; default `0.5`. |
| `--poetry-group-size N` | Maximum verse lines per planned translation request; default `8`. |

### Translation and execution

| Option | Purpose |
|---|---|
| `--test` | Translate only a preview sample. |
| `--test_num N` | Number of test units; default `10`. |
| `--resume` | Continue from the loader's saved checkpoint. |
| `--prompt VALUE_OR_FILE` | User/system prompt template; the user template requires `{text}`. |
| `--temperature FLOAT` | Sampling temperature; default `1.0`. |
| `--use_context` | Send an evolving narrative context with compatible translators. |
| `--context_paragraph_limit N` | Context history limit used with `--use_context`. Parser default `0` means the translator default (3 paragraphs for ChatGPT), not zero history. |
| `--accumulated_num N` | EPUB token/character accumulation and SRT subtitle-block character batching (capped at 512 for SRT); ignored in EPUB plan mode. |
| `--batch_size N` | Aggregated unit count for loaders that support it. |
| `--block_size N` | Merge paragraphs into delimiter-translated blocks. |
| `--sentence_mode` | Translate EPUB paragraphs sentence by sentence; incompatible with plan mode. |
| `--parallel-workers N` | Parallel EPUB chapters or Markdown batches/sections; default `1`. |
| `--batch` | Submit an EPUB ChatGPT Batch API job; incompatible with plan mode. |
| `--batch-use` | Consume a previously submitted batch job; incompatible with plan mode. |
| `--interval SECONDS` | Gemini request interval; default `0.01`. |
| `--extra_body JSON` | Extra request fields for ChatGPT/OpenAI-derived request paths (including OpenAI-style custom providers and xAI); other translators such as Claude, Gemini, Qwen, and Groq ignore it. |
| `--quiet` | Suppress EPUB progress bars and paragraph echoes, not reports/errors. |
| `--proxy URL` | Set HTTP/HTTPS proxy environment variables for the run. |

### Model routing and credentials

| Option | Purpose |
|---|---|
| `--model MODEL` / `-m MODEL` | Built-in translator route. Mutually exclusive with `--provider`. |
| `--model_list IDS` | Exact comma-separated IDs for OpenAI, Groq, Gemini, or a custom provider. |
| `--provider NAME` | Provider from project/global `bbm_providers.json`; conflicts with `--model`. |
| `--api_key KEY` | Custom-provider key; prefer its configured environment variable. |
| `--api_base URL` | Override the selected translator endpoint. |
| `--deployment_id ID` | Azure OpenAI deployment; also requires `--api_base`. |
| `--ollama_model MODEL` | Ollama model; defaults its endpoint to `http://localhost:11434/v1`. |
| `--openai_key KEY` | OpenAI-compatible key; prefer `BBM_OPENAI_API_KEY`. |
| `--claude_key KEY` | Anthropic key; prefer `BBM_CLAUDE_API_KEY`. |
| `--gemini_key KEY` | Gemini key; prefer `BBM_GOOGLE_GEMINI_KEY`. |
| `--groq_key KEY` | Groq key; prefer `BBM_GROQ_API_KEY`. |
| `--xai_key KEY` | xAI key; prefer `BBM_XAI_API_KEY`. |
| `--orcarouter_key KEY` | OrcaRouter key; prefer `BBM_ORCAROUTER_API_KEY`. |
| `--qwen_key KEY` | Qwen key; prefer `BBM_QWEN_API_KEY`. |
| `--deepl_key KEY` | DeepL key; prefer `BBM_DEEPL_API_KEY`. |
| `--caiyun_key KEY` | Caiyun key; prefer `BBM_CAIYUN_API_KEY`. |
| `--custom_api VALUE` | Legacy custom translator API; prefer `BBM_CUSTOM_API`. |

Do not put secrets directly on a shared command line. Environment variables are safer for
agent and CI use. The CLI does **not** load `.env` files itself: export the variables first,
or source a local git-ignored file before running, for example
`set -a; source .env; set +a; bbook_maker ...`.

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
`--retranslate <translated_filepath> <file_name_in_epub> <start_str> <end_str>`<br>

If a file in an EPUB is not translated well, this re-translates part of it separately.
Argparse requires all four values. Use an empty `end_str` to retranslate only the starting
tag; an empty `file_name_in_epub` enables automatic filename lookup.

- Retranslate from start_str to end_str's tag:

        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'

- Retranslate the `start_str` tag (empty fourth value):
        
        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''

- Retranslate the `start_str` tag and auto-find the filename (empty second and fourth values):
        
        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' '' 'in spite of the present book shortage which' ''

**Warning:**

**It deletes from the tag at start_str of the finished book to the next tag at end_str, and then re-translates.**

**Therefore, make sure the tag after `end_str` is translated content. When `end_str` is an empty string, the tag after `start_str` is used. There can be missing translations between the two strings, but a non-translated end boundary will cause problems.**




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
