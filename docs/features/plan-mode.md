# Plan mode

## What it does

An EPUB keeps its text in many kinds of markup: paragraphs, headings, list items, table cells, blockquotes, verse lines, captions. Plan mode finds all of them. The loader partitions the whole book into units, groups the units by their tag signature (the tag and its classes), and asks the model which signatures are worth translating. The answer is written to `<book>_plan.json`, which every later run of the same book reuses. Without a plan, only the `--translate-tags` selection is translated, `<p>` by default, and verse or a table outside `<p>` is left in the source language without a word.

Plan mode also groups the work. Consecutive units share one request up to a token budget (`--accumulated_num`) and a unit cap (`--max-batch-units`, default 16). Each request asks for the units back by id, so a reply that drops, merges or reorders units is caught and retried in smaller pieces instead of shifting every translation by one slot. Plan mode is on by default for an EPUB on any LLM route; you do not need a flag. The numbers behind the defaults are on [Why 16 units per request](../evaluation/grouping-batch-size.md).

## Setup

Nothing to install. Plan mode needs an EPUB and a model to classify with. That is the translating model on any LLM route. On a [machine-translation](../machine-args.md) route there is no model to ask, so the run falls back to the `--translate-tags` selection, unless you name a classifier with `--classify-model` (see [Choosing the classifier](#choosing-the-classifier)).

The flags:

| flag | what it does |
|---|---|
| `--plan-classify auto` | Default. The translating model decides, over a JSON schema where the endpoint verifiably applies one, otherwise over a plain conversation with one-word `skip`/`translate`/`unsure` answers. Unsure and unreadable answers translate. |
| `--plan-classify model` | Like `auto`, but an unresolved row stops the run instead of falling back. |
| `--plan-classify all` | Translate every unit; no classification, no plan file. |
| `--plan-classify none` | No plan: translate the `--translate-tags` selection, as before plan mode existed. |
| `--plan-classify agent` | Write the plan with samples, print instructions for a coding agent (or you), and stop. Rerun the same command to translate. |
| `--classify-model MODEL` | Classify with another model, or with a Jev-compatible classifier (`jev`). Named on the command line, it puts the run in `model` mode, so a classification failure stops the run. `--plan-classify-model` is the old name and still works. |
| `--classify-base-url URL` | Where that model is served, when it is not the run's endpoint: an OpenAI-compatible address, or a Jev-compatible classifier's URL. |
| `--classify-key KEY` | The key for `--classify-base-url`. |
| `--plan-dry-run` | Print the per-signature coverage table, write the plan with every decision empty, and exit. No key needed. |
| `--plan-min-coverage FRACTION` | Stop when the plan covers less than this share of the book's text (default 0.5; `0` turns the check off). |
| `--accumulated_num N` | Token budget per request. Unset, the run derives it: 1200 with the stock prompts, up to 1600 under a long custom `--prompt`, 800 on an endpoint without a strict schema (session runs keep the un-halved value). `1` turns grouping off. |
| `--max-batch-units N` | Most units per request. Default 16; 8 on an endpoint without a strict schema. |
| `--exclude-translate-tags TAGS` | Tags whose content is never sent (default `sup,code`). |
| `--only_filelist`, `--exclude_filelist` | Plan only these internal files, or skip these. The dry run honors them too. |

Do not pass these in plan mode: `--translate-tags` (ignored, the plan covers everything), `--allow_navigable_strings` (ignored), `--block_size` and `--batch_size` (they re-cut text the plan already partitioned). `--retranslate`, `--batch` and `--sentence_mode` contradict a plan and are refused.

## Choosing the classifier

The classifier is found in this order: `--classify-model`, then the provider entry's `classify_model`, then the translating model.

- **How it is asked.** Where the classifier's endpoint verifies a strict JSON schema, one request carries a page of signatures and the reply is held to the schema. Elsewhere it is a plain conversation: five signatures per turn, each answered `skip`, `translate` or `unsure`. Two missed replies in a row step down to three per turn, then to one. Below one, classification stops and the remaining signatures are translated. Each step prints one line.
- **A classifier of its own** (named by flag or provider entry) plans the book whatever the translating route can do. The run prints `plan mode: on (classified by …)`. This is how a machine-translation route gets a plan. Google Translate with gpt-5.6-luna as the classifier was run end to end on the test book: all 31 signatures decided, coverage 99.8%.
- **Agent mode asks no model.** `--plan-classify agent` hands every undecided row to you or your coding agent. A named classifier does not pre-fill the plan, because an agent judges worse from pre-filled answers; the run warns that it is ignored. `--plan-classify all` ignores it too.
- **Where it is asked and with which key** is on [Provider file and extra models](../providers.md#where-each-model-is-asked). A classifier on its own address prints its own usage line at the end of the run.
- **Jev**, TypeSafe's classifier, is built for exactly this question: translate or skip, a page of signatures per request, in one cheap round trip. `--classify-model jev` uses it; a Jev-compatible server such as Featherless's Simple Jev works too. Which key goes where, and the URL rules, are on [Provider file and extra models](../providers.md#jev-and-jev-compatible-classifiers).
- **Jev's gate.** A doubtful `skip` becomes `translate`, so no content is lost to it. The gate is 0.95 on the probability of Jev's chosen answer, measured over 662 plan signatures from 45 EPUBs against gpt-5.6-luna. At that value about nine of ten of Jev's skips fall back to `translate`; what it still skips is apparatus (copyright lines, line numbers, note marks, index locators). On that corpus Jev saves little over translating everything. The plan file marks each fallback on its row (`… below the gate: translate`). `--classify-min-confidence P` moves the gate for a run (`BBM_JEV_MIN_CONFIDENCE` does the same without the flag); lower keeps more of Jev's skips, at your risk. See [Jev as the plan classifier](../evaluation/plan-classifier-jev.md).

=== "A machine-translation route with an LLM classifier"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_format google \
      --classify-model gpt-5.6-luna \
      --language zh-hans
    ```

    The classifier goes to OpenAI and reads `OPENAI_API_KEY`. The translation stays with Google.

=== "Jev from the shipped provider file"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --provider openai-jev \
      --use_context session
    ```

    The one-line way: translates with gpt-5.6-luna at OpenAI (`OPENAI_API_KEY`) and classifies the plan with Jev (`JEV_API_KEY`), both from the `openai-jev` entry of the shipped `bbm_providers.example.json`.

=== "Jev at TypeSafe"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
      --classify-model jev \
      --use_context session
    ```

    Reads `JEV_API_KEY` or `TYPESAFE_API_KEY`.

=== "Jev through a gateway"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
      --classify-model typesafe-ai/jev \
      --classify-base-url https://ai-gateway.vercel.sh/typesafe \
      --classify-key "$GATEWAY_KEY" \
      --use_context session
    ```

    The key must be named: the Jev variables are sent on their own only to a typesafe.ai address.

=== "Simple Jev"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
      --classify-model featherless-ai/Qwen3.8-27B-classifier \
      --use_context session
    ```

    Asks Featherless's classifier endpoint and reads `FEATHERLESS_API_KEY`. For the keyless demo, add `--classify-base-url https://simple-jev-demo-api.featherless.ai/v1/classifier`.

## Recommended commands

The command per kind of book (novel, textbook, paper, dictionary), per endpoint (hosted, on-device, machine translation) and per system is on [Recommended settings for EPUB](recommended-epub.md).

## What can go wrong

These are the lines the run prints, what they mean, and what to do.

- **`plan mode: on (endpoint verified strict JSON schema)`**, `plan mode: on (no structured output here; classifying over a plain session)` or `plan mode: on (classified by …)`. Not a problem. It says how the plan will be decided.
- **`plan: this endpoint missed the reply format twice at 5 per turn; continuing at 3 per turn`**. The plain-conversation classifier stepped down. Nothing to do; it costs more turns. **`plan: this endpoint missed the reply format twice even one at a time; classification stops here and the remaining N signature(s) are translated`**. The model cannot hold the one-word format. The book is still translated, generously. For a better plan, name a stronger classifier with `--classify-model`, or use `--plan-classify agent`.
- **`… doesn't apply JSON schema (…), using delimiter method`** or **`… honors JSON schema shape but not value constraints; using the delimiter method for translation, schema kept for classification`**. Not a problem. The endpoint does not do strict schema decoding, so translation uses a delimiter format. Expected on the anthropic route, most proxies and local servers. [Why the tool probes the endpoint](../evaluation/structured-output-ladder.md) explains it.
- **`plan mode: off (…)`**. The run translates the `--translate-tags` selection. The reason in brackets says why: a route with no model, `--translate-tags` given, or a failed probe. If you did not expect it, read the reason. On a route that does not plan by itself, the run suggests `--plan-classify model`.
- **`N misaligned batches this run — if this keeps happening, a lower --max-batch-units or --accumulated_num may fit this model better`**. Printed from the third recovered batch. The model keeps miscounting large requests. Nothing is lost (each bad batch was retried in halves), but it costs requests. On the next run halve the unit cap: `--max-batch-units 8`, then `4`.
- **`Plan coverage X% is below the required 50.0% — refusing to translate a fraction of the book silently.`** The plan would skip most of the book. Open `<book>_plan.json` and look at what was marked `skip`. A dictionary or a book with a large apparatus may really translate less; then lower `--plan-min-coverage` on purpose.
- **`--classify-model names a classifier, and --plan-classify agent leaves every row to your agent and asks no model; it is ignored this run.`** (or `… all translates the whole partition …`). As it says; drop the flag or the mode.
- **`--plan-classify model asks an LLM to rule on every plan signature, and the google format translates through one fixed engine with no model to ask. …`** A fixed-engine run in `model` mode with no classifier of its own. Add `--classify-model`, or use `agent` or `all`.
- **`--classify-model needs an OpenAI-compatible endpoint; … resolves to the … format.`** A `--classify-base-url` that is not OpenAI-shaped. **`--classify-base-url names where --classify-model is served, and no --classify-model was given. …`** Name the model too.
- **`No API key for the jev classifier at … Pass --classify-key, …`** The Jev variables are read only for a typesafe.ai address and `FEATHERLESS_API_KEY` only for a featherless.ai one. Through a gateway, name the key with `--classify-key`.
- **`… is a Jev-compatible classifier with no known default address; name its endpoint with --classify-base-url.`** An id ending in `-classifier` that is not Featherless's. Add its URL.
- **`BBM_JEV_MIN_CONFIDENCE must be a number from 0 to 1; got …`** Fix or unset the variable.
- **`<book>_plan.json has N undecided signature(s) …`**. After `--plan-classify agent`, some rows still have no decision. Fill every `action` with `translate` or `skip`, then rerun.
- **`…: invalid action '…' — use …`**. A typo in a hand-edited plan. Fix the JSON and rerun.
- **A resume refused with a fingerprint message.** The book or the plan changed since the checkpoint was written. Rerun with the original flags, or delete the checkpoint and start over, knowing what gets paid again.
- **The plan skipped something you wanted.** Delete `<book>_plan.json` and run again, or edit its `action` fields by hand. A `--test` run classifies the whole book, not only the slice, so its plan is the real one.
