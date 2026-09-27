# EPUB flow: plan → classify → full run

Loaded from SKILL.md when the book is an `.epub`. `ROUTE` and `CONTEXT`
come from `references/route-setup.md`.

User pages, when you need more than this file says:
`docs/formats/epub.md` (what the EPUB loader reads and writes, every flag
that applies), `docs/features/plan-mode.md` (the plan, the classifier, every
plan line the run prints), `docs/features/session-mode.md` (the history,
compaction, handoff), `docs/cmd.md` (every flag).

Two runs of one command with small flag changes: **plan → full**. A smoke
test sits between them, optional and skipped by default (step 4 says when a
book has earned one).

## The base command

```bash
set -a; source .env; set +a
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" \
  --language "$LANG" --plan-classify agent "${CONTEXT[@]}"
```

Per step: `--plan-classify agent` always (unless the user named a
classifier: "A classifier the user names" below); the full run adds `--quiet`, and
`--resume` only once a cache exists (step 5); the optional smoke adds
`--quiet --test --test_num 8`. Nothing from the "Never pass" list below.

By kind of book (the user-facing version, with the endpoint and system rows, is `docs/features/recommended-epub.md`):

| book | what changes |
|---|---|
| novel | nothing: the base command, session on |
| textbook with tables and formulas | nothing on the command; in step 3 read the table-cell, caption and sidebar signatures with care, they are where a textbook's real text hides. MathML and SVG never reach the model, so equations stay as they are |
| paper as EPUB | as a novel |
| dictionary, critical edition, apparatus-heavy | the plan legitimately covers less; lower `--plan-min-coverage` deliberately and say so in the plan summary |
| a long book where wall-clock matters more than consistency | `--parallel-workers 4` with bare `--use_context` (session is refused with workers); say that continuity stops at every chapter boundary |

## 2. Plan (agent mode translates nothing)

Run the base command once. It partitions the whole book, writes
`<book>_plan.json`, prints a handoff block, and exits (code 3) without
translating.

**It stops only while a row is still `null`.** Once every row is decided,
the same command is the paid full run of the whole book: it does not stop
to show the plan again (a field test spent 14 units on the ChatGPT plan
this way). After step 3 the base command is a paid run however it is
dressed: `--quiet` and a log redirect keep it out of the conversation, they
do not stop it. To look at the plan again without paying, read
`<book>_plan.json` itself, or run the base command with `--plan-dry-run`,
which prints a fresh report, keeps your edited plan file and never
translates. Run the base command again only as the smoke (step 4) or the
full run (step 5).

**Offline on every route.** Nothing is asked of the endpoint until the
first paid request, so a wrong model id or a dead gateway surfaces there,
not here; with the smoke skipped, that is the first minute of the full run,
and it costs nothing.

What the report gives you, and what each part is for:

- **A signature per row** (`p.calibre_13`, `span.page-no` …) with unit
  count, char total, share of the book, and up to 5 real samples: the
  evidence you classify from in step 3.
- **Coverage**, checked against `--plan-min-coverage`. Every text node is
  either a translation unit or a skip with a stated structural reason, and
  the run proves the accounting adds up, so a low number means the book
  really is mostly apparatus, not that something was quietly dropped.
- **Grouped batches.** Consecutive units share one request under the
  grouping caps (`--accumulated_num` tokens, `--max-batch-units` units), so
  each unit is translated with its neighbours in view. The report's
  `batches:` line says how the partition packed, and the narration line
  under it carries the true per-request numbers for the route.
- **Inline markers.** An excluded inline (`<code>`, `<sup>`, an `<img>`)
  that is short, or of any length when it carries no prose word (a URL, a
  spaced formula), does not split its sentence: the model sees a `⟦code1⟧`
  token and the original node is put back at that spot afterwards.

Symptom → knob, when reading the report:

| symptom in report | knob |
|---|---|
| short lines split awkwardly across requests | raise `--accumulated_num` (the token budget is what usually splits them) |
| `N marker(s) missing … — reconciled` lines | benign one-off; if most units say it, the model ignores the token instruction: try a stronger model |
| legit low coverage (dictionary, critical edition, apparatus-heavy) | lower `--plan-min-coverage` deliberately, and say so in the plan summary |
| visible text under a `hidden` skip reason, or vice versa | inspect the epub's CSS before overriding |

## 3. Classify (you are the classifier)

