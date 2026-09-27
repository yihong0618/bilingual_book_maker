"""Bring an existing Markdown file and its local images into a bundle.

This is the entry every extractor shares. Datalab writes the same layout
from a PDF; a hand-written chapter, an OpenDataLoader run or anyone's own
harness arrives here instead, and from `source.md` onwards nothing
downstream can tell which one it was -- imported Markdown is never described
as a Datalab job.

The raw file is kept exactly as it was read. `source.md` is the copy whose
image references were rewritten to bundle-relative paths, and it is the one
a person edits before translating.
"""

import re
import shutil
from pathlib import Path
from urllib.parse import unquote

from .bundle import contained_path, sha256_file, sha256_text
from .errors import PipelineError
from .messages import STAGE_COMPLETE
from .preflight import inspect, parse_markdown

STAGE = "import"

INLINE_IMAGE = re.compile(r"(!\[[^\]\n]*\]\()([^)\n]+)(\))")
INLINE_LINK = re.compile(r"(?<!\!)(\[[^\]\n]+\]\()([^)\n]+)(\))")


def import_markdown(
    bundle, input_path, *, pandoc, origin=None, stage=STAGE, kind="markdown"
):
    """Copy `input_path` and its local images into `bundle`.

    `stage` and `kind` exist for the extraction adapters: a Datalab run has
    already written Markdown and images to a working directory, and the
    only differences from an import are the stage its status is recorded
    under and the fact that the origin was a PDF.
    """
    source_file = Path(input_path)
    if not source_file.is_file():
        raise PipelineError(f"no Markdown file at {source_file}", stage=stage)
    raw = source_file.read_text(encoding="utf-8")
    if not raw.strip():
        raise PipelineError(f"{source_file} has no content", stage=stage)

    bundle.create()
    bundle.set_stage(stage, "running")

    try:
        rewritten, copied = _relocate_assets(raw, source_file.parent, bundle)
        bundle.raw_source.write_text(raw, encoding="utf-8")
        bundle.source.write_text(rewritten, encoding="utf-8")
        report = inspect(rewritten, root=bundle.root, pandoc=pandoc)
        report.raise_if_problems(stage)
    except PipelineError:
        # The manifest is the only record a later stage reads; a refusal
        # that left the stage "running" would let translate start on a
        # source that was never accepted.
        bundle.set_stage(stage, "failed")
        raise

    meta = _document_metadata(pandoc, rewritten, report)
    bundle.update_manifest(
        source={
            "origin": str(origin or source_file),
            "kind": kind,
            "imported_from": str(source_file),
            # The bytes the stage started from, so a rerun can tell whether
            # it is being asked to redo work it already did.
            "origin_sha256": sha256_file(origin or source_file),
            "raw_sha256": sha256_text(raw),
            "working_sha256": sha256_text(rewritten),
            "name": Path(origin or source_file).stem,
            "title": meta.get("title"),
            "author": meta.get("author"),
            "assets": bundle.asset_fingerprints(),
            "assets_copied": copied,
        }
    )
    bundle.add_limitations(report.preserved_lines())
    bundle.set_stage(stage, "completed", images=len(set(report.images)))
    for line in report.preserved_lines():
        print(line)
    print(STAGE_COMPLETE.format(stage=stage))
    return report


