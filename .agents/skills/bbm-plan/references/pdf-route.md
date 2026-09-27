# PDF flow: a bilingual, reflowable EPUB (`--to-epub`)

Loaded from SKILL.md when the book is a `.pdf`. `ROUTE` and `CONTEXT` come
from `references/route-setup.md`.

User pages, when you need more than this file says:
`docs/features/pdf-to-epub.md` (what the route does, the vision-model pass,
every terminal line and its fix), `docs/features/recommended-pdf.md`
(the command per document type and per system), `docs/installation-pdf.md` (the install
per system), `docs/formats/pdf.md` (every flag that applies, and the older
text route), `docs/docker.md` (the `pdf` image). Measurements behind the
advice: `docs/evaluation/pdf-*.md`.

## Recommend the bilingual EPUB, not the txt

Recommend `--to-epub` and say why in one line: it reads the PDF into
Markdown, translates that, and writes a **bilingual, reflowable EPUB with a
table of contents** next to the PDF (`<name>_bilingual.epub`), every
paragraph followed by its translation. Without the flag a PDF takes the old
route, which writes a bilingual `.txt` with no structure; only offer that if
the user asks for txt (then follow `references/plain-formats.md` as for a
TXT, with the `--batch_size` recommendation). This route is experimental: a
PDF is a page description with no paragraphs or headings in it, so the
extractor guesses the structure back, and a reflowable bilingual EPUB with
a working TOC out of that is already a good result. Say so up front.

**There is no plan step.** The Markdown loader translates every block, so
`--plan-classify` and the whole plan/classify sequence do not apply, and
`--classify-model` does nothing here (the run warns). Credentials, route,
language, prompt and the context flags from `references/route-setup.md`
apply unchanged; every Markdown-loader flag works the same here
(`--glossary`, `--parallel-workers` except with a session, `--test`).
Code blocks and tables are kept as they are, untranslated, as on a
Markdown book; the run lists them (`Preserved without translation: code
blocks (2).`). `--quiet` is not one of those flags: it belongs to the EPUB
loader and the run warns; the log redirect is what keeps a run out of the
conversation.

## Before the first command: check the machine

In this order; stop with the install line if one is missing (the run
refuses before the PDF is opened, naming the missing one, so nothing is
paid):

1. **The PDF extra**, an optional install: `python -c "import docling,
   pypdfium2"`. If it fails, the user installs it from a clone of the
   repository with `pip install ".[pdf]"`: the extra reuses a PyTorch that
   is already installed, where the locked requirements files would pin and
   download their own. Two systems add PyTorch's own index to that line,
   and the wrong choice costs gigabytes or silently loses the GPU:
   **Linux without an NVIDIA card** adds the CPU index (about 380 MB
   instead of about 3.2 GB of CUDA it cannot use); **Windows with an
   NVIDIA card** adds the CUDA index (`cu126`) plus the NVIDIA driver, or
   it runs on the processor. macOS: the plain line, nothing to choose. Send
   the user to their system's tab on `docs/installation-pdf.md` for the
   exact line rather than improvising one. Do **not** tell them `pip
   install "bbook_maker[pdf]"`: the published package does not carry the
   route yet, and pip answers an unknown extra with a warning and a
   successful install without it, so they arrive back at the same refusal.
   The models (about 500 MB) download on the first run. **No Java**:
   instructions that mention a JRE or Adoptium are out of date.
