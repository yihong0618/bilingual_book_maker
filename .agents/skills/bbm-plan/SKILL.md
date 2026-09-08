---
name: bbm-plan
description: Translate a whole EPUB into a bilingual book with bilingual_book_maker's plan mode - greedy partition, agent-reviewed classification plan, then a full resumable run. Use when the user wants a book translated well (running heads, page numbers, and apparatus skipped deliberately) rather than a quick --translate-tags pass, or asks for "plan mode" / "bbm" translation.
---

# bbm-plan: plan-mode EPUB translation

**Hard constraint: this skill uses `--plan-classify agent`, and only that.**
The plan arrives with its uncertain signatures set to `"action": null` and
the translate run refuses to start while any null remains, so *you*, the
coding agent, own the classification step against the real samples. Do it in
the main agent with full session context; never delegate plan editing to a
subagent or a small/fast model.

Repo: the repository this skill ships in (`make_book.py` at its root). Run
every command from the repo root. Plan mode is **epub-only**.

You are the trained CLI operator. You pick every flag from the **flag menu**
below and state each choice with a one-line reason; the menu's defaults are
the recommendation, and you follow them unless the user asks for something
else or the book argues otherwise. The user hands over a book, credentials
and (if they have one) a prompt file, approves the plan and the cost, and
gets a bilingual epub back — they are never asked to experiment with flags,
halt semantics or resume mechanics.

Two runs of one command with small flag changes: **plan → full**. A smoke
test sits between them, optional and skipped by default — §4 says when a
book has earned one.

All state lives on disk (`bbm_providers.json`, `.env`, `<book>_plan.json`,
the resume cache, `run.log`), so any step can be redone after a crash or in
a new session.

## 0. Credentials and route — probe first, then ask

Find out what the user already has before asking for anything. Three
sources, all checked for **presence only** — never print a value:

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
for v in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "BBM_API_KEY", "BBM_ORCAROUTER_API_KEY"):
    print(v, "set" if os.environ.get(v) else "unset")
