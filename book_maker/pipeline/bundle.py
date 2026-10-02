"""The output bundle: its layout on disk, and the manifest that describes it.

    book-output/
      source.raw.md          preserved extractor output or imported source
      source.md              the translation input, editable
      assets/                local images, shared by every Markdown version
      book_bilingual.md      the editable bilingual deliverable
      book_bilingual.epub    the self-contained reading edition
      manifest.json          stage status, fingerprints, versions, limits
      .work/                 resume state, job handles, temporary output

The manifest records run identity and recovery, not a second copy of the
book: fingerprints of what went in, what each stage produced, and the
limitations that were reported. It never holds a credential.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from .errors import PipelineError
from .messages import OCR_LANG_EMPTY

SCHEMA_VERSION = 1

RAW_SOURCE_NAME = "source.raw.md"
SOURCE_NAME = "source.md"
ASSETS_DIR = "assets"
BILINGUAL_NAME = "book_bilingual.md"
EPUB_NAME = "book_bilingual.epub"
MANIFEST_NAME = "manifest.json"
WORK_DIR = ".work"

# Inside .work/
TRANSLATE_STATE = "translate.state.json"
TRANSLATE_RESULT = "translate.result.json"
TRANSLATE_TEMP = "translate.partial.txt"
EXTRACTION_JOB = "extraction.job.json"
SUBMIT_UNKNOWN = "extraction.submit-unknown.json"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contained_path(root, relative, *, what="path"):
    """`root / relative`, refused when it would leave `root`.

    Extractor payloads name their own files and a bilingual Markdown file is
    hand-edited, so both can name `../../etc/something`. Resolved against a
    resolved root and compared, because a symlink inside the bundle is the
    other way out.
    """
    root = Path(root).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise PipelineError(f"{what} escapes the bundle: {relative}")
    return candidate


# One page-selection spelling for the whole harness, numbered from 1 the
# way a reader numbers pages. Each adapter converts it to whatever its own
# engine counts in; Datalab counts from 0, OpenDataLoader from 1, and a
# selection silently off by one page is the kind of mistake that is only
# found after the book has been paid for.
PAGE_SPEC = re.compile(r"^\s*\d+\s*(?:-\s*\d+\s*)?(?:,\s*\d+\s*(?:-\s*\d+\s*)?)*$")


def parse_ocr_lang(value):
    """The language codes in an `--ocr-lang` value, or None for none given.

    Split on commas, blanks dropped; the codes themselves are not checked
    here (the engine knows its own list and refuses an unknown one). A
    list already split is taken as it is, so a stage that parsed the flag
    can hand the result on without it being split a second time (which
    turned `["ch_sim", "en"]` into the code `['ch_sim'`, 260921).
    """
    if value is None:
        return None
    parts = value.split(",") if isinstance(value, str) else list(value)
    codes = [str(code).strip() for code in parts if str(code).strip()]
    if not codes:
        raise PipelineError(OCR_LANG_EMPTY)
    return codes


def parse_pages(spec):
    """`"1-20,25"` -> `[(1, 20), (25, 25)]`, inclusive and numbered from 1."""
    if spec is None:
        return None
    if not PAGE_SPEC.match(str(spec)):
        raise PipelineError(
            f"{spec!r} is not a page selection; write pages numbered from 1, "
            f"as in 1-20 or 1,3,5-7"
        )
    ranges = []
    for part in str(spec).split(","):
        first, _, last = part.partition("-")
        start = int(first)
        end = int(last) if last.strip() else start
        if start < 1:
            raise PipelineError("pages are numbered from 1; page 0 does not exist")
        if end < start:
            raise PipelineError(f"page range {part.strip()!r} ends before it starts")
        ranges.append((start, end))
    return ranges


def zero_based_pages(spec):
    """The same selection for an engine that counts pages from 0."""
    ranges = parse_pages(spec)
    if ranges is None:
        return None
    return ",".join(
        str(start - 1) if start == end else f"{start - 1}-{end - 1}"
        for start, end in ranges
    )


def one_based_pages(spec):
    """The same selection for an engine that counts pages from 1."""
    ranges = parse_pages(spec)
    if ranges is None:
        return None
    return ",".join(
        str(start) if start == end else f"{start}-{end}" for start, end in ranges
    )


class Bundle:
    """One output directory, with the manifest it carries."""

    def __init__(self, root):
        # Resolved once: every containment check below compares resolved
        # paths, and on macOS `/tmp` is a symlink to `/private/tmp`, so an
        # unresolved root makes an asset inside the bundle look outside it.
        self.root = Path(root).expanduser().resolve()

    # -- layout -------------------------------------------------------
    @property
    def raw_source(self):
        return self.root / RAW_SOURCE_NAME

    @property
    def source(self):
        return self.root / SOURCE_NAME

    @property
    def assets(self):
        return self.root / ASSETS_DIR

    @property
    def bilingual_markdown(self):
        return self.root / BILINGUAL_NAME

    @property
    def epub(self):
        return self.root / EPUB_NAME

    @property
    def manifest_path(self):
        return self.root / MANIFEST_NAME

    @property
    def work(self):
        return self.root / WORK_DIR

    def work_file(self, name):
        return self.work / name

    def create(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets.mkdir(exist_ok=True)
        self.work.mkdir(exist_ok=True)
        if not self.manifest_path.exists():
            self.write_manifest(
                {
                    "schema_version": SCHEMA_VERSION,
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "stages": {},
                    "limitations": [],
                }
            )
        return self

    # -- manifest -----------------------------------------------------
    def read_manifest(self):
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PipelineError(f"no bundle manifest at {self.manifest_path}")
        except (OSError, json.JSONDecodeError) as err:
            raise PipelineError(f"unreadable bundle manifest: {err}")
        if not isinstance(data, dict):
            raise PipelineError("bundle manifest is not an object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise PipelineError(
                f"bundle manifest schema {version!r} is not {SCHEMA_VERSION}; "
                f"use a new output directory"
            )
        return data

    def write_manifest(self, data):
        """Replace the manifest, via a temporary file in the same directory.

        A half-written manifest is a bundle that can no longer say what it
        holds, so the rename is the only thing the reader ever sees.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.manifest_path)

    def update_manifest(self, **sections):
        data = self.read_manifest()
        for key, value in sections.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
        self.write_manifest(data)
        return data

    def set_stage(self, stage, status, **fields):
        data = self.read_manifest()
        stages = data.setdefault("stages", {})
        record = stages.setdefault(stage, {})
        record["status"] = status
        record["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record.update(fields)
        self.write_manifest(data)
        return data

    def stage_status(self, stage):
        return (self.read_manifest().get("stages", {}).get(stage) or {}).get("status")

    def add_limitations(self, limitations):
        if not limitations:
            return
        data = self.read_manifest()
        recorded = data.setdefault("limitations", [])
        for line in limitations:
            if line not in recorded:
                recorded.append(line)
        self.write_manifest(data)

    def drop_limitations(self, limitations):
        """Remove exactly these lines from the manifest's limitations."""
        if not limitations:
            return
        data = self.read_manifest()
        gone = set(limitations)
        data["limitations"] = [
            line for line in data.get("limitations") or [] if line not in gone
        ]
        self.write_manifest(data)

    # -- fingerprints -------------------------------------------------
    def asset_fingerprints(self):
        """Every file under assets/, as bundle-relative path -> sha256."""
        if not self.assets.is_dir():
            return {}
        fingerprints = {}
        for path in sorted(self.assets.rglob("*")):
            if path.is_file():
                relative = path.relative_to(self.root).as_posix()
                fingerprints[relative] = sha256_file(path)
        return fingerprints
