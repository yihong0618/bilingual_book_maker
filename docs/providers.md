# Provider file and extra models

A run translates through one endpoint. Two kinds of step can use a second one:

- **Classification.** Plan mode asks a model which kinds of block in an EPUB are worth translating. By default that is the translating model. You can name another with `--classify-model`.
- **Steps that look at a page image.** Today there is one: on the [PDF route](features/pdf-to-epub.md), a vision model can correct the roles docling gives the regions of a page. It runs only when you name a model with `--img-model`.

You can name both on the command line or in a provider entry. This page covers the provider file, the two extra models, and which key goes where.

## The provider file

`--provider NAME` takes the route from a JSON file instead of flags. The run reads three files. A later one wins on a shared name:

1. `bbm_providers.example.json`, shipped next to `make_book.py`. The run uses it only for a name that is in neither of the other two files, and it says so every time, with the address and the key variable it used. Its `FILL-ME` entries are templates and are never used.
2. `~/.bbm/providers.json`.
3. `bbm_providers.json` in the directory you run from.

```json
{
  "providers": {
    "siliconflow": {
      "api_style": "openai",
      "base_url": "https://api.siliconflow.cn/v1",
      "default_models": ["Qwen/Qwen2.5-72B-Instruct"],
      "env_key": "BBM_SILICONFLOW_API_KEY"
    }
  }
}
```

```bash
bbook_maker \
  --book_name my_book.epub \
  --provider siliconflow \
  --use_context session
```

Flags you pass yourself win over the entry.

### Fields

| field | required | meaning |
|---|---|---|
| `api_style` | yes | `openai`, `anthropic`, `gemini`, `qwen`, `groq`, `xai` or `litellm` |
| `base_url` | no | the address; defaults to the style's own |
| `default_models` | no | models to use; required if you do not pass `--model` |
| `env_key` | no | the environment variable holding the key; required if you do not pass `--key` |
| `prices` | no | per million tokens, per model: `{"<model>": {"input": …, "output": …, "cached_input": …}}`. With a price for every model in the run, the progress bar shows money spent (`spent=$0.012`) instead of token counts |
| `currency` | no | default `USD`; `EUR`, `GBP`, `CNY`, `JPY` print with their symbol |
| `img_model` | no | the vision model for page-image steps. Stands in for `--img-model` |
| `img_base_url` | no | where `img_model` is served, when it is not this entry's own address. OpenAI-compatible only |
| `img_env_key` | no | the variable holding the key for `img_model`'s address |
| `classify_model` | no | the model for classification. Stands in for `--classify-model` |
| `classify_base_url` | no | where `classify_model` is served, when it is not this entry's own address. OpenAI-compatible, or a [Jev-compatible](#jev-and-jev-compatible-classifiers) classifier's URL |
| `classify_env_key` | no | the variable holding the key for `classify_model`'s address |

The spent amount is an estimate from the usage each request reports; the vendor's bill is the number that counts.

### The shipped `openai` entry turns the image step on

The example file's `openai` entry sets `"img_model": "gpt-5.6-luna"`. So `--provider openai` on a PDF with `--to-epub` runs the region-role pass on every page by default. That also holds when you have no `bbm_providers.json`, because the run then falls back to the example. The pass costs about 3,000 prompt tokens per page: 43,443 prompt and 4,319 completion tokens for 12 pages in the study behind it ([Where an LLM fixes layout](evaluation/pdf-structure-llm-roles.md)). To run without it, pass `--img-model none`, or copy the file and delete the line.

The `openai-jev` entry is the same `openai` entry plus `"classify_model": "jev"` with `JEV_API_KEY`, so `--provider openai-jev` translates with gpt-5.6-luna and classifies an EPUB's plan with [Jev](#jev-and-jev-compatible-classifiers). The `jev` entry is Jev on its own: it classifies and never translates, so `--provider jev` is refused with a hint to use `--classify-model`. No other entry names an image model or a classify model.

## The two extra models

### Where each model comes from

| step | first | then | last |
|---|---|---|---|
| image (`--img-model`) | the flag | the entry's `img_model` | **off** |
| classification (`--classify-model`) | the flag | the entry's `classify_model` | the run's own model |

The image step never falls back to the translating model. It runs only when a model is named for it, by the flag or by the entry, so no default needs a model that reads images. `--img-model none` turns an entry's image model off for one run.

