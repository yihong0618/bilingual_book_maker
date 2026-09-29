# Docker

Use Docker if you do not want to set up Python yourself. Images are published to the GitHub Container Registry on every merge to `main` and on every release tag. The container runs `python make_book.py`, so every flag on this site works unchanged.

## Images and tags

There are two images, built from one Dockerfile on `python:3.12-slim`, for `linux/amd64` and `linux/arm64`.

| tag | what is in it | use it for |
|---|---|---|
| `latest` (also `basic`) | the translator and its Python packages; a few hundred megabytes | EPUB, TXT, Markdown, SRT, and PDFs on the older text route |
| `pdf` | `latest` plus Pandoc 3.11 and the PDF packages (docling and PyTorch; on amd64 the CUDA build, several gigabytes) | `--to-epub` |
| `<version>`, `<version>-pdf` | the same two images at a release | pinning a release |
| `sha-<commit>`, `sha-<commit>-pdf` | the same two images at one commit | pinning a build |

```bash
docker pull ghcr.io/yihong0618/bilingual_book_maker:latest
```

## Mounts

- **Your book folder at `/book`.** Pass the book as `/book/<file>`. The translated book is written back into the same folder.
- **A named volume at `/root/.cache`** (the `pdf` image). The docling models download there on the first `--to-epub` run, about 500 MB. Without the volume every run downloads them again.

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

The `latest` image has no Pandoc and no PDF packages, so it cannot run `--to-epub`. Use the `pdf` tag.

=== "Linux with NVIDIA"

    Install the NVIDIA driver and the NVIDIA Container Toolkit on the host. Nothing else: PyTorch's wheels carry the CUDA runtime.

    ```bash
    docker run --rm --gpus all \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
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
      ghcr.io/yihong0618/bilingual_book_maker:pdf `
      --book_name /book/paper.pdf `
      --to-epub `
      --use_context session
    ```

=== "CPU only"

    The same image runs on the processor. `--device cpu` skips the accelerator detection. The output is the same; it is slower.

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

    The container is CPU-only whatever you pass: Docker runs a Linux VM that cannot see Metal. The image also ships the CUDA build of PyTorch. On Apple silicon the [native install](installation-pdf.md) is both faster (MPS) and much smaller.

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

## GPU only on amd64

The images are published for both architectures, but PyPI's PyTorch is a CUDA build only on x86_64:

| torch 2.7.1, Linux | |
|---|---|
| `manylinux_2_28_x86_64` | 821.0 MB — the CUDA build |
| `manylinux_2_28_aarch64` | 98.9 MB — no CUDA kernels |

So an arm64 Linux host that *does* have a card (GH200, Jetson) pulls the arm64 image by default and runs on the processor, however many `--gpus` you pass. Pull the x86_64 image there:

```bash
docker run --rm --platform linux/amd64 --gpus all \
  -v "$PWD":/book \
  -v bbm-models:/root/.cache \
  -e OPENAI_API_KEY \
  ghcr.io/yihong0618/bilingual_book_maker:pdf \
  --book_name /book/paper.pdf \
  --to-epub
```

## What is not in either image

The Codex route (`--api_format codex`). It drives a `codex` binary signed in on your machine; neither the binary nor the login is in the container. Use an API route in Docker.

## Build it yourself

A plain build gives the small image:

```bash
docker build --tag bilingual_book_maker .
```

The PDF image is the `pdf` stage:

```bash
docker build --target pdf --tag bilingual_book_maker:pdf .
```
