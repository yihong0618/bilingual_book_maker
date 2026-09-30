# Quick start

This page takes you from nothing to three bilingual books: an EPUB, a TXT and a PDF. It uses OpenAI's API and the default model, `gpt-5.6-luna`. Other endpoints are on [Translating with an LLM](llm-args.md).

## 1. Install

You need Python 3.10 or newer. A virtual environment is a good idea.

```bash
pip install -U bbook_maker
```

After the install, the command `bbook_maker` is on your PATH. From a checkout of the repository, `python make_book.py` is the same program; see [Installation](installation.md).

## 2. Give it a key

Put the key in the environment, so it stays off the command line.

```bash
export OPENAI_API_KEY=sk-...
```

You can pass `--key` instead. The lookup order is on [Translating with an LLM](llm-args.md#keys).

## 3. Try it on a few paragraphs first

`--test` translates only the first few paragraphs, so a mistake costs almost nothing. `--test_num` sets how many (default 10).

```bash
bbook_maker \
  --book_name test_books/animal_farm.epub \
  --test \
  --test_num 8
```

The sample books are in the repository's `test_books/` folder. Use your own file if you installed from pip.

## 4. An EPUB

```bash
bbook_maker \
  --book_name my_book.epub \
  --language zh-hans \
  --use_context session
```

- Output: `my_book_bilingual.epub`, beside the input.
- `--use_context session` keeps one conversation for the whole book, so names and terms stay consistent. See [Session mode](features/session-mode.md).
- The EPUB is translated through a plan by default: the tool finds every block of text (verse, tables and captions included) and asks the model which kinds are worth translating. See [Plan mode](features/plan-mode.md).

## 5. A TXT file

```bash
bbook_maker \
  --book_name test_books/the_little_prince.txt \
  --language zh-hans
```

- Output: `the_little_prince_bilingual.txt`, beside the input.
- Lines are sent in groups of `--batch_size` (default 10).

Markdown and SRT work the same way and give `<name>_bilingual.md` and `<name>_bilingual.srt`. See [Formats](formats/md.md).

## 6. A PDF

A PDF is best turned into a bilingual EPUB with a table of contents. That route needs the PDF extra and Pandoc 3.1.12 or newer; install them first with [PDF extra](installation-pdf.md). Then read two pages before you pay for the whole book:

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

Open `paper_pages-1-2_book/source.md` and read the headings. They become the table of contents. If they look right, run the whole file:

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --use_context session
```

- Output: the working folder `paper_book/` (with `source.md`, the images, `book_bilingual.md` and a manifest) and a copy of the book, `paper_bilingual.epub`, beside the PDF.
- A scanned PDF needs `--pdf-ocr`. The run tells you when it does.
- Without `--to-epub`, a PDF takes the older route and gives `paper_bilingual.txt`. See [PDF](formats/pdf.md).

## If a run stops

Press Ctrl+C at any time. The run saves what it has; an EPUB run leaves `my_book_bilingual_temp.epub` beside the input. Run the same command again with `--resume` to continue:

```bash
bbook_maker \
  --book_name my_book.epub \
  --use_context session \
  --resume
```

A `--to-epub` run needs no `--resume`: rerun the same command and it picks up the extraction and the translation where they stopped.
