# Translating with an LLM

If you have an OpenAI key, the default route needs nothing else:

```bash
export OPENAI_API_KEY=sk-...
bbook_maker --book_name my_book.epub --use_context session
```

This page covers the flags that decide *where* the requests go and *what* they ask for. They work the same for every input format. What each format adds is on the [Formats](formats/epub.md) pages.

## The route: model, endpoint, format

A route is an endpoint, not a model name. Three flags name it.

| flag | what it does |
|---|---|
| `--model` (`-m`) | The model id, exactly as the endpoint names it: `gpt-5-mini`, `claude-sonnet-4-6`, `openai/gpt-5-mini`. Default `gpt-5.6-luna` on the `openai` format. The `anthropic` format needs one. |
| `--api_base` | The endpoint URL. Defaults to the format's official host. An OpenAI-shaped endpoint ends in `/v1`. A pasted `…/v1/chat/completions` or a trailing slash is trimmed. |
| `--api_format` | The API the endpoint speaks: `openai`, `anthropic`, `codex`, `gemini`, `qwen`, `groq`, `xai`, `litellm`, or a [machine-translation](machine-args.md) engine. Inferred from `--api_base` when you leave it out: an Anthropic host means `anthropic`, everything else `openai`. A `claude-*` model id with no `--api_base` also selects `anthropic`. |

Examples, one per common case:

=== "OpenAI"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --model gpt-5.6-luna \
      --use_context session
    ```

=== "Anthropic"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --model claude-sonnet-4-6 \
      --use_context session
    ```

=== "Any OpenAI-compatible endpoint"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_base https://gateway.example.com/v1 \
      --model provider/model-id \
      --use_context session
    ```

=== "Gemini"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format gemini \
      --model gemini-flash-latest \
      --use_context
    ```

    Gemini keeps its own chat history, so it takes window mode (`--use_context`), not a session. `--interval` sets the pause between requests; this is how you stay under the free tier's rate limit.