Read `<book>_plan.json`. Rows with `"action": null` are the plan's open
questions: every one must become `"translate"` or `"skip"`, and the
translate run refuses to start while any null remains. For each: **name what
the text is first** (prose, verse, dialogue, heading, caption, running head,
page/line number, sigla, cross-reference label, boilerplate, decorative
marker), *then* rule; naming before ruling prevents rationalizing a snap
verdict. Judge from `samples`, `units`, `chars`, `pct`, `mean_chars`; when
the samples do not settle it, choose `"translate"`: over-translating is
cheap, losing content is not. Want more evidence? `unzip -p <book> <file>`
and read the markup around the signature.

**An inline skip cuts its text out of the surrounding block.** Skipping a
signature that wraps part of a word (a drop-cap initial, a small-caps
fragment, a decorative first letter) does not leave the word alone: the
block is assembled without that text, so `CHAPTER I` is sent to the model
as `HAPTER` and `MR. JONES` loses its `M`. Skip an inline signature only
when its text is *whole* (a URL, a page number, a standalone marker). After
editing the plan, rerun the plan step and read the affected block rows'
samples again: a decapitated sample is the tell, and it is visible before
anything is paid for. Do this re-look **before you resolve the last
null**: with one row still open the plan step stops at the handoff again;
with none open it translates the book.

Non-null rows (prose spine, headings, poetry) may also be changed if their
samples convince you, but the nulls are the required work. Hold a non-null
override to the same name-then-rule discipline, and **record every one in
the step-6 report**: the user should see where you disagreed with the
plan's defaults, not discover it in the output. Edit **only** the `action`,
`decided_by` and `content_type` fields; `key`, `scope`, `disposition` and
the rest are the tool's (`disposition` stays `null`). Validation is fail-closed: a typo'd
action, missing hash or edited book is a hard error on the next run, never
a silent default.

## 4. Smoke test: optional, skipped by default

**Default: skip it and go to step 5.** The plan step already caught the
structural mistakes offline and for free, a dead endpoint or wrong model id
fails in the first minute of the full run having paid nothing, and the
markup checks the smoke exists for are done at delivery either way (step
6). Say you are skipping it, in one line, when you state the flag choices.

**Run one when the book has earned it.** Any of:

- **The user brought a `--prompt` file**, or you are translating into a
  language or register this repo has not produced before. A prompt applies
  to every unit, and a prompt that reads well can still produce markup that
  does not.
- **You argued with the plan**: several resolved nulls, any non-null
  override, or a book whose apparatus is tangled enough that you want to
  see a `skip` hold before paying for the whole spine.
- **The book is big enough that a wasted full run is real money.** The plan
  report's char total is the number to judge on; when a full run would cost
  dollars rather than cents, eight units first is cheap insurance.
- **The user asks**, or asks what the output will look like before
  committing.

**How.** Base command + `--quiet --test --test_num 8 > smoke.log 2>&1`.
Check results **after** the run, from files, never from live output. Units
are consumed in **spine order**, so check which documents the first 8 units
come from: a large nav or title page can absorb the whole budget (a 458 KB
nav once ate all 20 units of a poetry smoke). When that happens, point the
smoke at a body chapter with `--only_filelist <content doc>` rather than
raising `--test_num`. A `skip` whose signature is not among the first 8
units cannot show in the smoke (the run says `N decided signature(s) do not
occur in this run's partition`); check it at delivery instead. Then read
the partial `<book>_bilingual.epub` back
with the step-6 checklist and check `smoke.log` for error lines. The cache
carries into the full run, so nothing paid here is re-paid, and the full
run that follows takes `--resume`.

## 5. Full run

Base command + `--quiet`, minus `--test`. Always in the background with
output to a log:

```bash
… --quiet > run.log 2>&1                # first run: no cache yet
… --quiet --resume > run.log 2>&1       # every rerun, and after a smoke
```

**`--resume` goes on the second run, not the first.** With no
`.<book>.temp.bin` it raises `can not load resume file` and translates
nothing. On any crash, rerun the same command with `--resume` added. If the
run stops with a fatal translation error, fix the cause (key quota, endpoint
down) and rerun; do not delete the cache unless the book or plan changed
intentionally.

## Flag menu: EPUB flags

**Defaults below are the recommendation.** Route and context flags are in
`references/route-setup.md`.

