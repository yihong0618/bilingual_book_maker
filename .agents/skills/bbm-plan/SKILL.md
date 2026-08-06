---
name: bbm-plan
description: Translate a whole EPUB into a bilingual book with bilingual_book_maker's plan mode - greedy partition, agent-reviewed classification plan, cheap smoke test, then a full resumable run. Use when the user wants a book translated well (running heads, page numbers, and apparatus skipped deliberately) rather than a quick --translate-tags pass, or asks for "plan mode" / "bbm" translation.
---

# bbm-plan: plan-mode EPUB translation

**Hard constraint: this skill uses `--plan-classify agent`, and only
that.** No other `--plan-classify` value exists as far as this skill is
concerned. The point of the skill is that *you*, the coding agent, own the
classification step against the real samples — the plan arrives with its
uncertain signatures set to `"action": null`, and the translate run
refuses to start while any null remains. There is no shortcut around
answering them. The planner role is judgment work expected of an advanced
agent: do it in the main agent with the full session context, never
delegate plan editing to a subagent or a small/fast model.

Repo: the repository this skill ships in (`make_book.py` at its root). Run
every command from the repo root. Plan mode is **epub-only**.

You are the trained CLI operator here: you pick every flag from the guide
below and state the choice with a one-line reason. The user is never asked
to experiment with flags, halt semantics, or resume mechanics — they hand
over a book and credentials, approve the plan and the cost, and get a
bilingual epub back.

The workflow is one curl plus three runs of one command with small flag
changes (plan → smoke → full). All state
lives on disk (`.env`, `<book>_plan.json`, the resume cache, `run.log`) — any
step can be redone after a crash or a new session.

## 0. Credentials

Copy `assets/env.example` (this skill dir) to `.env` at the repo root and
have the user fill it.
The one mandatory field is `MODEL` — the exact model name. Beyond that,
one API key, whichever provider the model lives at; the key that is filled
decides the route: an OpenAI-style key → `--model openai
--model_list "$MODEL"`, `BBM_CLAUDE_API_KEY` → `--model claude`,
`BBM_GOOGLE_GEMINI_KEY` → `--model gemini`, and so on. **Never `--model
chatgptapi` for an arbitrary model id** — that preset runs a hardcoded
GPT-3.5-family model discovery and ignores `--model_list` entirely
(`book_maker/cli.py:806-823`); only `openai` and `groq` honor it. `MODEL`
and `BBM_API_BASE` are skill-level fields — make_book.py does not read
them from env; translate them to flags yourself.

Source it in the same Bash call as the run: `set -a; source .env; set +a; …`.
Never echo values; verify presence with `[ -n "$MODEL" ]`-style checks. If
`.env` is unfilled, stop and ask — do not accept a pasted key as an
argument (it leaks into shell history and prompt logs).

## 1. Intake

Ask for: book path and target `--language` (e.g. `zh-hans`, `ja`). The
model comes from `.env`. Optional: `--single_translate` (replace instead
of bilingual), `--translation_style`.

Base command used by every step below (OpenAI-style route shown — swap the
provider flags per the key, as above):

```bash
set -a; source .env; set +a
API_BASE_FLAG=()
[ -n "$BBM_API_BASE" ] && API_BASE_FLAG=(--api_base "$BBM_API_BASE")
python make_book.py --book_name "$BOOK" --model openai \
  --language "$LANG" --plan-classify agent \
  --model_list "$MODEL" "${API_BASE_FLAG[@]}"
```

(The conditional flag is an array on purpose: `${VAR:+--flag "$VAR"}`
mis-tokenizes under zsh — macOS's default shell — into a single argv word
that argparse rejects. The array form works in bash and zsh alike.)

### Prompt-file probe

Look for an existing prompt template in two places: the book's own
directory, and the repo root you run make_book.py from — `prompt.json`,
`prompt.txt`, `prompt*.md`, `prompt_template*`.
**A candidate must carry a diff**: in a git repo, only files that are
untracked (new) or modified against HEAD count
(`git status --short -- 'prompt*'`) — cleanly tracked `prompt*` files are
the repo's shipped examples, not the user's voice, and are never offered.
The moment any such new or diff'd file is found, **ask the user first** —
before linting it, before building any command around it, and never
silently adopting or ignoring it. Only after the user says yes, lint it;
the contract (`book_maker/cli.py:parse_prompt_arg`) is:

- `.json`: an object with **only** the keys `user` (required) and `system`
  (optional) — any other key is rejected
- the `user` template must contain the literal placeholder `{text}`;
  `{language}` is optional
- `.txt` becomes the user template as-is (same `{text}` rule);
  `.md` is parsed as PromptDown

Fix or report lint problems now — the CLI would reject the file at run
start anyway, but a traceback after the user already approved a paid run
is the wrong place to learn about a missing `{text}`.

