<div align="left">

# Bilingual Book Maker

**[中文](./README-CN.md) | English**


The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt/md/srt/pdf files and books. This tool is exclusively designed for translating epub and other public domain works and is not intended for copyrighted works. Before using this tool, please review the project's **[disclaimer](./disclaimer.md)**.

[![Stars](https://img.shields.io/github/stars/yihong0618/bilingual_book_maker)](https://github.com/yihong0618/bilingual_book_maker/stargazers)
[![CI](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml/badge.svg)](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml)
[![PyPI](https://img.shields.io/pypi/v/bbook-maker.svg)](https://pypi.org/project/bbook-maker/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![litellm](https://img.shields.io/badge/%20%F0%9F%9A%85%20liteLLM-OpenAI%7CAzure%7CAnthropic%7CPalm%7CCohere%7CReplicate%7CHugging%20Face-blue?color=green)](https://github.com/BerriAI/litellm)

</div>


![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## Supported endpoints

OpenAI and Anthropic format endpoints are supported.
Usually it comes with three fields, two if you are using the official endpoints, such as `gpt-5.6-luna` (the default),
`claude-sonnet-4-6` or `deepseek-v4-flash-0731`. 
Specify `openai`, or `anthropic` at `--api_format` for API request formats.
This argument also supports selecting some machine-translation engines (`google`, `caiyun`, `deepl`, `deeplfree`,
`tencent`, `customapi` — not an OpenAI format) or `codex`
if you want to use your Codex quota instead. 

`--provider` is an alternative way to pass credentials, through a JSON config file
`bbm_providers.json`. 

Epub tags classification is auto enabled on JSON-schema endpoints, and on any endpoint that can hold a conversation — the codex route and plain reseller proxies included — where the model is asked for exact `skip`/`translate` verdicts instead. Only routes with no conversation at all (the MT engines) fall back to translating `p` tags only, so some poetry or verse may be omitted there. See plan mode for details.

Older flags (`--model gpt4o`,
`--model gemini`, `--openai_key`, …) still work: see
[Models and languages](./docs/model_lang.md).

## Preparation

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md/pdf books
3. Environment with internet access or proxy
4. Python 3.10+

## Quick Start

A sample book, `test_books/animal_farm.epub`, is provided for testing purposes.
`--test` translates only its first few paragraphs.

```shell
pip install -r requirements.txt      # or: pip install -U bbook_maker
```

Then:

```shell
cp bbm_providers.example.json bbm_providers.json
# edit base_url, default_models and env_key in ./bbm_providers.json
python3 make_book.py --book_name test_books/animal_farm.epub --provider openai --test
```

You can also pass the key on the command line:

```shell
python3 make_book.py --book_name test_books/animal_farm.epub \
  --key sk-... --model gpt-5.6-luna --api_base https://api.openai.com/v1 --test
```

To spend a [Codex](https://developers.openai.com/codex/cli) subscription:

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt-5.6-luna --api_format codex --test
```

Or hand it to a coding agent

```shell
git clone https://github.com/yihong0618/bilingual_book_maker.git
cd bilingual_book_maker
codex "Hi, please use bbm-plan to translate this book: test_books/animal_farm.epub into a bilingual Chinese-English edition, thanks."
```

## Endpoint flags

- `--api_format` names the API the endpoint speaks: `openai`, `anthropic`,
  `gemini`, `qwen`, `groq`, `xai`, `litellm`, `codex`, or one of the
  machine-translation engines (`google`, `caiyun`, `deepl`, `deeplfree`,
  `tencent`, `customapi`). A format that belongs to one vendor already
  knows that vendor's address, so the format and a `--key` are a whole
  command.
- **Any other OpenAI-compatible API**: `--api_base` (ending in `/v1`),
  `--key` the API key, and the model id in `--model`. Omit `--api_base` for
  OpenAI's own API, and `--model` for `gpt-5.6-luna`.
- Or translate through `--provider`: `bbm_providers.example.json` has an
  entry for each vendor below (Gemini, Qwen, xAI, Groq, OrcaRouter, Ollama,
  LiteLLM, DeepSeek, SiliconFlow, OpenRouter). Copy it to
  `bbm_providers.json`, set the key in it, and `--provider gemini` uses the
  Gemini API from it.
- `--use_context session` translates in session mode; the history compacts
  at 8k by default (`--context-compact-at` overrides).
- The old preset names and key flags still work, see
  [Migrating from the old flags](./docs/migration.md).

## Supported translation services
* DeepL
  Support DeepL model [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) need pay to get the token

  ```
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key}
  ```

* DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deeplfree
  ```

* [Claude](https://console.anthropic.com/docs)

  A `claude-*` model id selects the anthropic format on its own.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key}
  ```

* Google Translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google
  ```

* Caiyun Translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format caiyun --key ${caiyun_key}
  ```

* Gemini

  Google [Gemini](https://aistudio.google.com/app/apikey), over the Gemini
  API itself. Name any Gemini model id; without `--model` it is
  `gemini-flash-latest`. `--interval` sets the pause between requests, which
  is how the free tier's rate limit is stayed under.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format gemini --key ${gemini_key} --model gemini-flash-latest
  ```

* Qwen

  [Qwen-MT](https://www.aliyun.com/product/dashscope) on DashScope, a
  translation model: the request states a source and a target language.
  `qwen-mt-turbo` (the default) and `qwen-mt-plus` are supported, and
  `--source_lang` states the source language when auto-detection is not
  wanted.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format qwen --key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
  ```

* [Tencent TranSmart](https://transmart.qq.com)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format tencent
  ```

* [xAI](https://x.ai)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format xai --key ${xai_key} --model grok-4.3
  ```

* [OrcaRouter](https://www.orcarouter.ai)

  The [OrcaRouter](https://www.orcarouter.ai) gateway, defaulting to its
  `orcarouter/auto` smart routing. The address comes with the route, so there
  is no `--api_base`; the key is `--key` or `BBM_ORCAROUTER_API_KEY`.
  `--provider orcarouter` reaches the same place.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model orcarouter --key ${orcarouter_key}
  ```

  To name one model instead: `--provider orcarouter --model <id>`.

* [Ollama](https://github.com/ollama/ollama)

  Translate with [Ollama](https://github.com/ollama/ollama) self-hosted models.
  If the ollama server is not local, point `--api_base http://x.x.x.x:port/v1` at it.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base http://localhost:11434/v1 --model ${ollama_model_name}
  ```

* [groq](https://console.groq.com/keys)

  `--model` is required: GroqCloud's catalogue turns over, so pick a current
  id from [Supported Models](https://console.groq.com/docs/models).

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format groq --key [your_key] --model llama-3.3-70b-versatile
  ```

* [LiteLLM](https://docs.litellm.ai/docs/simple_proxy)

  A LiteLLM proxy, which fans out to whatever backends its own config names.
  `--model` is the name that config gives one of them. The default address is
  the proxy's own, on this machine; elsewhere it is `--api_base`.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format litellm --model ${name_in_your_litellm_config}
  ```

* [Codex](https://developers.openai.com/codex/cli)

  Spend your ChatGPT/Codex plan. Install the
  [Codex CLI](https://developers.openai.com/codex/cli). The default model is `gpt-5.6-luna`; `--api_format codex --model <id>` names another. One session is reused for the whole book and compacted at `--context-compact-at`;
  it runs sandboxed, with shell, MCP servers and browsing off, but hooks may still fire.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format codex --language zh-hans
  ```

## Custom API Provider

  When the built-in models do not cover your needs, define a provider in a JSON config file. Without a code change, any OpenAI-compatible or Anthropic-format API (DeepSeek, SiliconFlow, a local proxy, ...) becomes usable.

  Create `bbm_providers.json` in the current directory (or `~/.bbm/providers.json`):

  ```json
  {
    "providers": {
      "deepseek": {
        "api_style": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
        "env_key": "BBM_DEEPSEEK_API_KEY"
      },
      "siliconflow": {
        "api_style": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_models": ["Qwen/Qwen2.5-72B-Instruct"],
        "env_key": "BBM_SILICONFLOW_API_KEY"
      },
      "openai": {
        "api_style": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_models": ["gpt-5.6-luna"],
        "env_key": "OPENAI_API_KEY",
        "prices": {
          "gpt-5.6-luna": {"input": 0.20, "output": 1.20, "cached_input": 0.02}
        }
      }
    }
  }
  ```

  Config fields:

  | Field | Required | Description |
  |-------|----------|-------------|
  | `api_style` | Yes | API request format: `openai`, `anthropic`, `gemini`, `qwen`, `groq`, `xai` or `litellm` |
  | `base_url` | No | The API address. Omitted means the api_style's default address |
  | `default_models` | No | Default model list. Required if `--model` is not provided |
  | `env_key` | No | Environment variable name for API key. Required if `--key` is not provided |
  | `prices` | No | Prices per million tokens, per model: `{"<model id>": {"input": …, "output": …, "cached_input": …}}`. When every model in the run has a price, the progress bar shows money spent (`spent=$0.012`) instead of token counts, and the closing line shows both. Without `cached_input`, cache reads are charged at the input price. A model without a price puts the bar back on tokens, and the closing line names it |
  | `currency` | No | Currency code for the prices, default `USD`. `USD`, `EUR`, `GBP`, `CNY` and `JPY` print with their symbol; any other code prints after the amount, as in `0.500 CHF` |

  The spent amount and the token counts are estimates, accumulated from the usage each request reports — close enough to steer by, but the vendor's bill is the number that counts.

  Priority: project-level `./bbm_providers.json` overrides global `~/.bbm/providers.json`.

  `--model` names a model at that provider; without it the first of `default_models` is used.

  ```shell
  python3 make_book.py --provider deepseek --key sk-xxx --book_name test_books/animal_farm.epub

  export BBM_DEEPSEEK_API_KEY=sk-xxx
  python3 make_book.py --provider deepseek --book_name test_books/animal_farm.epub

  python3 make_book.py --provider deepseek --key sk-xxx --model deepseek-reasoner --book_name test_books/animal_farm.epub
  ```

## Usage

- Once the translation is complete, a bilingual book named `${book_name}_bilingual.epub` would be generated for EPUB inputs; for TXT/MD/SRT inputs a bilingual text (or subtitle) file named `${book_name}_bilingual.txt` (or `_bilingual.srt`) will be generated. For **PDF inputs** the tool will produce a bilingual `.txt` fallback and will also attempt to create `${book_name}_bilingual.epub` — if EPUB creation fails, the TXT fallback remains so you do not need to retranslate.
- If there are any errors or you wish to interrupt the translation by pressing `CTRL+C`, a temporary bilingual file (for example `{book_name}_bilingual_temp.epub` or `{book_name}_bilingual_temp.txt`) would be generated. You can simply rename it to any desired name.

## Params

- `--model`:

  The model id, exactly as the endpoint spells it. On the OpenAI format the default is `gpt-5.6-luna`. The second column is the `--api_format` the id needs:

  | model | `--api_format` | notes |
  |-------|---------------|-------|
  | `gpt-5.6-luna` | `openai` | the default, at OpenAI's own address |
  | `claude-sonnet-4-6` | `anthropic` | Anthropic's own address |
  | `gpt-4o-mini` | `openai` | OpenAI |
  | `deepseek/deepseek-v4-flash-0731` | `openai` | with the matching `--api_base` |
  | `gemini-flash-latest` | `gemini` | the default there, at Google's own address |
  | `qwen-mt-turbo` | `qwen` | the default there, on DashScope |
  | `llama-3.3-70b-versatile` | `groq` | Groq's own address |

  The old preset values still parse and are rewritten to a real model id with a note; [Migrating from the old flags](./docs/migration.md) lists them. Anything else is an endpoint: `--api_base <url> --key <key> --model <id>`, or a `--provider` entry (see the Custom API Provider section).

- `--key`:

  API key for the endpoint. Without the flag the key is read from `$BBM_API_KEY`, then from the format's own variable: `$OPENAI_API_KEY`, `$ANTHROPIC_API_KEY`, `$BBM_GOOGLE_GEMINI_KEY`, `$BBM_QWEN_API_KEY`, `$BBM_GROQ_API_KEY`, `$BBM_XAI_API_KEY`, `$BBM_CAIYUN_API_KEY` or `$BBM_DEEPL_API_KEY`. The old per-vendor flags (`--openai_key` and the rest) still work. `--api_key` is the same flag under its older name.

- `--api_format`:

  The API the endpoint speaks. When omitted it is inferred: an `anthropic.com` host, or a model id containing `claude` with no `--api_base`, means `anthropic`; anything else means `openai`. Pass it when the guess is wrong, to reach a vendor without typing its address, or to pick an engine.

  | format | key | notes |
  |--------|-----|-------|
  | `openai` (default) | required: `--key`, else `$BBM_API_KEY`, `$OPENAI_API_KEY`; not for a local address such as Ollama | any OpenAI-compatible endpoint: OpenAI itself, DeepSeek, OpenRouter, Ollama and the rest, the address in `--api_base` |
  | `anthropic` | required: `--key`, else `$BBM_API_KEY`, `$ANTHROPIC_API_KEY` | Anthropic itself, and gateways that speak the Messages API |
  | `gemini` | required: `--key`, else `$BBM_API_KEY`, `$BBM_GOOGLE_GEMINI_KEY`, `$GEMINI_API_KEY` | the Gemini API, default `gemini-flash-latest`; paced by `--interval` |
  | `qwen` | required: `--key`, else `$BBM_API_KEY`, `$BBM_QWEN_API_KEY`, `$DASHSCOPE_API_KEY` | Qwen-MT on DashScope, default `qwen-mt-turbo`; reads `--source_lang` |
  | `groq` | required: `--key`, else `$BBM_API_KEY`, `$BBM_GROQ_API_KEY`, `$GROQ_API_KEY` | GroqCloud; `--model` required |
  | `xai` | required: `--key`, else `$BBM_API_KEY`, `$BBM_XAI_API_KEY`, `$XAI_API_KEY` | xAI; `--model` required |
  | `litellm` | none for a proxy on this machine, else `--key` or `$LITELLM_MASTER_KEY` | a LiteLLM proxy, `http://localhost:4000` unless `--api_base` says otherwise; `--model` required |
  | `codex` | none: `codex login` (Codex CLI) | the local `codex app-server` sidecar on a ChatGPT/Codex plan, default `gpt-5.6-luna` |
  | `google` | none | Google Translate, free |
  | `caiyun` | required: `--key` or `$BBM_CAIYUN_API_KEY` | Caiyun |
  | `deepl` | required: `--key` or `$BBM_DEEPL_API_KEY` | DeepL (paid) |
  | `deeplfree` | none | DeepL free tier |
  | `tencent` | none | Tencent TranSmart, free |
  | `customapi` | none | a `{text, source_lang, target_lang}` format API |

- `--source_lang`:

  Source language. Stated, it reaches every LLM route's prompt as evidence ("Translate from english"), and rides in the request itself on `--api_format qwen` (whose request names a language pair) and `--api_format customapi`. Default: auto-detect, which states nothing.

- `--interval`:

  Seconds to wait between requests, e.g. `--interval 0.1` for 100ms. Only `--api_format gemini` paces itself with it; every other route ignores it. Default: `0.01`.

- `--test`:

  Use `--test` option to preview the result if you haven't paid for the service. Note that there is a limit and it may take some time.

- `--language`:

  Set the target language: a tag (`--language zh-hant`), a name (`--language "Traditional Chinese"`), or both at once — `--language "zh-hant:Traditional Chinese"`. The tag is what gets stamped on the output (inserted markup, `dc:language`, the provenance record) and names the structured-output field; the name is what the model is asked for. A bare tag or name behaves as before; the two-part form is for a language the built-in tables miss. Default `zh-hans`.
  The tag list ships in `docs/languages.md`; `python make_book.py --help` prints it too. The source language is never part of this flag — `--source_lang` states it when detection isn't enough.

- `--proxy`:

  Use `--proxy` option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890`.

- `--resume`:

  Use `--resume` option to manually resume the process after an interruption. An EPUB
  checkpoint records the run's language, prompt and model, and a resume under different
  ones stops with a message rather than splicing two runs into one book (checkpoints from
  older versions warn once and continue). Refused together with `--parallel-workers` and
  `--accumulated_num` above 1 — that path writes no checkpoint, so there would be nothing
  to resume.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google --resume
  ```

- `--translate-tags`:

  epub is made of html files. By default, we only translate contents in `<p>`.
  Use `--translate-tags` to specify tags need for translation. Use comma to separate multiple tags.
  For example: `--translate-tags h1,h2,h3,p,div`

- `--plan-classify` (epub only):

  **Plan mode**: classify epub tags with the translating model, or with codex / claude code.

  The value decides how is translation decision of each tag made:

  - `auto` (default): when the book is an epub, ask the LLM what to translate. On an endpoint verified to apply a strict JSON schema the question rides structured output; on any other endpoint that can hold a conversation (the codex route, plain proxies) it is asked over a plain session — three signatures at a time, answered with exact `skip,translate,unsure` replies; a reply that does not carry exactly as many verdicts as asked is re-asked one signature at a time, and `unsure` or anything unparseable counts as translate (never skip; the coverage gate polices skips). `--context-compact-at` bounds that classifier session too, whether or not `--use_context` was passed. Only when the route has no conversation at all, and when the plan fails, translate the `--translate-tags` selection instead. Rows decided over a plain session appear in `<book>_plan.json` with an `unnamed (…)` content type naming how the verdict was reached rather than what the content is.
  - `none`: no plan; only the `--translate-tags` selection.
  - `all`: translate the whole partition, no classification.
  - `model`: the translating LLM judges, then translates. `--plan-classify-model X` picks the model that classifies.
  - `agent`: writes the classification plan for the book and prints instructions to paste into your coding tool for classification. 
  (or you could also do it by hand). Then run the translation with `--plan-classify agent` again.

  - `--plan-dry-run`: print the per-signature table, write `<book>_plan.json`, and exit. Honors `--only_filelist` / `--exclude_filelist`.
  - `<book>_plan.json`: the translation plan; delete it to classify again.
  - `--plan-min-coverage` (default 0.5, range 0–1): plan mode aborts if the plan covers less than this fraction of the text. `0` disables the guard and values above `0.9` usually abort after classification is already paid for — both warn.
  - `--poetry-group-size` (default 8): consecutive short lines — verse, lists, tables of short entries — are translated together, up to this many per request, so each line sees its neighbours. Deprecated: general grouping and the session handoff give lines their neighbours now, and the units cap is `--max-batch-units`; the flag still works, warns, and is on its way out.

  ```shell
  # let the model judge which tags need translating
  python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify model
  # or hand it to an agent: stops, prints instructions, then you give them to your AI
  python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify agent
  ```

- `--exclude-translate-tags`:

  Use `--exclude-translate-tags` to exclude content within specified HTML tags from translation. This is useful for preserving code blocks, preformatted text, or other special content. Use comma to separate multiple tags.
  Default: `sup,code`.
  For example: `--exclude-translate-tags code,pre`

  **Tip**: Use `--exclude-translate-tags ""` to translate all content including code blocks (overrides the default exclusion).

- `--api_base`:

  If you want to change api_base like using Cloudflare Workers, use `--api_base <URL>` to support it.
  **Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**

- `--allow_navigable_strings`:

  If you want to translate strings in an e-book that aren't labeled with any tags, you can use the `--allow_navigable_strings` parameter. This will add the strings to the translation queue. **Note that it's best to look for e-books that are more standardized if possible.**

- `--prompt`:

  To tweak the prompt, use the `--prompt` parameter. Valid placeholders for the `user` role template include `{text}` and `{language}`. It supports a few ways to configure the prompt:

  - If you don't need to set the `system` role content, you can simply set it up like this: `--prompt "Translate {text} to {language}."` or `--prompt prompt_template_sample.txt` (example of a text file can be found at [./prompt_template_sample.txt](./prompt_template_sample.txt)).

  - If you need to set the `system` role content, you can use the following format: `--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'` or `--prompt prompt_template_sample.json` (example of a JSON file can be found at [./prompt_template_sample.json](./prompt_template_sample.json)).

  - A third key, `style`, is a standing instruction about how to write — register, tone, vocabulary — that rides in **every** request, and in session mode replaces the style the handoff would otherwise observe for itself. A section with no native slot on a route (`style` everywhere, `system` on the codex format) is appended to the user message instead of being dropped, and a run with `--prompt` prints one line at start naming which sections it adopted and where they landed. Samples carrying all three sections: [./prompt_sections_sample.json](./prompt_sections_sample.json) for a plain run, [./prompt_session_sample.json](./prompt_session_sample.json) for a session run.
  
  - You can now use [PromptDown](https://github.com/btfranklin/promptdown) format (`.md` files) for more structured prompts: `--prompt prompt_md.prompt.md`. PromptDown supports both traditional system messages and developer messages (used by newer AI models). Example:
  
      ```markdown
      # Translation Prompt
      
      ## Developer Message
      You are a professional translator who specializes in accurate translations.
      
      ## Conversation
      
      | Role | Content                                                        |
      | ---- | -------------------------------------------------------------- |
      | User | Please translate the following text into {language}:\n\n{text} |
      ```

  - You can also set the `user` and `system` role prompt by setting environment variables: `BBM_CHATGPTAPI_USER_MSG_TEMPLATE` and `BBM_CHATGPTAPI_SYS_MSG`.

- `--batch_size`:

  Use the `--batch_size` parameter to specify the number of lines for batch translation (default is 10, currently only effective for txt files).

- `--accumulated_num`:

  Wait for how many tokens have been accumulated before starting the translation. gpt3.5 limits the total_token to 4090. For example, if you use `--accumulated_num 1600`, maybe openai will output 2200 tokens and maybe 200 tokens for other messages in the system messages user messages, 1600+2200+200=4000, So you are close to reaching the limit. You have to choose your own
  value, there is no way to know if the limit is reached before sending.
  In EPUB plan mode this is a per-request token budget: consecutive units of any length share one request up to `N` tokens. With `--use_context session` — and always on the codex route, whose thread is a session either way — a default is derived from the run's own prompt overhead — `1600` with the stock prompts, up to `2000` under a fat custom `--prompt` (fewer requests is most of a session run's bill); pass `1` to turn grouping off. Minimum `1`.

- `--max-batch-units`:

  EPUB plan mode only: the most units one grouped request may carry (default `32`, half the measured fault-emergence level — our September 2026 measurement put the first content faults at 64 effective units per request, on prose). An endpoint that verifies JSON mode but not a strict schema automatically carries half this many (16), which is where miscounted replies actually live. Lower it if the run keeps printing misalignment recoveries; the token budget (`--accumulated_num`) is what bounds content either way.

- `--use_context`:

  Translate with context.
  Prompts the model to create a three-paragraph summary. If it's the beginning of the translation, it will summarize the entire passage sent (the size depending on `--accumulated_num`).
  For subsequent passages, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work. This improves consistency of flow and tone throughout the translation. The running summary is what the `openai`, `groq`, `xai`, `litellm` and `anthropic` formats do with this flag. The `gemini` format keeps its own chat history instead, and `qwen` keeps a window of recent translation pairs as translation memory — the same flag, the mechanism each route has.

- `--context_paragraph_limit`:

  Use `--context_paragraph_limit` to set a limit on the number of context paragraphs when using the `--use_context` option. This applies to window mode only.

- `--use_context session`:

  Session mode keeps one append-only history and re-reads it at the cache
  price, so the context can grow to about a chapter. When the history
  reaches the compact budget, the model writes a handoff report, which seeds
  the next window and is appended to `<book>_handoff.md`. Watch the progress
  bar's `cached=`: if it is still zero after a dozen requests, the endpoint
  is not reporting a cache; Ctrl+C and switch to window mode.

  - `--context-compact-at`:

    The estimated-token budget a rolling history may reach. In session mode the history is then compacted into a handoff report. Minimum `500`. When unset, every session run — grouped or not, the codex route included — compacts at `8000`, printed at start. It also bounds the plan classifier's own conversation on endpoints that classify over a plain session (which simply restarts there — no handoff), whether or not `--use_context` was passed. An explicit value always wins.

    The `8000` is pinned on measurement (September 2026, whole-book runs): the per-book cost optimum sits between `1500` and `4000`, `8000` runs 9–25% above it — inside single-run noise — and `20000` up to 56% more, in the one cell that also showed register drift. `8000` is the short edge of the band where a typical run compacts 0–1 times, so continuity costs the least; short windows did not hurt name consistency either, since the handoff re-states the recurring terms at every seam.

  - `--no-context-compact`:

    Session mode only. Skip the handoff report. The window still rolls over at the budget, but the next one starts empty instead of inheriting a summary. Cheaper, at the cost of continuity across the seam.

- `--glossary` / `--terminology`:

  A file of `term → translation` lines — one per line, `#` starts a note or a
  comment — that this run must render that way. The two spellings are one
  flag. Only the terms that occur in a request are sent with it, so a long
  file costs nothing on the paragraphs it does not touch. A missing file stops
  the run at parse time. Read by the openai- and codex-shaped routes for EPUB
  and Markdown books; the other routes say so and ignore it.

  A pinned term makes the translation say what you pinned, so pin only
  renderings you can stand behind.

  - `--glossary-auto on|off`:

    Whether a session run also keeps the renderings its own handoff reports
    establish, so recurring names stay unified across a window seam. On by
    default wherever a session runs (`--use_context session`, and the codex
    route's one thread); `off` asks the compact turn for a summary only.
    Learned terms live in this run and in `<book>_handoff.md`, and nowhere
    else — what a run taught itself is never treated as a pin you chose.

- `--parallel-workers`:

  Use `--parallel-workers` to process EPUB chapters or Markdown batches/sections in
  parallel. Values greater than `1` spin up multiple workers (recommended: `2-4`) and
  automatically fall back to sequential mode when there is only one unit of work. Other
  input loaders currently accept this shared CLI option but do not parallelize their work.

- `--temperature`:

  Sampling temperature for the openai and anthropic formats (the codex
  format has none). For example: `--temperature 0.7`.

- `--block_size`:

  Use `--block_size` to merge multiple paragraphs into one block. This may increase accuracy and speed up the process.
  For example: `--block_size 5`.

- `--single_translate`:

  Use `--single_translate` to output only the translated book without creating a bilingual version.

- `--no_disclosure`:

  An epub output says it is an AI translation (a machine translation on the `google`, `deepl`, `caiyun`, `tencent` and `customapi` engines): the tool is added as a translator contributor, a description line names the model, and a one-page note closes the book — a `Translation Credits` heading, then the model, the date and the unreviewed-translation note, nothing more. `--no_disclosure` leaves all of it out. The author, rights and source metadata are carried over either way.

- `--provenance`:

  Record how this file was made, invisibly: `bbm:` package metadata plus a `bbm_provenance.json` inside the book (format conversions rewrite the metadata and keep the file, so the record survives both ways), carrying the tool's build, the model, the endpoint **host**, the sanitized command line and the two languages. Never recorded: the API key under any spelling, the `--prompt` text, header values — a test sweeps every file in the archive for them. The endpoint host is there on purpose: a model claim alone is unverifiable, so the book names the model *as served by that host*, and a reseller that serves something else is the accountable party. Plan-mode and session runs record all this by themselves; this flag is the opt-in for a plain tag-mode run. A glossary named on the command line is embedded verbatim (`bbm_glossary.txt`, with its sha256) so pins are auditable; what a run learned by itself never is. Re-running the tool on its own output replaces the record. `--no_disclosure` turns this off too, and the sanitized command line does include file paths as you typed them.

- `--translation_style`:

  Apply custom CSS to translated EPUB text, for example
  `--translation_style "color: #808080; font-style: italic;"`.

- `--translation_color`:

  Shorthand for setting only the translated EPUB text color, for example
  `--translation_color "#1e90ff"`. If `--translation_style` is also present, the full style
  takes precedence.

- `--pdf_layout {none,top-bottom,side-by-side,all}`:

  Select additional bilingual PDF outputs for PDF inputs. The default `none` creates no
  extra PDF; `all` attempts both top-bottom and side-by-side layouts. The bilingual TXT and
  EPUB outputs are unaffected.

- `--sentence_mode`:

  Translate EPUB text sentence by sentence instead of translating each paragraph as one
  unit. It is incompatible with EPUB plan mode.

- `--batch` / `--batch-use`:

  Two-stage translation through the ChatGPT Batch API. Currently **refused on EPUB
  inputs**: the queue path is unreachable there, so such a run would translate live at
  full price and then submit an empty batch job instead of writing the book. Also refused
  on routes that do not implement the Batch API.

- `--quiet`:

  Suppress EPUB progress bars and per-paragraph source/translation echoes while retaining
  reports and errors. Recommended for log files and non-interactive agent runs.

- `--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"`:

  Retranslate from start_str to end_str's tag:

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'
  ```

  To retranslate only the tag containing `start_str`, pass an empty fourth argument:

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''
  ```

- `--extra_body`:

  Pass additional JSON parameters on the routes built on the OpenAI request
  path — `openai` and the OpenAI-style custom providers, and so also
  `groq`, `xai`, `litellm` and `--model orcarouter` — and on the `anthropic`
  route. Every other format says so and ignores it. It reaches the
  capability probe and the JSON rungs as well as the translate calls, so the
  endpoint is graded on the request the run actually makes. Provide a JSON
  object with the desired parameters.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
  ```

- `--extra_headers`:

  Extra HTTP headers sent with every request, on the same routes. They are
  set on the client, so the capability probe, the model check and the model
  listing carry them too. Values must be strings.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${openrouter_key} --api_base https://openrouter.ai/api/v1 --model anthropic/claude-haiku-4.5 --extra_headers '{"HTTP-Referer": "https://example.com", "X-Title": "bilingual_book_maker"}'
  ```

  If the endpoint refuses a request carrying either flag, it says so and
  quotes what the endpoint said, rather than quietly falling back.

  Common forms, for reference:

  ```shell
  # openai route — disable a local/vLLM chat template's thinking block
  --extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
  # openai route (chat completions) — reasoning effort and a token ceiling,
  # neither of which has its own flag (both model-dependent)
  --extra_body '{"reasoning_effort": "low", "max_completion_tokens": 2000}'
  # anthropic route — keep extended thinking off; for translation it mostly
  # buys deviation from the source, not quality
  --extra_body '{"thinking": {"type": "disabled"}}'

  # OpenRouter attribution (shown on its dashboard)
  --extra_headers '{"HTTP-Referer": "https://example.com", "X-Title": "bilingual_book_maker"}'
  # a gateway's own auth or routing header (the value stays out of the logs)
  --extra_headers '{"X-API-Key": "sk-gateway-..."}'
  ```

- `--provider`:

  Use a custom provider defined in `bbm_providers.json`; `--model` picks a model at it. See the "Custom API Provider" section above.

- `--api_key`:

  Same as `--key`.

### Examples

**Note if use `pip install bbook_maker` all commands can change to `bbook_maker args`**

```shell
# Test quickly
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --test --language zh-hans

# Test quickly for src
python3 make_book.py --book_name test_books/Lex_Fridman_episode_322.srt --key ${openai_key} --test

# Or translate the whole book
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --language zh-hans

# Gemini
python3 make_book.py --book_name test_books/animal_farm.epub --api_format gemini --key ${gemini_key} --model gemini-flash-latest

# Translate an EPUB with parallel chapter processing
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --parallel-workers 4

# Rotate across several models
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model_list gpt-5-mini,gpt-4o-mini

# Set env OPENAI_API_KEY to leave out --key
export OPENAI_API_KEY=${your_api_key}

# Name a model and add context, translating to Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt-4o --use_context --language ja

# Any OpenAI-compatible endpoint: base URL, key, and the model id it uses
python3 make_book.py --book_name test_books/animal_farm.epub --api_base "https://api.lingyiwanwu.com/v1" --key ${key} --model yi-34b-chat-0205

# DeepL, to Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key} --language ja

# Claude, to Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key} --language ja

# A custom translation API, to Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format customapi --api_base ${custom_api} --language ja

# A provider entry (e.g. DeepSeek); the key comes from the entry's env_key
python3 make_book.py --book_name test_books/animal_farm.epub --provider deepseek --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# Plan mode: auto-discover translatable content (poetry, blockquotes, table cells,
# ...) and batch verse lines in stanza windows; preview the plan with --plan-dry-run
python3 make_book.py --book_name test_books/animal_farm.epub --plan-dry-run
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify all

# Tweaking the prompt
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.json
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"

# translate txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --language zh-hans
# aggregated translation txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20

# Using Caiyun model to translate
# (the api currently only support: simplified chinese <-> english, simplified chinese <-> japanese)
# the official Caiyun has provided a test token (3975l6lr5pcbvidl6jl2)
# you can apply your own token by following this tutorial(https://bobtranslate.com/service/translate/caiyun.html)
python3 make_book.py --api_format caiyun --key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub


# Set env BBM_CAIYUN_API_KEY to leave out --key
export BBM_CAIYUN_API_KEY=${your_api_key}

```

More understandable example

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1'
```

Microsoft Azure Endpoints

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'
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

docker run --rm --name bilingual_book_maker --mount type=bind,source=$folder_path,target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/$book_name" --key $openai_key --language $language

# Linux
export folder_path=${your_folder_path}
export book_name=${your_book_name}
export openai_key=${your_api_key}
export language=${your_language}

docker run --rm --name bilingual_book_maker --mount type=bind,source=${folder_path},target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/${book_name}" --key ${openai_key} --language "${language}"
```

For example:

```shell
# Linux
docker run --rm --name bilingual_book_maker --mount type=bind,source=/home/user/my_books,target='/app/test_books' bilingual_book_maker --book_name /app/test_books/animal_farm.epub --key sk-XXX --test --test_num 1 --language zh-hant
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

- 书译 BookTranslator -> [Book Translator](https://www.booktranslator.app)

## Appreciation

Thank you, that's enough.

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: You can get a token from [OpenAI](https://platform.openai.com/account/api-keys) or [Anthropic](https://console.anthropic.com/account/api-keys).
[^black]: https://github.com/psf/black
