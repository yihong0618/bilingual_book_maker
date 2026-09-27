"""Which stage a source file needs, and whether it has already had it.

One copy of this decision, because there are now two front doors to the same
pipeline: the staged harness in `tools/pdf_to_book.py`, and `--to-epub` on
the main CLI. A second copy would be a second set of resume rules, and the
one that drifted would silently re-extract a book somebody had edited.
"""

from dataclasses import replace
from pathlib import Path

from .bundle import parse_ocr_lang, sha256_file
from .errors import PipelineError
from .importer import import_markdown
from .messages import (
    EXTRACTION_REUSED_OTHER_RUNTIME,
    OCR_REPLACE_NEEDS_OCR,
    PDF_OPTIONS_INERT,
    STAGE_COMPLETE,
    STRUCTURE_NOT_REUSED,
)
from .pdf_settings import (
    OCR_MODE_REPLACE_LAYER,
    ExtractionSettings,
    check_ocr_engine,
)

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown"}
PDF_SUFFIXES = {".pdf"}

# The one parser a PDF is read with. Spelled here rather than imported so
# that a Markdown import never touches the adapter -- importing it would
# pull in docling and torch for a file that is not going near them.
# `book_maker.pipeline.docling_parser.PARSER` is the same string, and a
# test holds them equal.
PDF_PARSER = "docling"


def source_kind(path):
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    raise PipelineError(
        f"{path} is neither a PDF nor a Markdown file; this harness reads "
        f"{', '.join(sorted(PDF_SUFFIXES | MARKDOWN_SUFFIXES))}"
    )


def check_pdf_options(kind, options):
    """The device for a PDF run, and a refusal when it cannot apply.

    A Markdown import reads no PDF, so `--pdf-ocr`, `--device`,
    `--ocr-lang` and `--pages` have nothing to act on there. Accepting one
    silently would let an operator believe a page selection, a device or a
    set of OCR languages was honoured when the file they handed in never
    went near the parser, so typing any of them with Markdown is an error
    rather than a no-op.

    `--ocr-replace-layer` replaces what the OCR engine reads, so without
    `--pdf-ocr` there is nothing to replace the layer with: refused too.
    An `--ocr-engine` other than auto is a PDF option like the others, and
    with `--pdf-ocr` an engine this install cannot run is refused here,
    before a page is read.
    """
    device = getattr(options, "device", None)
    pdf_ocr = getattr(options, "pdf_ocr", False)
    pages = getattr(options, "pages", None)
    ocr_lang = getattr(options, "ocr_lang", None)
    img_model = getattr(options, "img_model", None)
    replace_layer = getattr(options, "ocr_replace_layer", False)
    engine = getattr(options, "ocr_engine", None) or "auto"
    if kind != "pdf" and (
        device
        or pdf_ocr
        or pages
        or ocr_lang
        or img_model
        or replace_layer
        or engine != "auto"
    ):
        raise PipelineError(PDF_OPTIONS_INERT)
    if replace_layer and not pdf_ocr:
        raise PipelineError(OCR_REPLACE_NEEDS_OCR)
    if pdf_ocr:
        check_ocr_engine(engine)
    return device_for(device)


def device_for(device):
    """The device as asked for; `auto` is docling's own detection."""
    return (device or "auto").lower()


