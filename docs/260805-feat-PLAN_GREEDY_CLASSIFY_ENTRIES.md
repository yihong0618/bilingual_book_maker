# Plan mode v2: greedy partition + classification entries

Branch `feat/translation-plan-partition` (PR #545), 260805. Supersedes the
design in `260731-feat-PLAN_LLM_CLASSIFY.md`, whose classifier now lives in a
package and asks about a different candidate set. Read that one only for the
history of the guardrails.

## What changed, in one line

Plan mode stopped guessing which text is worth translating from its shape, and
started either translating all of it or asking someone.

## 1. Greedy partition (schema 3)

The partition now skips text only for reasons that are *structurally free* —
whitespace, links, symbol-only runs, and the ancestor reasons (hidden, ruby,
pagebreak, non-content, excluded tags). Everything else becomes a unit.

Deleted: `classify_skip`'s numeric and roman-ref branches (`_NUMERIC_TEXT_RE`,
`_ROMAN_TOKEN_RE`, `_DIGIT_TOKEN_RE`), `is_trivial_unit` with its CJK and
prose-tag exemptions, `FilePlan.trivial`, `TranslationPlan.trivial_rows`,
`_is_single_roman`, and the per-signature `sig_has_prose` vote.

**Why, measured.** The heuristics were assumed to save ~30% of tokens. Built
over every epub on hand (chars as token proxy), they reclaimed 0–6.1%:

| book | old coverage | greedy adds | added units |
|---|---|---|---|
| gilgamesh.epub | 98.3% | +0.9% | +64 (1.1%) |
| animal_farm.epub | 99.9% | +0.0% | 0 |
| Liber_Esther.epub | 99.9% | +0.0% | +1 |
| lemo.epub | 94.5% | +0.4% | 0 |
| rigveda_sanskrit.epub | 94.3% | +6.1% | 0 |
| cia-rdp96….epub | 99.9% | +0.0% | 0 |

Verified after the change: gilgamesh 98.3% → **99.1%**, remaining skips
`hidden=2221, symbol=1015, pagebreak=707, link=86, excluded-tag=37` — all
structural. The ~30% figure must have come from tag-mode `p`-only filtering,
which is a different thing entirely.

What the heuristics were costing: rigveda's verse numbers (6.1% of the book),
"No"/"Sí" dialogue in short blocks, and drop caps — the last of which needed
the `sig_has_prose` vote as a patch, itself file-order dependent. Greedy makes
the whole class of bug impossible: a drop cap is simply the first letter of
its unit.

**Consequences to know.** Line-number spans now ride along with their verse
(`"I 5He who saw the Deep…"` in gilgamesh) because that is what the block
contains. `PLAN_SCHEMA_VERSION` → 3, since the unit list changed and resume
caches are positional. `force-translate` no longer has a behavior (it existed
to bypass the trivial filter); it still parses so pre-v3 plan JSONs load,
mapped to plain `translate` with a printed note.

## 2. Short-unit grouping (tier 2)

`assign_groups()` runs the untouched poetry pass, then sweeps whatever is
left: any consecutive run of ungrouped units under `SHORT_UNIT_MAX_CHARS`
(70) is windowed regardless of tag or signature, capped by both `group_size`
lines and `GROUP_MAX_CHARS` (500). A run of one stays solo.

This exists because greedy turns page numbers, verse refs and one-word labels
into units, and the poetry pass only groups structural siblings — so mixed
junk would be one request each. `group_id` semantics, `_iter_plan_chunks` and
the positional resume cache are untouched by construction.

**Finding: this does not help rigveda, and the reason matters.** rigveda has
no separate verse-number units at all — `1.1.1` is *inline* at the head of
each `p.v` mantra, which is where the +6.1% chars came from (`added units 0`
in the table was the clue). The book is 11601 units of 71–78 chars, and both
tiers miss them: tier 1 needs a run median < 70 (of 1028 hymn runs, 13
qualify), tier 2 needs units < 70. Not a regression — rigveda has always cost
~11k requests in plan mode. Raising `POETRY_MAX_MEDIAN_CHARS` 70 → ~110 would
batch it into ~1.4k, but that is a tier-1 change affecting every book with
70–110 char paragraphs, so it was left for the user to decide.

## 3. Two context bugs fixed on the way

