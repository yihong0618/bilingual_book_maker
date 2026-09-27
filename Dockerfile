FROM python:3.12-slim AS core

LABEL org.opencontainers.image.source="https://github.com/yihong0618/bilingual_book_maker" \
      org.opencontainers.image.description="AI translation tool that creates bilingual epub/txt/srt/md books" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so the (slow) install layer is cached across code changes.
# requirements.txt is a complete hash-pinned lock export, which puts pip in
# hash-checking mode; every dependency ships manylinux wheels for amd64 and
# arm64, so no apt packages and no compiler are needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY book_maker/ ./book_maker/
COPY make_book.py ./

# The CLI writes two directories relative to the working directory:
# log/buglog.txt on every epub run, and batch_files/ on the openai batch
# route. Pre-created and world-writable so `docker run --user <uid>` works
# too. The container runs as root otherwise (owner, 260921): it holds one
# book and one key, and writing into the mounted folder must always work.
RUN mkdir -p /app/log /app/batch_files \
    && chmod 777 /app/log /app/batch_files

# argv is passed through verbatim, so every CLI flag works unchanged.
ENTRYPOINT ["python", "make_book.py"]
CMD ["--help"]

# ---------------------------------------------------------------------------
# The `pdf` tag: the PDF reading edition (--to-epub). The image above carries
# none of it. This stage adds Pandoc (which builds the EPUB) and the route's
# Python packages from requirements-pdf-gpu.txt -- docling and PyTorch, which
# on amd64 means the CUDA wheels and several gigabytes. That is why it is its
# own tag and never `latest`.
#   GPU means NVIDIA CUDA and nothing else, and only on the amd64 variant
#   of this image -- see the warning below. Linux: an NVIDIA driver plus the
#   NVIDIA Container Toolkit, then `docker run --gpus all`; torch's wheels
#   carry the CUDA runtime, so there is nothing else to install. Windows:
#   the same, through Docker Desktop's WSL2 backend, with the WSL-capable
#   NVIDIA driver installed on Windows itself rather than inside WSL
#   (Windows-containers mode cannot do it). Without a GPU the same image
#   runs on the CPU -- pass `--device cpu` to skip the detection. On macOS
#   the container is CPU-only whatever is passed: Docker runs a Linux VM
#   that cannot see Metal.
#   ARM64 WARNING: this image is built for linux/amd64 AND linux/arm64, but
#   PyPI's torch is only a CUDA build on x86_64 (2.7.1: 821.0 MB there
#   against 98.9 MB on aarch64, which carries no CUDA kernels). An arm64
#   host with a card -- GH200, Jetson -- pulls the arm64 manifest by
#   default and runs on the CPU however many --gpus are passed. Force the
#   x86_64 image with `--platform linux/amd64` there, or build the stage
#   against PyTorch's aarch64 CUDA index.
#   Codex: the codex route drives a `codex` binary signed in on the host;
#   neither the binary nor the login is in this image.
# No Java: the Java extractor was retired in favour of docling (260921).
# The docling models download on the first --to-epub run into /root/.cache;
# mount a volume there to keep them between runs.
FROM core AS pdf
# Pandoc comes from its GitHub release, not apt: the route needs 3.1.12 or
# newer (its EPUB contents point at headings) and Debian 13 ships 3.1.11.
# TARGETARCH is amd64 or arm64 under buildx, matching the release's .deb names.
ARG TARGETARCH
ARG PANDOC_VERSION=3.11
ADD https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${TARGETARCH}.deb /tmp/pandoc.deb
RUN apt-get update \
    && apt-get install -y --no-install-recommends /tmp/pandoc.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/pandoc.deb \
    && pandoc --version | head -1
COPY requirements-pdf-gpu.txt ./
RUN pip install --no-cache-dir -r requirements-pdf-gpu.txt

# The last stage is what a plain `docker build .` produces, so the small
# image stays the default; this stage is `core` under another name.
FROM core AS default