2. **Pandoc 3.1.12 or newer** on PATH (`pandoc -v`;
   https://pandoc.org/installing.html). Ubuntu 24.04 and Debian 13 apt
   ship older releases; an older one is refused by version before the PDF
   is opened.
3. **Only for a scanned PDF: `--pdf-ocr`.** Do not pass it speculatively:
   it costs several times the time and changes nothing on a born-digital
   PDF, and a page with no text layer is refused without it, never silently
   skipped, so the run tells you when it is needed. Layout, headings and
   tables are detected either way. A scan not in English also needs
   `--ocr-lang` (below). A scan that already carries an OCR layer
   (Internet Archive, ABBYY) is readable **without** `--pdf-ocr`: the run
   names such pages (`… carry only an invisible OCR text layer …`) and uses
   the layer. Run without the flag first; only when `source.md` shows the
   layer is garbage add `--pdf-ocr --ocr-replace-layer` with the scan's
   `--ocr-lang`.
   What counts as garbage: words, numbers or subscripts that differ from
   the page (`E_n` read as `En`, `5.22` as `522`, a sentence with words
   missing). A layer that only drops accents or polytonic Greek
   diacritics is good: keep it, fresh OCR reads such a page worse. Unsure?
   Run the two pages both ways and compare each `source.md` with the page
   (render it with pypdfium2 and look).
   `--ocr-engine` picks the engine (`auto` default; rapidocr for
   simplified Chinese, tesseract or ocrmac for English, measured):
   `docs/features/pdf-ocr-engines.md`. **Read the `OCR engine: …,
   languages: …` line the run prints**: `auto` is ocrmac only where ocrmac
   is installed (the pdf extra brings it on a Mac, an older install may
   lack it) and rapidocr otherwise, which read an English typewriter scan
   as fragments. tesseract needs each language's data installed with
   tesseract itself, and its default list includes Spanish, so pass
   `--ocr-lang iso:en` (or the scan's language) with it.
4. `--device` only if the default misbehaves: `auto` detects CUDA or MPS
   and falls back to the CPU. `--device cpu` gives the same text, only
   slower; it is never a downgrade in quality.

## Two pages first, and read `source.md` before paying

**Always.** Run once on the first two pages with `--test`, so the
extraction happens on two pages and only a few blocks are paid for, then
**read `<name>_pages-1-2_book/source.md`, the headings at least**, before
the full run. The top heading is `# <file name>` when the PDF opens with
none; it is translated like any heading, so rename it in `source.md` to
the real title first. The headings become the EPUB's table of contents, and the
extractor's heading detection is good on papers and much weaker elsewhere
(a Word-exported PDF can arrive with almost none). A page whose Markdown is
thousands of lines is trash, not a long page: the run warns about a page
that extracted far more text than a printed page holds. Fix the Markdown in
the bundle and rerun; the extraction is reused, a finished translation too
(delete `book_bilingual.md` to translate again).

```bash
# first look: extract two pages, translate a few blocks, then read source.md
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" --language "$LANG" --to-epub --pages 1-2 --test
# the full run (a session: a PDF extracts into many short blocks)
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" --language "$LANG" --to-epub "${CONTEXT[@]}" > run.log 2>&1
```

A rerun of the same command reuses the extraction and a finished
translation; no `--resume` is needed on this route.

## Flag recommendations

| flag | when |
|---|---|
| `--to-epub` | every PDF, unless the user asked for txt |
| `--use_context session` | the default on the openai/anthropic routes here too (route-setup step 5); `--parallel-workers` is refused with it |
| `--pdf-ocr` | a **scanned** PDF only (the run refuses without it and says so). Not for tables: those are detected either way |
| `--ocr-lang iso:ja` | with `--pdf-ocr`, a scan in a language the engine's default does not read (on a Mac `auto` is ocrmac, default English, Spanish, French, German; elsewhere rapidocr, default Chinese and English, one language per run). Portable `iso:` tags (`iso:zh`, `iso:zh-Hant`, `iso:ja`, `iso:ko`) work on every engine; not `ch_sim` on rapidocr, which refuses it before reading a page. Always name the language: the pdf extra installs ocrmac on a Mac, and with its default languages it read a Chinese scan as Latin. A typed PDF ignores it |
| `--ocr-replace-layer` | only with `--pdf-ocr`; when the embedded text is wrong, not merely invisible. Every page is OCR'd and the layer dropped; always name `--ocr-lang`. Toggling it re-extracts |
| `--device cpu` | when the detected accelerator misbehaves; same output, slower |
| `--pages 12-30` | the user wants one chapter or a range, or the paper's bibliography and appendix are not worth paying for; numbered from 1. The book is `<name>_pages-12-30_bilingual.epub` beside the whole-book one, never over it. A selection starting mid-section gets a `Page 12` heading in `source.md`; rename it there before the full run if the user wants a real title |
| `--pdf-image-dpi N` | how sharp the figures are, in dots per inch of the PDF's own page size (72 to 600). **Default 200**: sharp on a tablet or an iPad, nothing to pass when the user asks for sharp figures. 300 for figures with tiny labels (schematics, dense plots), 150 for a smaller book. A rerun at another value redraws the figures only and rebuilds the EPUB; extraction and translation are kept. A picture embedded in the PDF at a lower resolution keeps its own (the run says how many); formulas keep theirs |
| `--glossary` | the same file contract as on an EPUB; worth it on a paper with recurring terms |
| `--img-model gpt-5.6-luna` | a paper, a textbook, anything with code listings: a vision model corrects docling's region roles (an author line taken for a heading, a listing read as footnotes; 40 of 66 label faults fixed in the study, `docs/evaluation/pdf-structure-llm-roles.md`). About 3k prompt tokens a page. `(--provider openai)` already turns it on through the example's `img_model`; `--img-model none` turns it off. Needs an OpenAI-compatible endpoint that reads images; a local route gets it only with `--img-base-url` (and `--img-key`) at a hosted one. Changing it re-extracts |
| `--no-formula-images` | almost never: it replaces every display equation's picture with a bare placeholder |

By document type (the full run; every one starts with the two-page first
look; the user-facing version is `docs/features/recommended-pdf.md`):

| document | add to the full run | say to the user |
|---|---|---|
| novel | `--use_context session` | chapters set without numbering get their level from font size alone: read the headings in `source.md`. The role pass was measured on papers, web pages and code, not novels: offer `--img-model none` on a long novel to save its tokens |
| textbook with tables and formulas | `--pages A-B` per chapter, `--glossary` if terms recur | a bold theorem, lemma or corollary statement can come out as a heading: read the headings in `source.md` and demote it to a paragraph there. Tables are detected without OCR; display formulas become pictures, not translated (`docs/evaluation/pdf-formulas-as-images.md`); inline maths is not covered |
| paper | `--use_context session`, `--img-model gpt-5.6-luna` unless the entry already names one; `--pages` to leave out the bibliography | heading levels were exact on 187 of 195 headings across 20 arXiv papers (`docs/evaluation/pdf-heading-levels.md`) |
| scanned book | `--pdf-ocr` (not when the scan carries an OCR layer: see check 3), plus `--ocr-lang iso:<lang>` outside Chinese/English | JBIG2-masked scans (Internet Archive, ABBYY) get their page image from pypdfium2 and the run says so; still read `source.md` before paying. `--img-model` buys little here: it cannot rebuild a page docling shattered |
| scanned book with a garbage layer | `--pdf-ocr --ocr-replace-layer --ocr-lang iso:<lang>` (`iso:zh` for a Chinese scan) | only after a first look without it showed the layer is wrong; on a good layer fresh OCR reads worse. A page read empty is named and kept empty; nothing read anywhere stops before translation |
| Chinese scan | `--pdf-ocr --ocr-lang iso:zh` (`iso:zh-Hant` for traditional) | horizontal text reads well; **vertical** text comes back with its columns in the wrong order (CER 0.905 on the one page measured): do not translate it unreviewed (`docs/evaluation/pdf-ocr-llm-vs-local.md`) |

By system (the route needs no GPU; the device changes speed, never the
text):

| system | install (from a clone) | device |
|---|---|---|
| macOS, Apple silicon | `pip install ".[pdf]"` (nothing to choose) | `auto` finds MPS; the run prints `PDF extraction device: mps.` Docker cannot reach MPS on a Mac: install natively |
| Linux with NVIDIA | `pip install ".[pdf]"` | `auto` finds CUDA; `--device cuda` makes the run refuse, with the reason, if it cannot use it |
| Linux, CPU only | `pip install ".[pdf]"` plus PyTorch's CPU index (the line is on `docs/installation-pdf.md`; the plain line would pull about 3 GB of CUDA) | `--device cpu`; one two-page OCR scan took 26.3 s on the CPU against 10.6 s on MPS, identical text |
| Windows with NVIDIA | `pip install ".[pdf]"` plus PyTorch's `cu126` index (the line is on `docs/installation-pdf.md`), and the NVIDIA driver | `auto` finds CUDA |
| Windows, CPU only | `pip install ".[pdf]"` (PyPI's Windows wheel is already the CPU build) | `--device cpu` |
| Docker | image `ghcr.io/yihong0618/bilingual_book_maker:pdf` (Pandoc and the PDF packages inside); mount the book's folder and a models volume at `/root/.cache` | `--gpus all` on Linux or Windows (WSL2) with NVIDIA, amd64 image only (`docs/docker.md`) |

## What to tell the user up front

One line each, because they are limits of the format rather than of the
run: figures stay pictures and their labels are not translated (drawn at
200 DPI by default, sharp on a tablet; `--pdf-image-dpi` above); **display
equations are not decoded, so they are kept as pictures** cropped from the
page where they stood (seen, not translated, not searchable); **inline**
mathematics inside a paragraph is not covered at all (on a scan it arrives
as whatever OCR made of it); a sentence containing `\s`, `[u](y)` or `<k>`
can be refused before translation as raw TeX, a missing link or raw HTML
(the message names the block; escape it in `source.md` and rerun); the EPUB
carries no translation-metadata file and `--no_disclosure` is not honoured
on this route: the credit line is always added.

## Deliver

Read the book back before you hand it over. Unzip `<name>_bilingual.epub`
(or open `<name>_book/book_bilingual.md`) and check, in one early and one
late section:

- is the translation in the target language, right after its original?
- does the table of contents list the real headings, at sensible levels,
  with no author line, figure label or `Page N` placeholder the user did
  not approve?
- are figures and formula pictures present where they stood, at the
  expected size (`sips -g pixelWidth <png>`: a figure a third of a page
  wide at 200 DPI is about 550 px)?
- any delimiter or JSON residue in the text, or Pandoc syntax shown as
  text (`{#…}`, `:::`, a stray `*`) in a heading or the contents?
- is any translation identical to its original? A quotation in another
  script (Greek beside its English rendering) has come back unchanged. The
  run names such blocks (`… block(s) came back identical to the original
  …`); keeping one the book already renders nearby may be right. Tell the
  user which; to mend one, write the translation into its
  `bbm-translation` div in `<name>_book/book_bilingual.md`, run
  `python tools/pdf_to_book.py export <name>_book`, then copy
  `<name>_book/book_bilingual.epub` over `<name>_bilingual.epub` (the
  export writes only inside the bundle). Rerunning the `make_book.py`
  command instead is refused and names these two steps; it rebuilds
  nothing.

Then report the settings used (the run prints the OCR engine and languages,
the device and, with `--img-model`, the image model's token line), what the
first look and the read-back showed, and hand over `<name>_bilingual.epub`.

## Failure lines (all fail loud; `docs/features/pdf-to-epub.md#what-can-go-wrong` has every one)

| symptom | meaning |
|---|---|
| `reading a PDF needs the pdf extra, which is not installed …` | the extra is missing. From a clone: `pip install ".[pdf]"` (with PyTorch's CPU or CUDA index on the two systems in check 1). Not `pip install "bbook_maker[pdf]"`. Nothing was paid |
| `Pandoc is required for --to-epub …` | install it (check 2); it is checked before the PDF is opened |
| `pandoc 3.x is too old for EPUB export; Pandoc 3.1.12 or newer is required …` | apt's Pandoc (Ubuntu 24.04: 3.1.3, Debian 13: 3.1.11); install the release from pandoc.org. Nothing was paid |
| `N of M selected pages have no text layer …; rerun with --pdf-ocr` | a scanned PDF; the flag, not a different tool |
| `The parser produced no text for a document whose pages have no text layer …`, or `no text was recognised on page(s) …`, on a scan in a script the engine's default does not read | check the `OCR engine: …, languages: …` line; rerun with `--ocr-lang` (`iso:ja`, `iso:ko`, `iso:zh-Hant`); the bundle is read again |
| an OCR language the engine has no model for (the message names the engine and carries its list) | a code the engine does not know (`ch_sim` on rapidocr); use an `iso:` tag or a code from the list; nothing was read or paid |
| `page N: the OCR engine read nothing where the PDF carried a text layer; …` | `--ocr-replace-layer` read that page empty and it stays empty; rerun with the page's `--ocr-lang`, or without the flag to keep the layer |
| `The OCR engine read nothing on any selected page, and with --ocr-replace-layer …` | stopped before translation, nothing paid. Usually the wrong or missing `--ocr-lang`; else drop the flag |
| `--img-model needs an OpenAI-compatible endpoint …` | the image model would be asked at a non-OpenAI endpoint; add an OpenAI-compatible `--img-base-url` (key in `--img-key` or the entry's `img_env_key`) or drop it. Before extraction; nothing paid |
| `… did not read the probe image (…); image steps are skipped this run` | the model cannot see pictures there; the run goes on with docling's labels. Informational |
| `Region roles: C of D asked items on page N changed; read that page in source.md …` | most of a page was relabeled; read that page before translating |
| `Extraction: removed N control character(s) that docling read from glyphs on page(s) …` | informational: stray control bytes from odd glyphs, dropped so the EPUB builds. Look at the named pages in `source.md` for a missing symbol (a minus, an approximately-equal sign) |
| `Warning: page N extracted C characters, several times what a printed page holds …` | usually a figure carrying hidden text; remove it from `source.md` or leave the page out with `--pages` |
| `Unsupported Markdown structure: raw tex … at block N under heading "…". Normalize the source before translation.` | a sentence the EPUB writer cannot carry as text (raw TeX, raw HTML, a link-like `[u](y)`). Escape it in `source.md` (a backslash before `\`, `[` or `<`) and rerun; stopped before translation. Raw TeX a *model* writes into a translation is caught only at export: escape it in `book_bilingual.md` and export as for an echo (Deliver) |
| `Unsupported Markdown structure: control character U+0000 at line N. …` | `source.md` still holds a control character (an older bundle, or an edit); delete it and rerun. Nothing was translated |
| `EPUB navigation is invalid: …` | the headings do not form a usable contents; fix the levels in `source.md` (one `#` title, then `##`, `###`) and rerun |
| `Error: translate failed: Source or translation settings changed; start a new translation bundle.` | model, language or `source.md` changed after a partial translation; rerun with the original settings, or move the bundle aside |
