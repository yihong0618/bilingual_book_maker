# Routes: model name → endpoint shape → flags

Loaded from SKILL.md §0/§1b. Everything here is verified against the code
(`book_maker/cli.py`, `book_maker/translator/`) and, where marked, against a
live gateway on 2026-08-07.

## The one rule that decides everything

There is no model-name whitelist. A route is three flags plus the model id
the endpoint itself uses:

```
--api_base "$ROOT"  --key "$KEY"  [--api_format ...]  --model "$MODEL"
```

`--api_format` is inferred from the `--api_base` host first (`*.anthropic.com`
means `anthropic`, anything else `openai`), then from the model id (`claude`
or `anthropic` in it means `anthropic`). Pass it only to correct a wrong
guess. Any model id reaches any endpoint; nothing has to be registered.

| the endpoint speaks | flags |
|---|---|
| the OpenAI shape | `--api_base "$ROOT/v1" --model "$MODEL"` |
| the anthropic shape, on an anthropic.com host | `--api_base "$ROOT" --model "$MODEL"` |
| the anthropic shape, on a gateway domain | the same plus `--api_format anthropic` |
| an entry in `bbm_providers.json` / `~/.bbm/providers.json` | `--provider NAME`, optionally `--model "$MODEL"` |
| the OrcaRouter gateway | `--model orcarouter`, no `--api_base` |
| nothing: a local Codex sidecar on the user's plan | `--api_format codex`, no key, no base (SKILL.md §1c) |

On the openai format `--model` may be left out; it defaults to
`gpt-5.6-luna`. Every other format wants an id, and the anthropic format
errors without one.

`codex` is the one route with no endpoint to probe. It is not a model id and
not a host, so none of the probes below apply to it. Spell it
`--api_format codex`; `--model codex` is the same route under an older
spelling.

A gateway asked for the anthropic shape it does not serve answers 404, and
the run stops naming `--api_format openai` as the fix. `--api_base` may be
pasted with its path (`.../v1/chat/completions`); the path is trimmed.

Groq, xAI, a LiteLLM proxy and every aggregator are reached through the
OpenAI shape. The first three are also `--api_format groq` / `xai` /
`litellm`, which is the same OpenAI route with that vendor's address filled
in, so everything the OpenAI translator does — the structured-output probe,
context, async, batch — applies to them.

Gemini and Qwen are the two exceptions: each has a translator of its own
because neither protocol is the OpenAI shape. `--api_format gemini` talks to
the Gemini API (native constrained decoding, its own chat history, paced by
`--interval`), and `--api_format qwen` talks to Qwen-MT on DashScope, whose
request states a source and target language instead of carrying a prompt.
Both vendors *also* serve an OpenAI-compatible base, which the `openai`
route reaches — use that when the run needs the OpenAI translator's batch
and session-context machinery.

## `--provider NAME`: the same route, written once

An endpoint used more than once belongs in a provider file, not on every
command line. `bbm_providers.json` in the working directory is read first,
then `~/.bbm/providers.json`; a project entry wins on a shared name. Each
entry is the route spelled out:

```json
{
  "providers": {
    "nvidia": {
      "api_style": "openai",
      "base_url": "https://integrate.api.nvidia.com/v1",
      "default_models": ["moonshotai/kimi-k2-thinking"],
      "env_key": "NVIDIA_API_KEY"
    }
  }
}
```

The `providers` wrapper is required; the loader reads nothing from a file
without it.

`api_style` is any `--api_format` that names an endpoint — `openai`,
`anthropic`, `gemini`, `qwen`, `groq`, `xai`, `litellm` — plus `claude`,
which older files use for `anthropic`. Any other OpenAI-compatible host is
`openai` with its address in `base_url`; an unrecognised style is refused
with that entry printed. The shipped example file has an entry per vendor. `default_models` becomes `--model` when it holds one
id and `--model_list` when it holds several. `env_key` is read for the key
ahead of `BBM_API_KEY` and the format's own variables. **Explicit flags
win**, so `--provider nvidia --model <id>` keeps the user's model. An
unknown name is an error that names both files.

## `--model orcarouter`: a gateway with no address to type