### Plan mode

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--plan-classify` | `auto`, `none`, `all`, `model`, `agent` | **`agent`** | the user names a classifier: `auto` ("A classifier the user names" below) |
| `--classify-model` (old name `--plan-classify-model`), `--classify-base-url`, `--classify-key` | a model id, or `jev` | **not passed** | the user names a classifier (below). With `agent` it is ignored, with a warning: agent mode never pre-fills the plan, because an agent judges worse from pre-filled verdicts |
| `--classify-min-confidence P` | 0 to 1, Jev-compatible classifiers only | **not passed**: 0.95, measured | the user asks for more of Jev's skips kept; lower keeps more, at their risk |
| `--plan-min-coverage` | 0.0–1.0 | **0.5** | a dictionary, critical edition or apparatus-heavy book legitimately translates less; lower it deliberately and say so |
| `--exclude-translate-tags` | comma-separated tags; `""` excludes nothing | **`sup,code`** | the book puts real prose in one of those, or another tag is pure apparatus |
| `--accumulated_num` | integer (tokens per request) | *unset*, derived per run: `1200` stock prompts, up to `1600` under a long `--prompt`, `800` off-schema; session runs keep the un-halved value; the run narrates its choice | the run keeps printing misalignment recoveries (shorten it), or the user wants fewer, larger requests for cost (`1` turns grouping off). `docs/evaluation/grouping-batch-size.md` |
| `--max-batch-units` | integer (units per request) | `16` (`8` automatically off-schema), a chosen margin under the measured 64-unit fault onset | the run keeps printing misalignment recoveries: halve it (`8`, then `4`). Never past `48` |

### Output form

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| *(bilingual)* | — | **bilingual: translation added beside the original.** The default, and the assumption (SKILL.md) | — |
| `--single_translate` | on/off | **off** | **only** when the user asked for a translated-only book in so many words. The original is replaced, so there is nothing to compare against afterwards |
| `--translation_style` | CSS declarations | *unset* | the translation should be visually separated, e.g. `"color:#808080;font-style:italic"`. It replaces `--translation_color` rather than merging with it (the run says so) |
| `--translation_color` | a colour | *unset* | the user wants only a colour and no other CSS |
| `--no_disclosure` | on/off | **off: the epub says it is an AI translation** (one small line below the book intro, "Translated by \<model\>, \<year\>.") | the user asks for the marking gone in so many words. Say what they lose: a reader can no longer tell the translation from a human one, and the model is no longer recorded (it turns off `--translation-metadata` too) |

### Scope and run control

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--only_filelist` / `--exclude_filelist` | comma-separated internal filenames, **OPF-relative** (`s04.xhtml`, not `EPUB/s04.xhtml`) | *unset* (whole book) | the user wants specific chapters, or a smoke must skip front matter. A name the book does not have fails loud, before anything is paid for. **An only-list wins outright** |
| `--test` / `--test_num` | flag + integer | *unset*: the smoke is optional (step 4) | a smoke: `--test --test_num 8`, or ~20 on poetry-heavy books once the first N units are known to be body text |
| `--quiet` | on/off | **on for every paid run** | never off for a run that translates |
| `--resume` | on/off | **off on the first run, on for every rerun** | never off after a crash. A cache written with `--only_filelist` is refused by the full run, whose filters differ |
| `--parallel-workers` | integer | **1 (sequential)** | a long book where wall-clock matters more than consistency. Then drop to bare `--use_context`: **session is refused with it**, and window context is per chapter, so continuity stops at every chapter boundary. Never on codex |

### Never pass in plan mode

- `--translate-tags`: the plan partitions the whole book; the run says it
  is ignoring it.
- `--plan-dry-run`: it returns *before* classification, so its plan has
  every `action` still `null` and no agent handoff block. The base command
  writes the same plan *and* hands off.
- `--allow_navigable_strings`: ignored; the plan accounts for every text
  node.
- `--batch` / `--batch-use`, `--retranslate`, `--sentence_mode`: **refused**
  in plan mode; the run prints which flag and exits 1.
- `--block_size` / `--batch_size`: they re-cut text the plan has already
  partitioned.
- `--poetry-group-size`: deprecated; grouping covers verse.
- `--parallel-workers` on codex, or with `--use_context session`: refused
  on the command line.

## A classifier the user names

Agent mode is the default because you judge better than a pre-filled plan.
When the user asks for a classifier to decide the skips ("use Jev", "let
gpt-5.6-luna decide what to skip"), that is their call: use `auto` with it,
say in the choices block that the plan is the classifier's, not yours, and
still read its skips. This picks another mode; agent mode itself never
takes a pre-filled plan. None of these is such a request: naming the
*translation* model, a Jev key being set, or a provider entry carrying a
`classify_model`. Nor is "agent mode": then it stays agent mode.

```bash
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" --language "$LANG" \
  --plan-classify auto --classify-model jev "${CONTEXT[@]}" \
  --quiet --test --test_num 8 > smoke.log 2>&1
```

- **Which classifier.** `jev` is TypeSafe's (reads `JEV_API_KEY` or
  `TYPESAFE_API_KEY`); `--provider openai-jev` from the shipped example
  does the same in one word. Featherless's keyless demo is
  `--classify-model featherless-ai/Qwen3.8-27B-classifier
  --classify-base-url https://simple-jev-demo-api.featherless.ai/v1/classifier`.
  Any chat model id works too. Gateways and key rules:
  `docs/providers.md#jev-and-jev-compatible-classifiers`.
