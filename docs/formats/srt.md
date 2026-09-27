# SRT

Subtitles are translated block by block. Each block keeps its number and its timeline.

## How the file is read

- The file is split into blocks at blank lines. A block is a number, a timeline and one or more lines of text.
- By default each block is its own request. `--accumulated_num N` puts consecutive blocks into one request, up to `N` characters (capped at 512).
- The loader's own prompt tells the model to leave each block's number and timeline alone. If you replace the `user` template with `--prompt`, say that in your template too, or the answer will not parse as SRT. The run warns about this.

## What you get

- `<name>_bilingual.srt` beside the input. Each block keeps its number and timeline, with the original text followed by the translation. With `--single_translate`, the translation only.
- On Ctrl+C or an error: `<name>_bilingual_temp.srt` and a checkpoint. Rerun with `--resume`.

## Recommended command

```bash
bbook_maker \
  --book_name test_books/Lex_Fridman_episode_322.srt \
  --language zh-hans \
  --accumulated_num 400
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

### SRT flags

| flag | what it does |
|---|---|
| `--accumulated_num N` | Characters of subtitle blocks sent in one request (capped at 512 for SRT). |

## Not for this format

- `--batch_size`: not read by the SRT loader; group with `--accumulated_num`.
- `--use_context`: the SRT loader never hands context to the model, in either mode; the run warns.
- `--context_paragraph_limit`, `--context-compact-at`, `--no-context-compact`: context flags; nothing reads them here.
- `--glossary`, `--glossary-auto`: forwarded by the EPUB, Markdown and PDF loaders only; the run warns.
- `--max-batch-units`, `--plan-classify`, `--plan-dry-run`, `--plan-min-coverage`, `--poetry-group-size`: plan mode is EPUB only.
- `--classify-model`, `--classify-base-url`, `--classify-key` (and `--plan-classify-model`, the old name): nothing classifies on this format yet. The run warns that the flag is ignored.
- `--translate-tags`, `--exclude-translate-tags`, `--allow_navigable_strings`: EPUB markup selectors; the run warns.
- `--only_filelist`, `--exclude_filelist`: EPUB internal files; ignored.
- `--block_size`, `--sentence_mode`: EPUB only; ignored.
- `--parallel-workers`: the SRT run stays serial; the run warns.
- `--translation_style`, `--translation_color`: subtitles carry no style; the run warns.
- `--no_disclosure`, `--translation-metadata`: the output carries no credit line and no metadata file; the run warns.
- `--retranslate`: EPUB only; refused.
- `--quiet`: EPUB only; the run warns.
- `--batch`, `--batch-use`: the SRT loader does not implement the Batch API.
- `--to-epub`, `--pdf-ocr`, `--ocr-replace-layer`, `--device`, `--ocr-lang`, `--pages`, `--no-formula-images`, `--pdf_layout`, `--img-model`, `--img-base-url`, `--img-key`: PDF only. `--to-epub` on this format stops the run; the others warn or do nothing (the three image flags: the run warns that only the PDF route has an image step).
