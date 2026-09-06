# Endpoints, models and languages

A route is chosen by the endpoint it talks to, not by a model name. There is
no built-in model list to keep up to date: `--model` takes whatever id
your endpoint serves.

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.openai.com/v1 --key sk-... \
  --model gpt-5-mini --language ja
```

Old `--model` commands still work — they are rewritten into these flags and
the substitution is printed. [Migrating from the old flags](migration.md)
has the full table.

## The route flags

| Flag | Meaning |
|---|---|
| `--model` | Model id, exactly as the endpoint names it. On the `openai` format it may be left out: it defaults to `gpt-5.6-luna`. |
| `--api_base` | Endpoint URL. Defaults to the format's official host; `…/v1`, `…/v1/` and `…/v1/chat/completions` all work. |
| `--key` | API key. Comma-separate several to rotate them and spread rate limits. |
| `--api_format` | Wire format. Inferred; pass it only when the guess is wrong. |
| `--provider` | A named endpoint from a provider file, standing in for the four above. |

`--model_list a,b` rotates across several models and is also what older
commands used; name a model in one flag or the other, not both.

`--api_format` is one of `openai` (default), `anthropic`, `gemini`, `qwen`,
`groq`, `xai`, `litellm`, `codex`, or the fixed machine-translation engines
`google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi`.

The five vendor formats each carry their own endpoint, so the format and a
key are a whole route with no `--api_base` to look up:

| Format | Endpoint | `--model` |
|---|---|---|
| `gemini` | the Gemini API | optional, default `gemini-flash-latest` |
| `qwen` | Qwen-MT on DashScope | optional, default `qwen-mt-turbo` |
| `groq` | `https://api.groq.com/openai/v1` | required |
| `xai` | `https://api.x.ai/v1` | required |
| `litellm` | `http://localhost:4000` | required, the name in the proxy's config |

`gemini` and `qwen` are protocols of their own — Gemini's native
constrained decoding, its safety settings and chat history; Qwen-MT's
source/target language pair in place of a prompt — and `--interval` paces
the gemini route, which is how the free tier's rate limit is stayed under.
The other three are the OpenAI route at another address and keep everything
it has. None of the five is ever inferred from a host: name the format, or
give the vendor's OpenAI-compatible `--api_base` and get the `openai` route.

`codex` is not an endpoint at all: it drives a local `codex app-server`
sidecar and bills the run to your ChatGPT plan, so it takes no `--key` and no
`--api_base`, and `--model` is optional (default `gpt-5.6-luna`). It is never
inferred; name it explicitly. See the Codex entry under "Translate Service"
in the README.

Inference goes in this order: an explicit `--api_format` wins; then the
`--api_base` host (`anthropic.com` means the anthropic shape, anything else
the OpenAI shape); then, when no endpoint was named, a model id mentioning
`claude` or `anthropic`, `anthropic/claude-sonnet-4-6` included.

A gateway asked for the anthropic shape it does not serve answers 404 or
405 on `/v1/messages`. The run stops and names the fix: rerun with
`--api_format openai`. On Anthropic's own host a 404 means the model does
not exist and is reported as such.

Credentials come from `--key`, then `BBM_API_KEY`, then the format's
conventional variable — see [Environment settings](./env_settings.md). An
endpoint on localhost needs no key.

## Named endpoints: `--provider`

An endpoint used more than once can be written down instead of retyped.
`bbm_providers.json` in the working directory is read first, then
`~/.bbm/providers.json`; a project entry wins on a shared name.

```json
{
  "nvidia": {
    "api_style": "openai",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "default_models": ["moonshotai/kimi-k2-thinking"],
    "env_key": "NVIDIA_API_KEY"
  }
}
```

```sh
bbook_maker --book_name book.epub --provider nvidia --language ja
```

`api_style` is any `--api_format` that names an endpoint — `openai`,
`anthropic`, `gemini`, `qwen`, `groq`, `xai`, `litellm` — plus `claude` as
the older spelling of `anthropic`. Any other OpenAI-compatible host is
`openai` with its address in `base_url`. The shipped
`bbm_providers.example.json` has an entry for each. Explicit flags still win over the entry, and the entry's `env_key` travels
exactly as far as the entry's address does. Asking for another wire format
at the entry's own gateway moves nothing, so the key is still read. An
`--api_base` pointing somewhere else does move the run, and so does an
`--api_format` override on an entry with no `base_url`, since there the
format is what supplies the address — the key is not read for another
vendor's host, and the run says which two addresses it is comparing. Pass
`--key` when you meant to reuse it. `default_models` supplies `--model` when it
holds one id and `--model_list` when it holds several, and `env_key` names
the variable to read the key from; no secret goes in the file. Anything
passed explicitly wins, so `--provider nvidia --model <id>` keeps that
model. An unknown name is an error naming both files.

