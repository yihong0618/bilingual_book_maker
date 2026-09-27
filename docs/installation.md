# Installation

bilingual book maker is a Python program. It needs Python 3.10 or newer. A virtual environment keeps it apart from your other packages.

## From PyPI

```bash
pip install -U bbook_maker
```

This gives you the `bbook_maker` command. It translates EPUB, TXT, Markdown and SRT files, and PDFs on the older text route.

The published package does not carry the PDF-to-EPUB route yet. If you want that, install from a checkout (below) and add the [PDF extra](installation-pdf.md).

## From a checkout

Use a checkout if you want the newest code or the PDF-to-EPUB route.

```bash
git clone https://github.com/yihong0618/bilingual_book_maker.git
```

```bash
cd bilingual_book_maker
```

```bash
pip install -r requirements.txt
```

`requirements.txt` is the pinned, tested set. `pip install .` also works and takes whatever versions resolve today. From a checkout the command is `python make_book.py`, with the same flags as `bbook_maker`.

## The PDF extra

If you want to turn PDFs into bilingual EPUBs (`--to-epub`), add the PDF extra from the same checkout. It brings docling and PyTorch; the models (about 500 MB) download on the first run, and you also need Pandoc 3.1.12 or newer.

```bash
pip install ".[pdf]"
```

The extra reuses a PyTorch you already have instead of downloading a pinned one. That line is right on Apple silicon, on Linux with an NVIDIA GPU, and on Windows without one. Linux without an NVIDIA GPU adds PyTorch's CPU index, or it downloads about 3 GB of CUDA it cannot use; Windows with an NVIDIA GPU adds PyTorch's CUDA index. [Installing the PDF extra](installation-pdf.md) has the line for every case.

Do not run `pip install "bbook_maker[pdf]"`. The published package does not carry the route yet: pip only warns and installs the release without it.

## Docker

If you would rather not install Python packages, use the published image. See [Docker](docker.md).

## Check the install

```bash
bbook_maker --help
```

From a checkout:

```bash
python make_book.py --help
```

The help text is the authority on every flag. [Command line options](cmd.md) lists them in one table.
