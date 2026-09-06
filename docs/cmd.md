# Command Line Options

`book_maker/cli.py` and `python3 make_book.py --help` are the runtime source of truth.
The inventory below is checked against every long option registered with `argparse`; the
sections after it provide additional notes for selected workflows.

## Complete option inventory

### Input, scope, and output

| Option | Purpose |
|---|---|
| `--book_name PATH` | Input EPUB, TXT, Markdown, SRT, or PDF path (required). |
| `--language LANGUAGE` | Target language, or `SOURCE:TARGET` (e.g. `en:zh-hant`) to state the source; default `zh-hans`. |
| `--source_lang LANGUAGE` | Source language for models such as Qwen; default `auto`. |
| `--single_translate` | Output translation only instead of bilingual text. |
| `--no_disclosure` | Do not mark the epub as an AI translation, or a machine translation on the engine formats (translator credit, description line, closing note). Silences `--provenance`'s machine record too. |
| `--provenance` | Record how the file was made, invisibly: `bbm:` package metadata plus `bbm_provenance.json` in the book (conversions rewrite metadata; the file survives) — the tool's build, model, endpoint host, sanitized command line, languages; never the key or the `--prompt` text. Automatic on plan-mode and session runs; this is the tag-mode opt-in. A `--glossary` file is embedded verbatim with its sha256; learned terms never are. |
| `--translate-tags TAGS` | Comma-separated EPUB tags; default `p`, ignored in plan mode. |
| `--exclude-translate-tags TAGS` | EPUB ancestor tags to exclude; default `sup,code`; `""` clears it. |
| `--allow_navigable_strings` | Include otherwise untagged EPUB strings; redundant in plan mode. |
| `--only_filelist FILES` | Include only these comma-separated internal EPUB files. |
| `--exclude_filelist FILES` | Exclude these comma-separated internal EPUB files. |
| `--translation_style CSS` | CSS applied to translated EPUB entries. |
| `--translation_color COLOR` | Color-only shorthand; `--translation_style` takes precedence. |
| `--pdf_layout MODE` | Additional PDF output: `none`, `top-bottom`, `side-by-side`, or `all`. |
| `--retranslate OUT FILE START END` | Retranslate an EPUB range in an existing output. EPUB only — refused elsewhere. |

### EPUB plan mode

| Option | Purpose |
|---|---|
| `--plan-dry-run` | Build and print the EPUB plan, write `<book>_plan.json` with every `action` still `null`, and exit. No credentials needed. |
| `--plan-classify {auto,none,all,model,agent}` | No plan, the whole partition, model triage, or coding-agent triage. Default `auto`: model triage on any epub endpoint that can answer — over structured output where a strict JSON schema is verified, over a plain conversation (exact `skip`/`translate` replies; anything else translates) elsewhere, codex included; tag mode only where no conversation exists. |
| `--plan-classify-model MODEL` | Classification model; implies model mode and conflicts with `all`/`agent`. |
| `--plan-min-coverage FRACTION` | Fail if selected planned text is below this fraction; default `0.5`, must be between 0 and 1 (`0` disables the guard, values above `0.9` usually abort — both warn). |
| `--poetry-group-size N` | Deprecated — general grouping and the session handoff give short lines their neighbours now, and the units cap is `--max-batch-units`. Still works (default `8`, minimum `1`) but warns. |

### Translation and execution