## OrcaRouter

```sh
bbook_maker --book_name book.epub --model orcarouter --language ja
```

`--model orcarouter` sends the run to the OrcaRouter gateway and asks for
its smart-routing model, `orcarouter/auto`. It needs no `--api_base`, and
one you pass yourself wins. The key is read from `BBM_ORCAROUTER_API_KEY`
before the usual fallbacks. It is a supported route, not a legacy alias, so
nothing is rewritten. To pin one model at the gateway, name the endpoint
like any other: `--api_base https://api.orcarouter.ai/v1 --model <id>`.

## OpenAI-compatible endpoints

Everything below is the same route with a different `--api_base`. Structured
output, `--use_context`, parallel workers, async and the Batch API are
available on all of them to the extent the endpoint itself supports them —
support is probed at runtime rather than assumed from the model name.

`--use_context session` also needs the endpoint to charge less for cached
prompt tokens. Watch the `cached=` count on the progress bar: still zero
after a dozen requests means the endpoint is not caching, and window mode
is cheaper.

| Vendor | `--api_base` |
|---|---|
| OpenAI | `https://api.openai.com/v1` (default) |
| Groq | `https://api.groq.com/openai/v1`, or `--api_format groq` |
| xAI | `https://api.x.ai/v1`, or `--api_format xai` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Gemini (compatibility mode) | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Alibaba Qwen (DashScope) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| SiliconFlow | `https://api.siliconflow.cn/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Azure OpenAI | the deployment's OpenAI-compatible URL; `--model` names the deployment |
| Ollama | `http://localhost:11434/v1` |
| vLLM / LM Studio / llama.cpp | whatever host they serve |

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.groq.com/openai/v1 --key gsk-... \
  --model llama-3.3-70b-versatile
```

`--extra_body` passes vendor-specific request fields on this route.

## Anthropic

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.anthropic.com --key sk-ant-... \
  --model claude-sonnet-4-6 --language zh-hans
```

Any model id the endpoint serves is accepted. Claude uses one model per run,
so extra `--model_list` entries are announced and ignored rather than
silently dropped. A gateway that serves the anthropic shape from an
OpenAI-style `/v1` base is handled: the trailing `/v1` is trimmed, because
the SDK appends its own.

Classification through this format uses the prompt rung — the endpoint is not
asked to compile a schema.

## Machine-translation engines

These speak their own protocols and take no model, so naming one is an error
rather than a silent no-op.

| `--api_format` | Credential |
|---|---|
| `google` | none |
| `deeplfree` | none |
| `tencent` | none |
| `customapi` | none; the endpoint URL goes in `--api_base` |
| `caiyun` | required |
| `deepl` | required (RapidAPI DeepL Translator) |

They translate text and nothing else: no context window, no structured
output, and no plan classification. `--source_lang` reaches `customapi`
(it goes into the request body); the other engines detect the source
themselves. (On the LLM routes the flag reaches the prompt — see
Languages below.)

## Languages

`--language LANGUAGE` sets the target language and defaults to `zh-hans`.
It takes a tag (`zh-hant`), a name (`"Traditional Chinese"`), or both at
once — `--language "zh-hant:Traditional Chinese"` — for a language the
built-in tables miss. The tag half is mechanical: stamped on the inserted
markup and `dc:language`, recorded in provenance, and it names the
structured-output field. The name half is what the model is asked for in
the prompt. A bare tag or name resolves through the tables as before; a
value matching no tag still runs, prints one `Note:` at startup, and
stamps nothing. The full table: [Language tags and names](languages.md).

```sh
bbook_maker --help
bbook_maker --book_name book.epub --api_format google --language ja
```

`--source_lang` states the source language rather than detecting it; stated,
it reaches every LLM route's prompt and the request body on
`qwen`/`customapi`. The default `auto` states nothing. Not every endpoint
supports every language the parser accepts.