- **There is no handoff.** The classifier decides every row, and the run
  goes straight on to translate. So the first run is the smoke above
  (classification covers the whole book whatever `--test` says, and the
  run says so), then the full run is the same command with `--resume` in
  place of `--test --test_num 8`, output to `run.log`.
- **Read its skips before the full run.** In `<book>_plan.json` each
  row's `content_type` carries the verdict and its confidence: `jev
  verdict skip, confidence 0.98`, or `jev verdict skip at confidence 0.62,
  below the gate: translate` for a skip the gate turned back. Report the
  skips and their confidences to the user; overrule one only by telling
  them.
- **The gate.** A Jev skip below 0.95 becomes `translate`; about nine of
  ten do on the measured corpus, and what remains is apparatus.
  `--classify-min-confidence 0.5` keeps more skips (9 against 1 on the
  test book). Only on a Jev-compatible classifier; the run warns
  otherwise.

## Halt / resume: safe by construction

- **Progress saves after every chapter** and on interrupt or crash. To halt
  a background run: `kill -INT <pid>`; even SIGKILL loses at most the
  current chapter.
- **SIGINT does not halt a `--parallel-workers` run promptly.** Every
  chapter is dispatched up front, so the process exits only after all of
  them finish. Checkpoints stay correct; only the stopping fails. Say so
  before a big parallel run, and use SIGKILL when a run must stop now.
- **A halted run exits 130**, a finished one 0, the agent handoff 3, and
  every refusal 1. Read the code, not the log, when a background run ends.
- **Resume = rerun the same command with `--resume` added.** Same book, same
  plan, continues where it stopped.
- **Do not edit the plan or swap the book between halt and resume**: the
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
- is any translation byte-identical to its original (an echo, not a
  translation)?

A zero exit code and a clean log do not answer any of those.

Then report the end-of-run coverage/skip stats, every classification
decision you made (resolved nulls and any non-null overrides, with the
name-then-rule reasoning), what the read-back showed, and hand over
`<book>_bilingual.epub`.

## Failure lines (all fail loud by design)

Route-wide lines (schema verdicts, retries, codex, session) are in
`references/route-setup.md`; every plan line is explained on
`docs/features/plan-mode.md#what-can-go-wrong`.

| symptom | meaning |
|---|---|
| `refused the … request shape; using a simpler one` | classification's ladder descended a rung. Informational |
| a `--test` run printing its request count, or that classification covers the whole book regardless of `--test` | **not a failure.** Compatibility narration; the smoke recipe triggers both by design |
| `--test_num counts units, not requests: this slice is 8 unit(s) in 2 request(s). A slice this small may never reach a group rollover or a session compaction` | **not a failure.** The smoke is for markup and skips, not for rollovers; leave `--test_num` at 8 |
| `N decided signature(s) do not occur in this run's partition and were left untouched` | informational: those rows are not in this `--test` slice or `--only_filelist` selection; they apply in the full run |
| `skipped characters: user-excluded=N, excluded-tag=M` | characters, not units, that the plan's `skip` rows and excluded tags keep out of the translation |
| `N misaligned batches this run — … lower --max-batch-units or --accumulated_num` | the model keeps miscounting large batches; follow the hint on the next run |
| `… N invented (⟦…⟧) — reconciled` | the model typed a marker token where none belongs; the run scrubbed it. Informational, worth a read-back look only if it repeats |
| fingerprint refusal on `--resume` | book file or plan changed since the cache was written; delete the cache only if that was intentional. A checkpoint refusal naming language/prompt/model means the resume flags differ from the original run's: rerun with the original flags, or delete the checkpoint |
| `undecided signature(s)` on plan load | null actions remain: answer every open question, then rerun |
| `invalid action` on plan load | typo in a hand-edited `action`: fix the JSON, rerun |
| coverage-gate error / empty plan | the plan skips nearly everything: re-check the plan |
| `--only_filelist / --exclude_filelist names N document(s) this book does not have` | a typo, caught before anything is paid for; the message lists the near matches |
| legacy-cache refusal | the cache came from an old tag-mode run: delete it |
| `… names a classifier, and --plan-classify agent … it is ignored this run` | a `--classify-model`, `--classify-base-url` or `--classify-key` flag, or an entry's `classify_model`, rode along; drop it (agent mode asks no model), or switch to `auto` if the user named that classifier |
