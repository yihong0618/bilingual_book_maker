# Route setup: credentials, the route, the context flags

Loaded from SKILL.md's intake, for every book. It ends with two bash arrays,
`ROUTE` and `CONTEXT`, that every flow file's commands use.

User pages behind this file, for anything not spelled out here:
`docs/llm-args.md` (model, endpoint, format, keys, retries, on-device
models), `docs/providers.md` (the provider file, extra models, which key
goes where), `docs/env_settings.md` (every key variable),
`docs/features/session-mode.md` (the context modes).

## 1. Probe first, then ask

Find out what the user already has before asking for anything. Three
sources, all checked for **presence only**; never print a value:

```bash
set -a; [ -f .env ] && source .env; set +a
python3 - <<'EOF'
import json, os, pathlib
seen = []
for f in (pathlib.Path("bbm_providers.json"), pathlib.Path.home()/".bbm"/"providers.json"):
    if f.is_file():
        for name, e in json.load(open(f)).get("providers", {}).items():
            key = e.get("env_key") or "BBM_API_KEY"
            seen.append(f"{name}: {e.get('api_style')} {e.get('base_url','(default host)')} "
                        f"model={(e.get('default_models') or ['(none)'])[0]} {key}={'set' if os.environ.get(key) else 'UNSET'}")
print("\n".join(seen) or "no provider entries")
for v in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BBM_API_KEY", "BBM_ORCAROUTER_API_KEY",
          "JEV_API_KEY", "TYPESAFE_API_KEY", "FEATHERLESS_API_KEY"):
    print(v, "set" if os.environ.get(v) else "unset")
EOF
command -v codex >/dev/null && codex login status 2>&1 | head -1 || echo "codex: not installed"
```