`--model orcarouter` sends the run to OrcaRouter's OpenAI-shaped endpoint
and asks for its smart-routing model, `orcarouter/auto`. It is a supported
route, not a legacy alias, so nothing is rewritten, and the key comes from
`BBM_ORCAROUTER_API_KEY`.

`orcarouter/auto` is the default, not a pin — to send the run to one named
model instead of the smart router, use that entry:
`--provider orcarouter --model <id>`. A probe reads the address
(`https://api.orcarouter.ai/v1`) from there; it is never a flag you pass.

## Binding `$KEY`, `$ROOT`, `$MODEL` from the entry before any probe

The entry decides which key variable to read. Do **not** take "whichever
key is set" — `.env` is sourced into a shell that may already export other
providers' keys from `~/.zshenv`, and a stale one would route the run to an
endpoint the user never chose. Exit before curl when anything is missing:
an empty bearer token produces a 401 that reads like a bad key.

```bash
route_env() {   # $1 = provider name, as in bbm_providers.json / ~/.bbm/providers.json
  eval "$(python3 - "$1" <<'EOF'
import json, os, pathlib, shlex, sys
name = sys.argv[1]; entry = None
for f in (pathlib.Path.home()/".bbm"/"providers.json", pathlib.Path("bbm_providers.json")):
    if f.is_file():
        entry = json.load(open(f)).get("providers", {}).get(name, entry)
if entry is None:
    print(f'echo "no provider entry named {name}" >&2; return 1'); sys.exit()
style = entry.get("api_style")
# gemini and qwen speak their own protocols: the probes below would send this
# entry's key to the wrong endpoint in the wrong shape, so refuse rather than
# guess a shape for them.
OPENAI_SHAPED = {
    "openai": "https://api.openai.com",
    "groq": "https://api.groq.com/openai",
    "xai": "https://api.x.ai",
    "litellm": "http://localhost:4000",
}
if style in ("gemini", "qwen"):
    print(f'echo "provider {name}: api_style {style!r} is a protocol of its own; run it with --provider {name}, these probes do not apply" >&2; return 1'); sys.exit()
if style not in OPENAI_SHAPED and style not in ("anthropic", "claude"):
    print(f'echo "provider {name}: api_style {style!r} is not a format; the loader refuses this entry" >&2; return 1'); sys.exit()
shape = "anthropic" if style in ("anthropic", "claude") else "openai"
default_root = "https://api.anthropic.com" if shape == "anthropic" else OPENAI_SHAPED[style]
root = (entry.get("base_url") or default_root).rstrip("/")
root = root[:-3] if root.endswith("/v1") else root
key_var = entry.get("env_key") or "BBM_API_KEY"
model = (entry.get("default_models") or [""])[0] or ("gpt-5.6-luna" if shape == "openai" else "")
print(f"SHAPE={shape} ROOT={shlex.quote(root)} MODEL={shlex.quote(model)} KEY_VAR={key_var}")
EOF
)"
  [ -n "${SHAPE:-}" ] || return 1
  KEY="$(printenv "$KEY_VAR" 2>/dev/null || true)"   # same in bash and zsh; .env is exported by set -a
  [ -n "$MODEL" ] || { echo "entry $1 names no model (the anthropic format needs one)" >&2; return 1; }
  [ -n "$KEY" ]   || { echo "$KEY_VAR is unset — fill .env" >&2; return 1; }
}
```

`$ROOT` comes out with no trailing `/v1` whatever the entry wrote, so every
probe path below is spelled in full. On a gateway the key belongs to the
*gateway*, not the model's vendor: a Claude model reached over the OpenAI
shape reads the gateway entry's `env_key`, because that is the shape being
spoken.

## Shapes, and how to probe each

`$ROOT` is the scheme+host with no `/v1` (`route_env` guarantees it), so
every path below is written out in full. Each probe is one tiny call, a
fraction of a cent.

**No token cap on the OpenAI shape.** `max_tokens: 1` looks thrifty and is a
false negative twice over: gateways reject caps below their own floor
(measured: `max_tokens must be greater than 2` on *every* model of one
gateway), and OpenAI's own o-series/gpt-5 models reject `max_tokens`
outright in favour of `max_completion_tokens`. A probe must test one thing.
The repo's own probes send no cap for exactly this reason
(`translator/capabilities.py`, `probe_structured_output` and
`probe_model_route`); match them. A capped probe against gpt-5.6-luna came
back "'max_tokens' is not supported with this model" and confirmed nothing.
The reply is a few tokens of "Hi!".