def already_prepared(bundle, input_path, parser, pages, settings=None, device=None):
    """Whether this bundle already holds this input, prepared this way.

    A second run over a finished bundle must not buy the extraction again,
    and must not overwrite a `source.md` somebody edited between the two
    runs -- editing it is the whole reason the stage is separate.

    Prepared this way: the same parser, the same pages, and the same
    extraction settings (`ExtractionSettings.identity()`: OCR on or off,
    its engine, mode and languages, the table mode, the formula pictures).
    A rerun that changes any of them is asking for different text, and
    reusing the old one would honour the flag in name only. Without OCR
    the OCR fields are inert and do not count; a manifest from before a
    key existed counts as that key's default, which is what those runs did.

    The docling version and the device are provenance, not settings: the
    extraction stands, and the operator is told once that it was made
    elsewhere.
    """
    settings = settings or ExtractionSettings()
    if not bundle.manifest_path.is_file():
        return False
    manifest = bundle.read_manifest()
    stages = manifest.get("stages") or {}
    done = [
        name
        for name in ("import", "extract")
        if (stages.get(name) or {}).get("status") == "completed"
    ]
    if not done:
        return False
    source = manifest.get("source") or {}
    if source.get("origin_sha256") != sha256_file(input_path):
        return False
    extraction = manifest.get("extraction") or {}
    if "extract" in done:
        if extraction.get("provider") != parser:
            return False
        if (extraction.get("page_range") or None) != (pages or None):
            return False
        if ExtractionSettings.from_manifest(extraction).identity() != (
            settings.identity()
        ):
            return False
        # Asked for a structure pass that never ran (the endpoint could not
        # see a page then) or did not finish: that bundle holds the
        # detector's labels, in part or whole, not what this run asks for,
        # so it is extracted again (rulings 260923). Only `complete` counts.
        if settings.structure and not _structure_complete(extraction):
            print(
                STRUCTURE_NOT_REUSED.format(
                    status=_structure_status(extraction), model=settings.structure
                )
            )
            return False
        _report_other_runtime(extraction, device_for(device))
    return done[0]


def _structure_status(extraction):
    """The recorded pass outcome; a manifest from before the key says less."""
    status = extraction.get("structure_status")
    if status:
        return status
    if extraction.get("structure_applied") is False:
        return "not_run"
    return "unrecorded"


def _structure_complete(extraction):
    from .decisions import STATUS_COMPLETE

    return _structure_status(extraction) == STATUS_COMPLETE


def _report_other_runtime(extraction, device):
    """One line when the reused extraction ran on another docling or device."""
    old_version = extraction.get("version")
    new_version = _installed_docling()
    old_requested = extraction.get("device_requested") or "auto"
    other_version = new_version is not None and old_version != new_version
    if not other_version and old_requested == device:
        return
    print(
        EXTRACTION_REUSED_OTHER_RUNTIME.format(
            old_version=old_version or "unknown",
            old_device=extraction.get("device") or old_requested,
            new_version=new_version or "unknown",
            new_device=device,
        )
    )


def _installed_docling():
    """docling's installed version, read without importing docling."""
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:
        return None


def prepare(
    bundle,
    input_path,
    *,
    pandoc,
    device=None,
    pages=None,
    ocr=False,
    ocr_lang=None,
    formula_images=True,
    progress=True,
    settings=None,
    structure=None,
    ocr_replace_layer=False,
    ocr_engine="auto",
):
    """Import or extract, chosen by the input's suffix alone.

    The extraction settings are built from `ocr`, `ocr_engine`, `ocr_lang`,
    `formula_images` and `ocr_replace_layer` unless `settings` is given,
    and the same value is what the bundle is compared against and what the
    extraction runs with.

    The adapter is imported here rather than at the top of the file so a
    Markdown import never pulls in the PDF parser, its models or its
    process management for a file it is not going to read.

    `structure` (a `docling_parser.StructureRequest`) puts its model, base and
    revision into the settings, so a bundle made by another model, or by
    none, is not reused for this one.
    """
    kind = source_kind(input_path)
    if settings is None:
        settings = ExtractionSettings(
            ocr=bool(ocr),
            ocr_engine=ocr_engine or "auto",
            ocr_mode=OCR_MODE_REPLACE_LAYER if ocr_replace_layer else "default",
            ocr_lang=tuple(parse_ocr_lang(ocr_lang) or ()),
            formula_images=bool(formula_images),
        )
    if structure is not None:
        settings = replace(
            settings,
            structure=structure.model,
            structure_rev=structure.rev,
            structure_base=getattr(structure, "base", None),
        )
    finished = already_prepared(
        bundle,
        input_path,
        PDF_PARSER if kind == "pdf" else None,
        pages,
        settings,
        device=device,
    )
    if finished:
        print(STAGE_COMPLETE.format(stage=finished))
        return None
    if kind == "markdown":
        return import_markdown(bundle, input_path, pandoc=pandoc)
    from .docling_parser import extract_pdf

    return extract_pdf(
        bundle,
        input_path,
        pandoc=pandoc,
        device=device or "auto",
        page_range=pages,
        settings=settings,
        progress=progress,
        **({"structure": structure} if structure is not None else {}),
    )
