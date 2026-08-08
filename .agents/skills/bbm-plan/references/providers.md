# Routes: model name → endpoint shape → flags

Loaded from SKILL.md §0/§1b. Everything here is verified against the code
(`book_maker/cli.py`, `book_maker/translator/`) and, where marked, against a
live gateway on 2026-08-07.

## The one rule that decides everything

`--model` only accepts keys of `MODEL_DICT`
(`book_maker/translator/__init__.py`) — argparse rejects anything else. So a
model id the repo has never heard of (`gpt-5.6-luna`, `claude-haiku-4.5`,
`deepseek-v4-flash`) can only arrive through a flag that carries an arbitrary
string:

| the id you have | how it reaches the translator |
|---|---|
| is a MODEL_DICT key (`claude-haiku-4-5-20251001`, `gemini`, `groq`, `qwen`) | `--model <that key>` |
| is not, and the endpoint speaks the OpenAI shape | `--model openai --model_list "$MODEL"` |
| is not, and the endpoint speaks anthropic/gemini/qwen natively | `--provider <name>` + `--model_list "$MODEL"` (below) |

**Never `--model chatgptapi` for an arbitrary id**: that preset runs a
hardcoded GPT-3.5-family discovery and ignores `--model_list`
(`cli.py:806-823`). Only `openai`, `groq`, `gemini` and `--provider` honor it;
every other `--model` value refuses the combination loudly (`cli.py:828-836`).

## Binding `$KEY` and `$ROOT` before any probe

The shape decides which key variable to read. Do **not** take "whichever
key is set" — `.env` is sourced into a shell that may already export other
providers' keys from `~/.zshenv`, and a stale one would route the run to an
endpoint the user never chose. Exit before curl when either half is
missing: an empty bearer token produces a 401 that reads like a bad key.

```bash
route_env() {   # $1 = openai | anthropic | gemini
  case "$1" in
    openai)    KEY="${OPENAI_API_KEY:-}";        DEFAULT_ROOT=https://api.openai.com ;;
    anthropic) KEY="${BBM_CLAUDE_API_KEY:-}";    DEFAULT_ROOT=https://api.anthropic.com ;;
    gemini)    KEY="${BBM_GOOGLE_GEMINI_KEY:-}"; DEFAULT_ROOT=https://generativelanguage.googleapis.com ;;
    *) echo "unknown shape $1" >&2; return 2 ;;
  esac
  ROOT="${BBM_API_BASE:-}"; ROOT="${ROOT%/}"; ROOT="${ROOT%/v1}"
  ROOT="${ROOT:-$DEFAULT_ROOT}"
  [ -n "${MODEL:-}" ] || { echo "MODEL is unset in .env" >&2; return 1; }
  [ -n "$KEY" ]       || { echo "no key set for the $1 route" >&2; return 1; }
}
```

On a gateway the key belongs to the *gateway*, not the model's vendor: a
Claude model reached over the OpenAI shape uses `OPENAI_API_KEY`, because
that is the shape being spoken. Verified in bash and zsh.

## Shapes, and how to probe each

`$ROOT` is the scheme+host with no `/v1` (`route_env` guarantees it), so
every path below is written out in full. Each probe is one tiny call, a
fraction of a cent.

**No token cap on the OpenAI shape.** `max_tokens: 1` looks thrifty and is a
false negative twice over: gateways reject caps below their own floor
(measured: `max_tokens must be greater than 2` on *every* model of one
gateway), and OpenAI's own o-series/gpt-5 models reject `max_tokens`
outright in favour of `max_completion_tokens`. A probe must test one thing.
The repo's internal probe sends no cap for exactly this reason
(`chatgptapi_translator.py:_test_structured_outputs`); match it. The reply is
a few tokens of "Hi!".

**OpenAI shape** — the universal one. Most gateways serve every model they
host on it, whoever made the model.

```bash
curl -sS "$ROOT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.choices[0]`. → `--model openai --model_list
"$MODEL" --api_base "$ROOT/v1"`

**Anthropic shape**. `max_tokens` is *mandatory* here, unlike above; 16 is
past every floor seen so far.

```bash
curl -sS "$ROOT/v1/messages" \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.content[0]`. → `--model claude-…` (a MODEL_DICT
key) or a `claude` provider, with `--api_base "$ROOT"`. The reply's `model`
field echoes the id the endpoint actually resolved to
(`claude-haiku-4.5` → `claude-haiku-4-5-20251001`), which tells you whether a
MODEL_DICT key would have reached the same model. Real Anthropic requires
`x-api-key`; gateways commonly accept `Authorization: Bearer` too, so try
`x-api-key` first and Bearer second.