That prints every provider entry and whether its key is set, the
conventional key variables (the last three are classifier keys, used only
when the user names Jev: `references/epub-plan-mode.md`, "A classifier the
user names"), and whether the codex CLI is signed in
(`Logged in using ChatGPT`). The user wants the codex route and it is
signed in: skip steps 2 and 3, go to step 4. Then ask **one** question that covers the
route, the model and the prompt file together; the user answers once:

> Here is what I found: *(the list)*. Which do you want this book to spend:
> a provider entry, one of the bare keys, or your ChatGPT plan through
> codex? Any model other than the entry's default? And do you have a prompt
> or style file you want the translation to use?

Read the answer into the `ROUTE` array:

| the user picks | `ROUTE` | format |
|---|---|---|
| a provider entry `NAME` | `(--provider NAME)`; add `--model "$MODEL"` only if they named a different one | the entry's `api_style`, which is the format: `openai`, `anthropic` (`claude` in older files), `gemini`, `qwen`, `groq`, `xai`, `litellm` |
| a bare `OPENAI_API_KEY` | `(--provider openai)`; step 2 is optional | openai |
| a bare `ANTHROPIC_API_KEY` | `(--provider anthropic)`; step 2 is optional | anthropic |
| `BBM_ORCAROUTER_API_KEY` | `(--model orcarouter)` | openai |
| codex, signed in | `(--api_format codex)`; step 4, nothing else to set up | codex |

The route decides the context flags (step 5). Say the choice back in one
line with the format it implies.

## 2. Nothing usable yet, or a bare key with no entry: hand over the file

Put the dummy where the run will read it, then ask the user to fill it. Do
not write the entry for them from guesses:

```bash
[ -f bbm_providers.json ] || cp .agents/skills/bbm-plan/assets/bbm_providers.example.json bbm_providers.json
[ -f .env ]               || cp .agents/skills/bbm-plan/assets/env.example .env
for f in bbm_providers.json .env; do   # one path per call: -q refuses two
  git check-ignore -q "$f" || echo "$f" >> "$(git rev-parse --git-common-dir)/info/exclude"
done                                    # common dir: a worktree's .git is a file
```

Then tell them exactly what to edit and stop until they say it is done:

- `bbm_providers.json`: keep the entry they will use, fill `base_url`,
  `default_models` (the exact id the endpoint spells) and `env_key`; delete
  the `FILL-ME` entry if unused. The file holds no secrets: `env_key` only
  names a variable. The fields are on `docs/providers.md`. A vendor with no
  style of its own is `openai` plus its `base_url`. Ask for the model's
  price (the `prices` block) when the user cares about the bill: the
  progress bar then shows `spent=$0.012` instead of token counts. The
  example carries gpt-5.6-luna's list price.
- The example's `openai` entry also sets `img_model: gpt-5.6-luna`, which
  only the PDF flow uses (`references/pdf-route.md`); leave it in unless
  the user wants to spend nothing on it. **Add a `classify_model` only
  when the user names a classifier** (`references/epub-plan-mode.md`, "A
  classifier the user names"); by default the EPUB flow's agent mode asks
  no model.
- `.env`: the variable `env_key` names, with the key as its value.

A bare `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` needs no file at all:
with no `bbm_providers.json`, `--provider openai` (or `anthropic`) reads
the shipped example's entry, prints one warning line saying so, and sends
the run to the vendor host with the conventional variable (and, for
`openai`, `img_model: gpt-5.6-luna`). Hand the file over only when the user
wants the warning gone, another model or price, or cannot write in the
repo root (then `~/.bbm/providers.json` is read instead). Otherwise rerun
the probe after step 2; it should show the entry with its key `set`.

**Keys never go on the command line.** The entry's `env_key` (or
`$BBM_API_KEY`) is read when `--key` is absent, which is why this skill
never passes it. The old per-vendor flags (`--openai_key`, …) still parse
and are rewritten to `--key` with a notice; `--api_key` is the same flag as
`--key`.

## 3. Endpoint probe: verify the entry before anything is paid (sub-cent)

The entry names the endpoint; prove it answers, so a typo'd model id or a
wrong shape surfaces here. Skip this on `codex` (nothing to curl). Three
ordered questions, each answered by a call:

**0. Bind `$KEY`, `$ROOT`, `$MODEL` from the entry**, and refuse to curl
without them. `route_env NAME` is defined in `references/providers.md`;
copy it verbatim. It reads the entry, never "whichever key is set",
because a stale export in `~/.zshenv` would silently route the run
somewhere the user never asked for:

```bash
set -a; source .env; set +a
route_env openai        # the provider name: sets KEY, ROOT, MODEL, SHAPE or exits
```

**1. Does this model id exist here?** On any OpenAI-shaped base the model
listing is free:

```bash
curl -sS "$ROOT/v1/models" -H "Authorization: Bearer $KEY" |
  python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print([m["id"] for m in d])'
```

If `$MODEL` is absent, stop and show the near matches: a typo'd id and an
unsupported path both return 404 later, and only this tells them apart. A
non-OpenAI-shaped endpoint has no such listing: skip to question 2 and let
the probe judge the id.

**2. Does it answer in that shape?** Use the recipes in
`references/providers.md` **verbatim**, token-cap rule included, for the
`$SHAPE` the entry declares.

**3. Anything to correct?** A passing probe means the entry is right and
`ROUTE` stays `(--provider NAME)`. On 404 retry with `/v1` added or removed
and tell the user which `base_url` to write into the entry; on an auth
rejection try that shape's native scheme. Stop and ask only when the key
itself is rejected by its own provider.

## 4. The codex route: a subscription, not an endpoint

`--api_format codex` names a route, not a model or a host (`--model codex`
is the same route spelled the older way): the run drives a local
`codex app-server` sidecar and spends the user's ChatGPT/Codex plan
allowance instead of API credits. Step 3 does not apply.

- **`--api_format codex` alone runs `gpt-5.6-luna`.** To name another
  model, add `--model "$MODEL"`, and offer only ids the user's plan lists.
- **No `--key`, no `--api_base`.** Run `codex login` once beforehand. The
  run checks that the sidecar is up and signed in before parsing the book,
  and prints `Codex: signed in (…), N% of the window remaining`, under
  `--quiet` too. Read it off the plan step's output before the paid run.
- **`--parallel-workers` is refused here**: turns serialize on one thread.
  **`--no-thinking` is refused too**: there is no request body to carry it.
- **The sidecar is stripped before any book text reaches it**: shell, exec,
  hooks, plugins, apps, browser, web search and every MCP server the user's
  codex config declares are disabled and checked; the turn is read-only in
  an empty private directory. The user's own codex setup is untouched.
- **A spent 5-hour window is waited out**, not failed. A weekly limit ends
  the run, printing when the allowance returns. Rerun later with `--resume`.
- **A resumed run starts a new thread**: nothing already translated is paid
  for again, but the earlier run's terminology and register are not carried
  over. The run prints this.
- Tell the user which allowance this spends: plan quota, not the API key in
  `.env`. Not in Docker: neither the binary nor the login is in the image.

## 5. The context flags, by format

| format | `ROUTE` | `CONTEXT` | why |
|---|---|---|---|
| openai (any OpenAI-shaped entry, `orcarouter`, and `groq`/`xai`/`litellm`, which are that route at their own address) | `(--provider NAME)` | `(--use_context session)` | one cached history, compacted at 8192 by default; costs less than window mode for several times the context |
| anthropic (`api_style: anthropic`) | `(--provider NAME)` | `(--use_context session)` | the same history, and this route keeps it |
| gemini, qwen | `(--provider NAME)` | `(--use_context)` | neither keeps a re-sendable session history, so `--use_context session` is refused; window mode is what they have |
| codex | `(--api_format codex)` | `()` | the thread is the context; a context flag has nothing to add (`--use_context` is accepted and does nothing) |

```bash
set -a; source .env; set +a
ROUTE=(--provider openai)                   # or (--provider NAME) / (--api_format codex)
CONTEXT=(--use_context session)             # or () on codex, (--use_context) on gemini/qwen
```

Arrays on purpose: `${VAR:+--flag "$VAR"}` mis-tokenizes under zsh (macOS's
default shell) into one argv word that argparse rejects. The array form
works in bash and zsh alike.

**A local model** (Ollama, llama.cpp, vLLM, LM Studio) is
`ROUTE=(--api_base http://localhost:11434/v1 --model <id>)` (Ollama's
address; the server's own otherwise), no key. Such a server rarely verifies
a strict schema; the probe decides, and the run then halves the unit cap
to 8 (and, outside a session, the budget to 800) by itself: do not lower
them further up front. With a session, leave `--context-compact-at` at
8192 unless the server's context window is smaller; then set it below that
window. A reasoning model (Qwen3-class) gets `--no-thinking`. On a CPU-only
machine a local model is slow for a whole book: say so and offer a hosted
route. `docs/llm-args.md#on-device-models-ollama-llamacpp-lm-studio` has
the rest.

## Flag menu: route and context (every flow)

**Defaults below are the recommendation.** The alternatives are listed so
you can honour a request without guessing at legal values; the flag's full
text is on `docs/cmd.md`.

### Route

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--provider` | a name from `bbm_providers.json` (repo root) or `~/.bbm/providers.json` | **the route, step 1** | the endpoint is an entry there: one word supplies `--api_base`, `--api_format`, the model(s) and the key variable. Explicit flags still win, so `--model` may ride along |
| `--model` | any model id the endpoint uses, verbatim; or `orcarouter` | the entry's `default_models`; unset on the openai format means `gpt-5.6-luna` | the user names a different model, or wants the OrcaRouter gateway. A ChatGPT plan is `--api_format codex` (step 4), not a `--model` value |
| `--model_list` | several ids, comma-separated | *unset*; one model goes in `--model` | rate limits force rotation. Refused with `--use_context session`; each id keeps its own prompt cache |
| `--key` | one key, or several comma-separated to rotate past rate limits | **never passed**; the entry's `env_key` (then `$BBM_API_KEY`, then the format's own variable) is read from the environment | never; omit on the codex route too |
| `--api_format` | `openai`, `anthropic`, `codex`, `gemini`, `qwen`, `groq`, `xai`, `litellm`, `google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi` | *unset*; inferred from `--api_base`, then from the model id | the run spends the user's ChatGPT plan (`codex`), or step 3 proved the guess wrong. The machine-translation formats cannot answer a question, so they are translation-only |
| `--api_base` | endpoint URL | *unset*; the entry's `base_url` | a gateway, proxy or local server. The OpenAI shape wants `…/v1`; the anthropic shape wants the bare host |
| `--proxy` | `http://127.0.0.1:7890`-style | *unset* | the user is behind one |
| `--no-thinking` | on/off | *off* | a reasoning model (local Qwen3-class, or a hosted one that thinks by default) spends tokens and time before every paragraph. Refused on codex |
| `--extra_body` / `--extra_headers` | JSON object | *unset* | the endpoint needs a vendor field or a header. A field in `--extra_body` wins over `--no-thinking` |
| `--temperature` | float | *unset* | output is erratic; lower it and check the markup again. codex ignores it |

### Context and consistency

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--use_context` | bare/`window`, `session` | **`session`** on openai and anthropic; **nothing** on codex; bare on gemini/qwen (step 5) | the progress bar's `cached=` count is still 0 after a dozen requests: the endpoint is not caching, and session mode re-reads the history at full price. Drop to bare `--use_context`. Drop to it too when a run must go parallel, where `session` is refused |
| `--context-compact-at` | estimated-token budget, minimum 1500 | **unset → 8192**, printed at start | leave it unset: 8192 is chosen for continuity, not cost. A lower value is cheaper in session mode (300 units: 360,681 tokens at 4096 against 396,197 at 8192); set it lower only when the user puts session cost above seams, or below a local server's context window. Past 16000 the cost climbs steeply (`docs/evaluation/session-compact-budget.md`). Needs `--use_context session` on an API route |
| `--no-context-compact` | on/off | *off* | a small model writes poor handoff reports and the text drifts after a seam; the next window then starts empty |
| `--context_paragraph_limit` | integer | *unset* (3 pairs) | window mode only, when the user wants a different number of pairs re-sent |
| `--prompt` | path to `.json` / `.txt` / `.md`, or a template string | *unset* unless the user has one | the user hands over their own voice/register. Lint first (`references/prompt-files.md`). The run prints where each section landed |
| `--glossary` / `--terminology` | path to a `term -> translation` file | *unset* unless the user has pinned terms | the user names renderings that must hold (people, places, titles). Hits-only: costs nothing on untouched paragraphs. openai-shaped and codex routes, EPUB, Markdown and PDF books. A pin is verbatim: use only renderings the user stands behind |
| `--glossary-auto` | `on`, `off` | **off** | only for a session run on a capable model when the user wants self-taught renderings kept across window seams |

## Failure lines on any route (all fail loud by design)

| symptom | meaning |
|---|---|
| `doesn't apply JSON schema … using delimiter method`, `honors JSON schema shape but not value constraints`, `no strict structured-output support` | **not a failure.** The endpoint does not do strict schema decoding, so translation uses the delimiter method. Expected on the anthropic route, most proxies and local servers; do not switch models over it (`docs/evaluation/structured-output-ladder.md`) |
| `retrying after … — attempt N, waiting Ns` | a slow or rate-limited provider; the run waits with no attempt limit and goes on by itself. Only a rejected key, a refused request or a missing model stops it |
| `--use_context session is not implemented for the … route` | that route keeps no history; use bare `--use_context`, or a route that does (step 5) |
| `--parallel-workers is not supported with --use_context session …` | choose one; bare `--use_context` keeps the workers |
| `handoff report failed (…); starting the next window` | one compaction produced no report. Informational; translation continues. If the text drifts after it, pin terms with `--glossary` |
| codex: `… is at capacity … retrying in 60 s` | usually Codex rate-limiting the network, not a missing model; the run retries by itself. Suggest another network or account if it repeats |
| codex: `… codex login, then run this again` | the sidecar is up but not signed in. One `codex login`, then rerun; nothing was paid |
| codex: waiting *N* min for the window to reset | the 5-hour plan window is spent; the run sleeps and continues by itself |
| codex: `the Codex plan allowance is spent and does not reset until …` | the weekly limit. The run exits 1, having saved what the loader checkpoints; rerun with `--resume` after the time it names |