**Delimiter batches poisoned `--use_context`.** `_do_batch_translate` joined
the batch with `@@` separators and handed the blob to `translate()`, which
saved it as ONE context entry — so every later prompt carried delimiter
markers and a single entry evicted three real paragraphs. Observable as
`context_list == ["one\n\n@@\n\ntwo"]`. Now context saving is suppressed
around the batch call (inside the existing try/finally, so the error path
restores it too) and one pair is saved per paragraph after alignment, matching
what the structured path already did.

**Parallel plan mode had no context isolation.** The plan branch of
`_translate_chapter_worker` drove the shared `translate_model`, so with
`--use_context --parallel-workers` every chapter appended into one global
`context_list`: out of reading order *and* a data race. Plan workers now use
`_clone_translator_for_context()` — a shallow copy with fresh context buffers.
Keys, model config and the API/probe locks stay shared on purpose (rate
limiting and the structured-output probe must remain global). Sequential runs
keep the shared instance, where accumulation is in reading order and useful.
A clone that trips `_fatal_error_detected` propagates the flag to the shared
model so other workers still stop.

## 4. Probe verdicts: strict-only for translation

The probe always graded endpoints `strict` / `shape` / `unsupported`, but
`_record_probe_result` collapsed that to a bool where `shape` counted as
supported. That was wrong for translation specifically: #544 pins the target
language as a *value* constraint, and a shape-only endpoint drops the pin,
leaving a schema that does nothing the delimiter method doesn't do better.

`_structured_support[model]` now stores the verdict string, and
`_ensure_structured_support(model, purpose=...)` gates on it:

- `translate` (default) requires `strict`
- `classify` accepts `strict` or `shape` — verdicts are linted locally against
  their enum, so an ignored value constraint costs one `unsure`, never a
  silently wrong translation

## 5. Classification: a package, a ladder, a lint

