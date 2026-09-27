# Command Line Options

`book_maker/cli.py` and `python3 make_book.py --help` are the runtime source of truth.
The inventory below is checked against every long option registered with `argparse`; the
sections after it provide additional notes for selected workflows.

## Complete option inventory

### Input, scope, and output

| Option | Purpose |
|---|---|
| `--book_name PATH` | Input EPUB, TXT, Markdown, SRT, or PDF path (required). |
| `--language LANGUAGE` | Target language: a tag (`zh-hant`), a name (`"Traditional Chinese"`), or `TAG:NAME` to pin both when the tables miss the language — tag → output stamps and field names, name → the prompt. Default `zh-hans`; list in `docs/languages.md`. |
| `--source_lang LANGUAGE` | Source language. Stated, it reaches every LLM route's prompt, and the request body on `qwen`/`customapi`; default `auto` (states nothing). |
| `--single_translate` | Output translation only instead of bilingual text. |
| `--no_disclosure` | Do not mark the epub as an AI translation: leaves out the one-line credit ("Translated by \<model\>, \<year\>.") below the book intro. Turns off `--translation-metadata` too. |
| `--translation-metadata` | Write a small `bbm_translation_metadata.json` into the book: the model, the date, and the sha256 of a `--glossary` file (whose text is embedded alongside; terms a run learned by itself are not). Nothing else — no command line, endpoint, languages or paths, and no package metas: what could have shaped the translation is recorded, who ran it is not. Format conversions keep manifest files, so the record survives them. Plan-mode and session runs write it by themselves; this flag adds it to a plain tag-mode run. `--no_disclosure` turns it off too. |
| `--translate-tags TAGS` | Comma-separated EPUB tags; default `p`, ignored in plan mode. |
| `--exclude-translate-tags TAGS` | EPUB ancestor tags to exclude; default `sup,code`; `""` clears it. |
| `--allow_navigable_strings` | Include otherwise untagged EPUB strings; redundant in plan mode. |
| `--only_filelist FILES` | Include only these comma-separated internal EPUB files. |
| `--exclude_filelist FILES` | Exclude these comma-separated internal EPUB files. |
| `--translation_style CSS` | CSS applied to translated EPUB entries. |
| `--translation_color COLOR` | Color-only shorthand; `--translation_style` takes precedence. |
| `--pdf_layout MODE` | Additional PDF output: `none`, `top-bottom`, `side-by-side`, or `all`. |
| `--to-epub` | PDF only: read the PDF with docling, translate the Markdown, write `<name>_bilingual.epub` beside it; figures stay pictures; the bundle stays in `<name>_book/` for editing and resume. Needs the `pdf` extra and Pandoc 3.1.12+ on PATH; no Java. See [installation-pdf.md](installation-pdf.md). |
| `--pdf-ocr` | PDF only, with `--to-epub`: read pages that carry no text layer, which are refused without it. Off by default; OCR costs several times the time and changes nothing on a born-digital PDF. Layout, headings and tables are detected either way. |
| `--ocr-replace-layer` | PDF only, with `--to-epub --pdf-ocr`: OCR every page and replace the PDF's own text layer instead of keeping it. Off by default; measured worse than a sound layer, for a layer that is wrong. A page read empty stays empty and is named. |
| `--no-formula-images` | PDF only, with `--to-epub`: leave display formulas as `<!-- formula-not-decoded -->` placeholders instead of cropping each one from the page as an image. The parser finds equations but never reads them, so the pictures are the only reason the mathematics reaches the book at all; they cost no model and no measurable time. Inline mathematics inside a paragraph is not a formula region and is not covered either way. |
| `--pdf-image-dpi N` | PDF, `--to-epub`: figure sharpness in DPI of the PDF's own page size; default 200, 300 for tiny labels; a rerun at another value redraws the figures only |
| `--img-model MODEL` | PDF only, with `--to-epub`: a vision model that corrects the layout detector's region roles (text, heading, title, caption, footnote, code) from a page image before export. Off unless named here or as the provider entry's `img_model`; `none` turns that off. Never the run's own model by fallback. About 3k prompt tokens per page. |
| `--img-base-url URL` | Endpoint for `--img-model` when it is not the run's (OpenAI-compatible only). |
| `--img-key KEY` | Key for `--img-base-url`. Default: the run's key when the endpoint is the run's own; else the provider entry's `img_env_key` when the endpoint is the entry's own; else the key the endpoint's format reads from the environment. A key is never sent to an address it was not given for. |
| `--device DEVICE` | PDF only, with `--to-epub`: where the extraction models run — `auto` (default; detects CUDA or MPS, falls back to CPU), `cpu`, `cuda`, `mps`, `xpu`. CPU is fully supported and produces the same output, only slower. |
| `--ocr-lang LANGS` | PDF only, with `--to-epub --pdf-ocr`: the languages the OCR engine reads on pages with no text layer (every page with `--ocr-replace-layer`), comma separated, in the engine's own codes (rapidocr: `ch`, `en`, `latin`; easyocr: `ch_sim`, `ja`, `ko`; ocrmac: `zh-Hans`, `ja-JP`), or portable BCP-47 tags behind `iso:` (`iso:zh-Hans`, `iso:zh-Hant`, `iso:ja`, `iso:ko`), which docling 2.129 maps onto whichever engine runs. rapidocr reads one language per run and uses the first. The engine is the one docling selects on the install (with the PDF extra, ocrmac on a Mac, whose default reads English, Spanish, French and German, and rapidocr elsewhere, whose default reads Chinese and English), and the run prints the engine and languages it used. A scan in another script needs the flag; the run says so when it meets a scanned page without it. An unknown code is refused before any page is read. The codes per engine, and which engine to choose: [Which OCR engine](features/pdf-ocr-engines.md). |
| `--ocr-engine ENGINE` | PDF only, with `--to-epub --pdf-ocr`: the OCR engine for pages with no text layer (every page with `--ocr-replace-layer`). `auto` (default) takes the first installed of ocrmac, rapidocr, easyocr; the `pdf` extra installs `rapidocr` with onnxruntime, models included, and on macOS also `ocrmac` (Apple's Vision framework); neither downloads anything, so `auto` reads with ocrmac on a Mac and rapidocr elsewhere; `easyocr` downloads its models on first use (`pip install easyocr`); `tesseract` uses the tesseract program and its language data from PATH. An engine that is not installed, or `ocrmac` off macOS, is refused before any page is read. Language codes differ by engine (`--ocr-lang`); the run names the engine it used. Which to choose, measured: [Which OCR engine](features/pdf-ocr-engines.md). |
| `--pages PAGES` | PDF only, with `--to-epub`: the pages to read, numbered from 1 (`12-30`, `1,3,5-7`); the rest is left out. The selection goes into the bundle and book names (`<name>_pages-12-30_…`), so a chapter never overwrites the whole book. A selection that starts inside a section gets a `Page N` heading above its first prose, in `source.md`. |
| `--retranslate OUT FILE START END` | Retranslate an EPUB range in an existing output. EPUB only — refused elsewhere. |

### EPUB plan mode

| Option | Purpose |
|---|---|
| `--plan-dry-run` | Build and print the EPUB plan, write `<book>_plan.json` with every `action` still `null`, and exit. No credentials needed. |
| `--plan-classify {auto,none,all,model,agent}` | No plan, the whole partition, model triage, or coding-agent triage. Default `auto`: model triage on any epub endpoint that can answer — over structured output where a strict JSON schema is verified, over a plain conversation (exact `skip`/`translate` replies; anything else translates) elsewhere, codex included; tag mode only where no conversation exists. |
| `--classify-model MODEL` | Model for every classification step (old name `--plan-classify-model`), or a Jev-compatible classifier (TypeSafe's Jev by default; Simple Jev at its URL); implies model mode on an epub. Asked over a JSON schema where its endpoint verifies one, else over a plain conversation. Default: the provider entry's `classify_model`, else the run's model. |
| `--classify-base-url URL` | Endpoint for `--classify-model` when it is not the run's: an OpenAI-compatible endpoint, or a Jev-compatible classifier's URL (a path ending in `/systemone` or `/classifier` is used as is). |
| `--classify-key KEY` | Key for `--classify-base-url`; same default rule as `--img-key`. A Jev host's own variable (`JEV_API_KEY`/`TYPESAFE_API_KEY` at typesafe.ai, `FEATHERLESS_API_KEY` at featherless.ai) is read implicitly only at that host; a provider entry may still bind a variable to its own address. |
| `--classify-min-confidence P` | Confidence gate for a Jev-compatible classifier, 0 to 1: a `skip` whose probability is below it becomes `translate`; `translate` is never gated. Default `0.95`, measured; below `0.5` the gate is off. `BBM_JEV_MIN_CONFIDENCE` sets it without the flag. |
| `--plan-min-coverage FRACTION` | Fail if selected planned text is below this fraction; default `0.5`, must be between 0 and 1 (`0` disables the guard, values above `0.9` usually abort — both warn). |
| `--poetry-group-size N` | Deprecated — general grouping and the session handoff give short lines their neighbours now, and the units cap is `--max-batch-units`. Still works (default `8`, minimum `1`) but warns. |

### Translation and execution

| Option | Purpose |
|---|---|
| `--test` | Translate only a preview sample. |
| `--test_num N` | Number of test units; default `10`. |
| `--resume` | Continue from the loader's saved checkpoint. An EPUB checkpoint records the run's language, prompt and model; a mismatch stops the resume (older checkpoints warn once and continue). Refused together with `--parallel-workers` and `--accumulated_num` above 1, where no checkpoint is ever written. |
| `--prompt VALUE_OR_FILE` | Prompt config: `user` (must contain `{text}`), `system`, and `style`. A `style` is a standing instruction said once where a window starts (the system message on the API routes, the thread instructions on codex), never repeated per request; a user-written style replaces the model's own style notes in handoff reports. A section with no native slot on a route (`system` on the `codex` format) is appended to the user message instead of dropped; a run with `--prompt` prints which sections it adopted and where. Sample: `prompt_template.json` (`style` shipped empty). |
| `--temperature FLOAT` | Sampling temperature, on the formats that take one; default `1.0`. The anthropic format always sends it. The openai format leaves it out, and the API default applies, when it equals that default and when the model rejects an explicit one (gpt-5.x, the o-series). The codex format has no such setting and ignores it. |
| `--use_context [window\|session]` | Send earlier paragraphs as context. Bare or `window`: re-send the last few source/translation pairs (the long-standing behaviour). `session`: one append-only history, re-read at the endpoint's prompt-cache rate. |
| `--context_paragraph_limit N` | Window mode only: context history limit. Parser default `0` means the translator default (3 paragraphs for ChatGPT), not zero history. |
| `--context-compact-at N` | Estimated-token budget for a rolling history. In session mode the history is compacted into a handoff report at this size; minimum `1500`. When unset, every session run — grouped or not, the `codex` format included — compacts at `8192`, printed at start. That default is chosen for continuity (the fewest window seams), not for price: a lower value is cheaper in session mode, because each request re-reads the carried history, so lower it if session cost matters more to you than seams, or set it to your model's input limit when that is smaller. Past about `16000` the cost climbs steeply. The measurements are on [Why the session compacts at 8192](evaluation/session-compact-budget.md). Also bounds the plan classifier's conversation on endpoints that classify over a plain session (restart there, no handoff), with or without `--use_context`. An explicit value always wins. |
| `--no-context-compact` | Session mode only: skip the handoff report. The window still rolls over at the budget, but the next one starts empty. |
| `--glossary FILE` / `--terminology FILE` | A file of `term → translation` lines (one per line; `#` starts a note or a comment) this run must render that way. Two names for one flag. Only the terms that occur in a request are sent with it. A missing file stops the run at parse time. Read by the openai- and codex-shaped routes for EPUB, Markdown, and PDF books; other routes warn and ignore it. |
| `--glossary-auto on\|off` | Whether a session run also keeps the renderings its own handoff reports establish. Off unless you ask for it. It needs a session to learn from (`--use_context session`, or the `codex` format's one thread) and a model that answers with names rather than prose; `off` asks the compact turn for a summary only. Learned terms stay in this run and in `<book>_handoff.md`, and nowhere else. |
| `--accumulated_num N` | EPUB token/character accumulation and SRT subtitle-block character batching (capped at 512 for SRT). In EPUB plan mode it is a per-request token budget: consecutive units of any length share one request up to `N` tokens (at most `--max-batch-units` units per request; half that when the endpoint verifies JSON mode but not a strict schema). Untyped, every plan run derives a default from the run's own prompt overhead — `1200` with the stock prompts, up to `1600` under a long custom `--prompt` — halved per request on an endpoint without a strict-schema verdict, but never below the `800` floor (so the per-request budget there is `800` whatever the prompt costs); session runs (`codex` included) keep the un-halved value. These are chosen safety margins, below the range any measurement found fault-free; see [Why 16 units per request](evaluation/grouping-batch-size.md). The run narrates the number and the route class. Pass `1` to turn grouping off. Minimum `1`. |
| `--max-batch-units N` | EPUB plan mode only: the most units `--accumulated_num`'s token budget may put in one request. Default `16`, a quarter of the level where content faults first appeared (64 units per request), as a safety margin. An endpoint that verifies JSON mode but not a strict schema carries half this many (`8`), where reply miscounts actually live. Raise it if you trust your model and want fewer requests; lower it if the run keeps printing misalignment recoveries. |
| `--batch_size N` | Lines or paragraphs sent in one request by the TXT, Markdown and PDF (text route) loaders. Default `10`. |
| `--block_size N` | Merge paragraphs into delimiter-translated blocks. |
| `--sentence_mode` | Translate EPUB paragraphs sentence by sentence; incompatible with plan mode. |
| `--parallel-workers N` | Parallel EPUB chapters or Markdown batches/sections; default `1`. Refused with `--use_context session` (one history) and on the `codex` format (one thread). |
| `--batch` | Submit a ChatGPT Batch API job. Refused on EPUB (the queue path is unreachable there: the run would translate live at full price and submit an empty job instead of writing the book) and on routes without the Batch API. The TXT, SRT and Markdown loaders do not implement it either (a run translates live), so no format uses it at present. |
| `--batch-use` | Consume a previously submitted batch job. Refused on EPUB, like `--batch`. |
| `--no-thinking` | Ask the model not to reason before answering. On the OpenAI-shaped routes the request field is negotiated from the endpoint's own rejections and cached per endpoint and model; if every spelling is refused the run warns once and continues without one. On `anthropic` it is `thinking: {"type": "disabled"}`. Refused on `codex` (a subprocess has no request body); warned as inert on the routes that build their own request. A field set in `--extra_body` wins. |
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
| `--source_lang LANG` | Source language. Stated, it reaches every LLM route's prompt as evidence, and the request itself on `qwen`/`customapi`; default `auto`. |
| `--interval SECONDS` | Pause between requests, default `0.01`. Only the `gemini` route paces itself with it. |
| `--provider NAME` | A named endpoint from `bbm_providers.json` (this directory) or `~/.bbm/providers.json`; the project file wins on a shared name, and a name in neither falls back to the shipped `bbm_providers.example.json`, with a warning naming the address and key variable it used (its `FILL-ME` templates excluded). Its `base_url`, `api_style` (`openai`, `anthropic`, `gemini`, `qwen`, `groq`, `xai` or `litellm`), `default_models` and `env_key` stand in for `--api_base`, `--api_format`, `--model`/`--model_list` and the key; its `img_model`/`img_base_url`/`img_env_key` and `classify_model`/`classify_base_url`/`classify_env_key` stand in for the the `--img-model`/`--img-base-url`/`--img-key` and `--classify-model`/`--classify-base-url`/`--classify-key` flags flags (see [Provider file and extra models](providers.md)). Flags you pass yourself win. |

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

![Two EPUB pages: on the left, with the style above, each translation sits in a gray box after its original; on the right, the default style](img/output_style.jpg)

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

## Batch size (TXT, Markdown, PDF text route)
`--batch_size`<br>

Use this parameter to specify the number of lines for batch translation. Default is 10. Read by the TXT, Markdown and PDF (text route) loaders; EPUB uses `--accumulated_num`.
```sh
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20
```

## Accumulated Num
`--accumulated_num <ACCUMULATED_NUM>`<br>

Wait for how many tokens have been accumulated before starting the translation. gpt3.5 limits the total_token to 4090. 

In EPUB plan mode you rarely need it: the run derives a per-request budget (1200 with the stock prompts, 800 on an endpoint without a strict schema) and prints it. See the `--accumulated_num` row above and [Why 16 units per request](evaluation/grouping-batch-size.md).

For example, if you use --accumulated_num 1600, maybe openai will
output 2200 tokens and maybe 200 tokens for other messages in the system messages user messages. 1600+2200+200=4000, so you are close to the limit. 

You have to choose your own
value, there is no way to tell if the limit is reached before sending request.
