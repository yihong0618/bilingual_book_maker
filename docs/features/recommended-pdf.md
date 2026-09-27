# Recommended settings for PDF

Start every PDF the same way: two pages, a few translated blocks, then read what came out.

```bash
python make_book.py \
  --book_name book.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

It extracts only those two pages and costs almost nothing. Open `book_pages-1-2_book/source.md` and read the headings at least: they become the table of contents. A page whose Markdown runs to thousands of lines is junk from a figure, not a long page. When it looks right, run the command for your document below. If something is off, fix it in `source.md` and rerun; the extraction is reused.

The route needs the [PDF extra](../installation-pdf.md) and Pandoc 3.1.12 or newer. What each flag does is on [PDF to bilingual EPUB](pdf-to-epub.md).

## If you need this, pass that

| if your PDF is… | pass | why | read more |
|---|---|---|---|
| any PDF you want as a book | `--to-epub --use_context session` | a reflowable EPUB with contents; a session gives each short block the text before it | [PDF to bilingual EPUB](pdf-to-epub.md) |
| a scan (no text layer) | `--pdf-ocr` | pages without text are refused until you pass it, so you never need to guess | [OCR flags](pdf-to-epub.md#setup) |
| a scan in Chinese, Japanese, Korean or another script outside the Latin alphabet | `--pdf-ocr --ocr-lang iso:ja` (or `iso:ko`, …) | the default OCR engine reads only its own languages: on a Mac (ocrmac) English, Spanish, French and German, elsewhere (rapidocr) Chinese and English | [OCR language](pdf-to-epub.md#setup) |
| a Chinese scan | `--pdf-ocr --ocr-lang iso:zh` (`iso:zh-Hant` for traditional) | not `ch_sim`: rapidocr refuses it | [Chinese scan](#by-document-type) |
| a simplified-Chinese scan | `--pdf-ocr --ocr-engine rapidocr --ocr-lang iso:zh` | lowest Han error in the measurement | [Which OCR engine](pdf-ocr-engines.md) |
| on a Mac, and you want no download | `--pdf-ocr` (auto picks ocrmac, which the pdf extra installs on a Mac; `--ocr-engine ocrmac` names it) | Apple's Vision framework, nothing to download; within a hair of the best on English and traditional Chinese, rapidocr better on simplified-Chinese scans | [Which OCR engine](pdf-ocr-engines.md) |
| a scan that already has an OCR layer (Internet Archive, ABBYY) | nothing extra | the run uses the layer and says so | [Scanned book](#by-document-type) |
| a scan whose text layer is garbage | `--pdf-ocr --ocr-replace-layer --ocr-lang <lang>` | every page is read again by the OCR engine | [Scan with a bad text layer](#by-document-type) |
| a paper, or anything with code listings | `--img-model gpt-5.6-luna` | a vision model fixes author lines taken for headings and listings read as footnotes, about 3,000 prompt tokens a page | [Correcting region roles](pdf-to-epub.md#correcting-region-roles-with-a-vision-model) |
| a long book you want a chapter at a time | `--pages 12-30` | each range gets its own book and never overwrites another | [PDF flags](../formats/pdf.md) |
| full of maths | nothing extra | display formulas are kept as pictures by default | [Why formulas are pictures](../evaluation/pdf-formulas-as-images.md) |
| translated with names or terms that must hold | `--glossary terms.txt` | pinned renderings, sent only with the blocks they occur in | [Session mode](session-mode.md) |
| on a machine whose GPU misbehaves | `--device cpu` | the same text, only slower | [By system](#by-system) |

## By document type

=== "Novel"

    A typed novel has few headings and no tables. Leave out front matter you do not want with `--pages`.

    ```bash
    python make_book.py \
      --book_name novel.pdf \
      --to-epub \
      --language zh-hans \
      --use_context session \
      --quiet
    ```

    Check the chapter headings in `source.md` first: a novel often sets chapters without numbering, and their level then comes from font size alone. The region-role pass was measured on papers, web pages and code listings, not on novels; with `--provider openai` it is on, and `--img-model none` saves its tokens.

=== "Textbook with tables and formulas"

    Tables are detected without OCR. Display formulas become pictures; the prose around them is translated, the equations are not.

    ```bash
    python make_book.py \
      --book_name textbook.pdf \
      --to-epub \
      --pages 12-30 \
      --language zh-hans \
      --use_context session \
      --glossary terms.txt
    ```

    Translate a chapter at a time with `--pages`; each range gets its own book. A textbook with code listings gains from `--img-model gpt-5.6-luna`: the listing's lines come out as code instead of footnotes. Inline mathematics inside a sentence is not a formula region and is not covered: it arrives as whatever the text layer or OCR made of it.

=== "Paper"

    A paper extracts into many short blocks; a session gives each block the text before it.

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --language zh-hans \
      --use_context session \
      --img-model gpt-5.6-luna
    ```

    Heading levels come out exact on most papers (187 of 195 headings across 20 arXiv papers, see [the evaluation](../evaluation/pdf-heading-levels.md)). `--img-model` demotes an author line or a figure label taken for a heading, for about 3,000 prompt tokens a page; leave it out to spend nothing on it. Leave out the bibliography with `--pages` if you do not want to pay for it.