`plan_classify.py` became `book_maker/loader/classify/`, one module per entry
(user's requirement — different methods live in different files):

```
candidates.py   the shared question: which signatures are worth asking about
                (spine >10%, h1–h6, poetry-group guardrails) and with what
                evidence
model.py        LLM entry: schema, prompt, paging, lint, merge
agent.py        agent entry: the paste-able instruction block
__init__.py     dispatch + MODES
```

**Ladder.** `structured_json` walks three rungs so a 反代 that drops
`response_format` can still classify: `json_schema` → `json_object` with the
schema inlined in the prompt → a plain completion asking for raw JSON. A
`BadRequestError` not mentioning `response_format` still propagates. Replies
are read with a balanced-brace extractor, because endpoints below strict
decoding wrap JSON in fences and prose regardless of instructions.

**Lint.** `lint_verdicts()` is the single place deciding what an ill-formed
answer means: missing signature, non-dict entry, or out-of-enum verdict all
become `unsure`. Hallucinated signatures are dropped here rather than reaching
`to_dict`, which now *raises* on a verdict for a signature the plan lacks
(under greedy that means the classifier answered the wrong question — there is
no trivia to resurrect any more).

**Paging** replaces the `MAX_CANDIDATES = 12` cap: 12 signatures per request,
all candidates covered. The cap silently left the smallest signatures
unreviewed. One failing page fails the whole run — a half-classified plan is
indistinguishable from a complete one in the JSON.

## 6. `--plan-classify {none,most,model,agent}`

Replaces `--plan-no-classify`, and is the only CLI door into plan mode —
`--translate-tags auto` is gone from the CLI surface (an "auto" value there
is just a tag name that matches nothing). The loader's switch is an explicit
`plan_mode` attribute; the CLI sets it together with the internal
`translate_tags = "auto"` so the tag-selection paths keep their established
no-match behavior. The switch was originally left keyed on the tag string,
which kept `--translate-tags auto` working as an undocumented backdoor into
plan mode — the second review pass caught it.

- `none` **(default) denotes the flag's absence**: no plan, translate the
  `--translate-tags` selection (default `p`) as always.
- `most` is plan mode with no classification: translate the whole greedy
  partition. Maps to the loader's `plan_classify = "none"`.
- `model` / `agent` as below. `--plan-classify-model X` implies `model` mode
  and is rejected alongside `most` or `agent` (both mean "no classifier").
- In plan mode `--translate-tags` is ignored; a note prints only when its
  value differs from the default `p` (argparse cannot tell an explicit `p`
  from the default, and a note on every run would be noise).
- The flag errors on non-epub books. Silently ignoring it was the original
  implementation, and for `agent` that is the worst case: the mode promises
  to stop before spending, and ignoring it translates the whole book.

Run flow: `most` and `model` translate in one run (model classifies first).
`agent` writes the plan JSON, prints the instruction block, and **stops** —
translating first would spend the whole book before anyone looked at the
questions. Rerunning the same command finds the plan and translates.

**Null actions (added later on 260805, user directive):** the agent-mode
plan originally went out with every row defaulted to `"translate"`, so a
rerun that edited nothing silently produced the greedy result — exactly
the lazy path a planner must not have. Candidate (uncertain) signatures
are now written with `"action": null`; `load_plan_overrides` refuses to
translate while any null remains and lists them. Certain rows (prose
spine, headings, poetry) keep the translate default. Only an explicit
null means undecided — a row *missing* its action key is still invalid
(damaged). `--plan-dry-run` still writes fully-decided plans (documented:
it pins the decisions).

The printed block is self-contained, so no skill needs to be installed: it
explains the row fields, the judgment call (with the asymmetry stated —
translating something unnecessary is cheap, losing content is not), the edit
contract (`action` only, never `book_sha256`), a deeper-sampling hint, and the
exact rerun command rebuilt from `argv`. It goes through `builtins.print`
because rich hard-wraps paths and commands mid-token. It also teaches the
same discipline the model-mode schema enforces mechanically: name what the
text is first, then rule.

Plan JSON rows carry `samples` (≤5 distinct texts, 80-char clipped)
alongside the legacy single `sample`, plus `units`, `chars`, `pct`, and
`mean_chars` — the same evidence the uncertainty heuristic keyed on, so a
judgment can be made without unzipping the book.

## Verified live

- **agent loop, end to end** (animal_farm, keyless `--model google`): first run
  wrote the plan + printed the block + translated nothing; edited one
  signature to `skip`; rerun translated and honored the edit.
- **model loop** (gilgamesh via cpamc `gpt-5.6-luna`, strict endpoint):
  `llm classification: 1 uncertain signature(s) reviewed, plan unchanged`.
  (The transcript predates the same-day flag redesign, so the note it also
  printed no longer exists verbatim.) One candidate looked like the
  guardrails working — poetry groups, spine and headings exempt — but the
  second review pass showed part of it was the tier-2 bug below: windowed
  units were wrongly counted as poetry.
- Ladder rungs 2/3 are unit-tested only; luna is strict so they did not fire.

## Post-review fixes (same day)

The self-review pass before the PR turned up:

- **`--plan-dry-run` crashed on every fresh run** — the dry-run path still
  read the removed `options.plan_no_classify` and hit `AttributeError` right
  after writing the plan. No test exercised a fresh dry run; there is one
  now (`test_plan_dry_run_writes_a_fresh_plan`).
- Two files had drifted from CI's `black . --check` (the session used ruff
  formatting); reformatted.
- The flag surface was then redesigned per user direction (see §6): `none`
  became the default meaning "no plan", `most` was added, and
  `--translate-tags auto` left the CLI.

A second, independent review pass over the committed range then found:

- **Tier-2 windows were silently exempt from classification.** Both grouping
  tiers share `Unit.group_id`, and the classifier's poetry exemption keyed on
  it — so any apparatus signature that landed in a short-unit window vanished
  from the candidate list, on exactly the apparatus-heavy books tier 2 was
  built for. The report also counted those windows as "poetry-grouped units".
  Units now carry an explicit `poetry` flag set only by the tier-1 pass; the
  exemption and the report read that, and the report prints windowed units on
  their own line. The old test passed with the bug because it hand-built
  units with `group_id=None`; the new one goes through `assign_groups`.
- **The plan-mode backdoor** described in §6 (`_plan_mode` keyed on the tag
  string).
- The "Verified live" model-loop transcript quoted a CLI note that the
  redesign had already removed (annotated above).

## Third pass: Codex adversarial review

The whole PR was then handed to Codex as an adversarial reviewer (findings in
`codex-findings.md`, method and evidence in
`docs/260805-eval-PR545_ADVERSARIAL_CODE_REVIEW.md`). 14 findings; every one
was reproduced or refuted before touching code.