**OpenAI shape** — the universal one. Most gateways serve every model they
host on it, whoever made the model.

```bash
curl -sS "$ROOT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.choices[0]`. → `--api_base "$ROOT/v1" --key "$KEY"
--model "$MODEL"`

**Anthropic shape**. `max_tokens` is *mandatory* here, unlike above; 16 is
past every floor seen so far.

```bash
curl -sS "$ROOT/v1/messages" \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.content[0]`. → `--api_base "$ROOT" --key "$KEY"
--model "$MODEL"` (add `--api_format anthropic` when the host is not
anthropic.com). The reply's `model` field echoes the id the endpoint actually
resolved to (`claude-haiku-4.5` → `claude-haiku-4-5-20251001`). Real Anthropic requires
`x-api-key`; gateways commonly accept `Authorization: Bearer` too, so try
`x-api-key` first and Bearer second.

**`--api_base` for this route takes the root, not `/v1`**: the SDK appends
`/v1/messages` itself. Passing `https://host/v1` used to produce
`/v1/v1/messages` and a 403 reading "HTTP node only allows access to
inference API paths"; a trailing `/v1` is now trimmed automatically (with a
printed note), so either form works — but say `https://host` and mean it.

**Gemini** has a route of its own — `--api_format gemini`, over the Gemini
API — which the OpenAI-shape and anthropic-shape probes here do not reach.
To probe from a shell, use its OpenAI-compatible base instead,
`https://generativelanguage.googleapis.com/v1beta/openai/`, with the
OpenAI-shape call above; that base is also the one to run against when the
translation wants batching or session context. **Qwen-MT** is the same
story at `https://dashscope.aliyuncs.com/compatible-mode/v1`.

## Inferring which shape to try first, from the model name

| model name starts with | try first | then |
|---|---|---|
| `claude-` | OpenAI if a gateway base is set; else anthropic | the other one |
| anything else (`gpt-`, `o1`, `o3`, `gemini-`, `grok-`, `llama`, `qwen`, …) | OpenAI | — |

OpenAI first at a gateway because aggregators serve Claude and Gemini
models on `/chat/completions` too. Go native only when the endpoint is
Anthropic's own, or when the gateway rejects the OpenAI shape.

**The chat call is the verdict; the listing is only a hint.** `GET
$ROOT/v1/models` (Bearer auth) is free on OpenAI-shaped endpoints and
returns `{"data":[{"id":…}]}`, but gateways routinely serve a model they do
not list, or list it under another id. A name missing from the listing is
no reason to give up: run the chat probe, and a reply is the answer. Fetch
the listing when the probe fails, to tell a typo'd id from an unsupported
path; both return 404, and only the listing separates them. The run does
the same (`translator/capabilities.py`, `probe_model_route`, once at the
first paid call). Some gateways add `supported_endpoint_types` per row (on
one aggregator every row read `['openai', 'anthropic']`); when it is there
it answers the shape question outright.

## Capability caveats per route

| `--api_format` | translation | plan-mode classification |
|---|---|---|
| `openai` (any host) | schema when the probe says `strict`, else delimiter | yes |
| `anthropic` | delimiter (no structured-output work was done for it) | yes, via the prompt rung |
| `codex` | one turn per unit, on a thread that is itself the context window | yes, via the prompt rung — the sidecar compiles no schema |
| `google`, `deepl`, `deeplfree`, `caiyun`, `tencent`, `customapi` | translation only | **no** |

Classification capability does not gate *this* skill — `--plan-classify
agent` makes no API call, you are the classifier. It matters only if someone
switches to `--plan-classify model`.

The machine-translation engines have one channel, and it translates
whatever it is handed. They cannot be asked a question.

## What the run's own probe does later

At first paid use, the OpenAI-shaped translator sends a one-key schema probe
and grades the endpoint `strict` / `shape` / `json` / unsupported. Only
`strict` gets a schema for *translation* — the translation schema pins the
target language as a value constraint, so an endpoint that ignores values
would drop it. Everything else falls back to the delimiter method, which is
fine and prints one yellow line. This is capability discovery, not an error;
do not report it to the user as a failure.