def _relocate_assets(text, source_dir, bundle):
    """Rewrite local image/link targets to `assets/...` and copy the files.

    Only the inline `](target)` form is handled, deliberately: reference
    style and HTML images are refused by preflight rather than half
    supported here, because the Markdown loader would hand them to the
    model as prose.
    """
    bundle.assets.mkdir(parents=True, exist_ok=True)
    mapping = {}
    copied = []

    def relocate(match, *, require_image):
        prefix, target, suffix = match.group(1), match.group(2), match.group(3)
        path, title = _split_target(target)
        if path is None:
            return match.group(0)
        if path in mapping:
            return f"{prefix}{_join_target(mapping[path], title)}{suffix}"
        local = _local_source(path, source_dir)
        if local is None:
            return match.group(0)
        if require_image and not _inside(local, source_dir):
            raise PipelineError(
                f"{path} names an image outside the Markdown file's directory; "
                f"the bundle packages only what sits beside the book",
                stage=STAGE,
            )
        if not local.is_file():
            if require_image:
                raise PipelineError(
                    f"{path} is referenced but not readable at {local}", stage=STAGE
                )
            return match.group(0)
        destination = _asset_destination(bundle, local, source_dir)
        if not destination.exists() or sha256_file(destination) != sha256_file(local):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, destination)
            copied.append(destination.relative_to(bundle.root).as_posix())
        relative = destination.relative_to(bundle.root).as_posix()
        mapping[path] = relative
        return f"{prefix}{_join_target(relative, title)}{suffix}"

    text = INLINE_IMAGE.sub(lambda m: relocate(m, require_image=True), text)
    text = INLINE_LINK.sub(lambda m: relocate(m, require_image=False), text)
    return text, copied


def _split_target(target):
    """`(path, title)` from the inside of `](...)`, or `(None, None)`."""
    value = target.strip()
    if not value:
        return None, None
    if value.startswith("<"):
        end = value.find(">")
        if end < 0:
            return None, None
        return value[1:end], value[end + 1 :].strip() or None
    parts = value.split(None, 1)
    return parts[0], (parts[1] if len(parts) > 1 else None)


def _join_target(path, title):
    rendered = f"<{path}>" if re.search(r"[\s()]", path) else path
    return f"{rendered} {title}" if title else rendered


def _inside(path, directory):
    directory = Path(directory).resolve()
    return path == directory or directory in path.parents


def _local_source(path, source_dir):
    """The file a target names, or None when it names something remote."""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", path) or path.startswith("#"):
        return None
    decoded = unquote(path.split("#", 1)[0])
    if not decoded:
        return None
    candidate = Path(decoded)
    if candidate.is_absolute():
        return candidate
    return (Path(source_dir) / candidate).resolve()


def _asset_destination(bundle, local, source_dir):
    """Where a source file lands under `assets/`.

    Its path relative to the Markdown file is kept when that path stays
    inside the source directory, so `figures/ch1/plate.png` does not
    collide with `figures/ch2/plate.png`. Anything else -- an absolute path,
    a `../` path -- is flattened to its name, with a counter if that name
    is taken by different bytes.
    """
    try:
        relative = local.relative_to(Path(source_dir).resolve())
    except ValueError:
        relative = Path(local.name)
    if relative.parts and relative.parts[0] == bundle.assets.name:
        # The source already keeps its images in an `assets/` directory;
        # nesting it inside the bundle's would give `assets/assets/...`.
        relative = (
            Path(*relative.parts[1:]) if len(relative.parts) > 1 else Path(local.name)
        )
    try:
        destination = contained_path(bundle.assets, relative, what="asset")
    except PipelineError:
        destination = bundle.assets / local.name
    if destination.exists() and sha256_file(destination) != sha256_file(local):
        stem, suffix = destination.stem, destination.suffix
        counter = 2
        while True:
            candidate = destination.with_name(f"{stem}-{counter}{suffix}")
            if not candidate.exists() or sha256_file(candidate) == sha256_file(local):
                return candidate
            counter += 1
    return destination


def _document_metadata(pandoc, text, report):
    """Title and author, from the document itself and nowhere else."""
    ast = parse_markdown(pandoc, text)
    meta = ast.get("meta") or {}
    result = {}
    for key in ("title", "author"):
        value = _meta_text(meta.get(key))
        if value:
            result[key] = value
    if "title" not in result:
        for level, heading, _ in report.headings:
            if level == 1 and heading:
                result["title"] = heading
                break
    return result


def _meta_text(node):
    if node is None:
        return None
    kind, content = node.get("t"), node.get("c")
    if kind == "MetaString":
        return str(content).strip()
    if kind in ("MetaInlines", "MetaBlocks"):
        from .preflight import plain_text

        if kind == "MetaBlocks":
            content = [i for b in content for i in (b.get("c") or [])]
        return plain_text(content)
    if kind == "MetaList" and content:
        return _meta_text(content[0])
    return None
