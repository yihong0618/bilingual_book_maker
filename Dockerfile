# Three images from one file (owner, 260929):
#
#   core      `latest`, `basic`, `<version>`  python:3.12-slim, the translator.
#             EPUB, TXT, Markdown, SRT, and PDFs on the older text route.
#   pdf       `pdf`, `<version>-pdf`          core + Pandoc + the PDF packages
#             with PyTorch's CPU build (requirements-pdf-cpu.txt). amd64 and
#             arm64. The image for every host without an NVIDIA card: macOS
#             (Apple silicon or Intel), arm64 Linux, Linux or Windows on the
#             CPU.
#   pdf-cuda  `pdf-cuda`, `<version>-pdf-cuda` the official
#             pytorch/pytorch CUDA *runtime* image + Pandoc + the translator
#             and the rest of the PDF packages. amd64 only. Linux with an
#             NVIDIA card (`docker run --gpus all`, NVIDIA driver plus the
#             NVIDIA Container Toolkit on the host), and Windows through
#             Docker Desktop's WSL2 backend (the WSL-capable NVIDIA driver on
#             Windows itself; Windows-containers mode cannot reach the GPU).
#
# Every tag also exists as `sha-<commit>` with the same suffix. Sizes on
# linux/amd64, measured 260929 with `docker image inspect`: core 278 MB, pdf
# 2.35 GB (the pdf packages 1.66 GB, torch 2.7.1+cpu 0.69 GB of that;
# Pandoc and cv2's GL libraries 0.38 GB). pdf-cuda's base alone is a 3.4 GB
# compressed download; the workflow's smoke job prints every image's size.
#
# GPU means NVIDIA CUDA and nothing else. pdf-cuda also runs on the CPU (the
# CI smoke does exactly that) and `--device cpu` skips the detection, but pdf
# is the smaller image for that. On macOS any container is CPU-only whatever
# is passed: Docker runs a Linux VM that cannot see Metal; a native install
# is faster there (MPS). An arm64 host with a card (GH200, Jetson) has no
# CUDA image here: pdf runs on its processor.
#
# The docling models download on the first --to-epub run into /root/.cache,
# about 500 MB; mount a named volume there (`-v bbm-models:/root/.cache`) to
# keep them between runs. No Java: the Java extractor was retired in favour
# of docling (260921).
#
# Codex: the codex route drives a `codex` binary signed in on the host;
# neither the binary nor the login is in any of these images.

# Pandoc comes from its GitHub release, not apt: the route needs 3.1.12 or
# newer (its EPUB contents point at headings); Debian 13 ships 3.1.11 and
# Ubuntu 22.04, pdf-cuda's base, older still.
ARG PANDOC_VERSION=3.11

# `core-deps` is not published: python:3.12-slim with requirements.txt and
# the runtime settings, but no application code. core and pdf both start from
# it and copy the application last, so a change to book_maker/ rebuilds one
# small COPY layer in each and never pdf's Pandoc or PyTorch layers.
FROM python:3.12-slim AS core-deps

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
# `core`: the translator.
FROM core-deps AS core
COPY book_maker/ ./book_maker/
COPY make_book.py ./