=== "Codex (ChatGPT plan)"

    ```bash
    python make_book.py \
      --book_name my_book.epub \
      --api_format codex
    ```

    Spends your ChatGPT plan through the [Codex CLI](https://developers.openai.com/codex/cli), which must be installed and signed in (`codex login`). It runs `gpt-5.6-luna`; add `--model <id>` for another. The thread is the context, so no `--use_context` is needed. It ignores `--api_base` and `--key`. When Codex answers that the selected model is at capacity, the run prints a `codex: … retrying in 60 s` line and asks again; that answer is usually Codex rate-limiting a network it distrusts, and another network or account helps.

A gateway that serves Claude models usually speaks the OpenAI shape. If a gateway answers 404 to the anthropic shape, the run stops and names `--api_format openai` as the fix.

`--api_format groq`, `xai` and `litellm` are the OpenAI shape at Groq, xAI and a LiteLLM proxy (`http://localhost:4000`). Each knows its address, so the format, a key and `--model` are the whole route. `--model` is required there.

`--model orcarouter` sends the run to the OrcaRouter gateway and its `orcarouter/auto` model. The key comes from `--key` or `BBM_ORCAROUTER_API_KEY`.

## One command per vendor

If your vendor is listed here, this is the whole command (the key can also come from the environment, see [Keys](#keys)):

```bash
# Claude: a claude-* model id selects the anthropic format on its own
bbook_maker --book_name my_book.epub --model claude-sonnet-4-6 --key ${claude_key} --use_context session
# Gemini, over the Gemini API; --interval paces the free tier
bbook_maker --book_name my_book.epub --api_format gemini --key ${gemini_key} --model gemini-flash-latest --use_context
# Qwen-MT on DashScope: a translation model with a source/target pair
bbook_maker --book_name my_book.epub --api_format qwen --key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
# xAI
bbook_maker --book_name my_book.epub --api_format xai --key ${xai_key} --model grok-4.3 --use_context session
# Groq: --model is required, pick a current id from console.groq.com/docs/models
bbook_maker --book_name my_book.epub --api_format groq --key ${groq_key} --model llama-3.3-70b-versatile --use_context session
# a LiteLLM proxy on this machine: --model is the name its config gives a backend
bbook_maker --book_name my_book.epub --api_format litellm --model ${name_in_your_litellm_config} --use_context session
# OrcaRouter's smart routing (orcarouter/auto); --provider orcarouter --model <id> pins one model
bbook_maker --book_name my_book.epub --model orcarouter --key ${orcarouter_key} --use_context session
# Ollama; point --api_base at another host if the server is not local
bbook_maker --book_name my_book.epub --api_base http://localhost:11434/v1 --model ${ollama_model_name} --use_context session
# any other OpenAI-compatible endpoint: base URL, key, and the model id it uses
bbook_maker --book_name my_book.epub --api_base "https://api.lingyiwanwu.com/v1" --key ${key} --model yi-34b-chat-0205 --use_context session
# Azure OpenAI: the deployment's OpenAI-compatible URL, the deployment name as the model
bbook_maker --book_name my_book.epub --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --key ${azure_key} --model 'deployment-name' --use_context session
# your ChatGPT plan through the Codex CLI (codex login first)
bbook_maker --book_name my_book.epub --api_format codex --language zh-hans
```

Machine-translation services (Google, DeepL, Caiyun, Tencent, a custom API) are on [Machine translation](machine-args.md). A vendor you use often belongs in the [provider file](providers.md).

## Keys

The key is looked up in this order:

1. `--key` (`--api_key` is the same flag). Several comma-separated keys are rotated, to go past per-key rate limits.
2. `BBM_API_KEY`.
3. The format's own variable: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `BBM_GOOGLE_GEMINI_KEY`, `BBM_QWEN_API_KEY`, `BBM_GROQ_API_KEY`, `BBM_XAI_API_KEY`.

An endpoint on localhost needs no key. A key on the command line is visible to other users of the machine in the process list; prefer the environment. The tool does not read `.env` files itself. Load one first:

```bash
set -a; source .env; set +a
```

More variables are on [Environment variables](env_settings.md).

## The provider file and extra models

`--provider NAME` takes the route from a JSON file instead of flags: `bbm_providers.json` in the directory you run from, or `~/.bbm/providers.json`. A name in neither falls back to the shipped `bbm_providers.example.json`, and the run says so.

```bash
bbook_maker \
  --book_name my_book.epub \
  --provider siliconflow \
  --use_context session
```

Two steps can use a model other than the translating one:

| flag | what it does |
|---|---|
| `--classify-model MODEL` | The model that decides an EPUB's plan, or a Jev-compatible classifier (`jev` for TypeSafe's). Default: the entry's `classify_model`, else the translating model. `--plan-classify-model` is its old name. |
| `--classify-base-url URL`, `--classify-key KEY` | Where that model is served and its key, when it is not the run's endpoint: an OpenAI-compatible address, or a Jev-compatible classifier's URL. |
| `--img-model MODEL` | A vision model for the steps that look at a page image (today: correcting region roles on the [PDF route](features/pdf-to-epub.md)). Default: the entry's `img_model`, else off. Never the translating model by fallback. `none` turns an entry's image model off. |
| `--img-base-url URL`, `--img-key KEY` | Where that model is served and its key (OpenAI-compatible only). |

The fields, the shipped entries, which key is sent where, and Jev and Jev-compatible classifiers are on [Provider file and extra models](providers.md). Note that the shipped `openai` entry names an image model, so `--provider openai` turns the PDF route's image step on.

## Language

| flag | what it does |
|---|---|
| `--language` | The target language. A tag (`zh-hant`), a name (`"Traditional Chinese"`), or `TAG:NAME` when the tables miss the language (`--language "zh-hant:Traditional Chinese"`). The tag is stamped on the output; the name is what the model is asked for. Default `zh-hans`. The full list is on [Language tags](languages.md). |
| `--source_lang` | The source language, stated rather than detected. Named in the prompt on every LLM route. Default: detect. |

## The prompt

`--prompt` takes a template string, a JSON string, or a `.json`, `.txt` or `.md` file. It has three sections:

- `user`: the template. Required. It must contain `{text}`; `{language}` and `{crlf}` are filled in too. Any other placeholder is refused when the run starts.
- `system`: the system message.
- `style`: a note on register and voice. It is said once where a context window starts, and handed on to every window in session mode.

A bare string or a `.txt` file is the `user` template. The run prints which sections it used and where. The format is on [Prompt files](prompt.md).

```bash
bbook_maker \
  --book_name my_book.epub \
  --prompt prompt_template.json \
  --use_context session
```

## Test runs and resuming

| flag | what it does |
|---|---|
| `--test` | Translate only the first paragraphs, to check the setup. |
| `--test_num N` | How many (default 10). |
| `--resume` | Continue an interrupted run from its checkpoint. An EPUB checkpoint records the language, prompt and model; a mismatch stops the resume. |

Always run `--test` once on a new endpoint. It costs almost nothing and shows you the output format and the route the run chose.

## Retries: what "patient" means

Many people run this tool against providers that are slow or rate-limited, where the first token or a 429 backoff can take minutes. So the tool never gives up on an error that could clear by waiting.

- A timeout, a dropped connection, a 429 or a 5xx is retried with no attempt limit. The wait starts at 1 second, doubles, and stops growing at 5 minutes.
- Every retry prints one line, so a long wait does not look like a hang:
  `retrying after RateLimitError (…) — attempt 4, waiting 8s`
- An error that will not clear by waiting stops the run at once: a rejected key, a permission error, a request the endpoint refuses, a model that does not exist.
- Ctrl+C always stops the run, even during a long wait. The progress is saved; rerun with `--resume`.

If you see the same retry line for a long time, the provider is down or rate-limiting you. The run will continue by itself when it comes back.

## On-device models (Ollama, llama.cpp, LM Studio)

A local model is an OpenAI-compatible endpoint on your own machine. Point `--api_base` at it; no key is needed.

```bash
bbook_maker \
  --book_name my_book.epub \
  --api_base http://localhost:11434/v1 \
  --model qwen3:8b \
  --use_context session
```

What to expect from an 8B or 16B model:

- **No strict JSON schema.** Most local servers do not enforce one. The run notices this with a one-request probe and switches to a delimiter format, printing a line such as `doesn't apply JSON schema … using delimiter method`. This is not an error.
- **Smaller requests.** On an endpoint without a strict schema, each request carries at most 8 units, half of the default 16, and outside session mode about 800 tokens of text. The run prints the numbers it chose. The defaults are chosen so that a small model can hold them.
- **Plan classification by one word.** Plan mode asks the model `skip` or `translate` for five kinds of block per turn. If the model misses the reply format twice in a row, the run asks three per turn, then one. If it still misses, classification stops and the rest is translated. Anything unreadable is treated as `translate`, so a weak model translates too much rather than too little. You can also hand classification to a hosted model with `--classify-model` and keep translating locally; see [Provider file and extra models](providers.md#examples).
- **Short context.** Set `--context-compact-at` to the model's input limit (minimum 1500) so a session window never overflows it.
- **Thinking models.** A model that reasons before it answers can be told not to with `--no-thinking`. The run finds the request field the server accepts from the server's own rejections. If your server needs a field of its own, `--extra_body` wins, for example `--extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'`.
- **No image step.** The PDF route's image step runs only on a model you name with `--img-model`, so a local model is never asked to read a page.

Flags to avoid on a small model:

- `--glossary-auto on`. It needs a model that answers with names, not prose.
- Raising `--accumulated_num` or `--max-batch-units`. Lower them instead when the run prints `misaligned batches this run`.
- `--model_list`. Each model keeps its own cache, and mixing models in one conversation hurts consistency.

If plan classification keeps failing on your model, `--plan-classify all` skips it and translates every block. See [Plan mode](features/plan-mode.md).

## Other request options

| flag | what it does |
|---|---|
| `--temperature` | Sampling temperature. The anthropic format always sends it; the openai format leaves it out when it equals the API default or when the model rejects one; codex ignores it. |
| `--no-thinking` | Ask the model not to reason before answering; thinking buys nothing on a paragraph and costs tokens and time. On the OpenAI-shaped routes the request field is found from the endpoint's own rejections and remembered; if the endpoint refuses every spelling, the run warns once and goes on without it. On anthropic it is `thinking: {"type": "disabled"}`. Refused on codex. Image requests carry it too. |
| `--extra_body JSON` | Extra fields on every request body (openai and anthropic routes). A field here beats the flag for it, `--no-thinking` included. |
| `--extra_headers JSON` | Extra HTTP headers on every request (openai and anthropic routes). |
| `--model_list IDS` | Several models to rotate across, to spread rate limits. Refused with `--use_context session`. |
| `--interval SECONDS` | Pause between requests. Only the gemini format uses it. |
| `-p`, `--proxy URL` | An HTTP proxy for the run, for example `http://127.0.0.1:7890`. |
| `--batch`, `--batch-use` | OpenAI's Batch API: submit a job, then build the book from it later. Not on EPUB. |

Common `--extra_body` and `--extra_headers` values:

```bash
# openai route: turn off a local/vLLM chat template's thinking block
--extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
# openai route (chat completions): reasoning effort and a token ceiling, neither of which has a flag
--extra_body '{"reasoning_effort": "low", "max_completion_tokens": 2000}'
# anthropic route: keep extended thinking off
--extra_body '{"thinking": {"type": "disabled"}}'
# OpenRouter attribution, shown on its dashboard
--extra_headers '{"HTTP-Referer": "https://example.com", "X-Title": "bilingual_book_maker"}'
# a gateway's own auth or routing header
--extra_headers '{"X-API-Key": "sk-gateway-..."}'
```