Prompt files stay **out of git**, same as `.env` — they are the user's
personal voice and may carry names/glossary content. If the working
directory is a repo, check with `git check-ignore prompt.json .env`; add
whatever isn't covered to `.git/info/exclude` (local-only — never edit the
project's tracked `.gitignore` for this).

## 1b. Endpoint probe (sub-cent, before anything else)

Prove key + endpoint + model name in one tiny call, so a typo surfaces now
and not after the classify work:

```bash
curl -sS "${BBM_API_BASE:-https://api.openai.com/v1}/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
```

Any JSON with a `choices` array passes. **Probe the format, don't ask the
user to fix it**: on a 404, retry with `/v1` appended to (or stripped
from) the base; on an auth-header rejection, try the provider's native
scheme (`x-api-key` for Anthropic, key-in-query for Gemini) and switch to
that provider's `--model` flag. Whatever base passes the probe is the one
`--api_base` gets. Only stop and ask when the key itself is rejected by
its own provider. (Structured-output capability is *not* tested here; the
probe at the smoke step does that.)

## 2. Plan (free — agent mode makes no API call)

Run the base command once. It partitions the whole book, writes
`<book>_plan.json`, prints a handoff block, and exits without translating.

### How the partition works (read this before judging its report)

```
partition → group → plan JSON → your edits → coverage gate → translate
```

1. **Partition is greedy and coverage-complete**: every text node in each
   rendered `<body>` becomes exactly one of — a translation unit, or a skip
   with a *structural* reason (whitespace, link, symbol, hidden, ruby,
   pagebreak, non-content, excluded tag). No content heuristics; the run
   prints and checks the invariant
   `total_chars == translated + sum(skip reasons)`. Nothing is silently
   dropped — anything questionable surfaces as a signature for you to judge.
2. **Grouping, tier 1 (poetry)**: sibling runs whose line median is short
   are stanza-shaped; they are windowed (`--poetry-group-size`, default 8
   lines/request) so verse gets translated with its neighbors as context.
3. **Grouping, tier 2 (short-unit sweep)**: leftover short units are packed
   into small windows purely to save per-request overhead. These are *not*
   poetry and stay classification candidates.
4. **Plan JSON**: one row per tag signature (`p.calibre_13` …) with unit
   count, char total, up to 5 real samples, and an `action`.
5. **Your edits**, then at translate time: fail-closed validation plus a
   coverage gate — if the edited plan would translate less than
   `--plan-min-coverage` (default 0.5) of the book's text, hard stop.

Symptom → knob, when reading the report:

| symptom in report | knob |
|---|---|
| verse split awkwardly across requests | raise `--poetry-group-size` |
| legit low coverage (dictionary, critical edition, apparatus-heavy) | lower `--plan-min-coverage` deliberately, say so in the plan summary |
| visible text under a `hidden` skip reason, or vice versa | inspect the epub's CSS before overriding; the resolver follows stylesheet source order |

## 3. Classify (you are the classifier)

Read `<book>_plan.json`. Rows with `"action": null` are the plan's open
questions — every one must become `"translate"` or `"skip"`, and the
translate run refuses to start while any null remains. For each: **name
what the text is first** (prose, verse, dialogue, heading, caption,
running head, page/line number, sigla, cross-reference label,
boilerplate, decorative marker), *then* rule — naming before ruling is
the same discipline the schema forces on LLM classifiers, and it prevents
rationalizing a snap verdict. Judge from `samples`, `units`, `chars`,
`pct`, `mean_chars`; when the samples do not settle it, choose `"translate"` —
over-translating is cheap, losing content is not. Want more evidence?
`unzip -p <book> <file>` and read the markup around the signature.

Non-null rows (prose spine, headings, poetry) may also be changed if their
samples convince you, but the nulls are the required work. Hold a non-null
override to the same name-then-rule discipline, and **record every one in
the step-6 report** (signature, what you named it, why you overrode) — the
user should see where you disagreed with the plan's defaults, not discover
it in the output. Edit **only** `action` fields. Validation is fail-closed
— a typo'd action, missing hash, or edited book is a hard error on the
next run, not a silent default.

## 4. Smoke test (pennies)

Base command + `--quiet --test --test_num 8 > smoke.log 2>&1`. `--quiet`
turns off the progress bars and the per-unit source/target echoes (without
it, a `--test` run prints the full text of every translated unit); the
redirect catches what remains. You check results **after** the run, from
files — never from live output.

The run probes the endpoint's structured-output support, translates the
first 8 units of the *real* plan (sequentially — `--test` forces it), and
writes the resume cache. Verify by unzipping the partial
`<book>_bilingual.epub`: right target language? formatting intact
(translation carries the same tag/class as its original)? Check
`smoke.log` for error lines. The cache is plan-fingerprint-guarded and
carries into the full run — nothing paid here is re-paid.

A schema-incapable endpoint, a wrong-language reply, or broken formatting
all surface **here**, not three chapters into the paid run. (Key and model
name were already proven at step 1b.)