| # | finding | outcome |
|---|---|---|
| 1 | plan mode replays a legacy tag-mode resume cache positionally | fixed — refuse the cache and exit 1 |
| 2 | resume fingerprint not bound to the book file | fixed — `book_sha256` in the fingerprint |
| 3 | Gemini clones share one mutable `convo` | fixed — clone calls `create_convo()` |
| 4 | clone fatal flag propagates only when translation *raises* | fixed — flag copied on marker returns too |
| 5 | `<br>` boundaries corrupt output | **half real** — see below |
| 6 | recovery EPUB replay ignores `--only_filelist` | fixed — `_item_in_processing_set` gate |
| 7 | an empty filtered plan reports 100% coverage | fixed — fail loud right after the build |
| 8 | CSS resolution depends on class-attribute order | fixed — `DisplayResolver` resolves by source order |
| 9 | plan-JSON validation is fail-open | fixed — fail-closed on hash, rows and actions |
| 10 | translation schema has no target-language constraint | resolved by merging main (#544) |
| 11 | structured batch probes one model, then rotates to another | fixed — re-probe after rotation or raise |
| 12 | tier-2 windows treated as poetry | already fixed in the second pass, concurrently |
| 13 | the 10% boundary is excluded, the docs say otherwise | comment fixed; behaviour kept |
| 14 | the invariant excludes XHTML `<head>` text | invariant reworded to the rendered `<body>` |

Three deserve more than a row.

**#5 was half a bug.** The claim covered both output modes; only one held. Run
side by side on `<p>one<br/>two<br/>three</p>`, bilingual mode produced
identical markup in plan mode and tag mode — no defect. Single-translate plan
mode produced `<p>YI ER SAN<br/><br/></p>` against tag mode's clean
`<p>YI ER SAN</p>`: the `<br>`s separated text nodes that had just been
replaced by one translation, so they separated nothing. The fix removes only
breaks sitting between two nodes the unit owns; leading, trailing and
foreign breaks stay (`test_single_translate_keeps_breaks_it_does_not_own`).
Splitting units at `<br>`, the reviewer's suggested fix, would have changed
grouping and the plan schema to solve a problem one of the two modes did not
have.

**#12 arrived three minutes after the fix.** The review snapshot predates
`9434633`; two independent passes found the same tier-2/poetry conflation,
which is a fair signal it was the most findable bug in the diff.

**#13 and #14 are doc/code mismatches, not defects.** Both were resolved by
correcting the prose: the 10% boundary is inclusive on purpose (an unasked
signature keeps its `translate` default, and over-translating is the cheap
error), and the partition invariant was only ever a claim about the rendered
body — spine `<title>` text is not rendered and is not accounted.

Also fixed while merging main: #544's language-schema test primes the probe
cache with `True`, but this branch stores verdicts, so it silently fell
through to the no-schema path. Seeded `"strict"`.

Suite after the merge: **307 passed, 3 skipped**, in ~112s. The three
failures in a full run — `test_deepl_free_translate_epub` (PyDeepLX vs the
installed httpx: `Client.__init__() got an unexpected keyword argument
'proxies'`), `test_openai_translate_epub_ja_prompt_txt` (`--model gpt3`), and
`test_pdf_layouts` (no reportlab) — are environment-bound and predate the
branch. Most of the wall clock is `tests/test_integration.py` making live
OpenAI calls (~70s of 112s in three tests).

## Deviations from the written plan

- `structured_json` returns a parsed object on every rung and does the
  fence/prose extraction itself (transport concern), rather than handing text
  to a classify-side `_lint_json_response`. The classify lint kept the
  semantic half. The "one reprompt retry on unparseable" was dropped: with the
  balanced-brace extractor plus three rungs, an unparseable reply means the
  endpoint cannot do the job, and a retry only spends money to say so again.
- `gather_candidates` no longer returns a dropped count (nothing is dropped).
- Step 2c (`--context-mode session`) and Step 6b (session-translate
  export/import) are **not implemented** — see `plan.md`.

## Where things are

| what | where |
|---|---|
| partition, grouping, plan JSON | `book_maker/loader/plan.py` |
| classification entries | `book_maker/loader/classify/` |
| probe verdicts, ladder | `book_maker/translator/chatgptapi_translator.py` |
| batch context | `book_maker/translator/base_translator.py` |
| worker isolation, agent stop | `book_maker/loader/epub_loader.py` |
| tests | `tests/test_translation_plan.py`, `tests/test_chatgptapi_translator.py`, `tests/test_cli.py` |