EOF
command -v codex >/dev/null && codex login status 2>&1 | head -1 || echo "codex: not installed"
```

That prints every provider entry and whether its key is set, the four
conventional key variables, and whether the codex CLI is signed in
(`Logged in using ChatGPT`). Then ask **one** question that covers the
route, the model and the prompt file together — the user answers once:

> Here is what I found: *(the list)*. Which do you want this book to spend —
> a provider entry, one of the bare keys, or your ChatGPT plan through
> codex? Any model other than the entry's default? And do you have a prompt
> or style file you want the translation to use?

Read the answer into the `ROUTE` array every later step uses:

| the user picks | `ROUTE` | format |
|---|---|---|
| a provider entry `NAME` | `(--provider NAME)` — add `--model "$MODEL"` only if they named a different one | the entry's `api_style`, which is the format: `openai`, `anthropic` (`claude` in older files), `gemini`, `qwen`, `groq`, `xai`, `litellm` |
| a bare `OPENAI_API_KEY` | `(--provider openai)` after step 0b | openai |
| a bare `ANTHROPIC_API_KEY` | `(--provider anthropic)` after step 0b | anthropic |
| `BBM_ORCAROUTER_API_KEY` | `(--model orcarouter)` | openai |
| codex, signed in | `(--api_format codex)` — §1c, nothing else to set up | codex |

The route decides the **default flag set** (§1d). Say the choice back in
one line with the format it implies.

### 0b. Nothing usable yet, or a bare key with no entry — hand over the file

Put the dummy where the run will read it, then ask the user to fill it.
Do not write the entry for them from guesses:

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
  the `FILL-ME` entry if unused. The file holds no secrets — `env_key`
  only names a variable. The shape is `{"providers": {NAME: {...}}}`;
  `api_style` is the format: `openai`, `anthropic`, `gemini`, `qwen`,
  `groq`, `xai` or `litellm`, and the example file ships an entry for each.
  A vendor with no style of its own is `openai` plus its `base_url`.
  An optional
  `prices` block (`{"<model id>": {"input", "output", "cached_input"}}`,
  per million tokens; `currency` defaults to USD) makes the progress bar
  show `spent=$0.012` instead of token counts, and the closing line show
  both. The example carries gpt-5.6-luna's list price. Ask for the price
  when the user cares about the bill; a model without one puts the bar
  back on tokens, and the closing line names it.
- `.env`: the variable `env_key` names, with the key as its value.

A bare key with no entry gets the same treatment: the example file's
`openai` / `anthropic` entries already point at the vendor hosts and read
the conventional variable, so nothing needs editing beyond deleting the
others. Rerun the probe afterwards; it should now show the entry with its
key `set`.

**Keys never go on the command line.** The handoff block reprints the
whole command, and argparse echoes it back on any mistyped flag, so a
`--key` would land in the terminal, the log and the transcript. The entry's
`env_key` (or `$BBM_API_KEY`) is read when `--key` is absent, which is why
this skill never passes it. The old per-vendor flags (`--openai_key`, …)
still parse and are rewritten to `--key` with a notice; `--api_key` is the
same flag as `--key`. If a key ever does reach the terminal, say so at once
and tell the user to rotate it.

## 1. Intake — what else to ask for

1. **Book path** and **target language** (`--language`, e.g. `zh-hans`,
   `ja`, `Simplified Chinese`). For a small language the tables may not
   know, pass both halves yourself: `--language "ain:Ainu"` — the
   tag before the colon is stamped on the output and names the reply
   field, the name after it is what the model is asked for. The tag list
   ships in `docs/languages.md`; a free-typed value matching no tag still
   runs and says so in one `Note:` line at startup. The source language
   is not part of this flag — `--source_lang` states it for a book whose
   short lines or names could be misdetected.
2. **Their prompt file** — asked in step 0's one question. If they hand
   one over, lint it before the first paid run — contract and commands in
   **`references/prompt-files.md`**. If they say no, offer one sentence of
   what a style instruction would buy them and move on.
   Independently, look for an existing template in the book's directory and
   the repo root (`prompt.json`, `prompt.txt`, `prompt*.md`,
   `prompt_template*`). **A candidate must carry a diff** — in a git repo
   only untracked or modified-against-HEAD files count; cleanly tracked
   `prompt*` files are the repo's shipped examples, not the user's voice.
   Found one? **Ask before doing anything with it** — never adopt or ignore
   it silently.
3. Anything they want to change from the flag menu's defaults: a visual
   style for the translation, specific chapters only.

**Bilingual is the assumption; do not ask.** The output keeps every original
next to its translation unless the user has asked for a translated-only
book in so many words ("just the Chinese", "replace the original", "not
bilingual", "single language"). *"Translate this book to Chinese"* is not
that request — it is the ordinary ask, and it means a bilingual book, which
is what the tool is for and what `--single_translate` off already does.
Never infer single-language output from the target language being named,
from the book being short, or from the user sounding brisk. Say which one
you are producing in the same line where you state the flag choices, so a
user who wanted the other one can say so before anything is paid for.

Base command — `ROUTE` from step 0, `CONTEXT` from §1d:

```bash
set -a; source .env; set +a
ROUTE=(--provider openai)                   # or (--provider NAME) / (--api_format codex)
CONTEXT=(--use_context session)             # or () on codex
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" \
  --language "$LANG" --plan-classify agent "${CONTEXT[@]}"
```

(Arrays on purpose: `${VAR:+--flag "$VAR"}` mis-tokenizes under zsh —
macOS's default shell — into one argv word that argparse rejects. The array
form works in bash and zsh alike.)

## 1b. Endpoint probe — verify the entry before the classify work (sub-cent)

The entry names the endpoint; prove it answers, so a typo'd model id or a
wrong shape surfaces here and not after the plan is built. Skip this on
`codex` (nothing to curl). Three ordered questions, each answered by a call:

**0. Bind `$KEY`, `$ROOT`, `$MODEL` from the entry**, and refuse to curl
without them. `route_env NAME` below is copied verbatim from
`references/providers.md`; it reads the entry, never "whichever key is set",
because a stale export in `~/.zshenv` would silently route the run somewhere
the user never asked for:

```bash
set -a; source .env; set +a
route_env openai        # the provider name — sets KEY, ROOT, MODEL, SHAPE or exits
```

**1. Does this model id exist here?** On any OpenAI-shaped base the model
listing is free:

```bash
curl -sS "$ROOT/v1/models" -H "Authorization: Bearer $KEY" |
  python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print([m["id"] for m in d])'