## 5. Full run

Base command + `--quiet --resume`, minus `--test`. Always in the
background with output to a log:

```bash
… --quiet --resume > run.log 2>&1
```

(Bash `run_in_background: true`; poll with `tail -5 run.log`.) On any crash,
rerun the identical command — resume is positional and fingerprint-guarded.
If the run stops with a fatal translation error, fix the cause (key quota,
endpoint down) and rerun; do not delete the cache unless the book or plan
changed intentionally.

## Flag guide — you choose, per book

| decision | flag | choose it when |
|---|---|---|
| output form | *(default)* bilingual | user reads both languages side by side — the usual ask |
| | `--single_translate` | user wants a translated-only book, original replaced |
| speed | *(default)* sequential | small book (< ~30 chapters), or first run with a new endpoint |
| | `--parallel-workers 4` | large book; plan mode isolates per-chapter state, safe with or without context |
| consistency | `--use_context` | fiction with recurring names/terms; costs extra tokens; in parallel runs context is chapter-local (until the phase-2 brief exists) |
| voice/style | `--prompt prompt.json` | user states a register ("literary", "plain modern") — encode it once in the system message |
| styling | `--translation_style "color:#808080;font-style:italic"` | bilingual output should visually separate the translation |
| scope | `--only_filelist` / `--exclude_filelist` | user wants specific chapters; exact internal names — a typo fails loud at the coverage gate |
| sampling | `--temperature` | leave the default unless output is erratic; then lower it and re-smoke |
| smoke size | `--test_num 8` (default here) | raise to ~20 on poetry-heavy books so the smoke spans a full stanza window or two |
| log noise | `--quiet` (always, on smoke and full run) | bars and per-unit echoes off; reports and errors still print — logs stay readable, context stays clean |

Do **not** pass in plan mode: `--accumulated_num` and
`--allow_navigable_strings` (both explicitly ignored — plan mode batches for
itself and accounts every node already; a non-1 `accumulated_num` also
disables the interrupt-save path), `--batch` (OpenAI batch API, untested
with plan mode), `--translate-tags` (plan mode overrides it), `--interval`
(Gemini route only, and only when it rate-limits), `--plan-dry-run` (it writes a plan with
every action already decided, which suppresses the null questions and the
agent handoff — the plan run of this workflow is already free).

## Halt / resume — safe by construction

- **Progress saves after every chapter** and on interrupt or crash. To halt
  a background run: `kill -INT <pid>` (SIGINT fires the save handler; even
  SIGKILL loses at most the current chapter).
- **Resume = rerun the identical command with `--resume`.** Replay is
  positional and fingerprint-guarded: same book, same plan, continues where
  it stopped. This is also why the smoke test is never wasted money.
- **Do not edit the plan or swap the book between halt and resume** — the
  fingerprint refusal is deliberate, protecting against translations landing
  on the wrong paragraphs. Changed your mind about a signature mid-book?
  Finish the run, or delete `.<book>.temp.bin` and restart cleanly. Never
  work around the refusal by deleting the cache *without* telling the user
  what gets re-paid.

## 6. Deliver

Report the end-of-run coverage/skip stats, every classification decision
you made (the resolved nulls and any non-null overrides, with the
name-then-rule reasoning), and hand over `<book>_bilingual.epub`. Suggest
spot-checking one early and one late chapter.

## Context hygiene

- **Never** let a translation run stream into the conversation — every
  paid run (smoke and full) gets `--quiet` *and* a log-file redirect. This
  applies to any command that translates, whichever step it appears in.
- Everything the next step needs is on disk; nothing critical lives only in
  conversation.
- **Compaction threshold:** for a small book (smoke + full run expected to
  finish within the session comfortably), do not compact at all. Only
  compact when context is genuinely pressured (≳70% used), at most once,
  and at the natural boundary: after plan editing, before the full run.
  Compacting more often than that costs more than it saves — each compact
  forces re-reading plan/log state that was already in context.

## Failure modes (all fail loud by design)

| symptom | meaning |
|---|---|
| `no strict structured-output support` | model can't do schema output for translation — pick another model |
| fingerprint refusal on `--resume` | book file or plan changed since the cache was written; delete cache only if that was intentional |
| `undecided signature(s)` on plan load | null actions remain — answer every open question in the plan JSON, then rerun |
| `invalid action` on plan load | typo in a hand-edited `action` — fix the JSON, rerun |
| coverage-gate error / empty plan | `--only_filelist` misspelled, or the plan skips nearly everything — re-check the plan |
| legacy-cache refusal | the cache came from an old tag-mode run — delete it |

## Next phase

`references/next-phase.md` in this skill dir: a book brief (short intro + character-name
glossary) drafted by the agent at classify time and injected through
`prompt.json`, making `--parallel-workers` runs terminology-consistent
without `--use_context`'s sequential warmup. Planned, not yet built.