| Option | Purpose |
|---|---|
| `--test` | Translate only a preview sample. |
| `--test_num N` | Number of test units; default `10`. |
| `--resume` | Continue from the loader's saved checkpoint. An EPUB checkpoint records the run's language, prompt and model; a mismatch stops the resume (older checkpoints warn once and continue). Refused together with `--parallel-workers` and `--accumulated_num` above 1, where no checkpoint is ever written. |
| `--prompt VALUE_OR_FILE` | Prompt config: `user` (must contain `{text}`), `system`, and `style`. A `style` goes into every request and verbatim into each handoff report. A section with no native slot on a route (`style` everywhere, `system` on the `codex` format) is appended to the user message instead of dropped; a run with `--prompt` prints which sections it adopted and where. Samples: `prompt_sections_sample.json`, `prompt_session_sample.json`. |
| `--temperature FLOAT` | Sampling temperature; default `1.0`. |
| `--use_context [window\|session]` | Send earlier paragraphs as context. Bare or `window`: re-send the last few source/translation pairs (the long-standing behaviour). `session`: one append-only history, re-read at the endpoint's prompt-cache rate. |
| `--context_paragraph_limit N` | Window mode only: context history limit. Parser default `0` means the translator default (3 paragraphs for ChatGPT), not zero history. |
| `--context-compact-at N` | Estimated-token budget for a rolling history. In session mode the history is compacted into a handoff report at this size; minimum `500`. When unset, every session run — grouped or not, the `codex` format included — compacts at `8000`, printed at start (pinned on measurement: the cost optimum sits at 1500–4000, 8000 runs 9–25% above it — noise — and it is where a typical run compacts 0–1 times, so continuity costs the least; 20000 cost up to 56% more and drifted). Also bounds the plan classifier's conversation on endpoints that classify over a plain session (restart there, no handoff), with or without `--use_context`. An explicit value always wins. |
| `--no-context-compact` | Session mode only: skip the handoff report. The window still rolls over at the budget, but the next one starts empty. |
| `--glossary FILE` / `--terminology FILE` | A file of `term → translation` lines (one per line; `#` starts a note or a comment) this run must render that way. Two names for one flag. Only the terms that occur in a request are sent with it. A missing file stops the run at parse time. Read by the openai- and codex-shaped routes for EPUB and Markdown books; other routes warn and ignore it. |
| `--glossary-auto on\|off` | Whether a session run also keeps the renderings its own handoff reports establish. On by default wherever a session runs (`--use_context session`, and the `codex` format's one thread); `off` asks the compact turn for a summary only. Learned terms stay in this run and in `<book>_handoff.md`, and nowhere else. |
| `--accumulated_num N` | EPUB token/character accumulation and SRT subtitle-block character batching (capped at 512 for SRT). In EPUB plan mode it is a per-request token budget: consecutive units of any length share one request up to `N` tokens (at most `--max-batch-units` units per request; half that when the endpoint verifies JSON mode but not a strict schema). Plan mode with `--use_context session` — and always on the `codex` format, whose thread is a session either way — derives a default from the run's own prompt overhead — `1600` with the stock prompts, up to `2000` under a fat custom `--prompt` (fewer requests is most of a session run's bill); pass `1` to turn grouping off there. Minimum `1`. |
| `--max-batch-units N` | EPUB plan mode only: the most units `--accumulated_num`'s token budget may put in one request. Default `32` — half the measured fault-emergence level (first content faults at 64 effective units, September 2026, 923 requests over four books). An endpoint that verifies JSON mode but not a strict schema carries half this many (16), where reply miscounts actually live. Lower it if the run keeps printing misalignment recoveries. |
| `--batch_size N` | Aggregated unit count for loaders that support it. |
| `--block_size N` | Merge paragraphs into delimiter-translated blocks. |
| `--sentence_mode` | Translate EPUB paragraphs sentence by sentence; incompatible with plan mode. |
| `--parallel-workers N` | Parallel EPUB chapters or Markdown batches/sections; default `1`. Refused with `--use_context session` (one history) and on the `codex` format (one thread). |
| `--batch` | Submit a ChatGPT Batch API job. Refused on EPUB (the queue path is unreachable there: the run would translate live at full price and submit an empty job instead of writing the book) and on routes without the Batch API. |
| `--batch-use` | Consume a previously submitted batch job. Refused on EPUB, like `--batch`. |
| `--extra_body JSON` | Extra fields on every request body, for the routes that build one (`openai`, `groq`, `xai`, `litellm`, `--model orcarouter`, `anthropic`); the others ignore it and say so. Reaches the capability probe and the JSON rungs too, so the endpoint is graded on the request the run makes. Merged over the named parameters, so a field here beats the flag for it. |
| `--extra_headers JSON` | Extra HTTP headers on every request, same routes. Set on the client, so the capability probe, the model check and the model listing carry them. Values must be strings. |
| `--quiet` | Suppress EPUB progress bars and paragraph echoes, not reports/errors. |
| `--proxy URL` | Set HTTP/HTTPS proxy environment variables for the run. |

### Endpoint and credentials

A route is an endpoint, not a model name.