```

If `$MODEL` is absent, stop and show the near matches — a typo'd id and an
unsupported path both return 404 later, and only this tells them apart. A
non-OpenAI-shaped endpoint has no such listing: skip to question 2 and let
the probe judge the id.

**2. Does it answer in that shape?** Use the reference's recipes
**verbatim**, token-cap rule included, for the `$SHAPE` the entry declares.

**3. Anything to correct?** A passing probe means the entry is right and
`ROUTE` stays `(--provider NAME)`. On 404 retry with `/v1` added or removed
and tell the user which `base_url` to write into the entry; on an auth
rejection try that shape's native scheme. Stop and ask only when the key
itself is rejected by its own provider.

## 1c. The codex route — a subscription, not an endpoint

`--api_format codex` names a route, not a model or a host (`--model codex`
is the same route spelled the older way): the run drives a local
`codex app-server` sidecar
and spends the user's ChatGPT/Codex plan allowance instead of API credits.
**Step 1b does not apply**: there is nothing to curl and no key to resolve.

```bash
python make_book.py --book_name "$BOOK" --api_format codex --language "$LANG" \
  --plan-classify agent
```

- **`--api_format codex` alone runs `gpt-5.6-luna`.** To name another
  model, add `--model "$MODEL"` to it, and offer only ids the user's plan
  lists.
- **No `--key`, no `--api_base`.** Run `codex login` once beforehand. The
  run checks that the sidecar is up and signed in before parsing the book,
  and reports how much of the 5-hour window is left.
- **`--parallel-workers` is refused here.** Turns serialize on one thread,
  so there is nothing to gain.
- **The sidecar is stripped before any book text reaches it.** Startup
  disables shell and exec, hooks, plugins and apps, browser and computer
  use, web search, and every MCP server the user's codex config declares —
  then reads the config back and refuses to run if any of them survived. The
  turn itself is `sandbox: read-only` with approvals off, in an empty
  private working directory that is deleted when the run ends, so the user's
  project config and hooks are not in play. The user's own codex setup is
  untouched; only this sidecar is.
- **A spent 5-hour window is waited out**, not failed. A weekly limit ends
  the run instead, printing when the allowance returns — its reset is too
  far off to sit through. Rerun later; the run is resumable either way.
- **A resumed run starts a new thread**, and the thread is the only context
  this route has. Nothing already translated is paid for again, but the
  earlier run's terminology and register are not carried into the new one.
  The run prints this.
- Tell the user which allowance this spends: plan quota, not the API key in
  `.env`.

Plan, classify and the full run are otherwise identical.

## 1d. Default flag set, by format

The route's format picks the context flags; everything else is the same
command. This is what you pass unless the user asked for something else or
the book argues otherwise — the flag menu explains each choice and the
legal alternatives.

| format | `ROUTE` | `CONTEXT` | why |
|---|---|---|---|
| openai (any OpenAI-shaped entry, `orcarouter`, and `groq`/`xai`/`litellm`, which are that route at their own address) | `(--provider NAME)` | `(--use_context session)` | one cached history, compacted at 8192 by default (`--context-compact-at` overrides); costs less than window mode for several times the context |
| anthropic (`api_style: anthropic`) | `(--provider NAME)` | `(--use_context session)` | the same history, and this route keeps it |
| gemini, qwen | `(--provider NAME)` | `(--use_context)` | neither keeps a re-sendable session history, so `--use_context session` is refused; window mode is what they have — Gemini's own chat history, Qwen's translation memory |
| codex | `(--api_format codex)` | `()` | the thread is the context; a context flag has nothing to add to it |

Common to all of them, per step: `--plan-classify agent` always; the full
run adds `--quiet`, and `--resume` only once a cache exists (§5); the
optional smoke adds `--quiet --test --test_num 8`. Nothing from the "Never
pass in plan mode" list, ever.

## 2. Plan (agent mode translates nothing)

Run the base command once. It partitions the whole book, writes
`<book>_plan.json`, prints a handoff block, and exits without translating.

**Offline on every route.** Nothing is asked of the endpoint until the
first paid request, so a wrong model id or a dead gateway surfaces there,
not here — with the smoke skipped, that is the first minute of the full
run, and it costs nothing.

What the report gives you, and what each part is for:

- **A signature per row** (`p.calibre_13`, `span.page-no` …) with unit
  count, char total, share of the book, and up to 5 real samples — this is
  the evidence you classify from in step 3.
- **Coverage**, checked against `--plan-min-coverage`. Every text node is
  either a translation unit or a skip with a stated structural reason, and
  the run proves the accounting adds up, so a low number means the book
  really is mostly apparatus — not that something was quietly dropped.
- **Grouped batches.** Consecutive units — whole paragraphs as well as
  verse, lists and short table entries — share one request under the
  general grouping caps (`--accumulated_num` tokens, `--max-batch-units`
  units), so each unit is translated with its neighbours in view. The
  budget defaults on every plan run (see the flag table); the report's
  `batches:` line says how the partition packed, and the narration line
  under it carries the true per-request numbers for the route. (The old
  `--poetry-group-size` knob is deprecated — grouping covers it.)
- **Inline markers.** An excluded inline (`<code>`, `<sup>`, an
  `<img>`) that is short — or of any length when it carries no prose
  word: a URL, a spaced formula — no longer splits its sentence: the
  model sees a `⟦code1⟧` token and the original node is put back at
  that spot afterwards.

Symptom → knob, when reading the report:

| symptom in report | knob |
|---|---|
| short lines split awkwardly across requests | raise `--accumulated_num` (the token budget is what usually splits them); `--poetry-group-size` is deprecated |
| `N marker(s) missing … — reconciled` lines | benign one-off; if most units say it, the model ignores the token instruction — try a stronger model |
| legit low coverage (dictionary, critical edition, apparatus-heavy) | lower `--plan-min-coverage` deliberately, and say so in the plan summary |
| visible text under a `hidden` skip reason, or vice versa | inspect the epub's CSS before overriding |

## 3. Classify (you are the classifier)

Read `<book>_plan.json`. Rows with `"action": null` are the plan's open
questions — every one must become `"translate"` or `"skip"`, and the
translate run refuses to start while any null remains. For each: **name what
the text is first** (prose, verse, dialogue, heading, caption, running head,
page/line number, sigla, cross-reference label, boilerplate, decorative
marker), *then* rule — naming before ruling prevents rationalizing a snap
verdict. Judge from `samples`, `units`, `chars`, `pct`, `mean_chars`; when
the samples do not settle it, choose `"translate"` — over-translating is
cheap, losing content is not. Want more evidence? `unzip -p <book> <file>`
and read the markup around the signature.

**An inline skip cuts its text out of the surrounding block.** Skipping a
signature that wraps part of a word — a drop-cap initial, a small-caps
fragment, a decorative first letter — does not leave the word alone: the
block is assembled without that text, so `CHAPTER I` is sent to the model as
`HAPTER` and `MR. JONES` loses its `M`. Skip an inline signature only when
its text is *whole* (a URL, a page number, a standalone marker). After
editing the plan, rerun the plan step and read the affected block rows'
samples again — a decapitated sample is the tell, and it is visible before
anything is paid for.

Non-null rows (prose spine, headings, poetry) may also be changed if their
samples convince you, but the nulls are the required work. Hold a non-null
override to the same name-then-rule discipline, and **record every one in
the step-6 report** — the user should see where you disagreed with the
plan's defaults, not discover it in the output. Edit **only** the `action`,
`decided_by` and `content_type` fields. Validation is fail-closed: a typo'd
action, missing hash or edited book is a hard error on the next run, never a
silent default.

## 4. Smoke test — optional, skipped by default

**Default: skip it and go to §5.** The plan step already caught the
structural mistakes offline and for free, a dead endpoint or wrong model id
fails in the first minute of the full run having paid nothing, and the
markup checks the smoke exists for are done at delivery either way (§6). On
most books a smoke buys a second start-up, a second plan load and a second
epub write to learn what §6 tells you anyway.

Say you are skipping it, in one line, when you state the flag choices.

**Run one when the book has earned it.** Any of:

- **The user brought a `--prompt` file**, or you are translating into a
  language or register this repo has not produced before. A prompt applies
  to every unit, and a prompt that reads well can still produce markup that
  does not.
- **You argued with the plan** — several resolved nulls, any non-null
  override, or a book whose apparatus is tangled enough that you want to see
  a `skip` hold before paying for the whole spine.
- **The book is big enough that a wasted full run is real money.** The plan
  report's char total is the number to judge on; when a full run would cost
  dollars rather than cents, eight units first is cheap insurance.
- **The user asks**, or asks what the output will look like before
  committing.

**How, when you do run one.** Base command + `--quiet --test --test_num 8 >
smoke.log 2>&1`. Check results **after** the run, from files — never from
live output.

Units are consumed in **spine order**, so before running, check which
documents the first 8 units come from. A large nav or title page can absorb
the whole budget (a 458 KB nav once ate all 20 units of a poetry smoke, so
the smoke translated zero verse); when that happens, point the smoke at a
body chapter with `--only_filelist <content doc>` rather than raising
`--test_num`.

Then read the partial `<book>_bilingual.epub` back with the §6 checklist —
a zero exit code and a clean log do not pass a smoke — and check
`smoke.log` for error lines. The cache carries into the full run, so
nothing paid here is re-paid, and the full run that follows takes
`--resume`.

Not a failure at this step: the endpoint being graded below `strict` and the
run announcing the delimiter method. That is the expected line on claude, on
most proxies, and on anything not natively OpenAI.

## 5. Full run

Base command + `--quiet`, minus `--test`. Always in the background with
output to a log:

```bash
… --quiet > run.log 2>&1                # first run: no cache yet
… --quiet --resume > run.log 2>&1       # every rerun, and after a smoke
```

**`--resume` goes on the second run, not the first.** With no
`.<book>.temp.bin` it raises `can not load resume file` and translates
nothing — so a run that follows a skipped smoke starts without it, and a run
that follows a smoke or a crash carries it.

(Bash `run_in_background: true`; poll with `tail -5 run.log`.) On any crash,
rerun the same command with `--resume` added. If the run stops with a fatal
translation error, fix the cause (key quota, endpoint down) and rerun; do
not delete the cache unless the book or plan changed intentionally.

## Flag menu — every choice, with the recommended default

**Defaults below are the recommendation.** Pick them unless the user asks
for something else or the book argues otherwise; the alternatives are listed
so you can honour a request without guessing at legal values.

### Route

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--model` | any model id the endpoint uses, verbatim; or `orcarouter` | the entry's `default_models`; unset on the openai format means `gpt-5.6-luna` | the user names a different model, or wants the OrcaRouter gateway, which `--model orcarouter` reaches with no `--api_base`. A ChatGPT plan is `--api_format codex` (§1c), not a `--model` value |
| `--model_list` | several ids, comma-separated | *unset*; one model goes in `--model` | rate limits force rotation. Naming a model in both flags is an error. Each id keeps its own prompt cache, so every switch re-pays the `--use_context session` history at full price |
| `--key` | one key, or several comma-separated to rotate past rate limits | **never passed**; the entry's `env_key` (then `$BBM_API_KEY`, then `$OPENAI_API_KEY` / `$ANTHROPIC_API_KEY`) is read from the environment | never; omit on the `codex` route too |
| `--api_format` | `openai`, `anthropic`, `codex`, `gemini`, `qwen`, `groq`, `xai`, `litellm`, `google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi` | *unset*; inferred from `--api_base`, then from the model id | the run spends the user's ChatGPT plan (`codex`, §1c), or step 1b proved the guess wrong. The machine-translation formats cannot answer a question, so they are translation-only |
| `--api_base` | endpoint URL | *unset*; the entry's `base_url` | a gateway, proxy or local server. The OpenAI shape wants `…/v1`; the anthropic shape wants the bare host |
| `--provider` | a name from `bbm_providers.json` (repo root) or `~/.bbm/providers.json` | **the route, step 0** | the endpoint is an entry there: one word supplies `--api_base`, `--api_format`, the model(s) and the key variable. Explicit flags still win, so `--model` may ride along. An unknown name is an error naming both files |
| `--proxy` | `http://127.0.0.1:7890`-style | *unset* | the user is behind one |

