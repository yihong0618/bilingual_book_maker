# EPUB

If you want a bilingual edition of an EPUB, this is the command most people need:

```bash
bbook_maker --book_name my_book.epub --language zh-hans --use_context session --quiet
```

It writes `my_book_bilingual.epub` beside the input. [Recommended settings for EPUB](../features/recommended-epub.md) has what to change for a textbook, a small model or a local server.

EPUB is the format this tool knows best. Every feature works on it.

## How the file is read

An EPUB is a zip of XHTML pages. The tool reads every page the book's spine lists.

- **With an LLM route (the default):** the book goes through [plan mode](../features/plan-mode.md). The loader finds every block that carries text (paragraphs, headings, list items, table cells, blockquotes, verse lines, captions), groups them by kind, and asks the model which kinds are worth translating. The answer is saved in `<book>_plan.json` and reused by later runs. Consecutive blocks then share one request, up to a token budget and a unit cap.
- **With a machine-translation route and no `--classify-model`, or with `--plan-classify none`:** only the `--translate-tags` selection is translated, `<p>` by default. Verse or a table outside `<p>` then stays in the source language.
- Links, emphasis and other inline markup inside a paragraph are replaced by numbered markers before translation and put back afterwards, so the model never sees raw HTML.
- Content inside `--exclude-translate-tags` (`sup` and `code` by default) is never sent.

## What you get

- `<name>_bilingual.epub` beside the input. Each translation sits right after its original, with the same tag and class. Ids and internal links are kept.
- A one-line credit below the book's intro: "Translated by \<model\>, \<year\>." `--no_disclosure` leaves it out.
- `bbm_translation_metadata.json` inside the book on plan and session runs: the model, the date, and the checksum of a `--glossary` file.
- `<book>_plan.json` beside the input (the plan), and `<book>_handoff.md` on a session run (the latest handoff report).
- On Ctrl+C or an error: `<name>_bilingual_temp.epub` and a checkpoint. Rerun with `--resume`.

## Recommended command

```bash
bbook_maker \
  --book_name my_book.epub \
  --language zh-hans \
  --use_context session \
  --quiet
```

Preview the plan first on an unusual book (a textbook, a bilingual edition, a book whose body is not in `<p>`). It needs no key and translates nothing:

```bash
bbook_maker \
  --book_name my_book.epub \
  --plan-dry-run
```

## Flags that apply

### Route and run

These work the same on every format.

| flag | what it does |
|---|---|
| `--book_name PATH` | The file to translate. The extension picks the format. |
| `-m`, `--model MODEL` | The model id, exactly as the endpoint names it. Default `gpt-5.6-luna` on the openai format. |
| `--key KEY` | API key; several comma-separated keys rotate. Falls back to `BBM_API_KEY`, then the format's own variable. |
| `--api_base URL` | The endpoint. Defaults to the format's official host. |
| `--api_format FORMAT` | The API the endpoint speaks, or a machine-translation engine. Inferred from `--api_base` when left out. |
| `--provider NAME` | A named endpoint from `bbm_providers.json`. |
| `--model_list IDS` | Several models to rotate across. Refused with `--use_context session`. |
| `--language LANGUAGE` | Target language: a tag, a name, or `TAG:NAME`. Default `zh-hans`. |
| `--source_lang LANGUAGE` | Source language, stated. Reaches every LLM prompt; sent as a field on `qwen` and `customapi`. |
| `--prompt VALUE_OR_FILE` | Custom prompt: `user` template (must contain `{text}`), `system`, `style`. |
| `--temperature FLOAT` | Sampling temperature, on the formats that take one. |
| `--no-thinking` | Ask the model not to reason before answering. The field is negotiated on the OpenAI-shaped routes; `thinking: disabled` on anthropic; refused on codex. |
| `--extra_body JSON` | Extra request-body fields on the openai and anthropic routes. |
| `--extra_headers JSON` | Extra HTTP headers on the openai and anthropic routes. |
| `--interval SECONDS` | Pause between requests. Only the gemini format uses it. |
| `-p`, `--proxy URL` | HTTP proxy for the run. |
| `--test` | Translate only the first paragraphs. |
| `--test_num N` | How many, with `--test` (default 10). |
| `--resume` | Continue an interrupted run from its checkpoint. |
| `--single_translate` | Write the translation only, without the original. |

