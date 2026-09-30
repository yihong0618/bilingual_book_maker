# Markdown

A Markdown file is translated prose block by prose block. Code, tables and front matter are kept as they are.

## How the file is read

- YAML front matter, fenced code blocks, tables and lines that carry no prose are passed through untranslated.
- Headings and paragraphs are translation blocks.
- Blocks are sent in batches of `--batch_size` (default 10), and a batch also stops at about 2000 characters. The heading path above a batch goes with it as a breadcrumb, so the model knows which section it is in.
- `--use_context` works here, in both window and session mode.

This is also the loader behind [PDF to bilingual EPUB](../features/pdf-to-epub.md): a PDF is first turned into Markdown, then translated here.

## What you get

- `<name>_bilingual.md` beside the input: each block followed by its translation. With `--single_translate`, the translation only.
- On a session run, `<name>_handoff.md` with the latest handoff report.
- On Ctrl+C or an error: `<name>_bilingual_temp.txt` and a checkpoint. Rerun with `--resume`.

## Recommended command

```bash
bbook_maker \
  --book_name my_doc.md \
  --language zh-hans \
  --prompt prompt_md.json \
  --use_context session \
  --batch_size 20
```

`prompt_md.json` in the repository is a prompt written for Markdown: it asks the model to keep the markup. There is no plan mode for Markdown, so in session mode every request re-reads the history. Raise `--batch_size` to send fewer, larger requests.

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

### Markdown flags

| flag | what it does |
|---|---|
| `--batch_size N` | Prose blocks sent in one request (default 10; a batch also stops at about 2000 characters). |
| `--use_context [window\|session]` | Carry earlier paragraphs: bare or `window` re-sends a few pairs; `session` keeps one history. |
| `--context_paragraph_limit N` | Window mode: how many pairs to re-send. |
| `--context-compact-at N` | Session mode: compact at this many estimated tokens (default 8192, minimum 1500). |
| `--no-context-compact` | Session mode: roll over without a handoff report. |
| `--glossary FILE` | `term -> translation` pins (openai-shaped and codex routes). `--terminology` is the same flag. |
| `--glossary-auto on\|off` | Keep the renderings a session's handoff reports establish. Off unless asked for. |
| `--parallel-workers N` | Translate several batches or sections at once. Refused with a session and on codex. |

## Not for this format

- `--accumulated_num`: not read by the Markdown loader; the run warns and points at `--batch_size`. Outside plan mode a session re-reads its history on every request, so a larger `--batch_size` keeps the request count down.
- `--max-batch-units`, `--plan-classify`, `--plan-dry-run`, `--plan-min-coverage`, `--poetry-group-size`: plan mode is EPUB only.
- `--classify-model`, `--classify-base-url`, `--classify-key` (and `--plan-classify-model`, the old name): nothing classifies on this format yet. The run warns that the flag is ignored.
- `--exclude-translate-tags`: accepted without a warning, but the Markdown loader does not read it.
- `--translate-tags`, `--allow_navigable_strings`: EPUB markup selectors; the run warns.
- `--only_filelist`, `--exclude_filelist`: EPUB internal files; ignored.
- `--block_size`, `--sentence_mode`: EPUB only; ignored.
- `--translation_style`, `--translation_color`: Markdown carries no style; the run warns.
- `--no_disclosure`, `--translation-metadata`: the output carries no credit line and no metadata file; the run warns.
- `--retranslate`: EPUB only; refused.
- `--quiet`: EPUB only; the run warns.
- `--batch`, `--batch-use`: the Markdown loader does not implement the Batch API.
- `--to-epub`, `--pdf-ocr`, `--ocr-replace-layer`, `--device`, `--ocr-lang`, `--pages`, `--no-formula-images`, `--pdf_layout`, `--img-model`, `--img-base-url`, `--img-key`: PDF only. `--to-epub` on this format stops the run; the others warn or do nothing (the three image flags: the run warns that only the PDF route has an image step).