### Plan mode

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--plan-classify` | `auto`, `none`, `all`, `model`, `agent` | **`agent`** — this skill's hard constraint | never, inside this skill |
| `--plan-min-coverage` | 0.0–1.0 | **0.5** | a dictionary, critical edition or apparatus-heavy book legitimately translates less; lower it deliberately and say so |
| `--poetry-group-size` | integer, short lines per request | **leave unset — deprecated** | never set it fresh; general grouping covers verse and the units cap is `--max-batch-units`. It still works for old command lines, and warns |
| `--exclude-translate-tags` | comma-separated tags; `""` excludes nothing | **`sup,code`** | the book puts real prose in one of those, or another tag is pure apparatus |

### Context and consistency

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--use_context` | bare/`window`, `session` | **`session`** on openai and anthropic; **nothing** on codex, where the thread is the context | the progress bar's `cached=` count is still 0 after a dozen requests: the endpoint is not caching, and session mode re-reads the history at full price. Drop to bare `--use_context`, which re-sends the last few pairs. Drop to it too when a run must go parallel, where `session` is refused. The bar shows `in= out= cached=` live (`spent=` when the entry carries `prices`, §0b) and the run ends with one closing line; under `--quiet` only the line |
| `--context-compact-at` | estimated-token budget, minimum 500 | **unset → 8192**, pinned, printed at start — every session run, grouped or not, codex included | leave it unset: owner-set for continuity, not cost — 8192 keeps the compaction count at the old defaults' level (9 vs 8 at 300 units), the margin local-device models need. It is NOT the cheap setting: under the halved budgets every request re-reads the window, so lower C (toward 4096) measures ~10% cheaper in session mode at 300 units — set it lower only when session cost outranks seams. Past 16000 the cost wall is steep and the only drift ever observed lived in the long-window cell. Set it only when the user insists on a number — an explicit value always wins. **Needs `--use_context session`** on an API route; without it the flag is accepted and does nothing. On `codex` it always applies |
| `--context_paragraph_limit` | integer | *unset* (the translator uses 3) | window mode only, when the user wants a different number of pairs re-sent |
| `--prompt` | path to `.json` / `.txt` / `.md`, or a template string | *unset* unless the user has one (§1) | the user hands over their own voice/register — the usual reason to set it. Three sections: `user` (must keep `{text}`), `system`, `style`; a section without a native slot on the route is appended to the user message, and the run prints where each landed |
| `--glossary` / `--terminology` | path to a `term → translation` file | *unset* unless the user has pinned terms | the user names renderings that must hold (people, places, titles). Hits-only: costs nothing on untouched paragraphs. openai-shaped and codex routes only; a pin is verbatim — use only renderings the user stands behind. `--glossary-auto` defaults on wherever a session runs and keeps the handoff's learned renderings; `off` if the user wants no self-taught terms |
| `--temperature` | float | *unset* | output is erratic; lower it and check the markup again. The openai format leaves an unset value out of the request and retries once without it if the model rejects one; the anthropic route sends `1.0` on every call; codex ignores it |