# ---------------------------------------------------------------------------
# `pdf`: core's dependencies + Pandoc + the PDF route's packages with PyTorch's CPU build.
# requirements-pdf-cpu.txt names PyTorch's CPU index itself and carries no
# nvidia/triton pins, so this is a few hundred megabytes of torch on both
# architectures instead of the gigabytes PyPI's Linux x86_64 wheel pulls in.
FROM core-deps AS pdf
# TARGETARCH is amd64 or arm64 under buildx, matching the release's .deb names.
ARG TARGETARCH
ARG PANDOC_VERSION
ADD https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${TARGETARCH}.deb /tmp/pandoc.deb
# The lock's opencv-python (rapidocr's dependency) is the GUI build: cv2 links
# libxcb, libGL and glib, and without them docling's first import of it fails
# with "libxcb.so.1: cannot open shared object file" (measured 260929).
RUN apt-get update \
    && apt-get install -y --no-install-recommends /tmp/pandoc.deb \
        libxcb1 libgl1 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/* /tmp/pandoc.deb \
    && pandoc --version | sed -n 1p
COPY requirements-pdf-cpu.txt ./
RUN pip install --no-cache-dir -r requirements-pdf-cpu.txt

COPY book_maker/ ./book_maker/
COPY make_book.py ./

# ---------------------------------------------------------------------------
# `pdf-cuda`: the official PyTorch image, whose torch, torchvision, triton
# and nvidia-*-cu12 libraries are the lock's own versions (the tag's torch
# version is pinned against requirements-pdf-gpu.txt by
# tests/test_docker_pins.py). The `-runtime` variant, never `-devel` (owner
# 260929: devel is 7.4 GB of compilers and headers nothing here uses). Its
# Python is conda's 3.11 under /opt/conda, torch pip-installed there from
# PyTorch's cu126 index (pytorch/pytorch v2.7.1 Dockerfile, conda-installs
# stage). linux/amd64 only: the base is published for nothing else.
FROM pytorch/pytorch:2.7.1-cuda12.6-cudnn9-runtime AS pdf-cuda

LABEL org.opencontainers.image.source="https://github.com/yihong0618/bilingual_book_maker" \
      org.opencontainers.image.description="AI translation tool that creates bilingual epub/txt/srt/md books" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pipefail, so the download guard below fails the build rather than the tee.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

ARG TARGETARCH
ARG PANDOC_VERSION
ADD https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${TARGETARCH}.deb /tmp/pandoc.deb
# cv2's system libraries as in `pdf`; Ubuntu 22.04 names glib without t64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends /tmp/pandoc.deb \
        libxcb1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* /tmp/pandoc.deb \
    && pandoc --version | sed -n 1p

# A virtual environment over the base's Python, seeing its site-packages:
# torch and its CUDA libraries are used from the base where they are, and
# where the lock pins another version of something conda installed, pip puts
# the pinned one in the venv in front of it instead of uninstalling conda's
# copy (pip cannot uninstall a conda package that has no RECORD file, and
# nothing here needs it to). `python` on PATH is the venv's; pip is the
# base's, always run as `python -m pip` so it installs into the venv.
RUN python -m venv --system-site-packages --without-pip /opt/bbm
ENV PATH=/opt/bbm/bin:$PATH

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

# The pdf extra minus what the base already has: tools/torch_base_requirements.py
# drops torch, torchvision, triton and nvidia-* from the tracked lock export
# (every other pin and hash verbatim) and first checks that the base's copies
# are the pinned versions, failing the build if not. The grep then fails the
# build if pip fetched any of them anyway.
COPY requirements-pdf-gpu.txt ./
COPY tools/torch_base_requirements.py /tmp/
RUN python /tmp/torch_base_requirements.py --check-installed requirements-pdf-gpu.txt \
        > /tmp/requirements-pdf-torch-base.txt \
    && python -m pip install --no-cache-dir -r /tmp/requirements-pdf-torch-base.txt 2>&1 \
        | tee /tmp/pip.log \
    && if grep -iE '(Collecting|Downloading) (\S*/)?(torch|torchvision|triton|nvidia)' /tmp/pip.log; then \
           echo "pip fetched a package the base image provides" >&2; exit 1; \
       fi \
    && rm /tmp/pip.log /tmp/requirements-pdf-torch-base.txt /tmp/torch_base_requirements.py

COPY book_maker/ ./book_maker/
COPY make_book.py ./

RUN mkdir -p /app/log /app/batch_files \
    && chmod 777 /app/log /app/batch_files

ENTRYPOINT ["python", "make_book.py"]
CMD ["--help"]

# The last stage is what a plain `docker build .` produces, so the small
# image stays the default; this stage is `core` under another name.
FROM core AS default
