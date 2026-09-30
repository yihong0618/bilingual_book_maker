# Docker

Use Docker if you do not want to set up Python yourself. Images are published to the GitHub Container Registry on every merge to `main` and on every release tag. The container runs `python make_book.py`, so every flag on this site works unchanged.

## Images and tags

There are three images, built from one Dockerfile.

| tag | what is in it | platforms | use it for |
|---|---|---|---|
| `latest` (also `basic`) | the translator and its Python packages on `python:3.12-slim`; about 280 MB on amd64 | amd64, arm64 | EPUB, TXT, Markdown, SRT, and PDFs on the older text route |
| `pdf` | `latest` plus Pandoc 3.11 and the PDF packages, with PyTorch's CPU build; about 2.4 GB on amd64 | amd64, arm64 | `--to-epub` on the processor: macOS, arm64 Linux, and any machine without an NVIDIA card |
| `pdf-cuda` | the official PyTorch CUDA runtime image plus Pandoc 3.11, the translator and the PDF packages; its base alone is a 3.4 GB download | amd64 | `--to-epub` on an NVIDIA GPU, on Linux or on Windows |
| `<version>`, `<version>-pdf`, `<version>-pdf-cuda` | the same three images at a release | | pinning a release |
| `sha-<commit>`, `sha-<commit>-pdf`, `sha-<commit>-pdf-cuda` | the same three images at one commit | | pinning a build |

```bash
docker pull ghcr.io/yihong0618/bilingual_book_maker:latest
```

## Mounts

- **Your book folder at `/book`.** Pass the book as `/book/<file>`. The translated book is written back into the same folder.
- **A named volume at `/root/.cache`** (the `pdf` and `pdf-cuda` images). The docling models download there on the first `--to-epub` run, about 500 MB. Without the volume every run downloads them again.

The container runs as root, so writing into the mounted folder always works. On Linux the files it writes belong to root: `chown` them afterwards, or add `--user $(id -u)`.

Pass the key as an environment variable rather than on the command line: `-e OPENAI_API_KEY`.

## An EPUB, per system

=== "Linux / macOS"

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:latest \
      --book_name /book/my_book.epub \
      --language zh-hans \
      --use_context session
    ```

=== "Windows PowerShell"

    ```powershell
    docker run --rm `
      -v "${PWD}:/book" `
      -e OPENAI_API_KEY `
      ghcr.io/yihong0618/bilingual_book_maker:latest `
      --book_name /book/my_book.epub `
      --language zh-hans `
      --use_context session
    ```

A test that needs no key at all, over the free Google route:

```bash
docker run --rm \
  -v "$PWD":/book \
  ghcr.io/yihong0618/bilingual_book_maker:latest \
  --book_name /book/animal_farm.epub \
  --api_format google \
  --test \
  --test_num 1 \
  --language zh-hant
```

## A PDF, per system

The `latest` image has no Pandoc and no PDF packages, so it cannot run `--to-epub`. With an NVIDIA card use the `pdf-cuda` tag; everywhere else use `pdf`.

=== "Linux with NVIDIA"

    Install the NVIDIA driver and the NVIDIA Container Toolkit on the host. Nothing else: the image carries the CUDA runtime.

    ```bash
    docker run --rm --gpus all \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf-cuda \
      --book_name /book/paper.pdf \
      --to-epub \
      --use_context session
    ```

=== "Windows with NVIDIA"

    Use Docker Desktop on the **WSL2 backend**. Install the WSL-capable NVIDIA driver on Windows itself, not inside WSL. Windows-containers mode cannot reach the GPU.

    ```powershell
    docker run --rm --gpus all `
      -v "${PWD}:/book" `
      -v bbm-models:/root/.cache `
      -e OPENAI_API_KEY `
      ghcr.io/yihong0618/bilingual_book_maker:pdf-cuda `
      --book_name /book/paper.pdf `
      --to-epub `
      --use_context session
    ```

=== "CPU only"

    The `pdf` image carries PyTorch's CPU build, for amd64 and arm64. `--device cpu` skips the accelerator detection. The output is the same as on a GPU; it is slower.

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

=== "macOS"

    The container is CPU-only whatever you pass: Docker runs a Linux VM that cannot see Metal. The `pdf` image runs natively on Apple silicon (arm64). The [native install](installation-pdf.md) is faster there (MPS).

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

## An NVIDIA card on arm64

`pdf-cuda` is built for amd64 only: the official PyTorch CUDA image it starts from is published for nothing else. On an arm64 Linux host with a card (GH200, Jetson), `pdf` runs on the processor.

## What is not in any image

The Codex route (`--api_format codex`). It drives a `codex` binary signed in on your machine; neither the binary nor the login is in the container. Use an API route in Docker.

## Build it yourself

A plain build gives the small image:

```bash
docker build --tag bilingual_book_maker .
```

The PDF images are the `pdf` and `pdf-cuda` stages:

```bash
docker build --target pdf --tag bilingual_book_maker:pdf .
docker build --platform linux/amd64 --target pdf-cuda --tag bilingual_book_maker:pdf-cuda .
```
