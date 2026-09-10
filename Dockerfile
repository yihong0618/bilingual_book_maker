FROM python:3.12-slim

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

RUN groupadd --gid 1000 bbm \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin bbm \
    # The CLI writes two directories relative to the working directory:
    # log/buglog.txt on every epub run, and batch_files/ on the openai batch
    # route. Pre-create them writable; /app itself stays root-owned so the
    # container cannot rewrite its own code. Group root + g+w keeps them
    # writable under `docker run --user <uid>` too, since an overridden uid
    # still runs with gid 0.
    && mkdir -p /app/log /app/batch_files \
    && chown 1000:0 /app/log /app/batch_files \
    && chmod 775 /app/log /app/batch_files
USER 1000:1000

# argv is passed through verbatim, so every CLI flag works unchanged.
ENTRYPOINT ["python", "make_book.py"]
CMD ["--help"]