`--plan-classify-model` is the old name of `--classify-model`. It still works and is not in `--help`. If you type both, `--classify-model` wins.

### Where each model is asked

- **Without a base URL**, the model is asked at the run's own endpoint, with the run's format and key.
    - An image model needs that endpoint to be OpenAI-shaped. On any other format the run stops before it starts: `--img-model needs an OpenAI-compatible endpoint; … resolves to the … format.`
    - A classify model works on any LLM route that can hold a conversation, the anthropic and codex routes included.
    - On a [machine-translation](machine-args.md) run there is no model to share an endpoint with. A classify model named without a base is then asked at the host its id implies: `--classify-model gpt-5.6-luna` goes to OpenAI.
- **With `--img-base-url` or `--classify-base-url`**, the model is asked there. The address must speak the OpenAI shape, or, for a classifier, the [Jev protocol](#jev-and-jev-compatible-classifiers); anything else is refused before anything is paid for.
- A base URL without its model stops the run: `--img-base-url names where --img-model is served, and no --img-model was given.` (and the same for `--classify-base-url`).

A named classify model always gets its own client, even on the run's address, so its tokens are counted apart from the translation's.

Before the image step runs, the run checks once that the model can read a picture. If it cannot, the run says `… did not read the probe image (…); image steps are skipped this run.` and goes on without the step.

### Which key goes where

A key is bound to an address. It is never sent to a host it was not meant for. For the image model and the classify model, the key is found in this order:

1. `--img-key` or `--classify-key`.
2. The run's key, but only when the model is asked at the run's own address. The run's address is the one after any `--api_base`.
3. The entry's `img_env_key` or `classify_env_key`, but only at the address that entry names for the model: its `img_base_url` or `classify_base_url`, else the entry's `base_url`. An `--api_base` that moves the run elsewhere does not carry the entry's key with it.
4. The variable the address's format reads, as for the run (see [Keys](llm-args.md#keys)).

When none of these holds a key, the run stops and names the flag to pass.

`--extra_body` and `--extra_headers` reach the image or classify model only when it is asked at the run's own address. Headers often carry a gateway's credential, so they never travel to another host.

### Examples

Translate on a local model, and let a hosted model classify the plan and read page images:

```json
{
  "providers": {
    "local-with-helpers": {
      "api_style": "openai",
      "base_url": "http://localhost:11434/v1",
      "default_models": ["qwen3:8b"],
      "classify_model": "gpt-5.6-luna",
      "classify_base_url": "https://api.openai.com/v1",
      "classify_env_key": "OPENAI_API_KEY",
      "img_model": "gpt-5.6-luna",
      "img_base_url": "https://api.openai.com/v1",
      "img_env_key": "OPENAI_API_KEY"
    }
  }
}
```

The same with flags:

```bash
bbook_maker \
  --book_name my_book.epub \
  --api_base http://localhost:11434/v1 \
  --model qwen3:8b \
  --classify-model gpt-5.6-luna \
  --classify-base-url https://api.openai.com/v1 \
  --classify-key "$OPENAI_API_KEY" \
  --use_context session
```

A local server needs no key, so the run's key is empty here; the classifier's key must be named.

## Jev and Jev-compatible classifiers

Jev is TypeSafe's classifier: a model built to answer typed questions, not to write. Plan mode's question for each kind of block, translate it or keep it, is that kind of question, and Jev answers a page of them in one cheap round trip. It translates nothing, so it can only be the classify model. Jev-compatible servers speak the same protocol; Featherless's Simple Jev, an open reimplementation on open models, is one.

### The commands

| classifier | flags | key |
|---|---|---|
| TypeSafe's Jev | `--classify-model jev` | `JEV_API_KEY` or `TYPESAFE_API_KEY`, sent only to `api.typesafe.ai` |
| Jev through a gateway | `--classify-model typesafe-ai/jev --classify-base-url https://ai-gateway.vercel.sh/typesafe --classify-key "$GATEWAY_KEY"` | named with `--classify-key` |
| Simple Jev at Featherless | `--classify-model featherless-ai/Qwen3.8-27B-classifier` | `FEATHERLESS_API_KEY`; the address defaults to `https://api.featherless.ai/v1/classifier` |
| Simple Jev's keyless demo | `--classify-model featherless-ai/Qwen3.8-27B-classifier --classify-base-url https://simple-jev-demo-api.featherless.ai/v1/classifier` | none |

For example, translating with gpt-5.6-luna and classifying with Jev:

```bash
bbook_maker \
  --book_name my_book.epub \
  --model gpt-5.6-luna \
  --classify-model jev
```

### The rules

- **A key is read from the environment only for its own host.** `JEV_API_KEY` and `TYPESAFE_API_KEY` go only to a typesafe.ai address, `FEATHERLESS_API_KEY` only to a featherless.ai one. Anywhere else, a gateway included, name the key with `--classify-key` or the entry's `classify_env_key`.
- **A base URL that already ends in `/systemone` or `/classifier` is used as it is.** Any other base gets the server's own path added: `/classifier` on featherless.ai, `/systemone` elsewhere, after a `/v1`.
- **Any other id ending in `-classifier` needs `--classify-base-url`.** Only Featherless's ids have a known address; for another the run stops and asks for one.
- `jev` alone asks for TypeSafe's current model, `jev-latest`.

### The gate: a doubtful skip is translated

Jev returns a probability with each answer. A `skip` below the gate is recorded as `translate`, so no content is lost to a skip Jev was unsure of. A `translate` is taken at any probability. The gate is 0.95 on the probability of the chosen answer, measured over 662 plan signatures from 45 EPUBs against gpt-5.6-luna. At that value about nine of ten of Jev's skips become `translate`, and what Jev still skips is apparatus: copyright lines, line numbers, note marks, index locators. So on that corpus Jev saves little over translating everything; see [Jev as the plan classifier](evaluation/plan-classifier-jev.md). The plan file shows each fallback on its row: `unnamed (jev verdict skip at confidence 0.61, below the gate: translate)`. `--classify-min-confidence P` (0 to 1) moves the gate for a run; `BBM_JEV_MIN_CONFIDENCE` does the same without the flag, and the flag wins. A lower value keeps more of Jev's skips: on the measured corpus those below 0.9 agreed with the reference 7–55% of the time, so the risk is yours.

### In a provider entry

If you want OpenAI to translate and Jev to classify, the shipped `openai-jev` entry already does it: `--provider openai-jev`. For any other pairing, write your own entry.

`classify_model`, `classify_base_url` and `classify_env_key` name a Jev classifier the same way the flags do:

```json
{
  "providers": {
    "openai-with-jev": {
      "api_style": "openai",
      "default_models": ["gpt-5.6-luna"],
      "env_key": "OPENAI_API_KEY",
      "classify_model": "typesafe-ai/jev",
      "classify_base_url": "https://ai-gateway.vercel.sh/typesafe",
      "classify_env_key": "JEV_API_KEY"
    }
  }
}
```

An entry that names `JEV_API_KEY` for its own gateway address has named the key for that address, and it is honored. Without such an entry, the Jev variables are never sent to a gateway.

Each Jev request sends the page's candidate lines once, with one short question per signature. See [Jev as the plan classifier](evaluation/plan-classifier-jev.md).

## What the run prints

A `--plan-dry-run` shows where each model would be asked, without a key:

```text
Classifier: gpt-5.6-luna at the openai endpoint's default host (cli)
Image model: off
```

The part in brackets is where the choice came from: `cli`, `provider` or `run`. Under `--plan-classify all` or `agent` the line reads `Classifier: none (--plan-classify agent asks nothing)`.

At the end of the run, a model on a client of its own prints its own usage line under the translation's:

```text
Classifier (gpt-5.6-luna at the endpoint's default host): tokens: in 7.6k, out 1.8k, cached 0 (3 requests)
```

The image model's line reads `Image model (<model> at <address>): …` and is printed after the extraction.

These warnings mean a flag does nothing this run:

- **`--img-model, --img-base-url and --img-key choose the vision model for the steps that look at a page image, and only the PDF route (--to-epub on a PDF) has one; …`** You named an image model on a book that is not a PDF on the `--to-epub` route.
- **`--classify-model names a classifier, and --plan-classify all translates the whole partition without classifying anything; it is ignored this run.`** The same for `--plan-classify agent`: agent mode leaves every row to your agent and asks no model.
- **`Nothing on this route classifies yet, so --classify-model is ignored on a … book.`** Only an EPUB has a classification step today.