### Output form

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| *(bilingual)* | — | **bilingual: translation added beside the original.** The default, and the assumption — see §1 | — |
| `--single_translate` | on/off | **off** | **only** when the user asked for a translated-only book in so many words. Naming a target language is not that request. The original is replaced, so there is nothing to compare against afterwards; `--translation_style` still applies |
| `--translation_style` | CSS declarations | *unset* | the translation should be visually separated, e.g. `"color:#808080;font-style:italic"`. It is the whole declaration block, so it replaces `--translation_color` rather than merging with it (the run says so) |
| `--translation_color` | a colour | *unset* | the user wants only a colour and no other CSS. Passing both: `--translation_style` wins, and the run says the colour was lost |
| `--no_disclosure` | on/off | **off — the epub says it is an AI translation**: one small line below the book intro, "Translated by \<model\>, \<year\>." | the user asks for the marking gone in so many words. Say what they lose: a reader can no longer tell the translation from a human one, and the model that made it is no longer recorded (it turns off `--translation-metadata` too). Ignored on non-epub output, which carries no credit |

### Scope and run control

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--only_filelist` / `--exclude_filelist` | comma-separated internal filenames, **OPF-relative** (`s04.xhtml`, not `EPUB/s04.xhtml`) | *unset* (whole book) | the user wants specific chapters, or a smoke must skip front matter. A name the book does not have fails loud on either list, before anything is paid for. **An only-list wins outright**: pass both and the exclude-list is unreachable |
| `--test` / `--test_num` | flag + integer | *unset* — the smoke is optional and skipped by default (§4) | you are running a smoke: `--test --test_num 8`, or ~20 on poetry-heavy books once you have confirmed the first N units are body text |
| `--quiet` | on/off | **on for every paid run** | never off for a run that translates — bars and per-unit echoes flood the log and your context; warnings and errors still print |
| `--resume` | on/off | **off on the first run, on for every rerun** | never off after a crash — replay is positional and fingerprint-guarded. With no `.<book>.temp.bin` it raises an uncaught traceback, so it goes on neither a smoke nor a full run that follows a skipped smoke; and a cache written with `--only_filelist` is refused by the full run, whose filters differ |
| `--parallel-workers` | integer | **1 (sequential)** | a long book where wall-clock matters more than consistency. Then drop to bare `--use_context`: **`--use_context session` is refused with it** (one history, which workers cannot share), and window context is per chapter anyway, so continuity stops at every chapter boundary. **Never on `codex`** (below) |
| `--extra_body` | JSON string | *unset* | the endpoint needs a vendor-specific parameter |
| `--accumulated_num` | integer (tokens per request) | *unset* — derived per run: `1200` stock prompts, up to `1600` under a fat `--prompt`, `800` off-schema; session runs keep the un-halved value; the run narrates its choice | the run keeps printing misalignment recoveries (shorten it), or you want fewer/larger requests for cost (a typed value always wins, un-halved; `1` turns grouping off). Trade-offs and measurements: `docs/session_grouping_eval.md` §7 |
| `--max-batch-units` | integer (units per request) | `16` (`8` automatically off-schema) — owner-set margin under the measured 64-unit fault onset | the run keeps printing misalignment recoveries: halve it (`8`, then `4`). Never past `48`. Detail: `docs/session_grouping_eval.md` §7 |

### Never pass in plan mode

- `--translate-tags` — the plan partitions the whole book; a tag selection
  has nothing left to select, and the run says it is ignoring it.
- `--plan-dry-run` — it returns *before* classification, so the plan it
  writes has every `action` still `null` and there is no agent handoff
  block to work from. The base command writes the same plan *and* hands
  off. The preview does forecast which channel classification would use
  on this route (structured output, a plain session, or plan mode off) —
  the partition is exact, the verdicts are what is missing.
- `--allow_navigable_strings` — explicitly ignored; the plan already
  accounts for every text node.
- `--batch` / `--batch-use`, `--retranslate`, `--sentence_mode` —
  **refused** in plan mode: the run prints which flag and exits 1.
  `--batch` is also refused on the codex route, which has no batch API.
- `--block_size` / `--batch_size` — they re-cut text the plan has already
  partitioned.
- `--parallel-workers` on `codex`: one thread is the whole context, and
  turns serialize anyway. Refused on the command line.
- `--parallel-workers` with `--use_context session`, on any route: one
  history is the whole context, and a worker cannot share it. Refused on
  the command line.

## Halt / resume — safe by construction

- **Progress saves after every chapter** and on interrupt or crash. To halt
  a background run: `kill -INT <pid>`; even SIGKILL loses at most the
  current chapter.
- **SIGINT does not halt a `--parallel-workers` run promptly.** Every
  chapter is dispatched up front, so the process exits only after all of
  them finish (measured: a signal at 20/70 chapters still translated
  70/70). Checkpoints stay correct; only the stopping fails. Say so before
  a big parallel run, and use SIGKILL when a run must stop now.
- **A halted run exits 130**, a finished one 0, the agent handoff 3, and
  every refusal 1. Read the code, not the log, when a background run ends.
- **Resume = rerun the same command with `--resume` added.** Same book, same
  plan, continues where it stopped. This is also why a smoke test is never
  wasted money, and why a full run that dies halfway is not either.
- **Do not edit the plan or swap the book between halt and resume** — the
  fingerprint refusal protects against translations landing on the wrong
  paragraphs. Changed your mind mid-book? Finish the run, or delete
  `.<book>.temp.bin` and restart cleanly. Never work around the refusal
  without telling the user what gets re-paid.

## 6. Deliver

**Read the epub back before you hand it over.** With the smoke skipped this
is the only look anyone takes at the markup, so it is not optional. Unzip
`<book>_bilingual.epub` and read around a translated unit in one early and
one late chapter:

- is it actually in the target language?
- does the translation sit **next to** its original, carrying the same tag
  and class (unless `--single_translate`)?
- are `id` attributes and internal fragment links intact?
- did the plan's `skip` decisions actually hold?
- is there any delimiter or JSON residue in the text?

A zero exit code and a clean log do not answer any of those.

Then report the end-of-run coverage/skip stats, every classification
decision you made (resolved nulls and any non-null overrides, with the
name-then-rule reasoning), what the read-back showed, and hand over
`<book>_bilingual.epub`.

## Context hygiene

- **Never** let a translation run stream into the conversation — every paid
  run gets `--quiet` *and* a log-file redirect.
- Everything the next step needs is on disk; nothing critical lives only in
  conversation.
- **Compaction threshold** — *your* context, not the run's: for a small book
  do not compact at all. Only compact when context is genuinely pressured
  (≳70% used), at most once, at the natural boundary — after plan editing,
  before the full run.

## Failure modes (all fail loud by design)

| symptom | meaning |
|---|---|
| `doesn't apply JSON schema … using delimiter method`, `honors JSON schema shape but not value constraints`, `no strict structured-output support` | **not a failure.** The endpoint does not do strict schema decoding, so translation uses the delimiter method. Expected on the anthropic route and most proxies; note it, do not switch models over it |
| `refused the … request shape; using a simpler one` | classification's ladder descended a rung. Informational |
| a `--test` run printing its request count (grouping merges the slice into few requests), or that classification covers the whole book regardless of `--test` | **not a failure.** New compatibility narration; the smoke recipe triggers both by design |
| `classifying over a plain session` | **not a failure.** The endpoint has no structured output, so plan classification runs over a conversation with verbatim `skip`/`translate` replies |
| `N misaligned batches this run — … lower --max-batch-units or --accumulated_num` | the model keeps miscounting large batches; follow the hint on the next run |
| `… N invented (⟦…⟧) — reconciled` | the model typed a marker token where none belongs; the run scrubbed it before writing. Informational — worth a read-back look only if it repeats |
| fingerprint refusal on `--resume` | book file or plan changed since the cache was written; delete the cache only if that was intentional. A checkpoint refusal naming language/prompt/model means the resume flags differ from the original run's — rerun with the original flags, or delete the checkpoint |
| `undecided signature(s)` on plan load | null actions remain — answer every open question, then rerun |
| `invalid action` on plan load | typo in a hand-edited `action` — fix the JSON, rerun |
| coverage-gate error / empty plan | the plan skips nearly everything — re-check the plan |
| `--only_filelist / --exclude_filelist names N document(s) this book does not have` | a typo, caught before anything is paid for; the message lists the near matches |
| `--use_context session is not implemented for the … route` | that route keeps no history; use bare `--use_context`, or a route that does (§1d) |
| legacy-cache refusal | the cache came from an old tag-mode run — delete it |
| `--use_context session` not supported for *txt/srt/pdf* | those loaders never hand context to the model; epub is where this workflow lives anyway |
| codex: `… codex login, then run this again` | the sidecar is up but not signed in. One `codex login`, then rerun; nothing was paid |
| codex: waiting *N* min for the window to reset | the 5-hour plan window is spent — the run sleeps and continues by itself |
| codex: `the Codex plan allowance is spent and does not reset until …` | the weekly limit. The run exits 1, having saved whatever the loader checkpoints; rerun with `--resume` after the time it names |
| `handoff report failed (…); starting the next window` | one compact produced no report. Informational; translation continues |

## Reference files

- **`references/providers.md`** — route table, probe recipes, per-format
  capability caveats. Read before the first command whenever the endpoint is
  not a plain OpenAI-shaped host.
- **`references/prompt-files.md`** — the `--prompt` file contract, linting,
  and keeping a user's prompt out of git.
