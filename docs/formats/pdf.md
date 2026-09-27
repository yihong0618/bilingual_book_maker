# PDF

A PDF can take two routes. Pick the first unless you want plain text.

| route | flag | output | needs |
|---|---|---|---|
| **PDF to bilingual EPUB** (experimental) | `--to-epub` | a reflowable EPUB with a table of contents, figures and display formulas as pictures | the [PDF extra](../installation-pdf.md) and Pandoc 3.1.12+ |
| **Text route** (older) | none | a bilingual `.txt`, plus an attempted EPUB | the base install |

## The `--to-epub` route

### How the file is read

1. [docling](https://github.com/docling-project/docling) reads the PDF's text layer with its layout and table models and writes Markdown: `<name>_book/source.md` and the images.
2. Pages with no text layer (a scan) are refused unless you pass `--pdf-ocr`; then the OCR models read them. A page that has a text layer keeps it, unless you also pass `--ocr-replace-layer`: then every page is OCR'd and the PDF's own layer is not used.
3. With `--img-model`, a vision model corrects the roles docling gave the page's regions (heading, caption, footnote, code) from the page image.
4. Heading levels are read from the page (numbering first, then font size and weight). Display formulas are cropped from the page as pictures.
5. The [Markdown loader](md.md) translates `source.md` into `book_bilingual.md`.
6. Pandoc builds the EPUB. Its navigation follows the headings.

A rerun of the same command reuses the extraction and a finished translation. Edit `source.md` before the translation runs if something came out wrong. Delete `book_bilingual.md` to translate again. [PDF to bilingual EPUB](../features/pdf-to-epub.md) has the recommended commands and the terminal lines to watch for.

### What you get

- `<name>_book/` beside the PDF: `source.md`, `images/`, `book_bilingual.md`, a manifest, and `.work/`.
- `<name>_bilingual.epub` beside the PDF, a copy of the finished book.
- In `source.md`, a `<!-- page N -->` comment where each page starts, numbered as in the PDF file (from 1, as `--pages` counts), empty pages included. Items docling gave no page, which is rare, come last, under `<!-- unplaced -->`; the run says how many.
- With `--pages 12-30`: `<name>_pages-12-30_book/` and `<name>_pages-12-30_bilingual.epub`, so a chapter never overwrites the whole book.

### Flags that apply

The route's own flags:

| flag | what it does |
|---|---|
| `--to-epub` | Take this route. |
| `--pdf-ocr` | Read pages with no text layer with the OCR models. Off by default; they are refused without it. |
| `--ocr-replace-layer` | With `--pdf-ocr`: OCR every page and use that text instead of the PDF's text layer. Off by default: a layer is kept and only pages without one are OCR'd. For a layer that is wrong (another language, garbage); on a clean scan the layer reads better. Toggling it reads the PDF again. |
| `--ocr-lang LANGS` | With `--pdf-ocr`: the languages the OCR engine reads, comma-separated, in the engine's own codes (rapidocr: `ch`, `en`, `latin`; easyocr: `ch_sim`, `ja`, `ko`; ocrmac: `zh-Hans`, `ja-JP`), or a BCP-47 tag behind `iso:` (`iso:zh`, `iso:ja`), which every engine accepts. rapidocr uses only the first language and refuses `ch_sim`: write `iso:zh` for Chinese. |
| `--device auto\|cpu\|cuda\|mps\|xpu` | Where the extraction models run. `auto` (default) detects an accelerator and falls back to the CPU. The CPU gives the same output, slower. |
| `--pages PAGES` | Only these pages, numbered from 1 (`12-30`, `1,3,5-7`). |
| `--no-formula-images` | Leave display formulas as `<!-- formula-not-decoded -->` placeholders instead of pictures. |
| `--img-model MODEL` | A vision model that corrects region roles from the page image. Off unless named here or as the provider entry's `img_model` (the shipped `openai` entry names one); `none` turns that off. Never the translating model by fallback. About 3,000 prompt tokens per page. |
| `--img-base-url URL` | Where that model is served, when it is not the run's endpoint (OpenAI-compatible only). |
| `--img-key KEY` | The key for `--img-base-url`; defaults to the run's key on the run's own endpoint. |
| `--quiet` | No live progress line during extraction. |

Every other flag goes to the Markdown translation unchanged. These are the ones that do something there:

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
| `--batch_size N` | Prose blocks per request (default 10). |
| `--use_context [window\|session]` | Carry earlier text. `session` is recommended: a PDF extracts into many short blocks. |
| `--context_paragraph_limit N` | Window mode: pairs to re-send. |
| `--context-compact-at N` | Session mode: compact at this many estimated tokens (default 8192, minimum 1500). |
| `--no-context-compact` | Session mode: roll over without a handoff report. |
| `--glossary FILE` | `term -> translation` pins (openai-shaped and codex routes). |
| `--glossary-auto on\|off` | Keep renderings from handoff reports. Learns only when a compaction happens, so a short paper at the default budget learns nothing. |
| `--parallel-workers N` | Several batches at once. Refused with a session. |

`--with-ocr` and `--no-gpu`, the route's old spellings, still work as `--pdf-ocr` and `--device cpu` for one release and print a notice. They are not in `--help`.

### Not for the `--to-epub` route

- `--no_disclosure`: not honored yet; the credit line is always added.
- `--translation-metadata`: Pandoc builds this EPUB, which carries no metadata file and no embedded glossary.
- `--pdf_layout`: belongs to the text route.
- `--accumulated_num`, `--max-batch-units` and the plan flags (`--plan-classify`, `--plan-dry-run`, `--plan-min-coverage`): plan mode is EPUB only; the Markdown loader groups with `--batch_size`.
- `--classify-model`, `--classify-base-url`, `--classify-key`: nothing on this route classifies yet. The run warns that the flag is ignored.
- `--translate-tags`, `--exclude-translate-tags`, `--allow_navigable_strings`, `--only_filelist`, `--exclude_filelist`, `--block_size`, `--sentence_mode`, `--translation_style`, `--translation_color`, `--retranslate`: EPUB input only.
- `--batch`, `--batch-use`: not implemented on this route.

## The text route (no `--to-epub`)

### How the file is read

The text of every page is taken with PyMuPDF and split into lines. Lines are sent in groups of `--batch_size` (default 10), with earlier text when `--use_context` is on (window or `session`) and the pins of a `--glossary`. There is no structure: no headings, no figures, no tables.

### What you get

- `<name>_bilingual.txt` beside the PDF.
- An attempt at `<name>_bilingual.epub` from the same text. If it fails, the TXT remains, so nothing is translated twice.
- With `--pdf_layout`: `<name>_bilingual_<layout>.pdf` for `top-bottom`, `side-by-side`, or both with `all`. This needs the `reportlab` package, which is not in the requirements; without it the run prints `pdf creation skipped: install reportlab first` and goes on.
- On Ctrl+C or an error: `<name>_bilingual_temp.txt`. Rerun with `--resume`.

```bash
bbook_maker \
  --book_name paper.pdf \
  --language zh-hans \
  --batch_size 20
```

### Flags that apply

The route and run flags in the table above, plus:

| flag | what it does |
|---|---|
| `--batch_size N` | Lines per request (default 10). |
| `--pdf_layout none\|top-bottom\|side-by-side\|all` | Also write a bilingual PDF in this layout (needs `reportlab`). |

### Not for the text route

- `--parallel-workers`: the run stays serial. The run warns.
- `--pdf-ocr`, `--ocr-replace-layer`, `--device`, `--ocr-lang`, `--pages`, `--no-formula-images`: `--to-epub` only. The run warns and reads the whole file.
- `--img-model`, `--img-base-url`, `--img-key`: the image step belongs to `--to-epub`. The run warns.
- `--classify-model` and its two companions: nothing classifies here. The run warns.
- Every EPUB-only flag listed for the `--to-epub` route above, and `--accumulated_num`, `--quiet`, `--no_disclosure`, `--translation-metadata`.
