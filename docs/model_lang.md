# Models and languages

`book_maker/translator/__init__.py::MODEL_DICT` and `bbook_maker --help` are the source of
truth for built-in `--model` choices. The list changes as providers add and retire models;
do not infer a valid CLI value from a marketing model name.

## Model routing

`--model` selects a built-in translator route. It is mutually exclusive with `--provider`.

### OpenAI-compatible routes

| `--model` value | Behavior |
|---|---|
| `chatgptapi` | Default GPT-3.5-family preset/discovery route. |
| `gpt4` | GPT-4 preset/discovery route. |
| `gpt4omini` | GPT-4o-mini preset. |
| `gpt4o` | GPT-4o preset. |
| `gpt5mini` | GPT-5-mini preset. |
| `o1preview`, `o1`, `o1mini`, `o3mini` | Matching reasoning-model presets. |
| `openai` | Arbitrary OpenAI-compatible model IDs; requires `--model_list`. |

Use `--openai_key` or, preferably, `BBM_OPENAI_API_KEY`. `OPENAI_API_KEY` remains supported
for backward compatibility.

```sh
bbook_maker --book_name book.epub --model openai \
  --model_list gpt-4.1-mini --language ja
```

An OpenAI-compatible gateway can be selected with `--api_base`. The endpoint still has to
serve the OpenAI chat-completions request shape.

### Anthropic Claude

`claude` selects the translator's default Claude model. Exact built-in Claude IDs are also
accepted, including the Claude 4/4.1/4.5/4.6 entries shown by `bbook_maker --help`.
Because argparse validates `--model`, an arbitrary `claude-*` string is **not** accepted
unless it is in that displayed list. Use `BBM_CLAUDE_API_KEY` (or `--claude_key`).

```sh
bbook_maker --book_name book.epub --model claude-sonnet-4-6 --language zh-hans
```

For an unlisted Claude ID on a gateway, use `--provider` or an OpenAI-compatible gateway
with `--model openai --model_list ...`.

### Gemini

- `--model gemini`: Gemini Flash route; accepts an exact comma-separated `--model_list`.
- `--model geminipro`: Gemini Pro preset.
- `--interval`: Gemini request interval in seconds.

Use `BBM_GOOGLE_GEMINI_KEY` (or `--gemini_key`).

### Qwen-MT

- `--model qwen` defaults to `qwen-mt-turbo`.
- `--model qwen-mt-turbo` selects the faster/cheaper MT model.
- `--model qwen-mt-plus` selects the higher-quality MT model.
- `--source_lang` sets the source language; its default is `auto`.

Use `BBM_QWEN_API_KEY` (or `--qwen_key`).

### Other built-in routes

| `--model` value | Credential |
|---|---|
| `groq` | `BBM_GROQ_API_KEY`; requires `--model_list`. |
| `xai` | `BBM_XAI_API_KEY`. |
| `google` | No API key. |
| `caiyun` | `BBM_CAIYUN_API_KEY`. |
| `deepl` | `BBM_DEEPL_API_KEY`. |
| `deeplfree` | No API key. |
| `tencentransmart` | No API key. |
| `customapi` | `BBM_CUSTOM_API` (legacy custom translator). |

## Custom providers

Use `--provider NAME` for a provider declared in project-level
`./bbm_providers.json` or global `~/.bbm/providers.json`. A provider may use the `openai`,
`claude`, `gemini`, or `qwen` API style and can supply a base URL, default model IDs, and
the name of its key environment variable.

```sh
bbook_maker --book_name book.epub --provider deepseek \
  --model_list deepseek-chat --language zh-hans
```

Prefer the provider's configured environment variable. `--api_key` works but exposes a
secret in shell history and process listings.

## Ollama

`--ollama_model MODEL` uses the local OpenAI-compatible Ollama endpoint. The default base
is `http://localhost:11434/v1`; override it with `--api_base` for a remote server.

## Languages

`--language LANGUAGE` sets the target language and defaults to `zh-hans`. The accepted
choices are generated from `book_maker/utils.py`; run the installed CLI to see the current
list:

```sh
bbook_maker --help
bbook_maker --book_name book.epub --model google --language ja
```

Not every provider supports every language accepted by the common parser. Provider-specific
translators may map or reject unsupported source/target combinations.