| Option | Purpose |
|---|---|
| `--model MODEL` | The model id, exactly as the endpoint names it (`gpt-5-mini`, `claude-sonnet-4-6`, `openai/gpt-5-mini`). Defaults to `gpt-5.6-luna` on the `openai` format; the `anthropic` format needs one. Old alias values are rewritten with a note. |
| `--api_base URL` | The endpoint. Defaults to the format's official host. A pasted `…/v1/chat/completions` or a trailing slash is trimmed. |
| `--key KEY` | API key; comma-separate several to rotate them. Prefer `BBM_API_KEY` or the format's own variable. |
| `--api_format FORMAT` | The API the endpoint speaks: `openai` (default), `anthropic`, `gemini`, `qwen`, `groq`, `xai`, `litellm`, `codex`, `google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi`. Inferred from the `--api_base` host, else from a `claude`/`anthropic` model id — the vendor formats are never inferred, so they are named. |
| `--api_format gemini` \| `qwen` | Google's and Alibaba's own protocols, each with its own translator: Gemini's native constrained decoding and chat history, and Qwen-MT's language-pair request. Defaults `gemini-flash-latest` and `qwen-mt-turbo`. |
| `--api_format groq` \| `xai` \| `litellm` | The OpenAI shape at Groq, xAI and a LiteLLM proxy (`http://localhost:4000`). Each carries its address, so the format and a key are the whole route. `--model` is required: those catalogues turn over, so none is assumed. |
| `--model codex` | The Codex CLI sidecar on a ChatGPT plan, the same as `--api_format codex`. It runs `gpt-5.6-luna`; `--api_format codex --model <id>` names another (the sidecar also offers `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.5`, `gpt-5.2`). |
| `--model orcarouter` | The OrcaRouter gateway and its smart-routing model `orcarouter/auto`. Needs no `--api_base`; one you pass wins. The key comes from `BBM_ORCAROUTER_API_KEY`. Not a legacy alias: nothing is rewritten. |
| `--model_list IDS` | Several model ids to rotate across, comma-separated. A single model belongs in `--model`; naming a model in both flags is an error. Refused with `--use_context session`: rotation makes every request a full-price cache miss and mixes models in one conversation. |
| `--source_lang LANG` | Source language, for the routes that want it stated (`qwen`, `customapi`); default `auto`. |
| `--interval SECONDS` | Pause between requests, default `0.01`. Only the `gemini` route paces itself with it. |
| `--provider NAME` | A named endpoint from `bbm_providers.json` (this directory) or `~/.bbm/providers.json`; the project file wins on a shared name, and a name in neither falls back to the shipped `bbm_providers.example.json`, with a warning naming the address and key variable it used (its `FILL-ME` templates excluded). Its `base_url`, `api_style` (`openai`, `anthropic`, `gemini`, `qwen`, `groq`, `xai` or `litellm`), `default_models` and `env_key` stand in for `--api_base`, `--api_format`, `--model`/`--model_list` and the key. Flags you pass yourself win. |

A gateway that serves Claude models speaks the OpenAI shape, and a gateway
`--api_base` is taken to be that shape. The anthropic format is inferred only
on Anthropic's own host, or from a `claude` model id with no `--api_base`. A
gateway asked for the anthropic shape it does not serve answers 404, and the
run stops naming `--api_format openai` as the fix.

Key lookup order: `--key` (`--api_key` is the same flag), then `BBM_API_KEY`,
then the format's own: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`BBM_GOOGLE_GEMINI_KEY`, `BBM_QWEN_API_KEY`, `BBM_GROQ_API_KEY`,
`BBM_XAI_API_KEY`, `BBM_CAIYUN_API_KEY`, `BBM_DEEPL_API_KEY` (each with the
vendor's own conventional variable after it). Endpoints on localhost need no
key.

The old `--model` preset names, the per-vendor `--*_key` flags,
`--ollama_model` and `--deployment_id` are no longer in the parser, but old
command lines still run: `book_maker/legacy_cli.py` rewrites
them into the flags above before the run starts and prints each rewrite. The
table is in [Migrating from the old flags](migration.md). The old
per-vendor key variables (`BBM_GROQ_API_KEY`, `BBM_GOOGLE_GEMINI_KEY`, …) are
still read for the route that used them.

Do not put secrets directly on a shared command line. Environment variables are safer for
agent and CI use. The CLI does **not** load `.env` files itself: export the variables first,
or source a local git-ignored file before running, for example
`set -a; source .env; set +a; bbook_maker ...`.

## Test translate
`--test` <br>

Use this option to preview the result if you haven't paid for the service or just want to test. Note that there is a limit and it may take some time.

```sh
bbook_maker --book_name test_books/Lex_Fridman_episode_322.srt --key ${openai_key} --model gpt-5-mini  --test
```

```sh
bbook_maker --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini  --test --language zh-hans
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

    bbook_maker --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'
**Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**

## Microsoft Azure Endpoints

Azure has no flag of its own. Point `--api_base` at the deployment's
OpenAI-compatible URL and name the deployment in `--model`:

    bbook_maker --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'

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