### EPUB flags

| flag | what it does |
|---|---|
| `--translate-tags TAGS` | Tags to translate when there is no plan (default `p`). Ignored in plan mode. |
| `--exclude-translate-tags TAGS` | Tags whose content is never translated (default `sup,code`; `""` clears it). |
| `--allow_navigable_strings` | Also translate text that sits outside any tag. Redundant in plan mode. |
| `--only_filelist FILES` | Translate only these internal files (OPF-relative names, comma-separated). |
| `--exclude_filelist FILES` | Skip these internal files. Ignored when `--only_filelist` is given. |
| `--plan-classify MODE` | How the plan is decided: `auto` (default), `none`, `all`, `model`, `agent`. See [Plan mode](../features/plan-mode.md). |
| `--classify-model MODEL` | Classify with another model (default: the provider entry's `classify_model`, else the translating model). Typed, it puts the run in `model` mode, so a failure stops the run; it also gives a machine-translation route a plan. `--plan-classify-model` is the old name. Ignored under `--plan-classify all` or `agent`. |
| `--classify-base-url URL` | Where that model is served, when it is not the run's endpoint (OpenAI-compatible only). |
| `--classify-key KEY` | The key for `--classify-base-url`. See [which key goes where](../providers.md#which-key-goes-where). |
| `--plan-dry-run` | Print the plan and write `<book>_plan.json` without translating. No key needed. |
| `--plan-min-coverage FRACTION` | Stop when the plan covers less than this share of the text (default 0.5). |
| `--poetry-group-size N` | Deprecated; still works and warns. Use `--max-batch-units`. |
| `--accumulated_num N` | In plan mode, the token budget per request (derived when unset; `1` turns grouping off). Without a plan, characters accumulated per request. |
| `--max-batch-units N` | Plan mode: the most units in one request (default 16; 8 on an endpoint without a strict schema). |
| `--block_size N` | Without a plan: merge paragraphs into delimiter-translated blocks. Ignored while `--accumulated_num` is above 1. |
| `--sentence_mode` | Translate sentence by sentence. Not with a plan; ignored while `--accumulated_num` is above 1. |
| `--use_context [window\|session]` | Carry earlier paragraphs: bare or `window` re-sends a few pairs; `session` keeps one history. See [Session mode](../features/session-mode.md). |
| `--context_paragraph_limit N` | Window mode: how many pairs to re-send. |
| `--context-compact-at N` | Session mode: compact the history at this many estimated tokens (default 8192, minimum 1500). |
| `--no-context-compact` | Session mode: roll over without a handoff report. |
| `--glossary FILE` | `term -> translation` pins, sent with the requests they occur in. openai-shaped and codex routes. `--terminology` is the same flag. |
| `--glossary-auto on\|off` | Keep the renderings a session's handoff reports establish. Off unless asked for. |
| `--parallel-workers N` | Translate several chapters at once (2 to 4 is useful). Refused with a session and on codex. |
| `--translation_style CSS` | CSS for the translated paragraphs. |
| `--translation_color COLOR` | Color only; `--translation_style` wins. |
| `--no_disclosure` | Leave out the one-line AI-translation credit, and the metadata file. |
| `--translation-metadata` | Write `bbm_translation_metadata.json` into a plain tag-mode run (plan and session runs write it anyway). |
| `--retranslate OUT FILE START END` | Retranslate a range of an existing bilingual EPUB. |
| `--quiet` | No progress bars or paragraph echoes; reports and errors still print. |

## Not for this format

- `--batch_size`: the EPUB loader groups with `--accumulated_num`; the run warns.
- `--batch`, `--batch-use`: refused on EPUB: the Batch API path is never reached there.
- `--to-epub`, `--pdf-ocr`, `--ocr-replace-layer`, `--device`, `--ocr-lang`, `--pages`, `--no-formula-images`, `--pdf_layout`, `--img-model`, `--img-base-url`, `--img-key`: PDF only. `--to-epub` on an EPUB stops the run; the others warn or do nothing (the three image flags: the run warns that only the PDF route has an image step).