=== "Scanned book"

    The run refuses a page with no text layer until you pass `--pdf-ocr`.

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --language zh-hans \
      --use_context session
    ```

    Without `--ocr-lang` the OCR engine reads its own default languages: on a Mac (ocrmac) English, Spanish, French and German, elsewhere (rapidocr) Chinese and English. A scan in another language needs `--ocr-lang`.

    **If the scan already carries an OCR layer** (Internet Archive and ABBYY FineReader files often do), try it without `--pdf-ocr` first: the run prints `… selected pages carry only an invisible OCR text layer …` and uses that layer, which usually reads better than a fresh local OCR. If the layer turns out to be garbage, see the next tab.

    `--img-model` does little on a scan: it cannot rebuild a page the layout detector shattered into fragments.

=== "Scan with a bad text layer"

    If `source.md` reads as nonsense although the page image is clear, the scan's text layer is wrong: recognised in the wrong language, or garbage from a poor OCR. `--ocr-replace-layer` drops it and reads every page with the OCR engine instead.

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --ocr-replace-layer \
      --ocr-lang iso:zh \
      --language en \
      --use_context session
    ```

    Put the scan's own language in `--ocr-lang` (`iso:zh` here, for a Chinese scan): in the wrong language the engine reads nothing. Try two pages first. A page the engine read nothing on is named and left empty; if no page was read, the run stops before any translation is paid for. Do not use it on a scan whose layer reads well: the fresh OCR reads worse than a good layer.

=== "Chinese scan"

    Name the language the OCR engine should read. An `iso:` tag works whichever engine runs: `iso:zh` for simplified characters, `iso:zh-Hant` for traditional. Not `ch_sim`: rapidocr refuses it before reading a page.

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --ocr-lang iso:zh \
      --language en \
      --use_context session
    ```

    Horizontal text reads well. **Vertical text** (traditional books set top to bottom, right to left) comes out with its columns in the wrong order. Check `source.md` before you translate a vertical scan. See [Why a vision model reads scans better](../evaluation/pdf-ocr-llm-vs-local.md).

## By system

The route needs no GPU. The device changes the speed, never the text. Install from a clone of the repository; [Installing the PDF extra](../installation-pdf.md) explains every line.

=== "macOS (Apple silicon)"

    ```bash
    pip install ".[pdf]"
    ```

    Install natively; `--device auto` finds MPS and the run prints `PDF extraction device: mps.` Docker cannot reach MPS on a Mac.

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --use_context session
    ```

=== "Linux with NVIDIA"

    ```bash
    pip install ".[pdf]"
    ```

    `--device auto` finds CUDA and the run prints `PDF extraction device: cuda.` To be sure, name it; the run then refuses, with the reason, if it cannot use it:

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --device cuda \
      --use_context session
    ```

=== "Windows with NVIDIA"

    ```bat
    pip install ".[pdf]" ^
        --extra-index-url https://download.pytorch.org/whl/cu126
    ```

    PyPI's Windows wheel is CPU-only, so name PyTorch's CUDA index, and install the NVIDIA driver. Then run as on Linux.

=== "CPU only"

    On Linux, add PyTorch's CPU index (about 380 MB instead of about 3.2 GB of CUDA):

    ```bash
    pip install ".[pdf]" \
        --extra-index-url https://download.pytorch.org/whl/cpu
    ```

    On Windows the plain `pip install ".[pdf]"` already gives the CPU build. The text is the same as on a GPU; extraction is slower (a two-page OCR scan: 26.3 s on the CPU against 10.6 s on Apple silicon's GPU).

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

=== "Docker"

    The `pdf` image tag carries Pandoc and the PDF packages. Keep the models in a volume so they download once.

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --use_context session
    ```

    Add `--gpus all` on Linux or Windows (WSL2) with an NVIDIA card; the GPU works only on the amd64 image. See [Docker](../docker.md).

## What to expect from any PDF

- Figures stay pictures and their labels are not translated.
- Display equations are pictures cropped from the page: seen where they stood, not translated, not searchable. Inline mathematics is not covered.
- A sentence containing `\s`, `[u](y)` or `<k>` can be refused before translation; escape it in `source.md` and rerun.
- The EPUB always carries the one-line AI-translation credit on this route; `--no_disclosure` is not honored here.
- A PDF is a page description, not a document: a heading one level off or a table that arrives as prose is the format showing through, and a minute's edit in `source.md`.