**`--api_base` for this route takes the root, not `/v1`**: the SDK appends
`/v1/messages` itself. Passing `https://host/v1` used to produce
`/v1/v1/messages` and a 403 reading "HTTP node only allows access to
inference API paths"; a trailing `/v1` is now trimmed automatically (with a
printed note), so either form works — but say `https://host` and mean it.

**Gemini shape**

```bash
curl -sS "$ROOT/v1beta/models/$MODEL:generateContent" \
  -H "x-goog-api-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"contents":[{"parts":[{"text":"hi"}]}]}'
```

Passes when the body has `.candidates[0]`. → `--model gemini` (+
`--model_list "$MODEL"`, which gemini honors) or a `gemini` provider, with
`--api_base "$ROOT"`. Default root when unset:
`https://generativelanguage.googleapis.com`. `--api_base` is threaded into
the client as of 2026-08-07; before that it was silently discarded, so on an
older checkout a custom Gemini base is a no-op, not an error.

## Inferring which shape to try first, from the model name

| model name starts with | try first | then |
|---|---|---|
| `gpt-`, `o1`, `o3`, `chatgpt` | OpenAI | — |
| `claude-` | OpenAI, **if** a gateway base is set; else anthropic | the other one |
| `gemini-` | OpenAI if a gateway base is set; else gemini | the other one |
| `grok-` | OpenAI | — (`--model xai` ignores `--model_list` and pins `grok-beta`) |
| `llama`, `mixtral`, `gemma`, `qwen3`, `deepseek`, anything else | OpenAI | — |
| `qwen-mt-turbo`, `qwen-mt-plus` | `--model qwen-mt-turbo` / `-plus` — the id itself, not the bare `qwen` alias | — MT-only, see caveat below |

Why OpenAI-first whenever `BBM_API_BASE` points at a gateway: aggregators
serve Claude and Gemini models on `/chat/completions` too, and that route
accepts arbitrary ids without a provider file. Go native only when the
endpoint is the vendor's own, or when the gateway rejects the OpenAI shape.

**Verify the name before the path.** `GET $ROOT/v1/models` (Bearer auth) is
free on OpenAI-shaped endpoints and returns `{"data":[{"id":…}]}`. Check
`$MODEL` is in that list *first*: a typo'd id and an unsupported path both
return 404, and only the listing tells them apart. Some gateways add
`supported_endpoint_types` per row — measured on one aggregator, every row
read `['openai', 'anthropic']`. When that field is there it answers the
shape question outright; read it instead of guessing.

## `--provider`: a named gateway (`cli.py:502-518`, `provider_loader.py`)

For a non-OpenAI shape with a custom model id, this is the only route.
`bbm_providers.json` in the working directory, or `~/.bbm/providers.json`:

```json
{"providers": {"mygw": {
  "api_style": "claude",
  "base_url": "https://api.example.com",
  "env_key": "MY_GATEWAY_KEY",
  "default_models": ["claude-haiku-4.5"]
}}}
```

`api_style` ∈ `openai` | `claude` | `gemini` | `qwen`. Then
`--provider mygw --model_list "$MODEL"` (mutually exclusive with `--model`).
Claude and qwen gained `set_model_list` on 2026-08-07; on an older checkout
those two api_styles die on an `AttributeError` and only the `default_models`
path works.

## Capability caveats per route

| route | translation | plan-mode classification |
|---|---|---|
| openai / gpt-* / xai | schema when the probe says `strict`, else delimiter | yes |
| claude | delimiter (no structured-output work was done for it) | yes, via the prompt rung |
| gemini | native schema | yes |
| groq, litellm | delimiter | yes |
| qwen-mt-*, customapi | translation only | **no** |
| google, deepl, deeplfree, caiyun, tencentransmart | translation only | **no** |

Classification capability does not gate *this* skill — `--plan-classify
agent` makes no API call, you are the classifier. It matters only if someone
switches to `--plan-classify model`.

`qwen-mt-*` and `customapi` are dedicated translation engines: their only
channel translates whatever it is handed rather than answering it. They
translate fine and cannot be asked a question.

## What the run's own probe does later

At first paid use, the OpenAI-shaped translator sends a one-key schema probe
and grades the endpoint `strict` / `shape` / `json` / unsupported. Only
`strict` gets a schema for *translation* — the translation schema pins the
target language as a value constraint, so an endpoint that ignores values
would drop it. Everything else falls back to the delimiter method, which is
fine and prints one yellow line. This is capability discovery, not an error;
do not report it to the user as a failure.
