"""docling as the PDF parser.

One process, in this interpreter: docling's layout and table models read
the page and return a document, which is exported as Markdown with the
pictures written beside it. There is no second engine, no sidecar and no
port -- the 260921 evaluation retired the Java route and its hybrid
wrapper, which was docling behind a server that degraded its output.

The device decision is docling's own (`decide_device`), not a hardware
probe of ours: `auto` resolves to whatever it finds and falls back to CPU,
`cpu` forces CPU, and a named accelerator that is not there fails by name.
We split its one refusal into two, because a CPU-only PyTorch build and a
machine with no accelerator need opposite answers from the operator.

OCR is off unless asked for. The models lay out and read tables either
way; OCR is for pages that carry no text layer at all, and those pages are
refused rather than silently skipped when it is off.
"""

import contextlib
import json
import logging
import re
import shutil
import time
from collections import deque
from pathlib import Path

from .bundle import (
    EXTRACTION_JOB,
    one_based_pages,
    parse_ocr_lang,
    parse_pages,
    sha256_file,
)
from . import pdf_figures, pdf_formula, pdf_headings, pdf_render
from .errors import PipelineError
from .importer import import_markdown
from .messages import (
    CONTROL_CHARACTERS_REMOVED,
    FORMULA_IMAGES,
    BACKEND_FAILED,
    INVISIBLE_TEXT_LAYER,
    JBIG2_MASK_RENDER,
    DEVICE_CPU_FALLBACK,
    DEVICE_NO_CUDA_BUILD,
    DEVICE_SELECTED,
    DEVICE_UNAVAILABLE,
    ENGINE_LAYOUT,
    ENGINE_OCR,
    EXTRACT_DONE,
    EXTRACT_PROGRESS_LABEL,
    EXTRACTION_EMPTY,
    OCR_ENGINE_CHOSEN,
    OCR_ENGINE_USED,
    OCR_LANG_DEFAULT,
    OCR_LANGUAGES_DEFAULT,
    OCR_LANGUAGES_GIVEN,
    OCR_REPLACE_ALL_EMPTY,
    OCR_REPLACE_EMPTY,
    OCR_REPLACE_EMPTY_MORE,
    OCR_REPLACING_LAYER,
    PAGE_MAP_UNPLACED,
    OCR_REQUIRED,
    PAGE_TOO_DENSE,
    PAGES_SCOPE,
    PAGES_SPAN_CONVERTED,
    PAGE_SCOPE,
    PDF_ROUTE_NOT_INSTALLED,
    SCANNED_PAGES,
    SELECTION_HEADING_ADDED,
    STRUCTURE_APPLIED,
    STRUCTURE_DETAIL_BUDGET,
    STRUCTURE_DETAIL_DEADLINE,
    STRUCTURE_DETAIL_FAILED,
    STRUCTURE_DETAIL_REJECTED,
    STRUCTURE_DETAIL_UNANSWERED,
    STRUCTURE_FAILED,
    STRUCTURE_HIGH_CHANGE,
    STRUCTURE_PARTIAL,
    TITLE_HEADING_ADDED,
)
from .pdf_common import (
    PAGE_MARKER,
    UNPLACED_MARKER,
    numbered_pages,
    _prose,
    blank_pages,
    check_recognised_text,
    dense_pages,
    first_selected_page,
    heading_for_top,
    strip_control_characters,
    text_layer_report,
)
from .pdf_settings import ExtractionSettings
from .progress import ProgressLine, ticking

STAGE = "extract"
PARSER = "docling"

DEVICES = ("auto", "cpu", "cuda", "mps", "xpu")

# What the export writes between two pages. docling emits this placeholder
# between pages and never before the first, so the numbered markers the
# rest of the pipeline reads are put in afterwards, by `_number_pages`.
PAGE_BREAK = "\x00bbm-page-break\x00"

# The line after the last page under which items with no page are written
# (`_export_pages`); not a page marker, so no page claims them.

# How many of the parser's log lines are kept for a failure message.
LOG_TAIL_LINES = 20

IMAGE_DIR = "images"

# docling's own record of the conversion, written before anything of ours
# touches the document: evidence for diagnosis and evaluation, read back by
# nothing in the pipeline.
SNAPSHOT = "docling.json"

# docling names the engine its auto OCR settled on in one log line
# ("Auto OCR model selected rapidocr with onnxruntime."); the word after
# the prefix is the engine.
AUTO_OCR_SELECTED = re.compile(r"Auto OCR model selected (\w+)")
IMAGE_REF = re.compile(r"(!\[[^\]]*\]\()([^)]*)(\))")

# Which renderer painted the page images the models read, as the manifest
# records it (`extraction.render_backend`).
RENDER_DOCLING_PARSE = "docling-parse"
RENDER_PDFIUM_PAGE_IMAGE = "pypdfium2-page-image"


def resolve_device(requested):
    """`(resolved, message)` for the device the models will actually use.

    Delegated to docling, which is the thing that will run them: `auto` is
    its detection, an unavailable accelerator is its refusal, and `cpu` is
    the one answer that needs no hardware at all.
    """
    requested = (requested or "auto").lower()
    if requested not in DEVICES:
        raise PipelineError(
            f"{requested!r} is not a device; choose one of {', '.join(DEVICES)}",
            stage=STAGE,
        )
    try:
        from docling.utils.accelerator_utils import (
            AcceleratorDeviceNotAvailableError,
            decide_device,
        )
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)
    try:
        resolved = decide_device(requested)
    except AcceleratorDeviceNotAvailableError:
        raise PipelineError(_unavailable(requested), stage=STAGE)
    # docling answers `cuda:0`; everything downstream takes the family.
    resolved = resolved.split(":", 1)[0]
    if requested == "auto" and resolved == "cpu":
        return resolved, DEVICE_CPU_FALLBACK
    return resolved, DEVICE_SELECTED.format(device=resolved)


def _unavailable(requested):
    """Which of the two CUDA refusals this is.

    docling raises the same error for a CPU-only PyTorch build and for a
    machine with no NVIDIA card, but the first is fixed by reinstalling
    and the second never is, so the operator is told which one happened.
    """
    if requested == "cuda":
        try:
            import torch

            if torch.version.cuda is None:
                return DEVICE_NO_CUDA_BUILD
        except Exception:
            pass
    return DEVICE_UNAVAILABLE.format(device=requested)


def _converter(device, settings, pdfium_page_images=False):
    """docling's `DocumentConverter`, configured for one conversion.

    Every field is set from `settings`, including the ones that match
    docling's defaults today: what the pipeline asked for is then written
    down here rather than inherited from whatever a docling release ships.

    `pdfium_page_images` keeps docling-parse for the text and takes the
    page images from pypdfium2 (`pdf_render`), for a PDF docling-parse
    renders wrongly. It is decided from the file, not asked for, so it is
    not part of `settings`.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel import pipeline_options as po
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)

    options = po.PdfPipelineOptions()
    # Layout and table structure run on every page: they are what this
    # parser is for.
    options.do_table_structure = True
    if settings.table_mode == "v2":
        options.table_structure_options = po.TableStructureV2Options()
    else:
        options.table_structure_options = po.TableStructureOptions(
            mode=po.TableFormerMode(settings.table_mode)
        )
    # Pictures are written as files and described by nobody: generated alt
    # text has been measured inventing content, and a translation would
    # then carry the invention.
    options.generate_picture_images = True
    # Formulas are cropped as pictures instead of decoded: the owner's
    # decision (260922, docs/260922-feat-PDF_FORMULA_IMAGES.md); the 260921
    # measurement behind it is not trusted and a fresh paired evaluation is
    # running. Code enrichment is off for the same reason: generated text is
    # not source text.
    options.do_formula_enrichment = False
    options.do_code_enrichment = False
    options.do_ocr = settings.ocr
    if settings.ocr:
        engine = {
            "auto": po.OcrAutoOptions,
            "rapidocr": po.RapidOcrOptions,
            "easyocr": po.EasyOcrOptions,
            "ocrmac": po.OcrMacOptions,
            "tesseract": po.TesseractCliOcrOptions,
        }[settings.ocr_engine]
        fields = {"mode": po.OcrMode(settings.ocr_mode)}
        if settings.ocr_lang:
            fields["lang"] = list(settings.ocr_lang)
        options.ocr_options = engine(**fields)
    options.accelerator_options.device = device
    format_option = (
        PdfFormatOption(
            pipeline_options=options, backend=pdf_render.pdfium_image_backend()
        )
        if pdfium_page_images
        else PdfFormatOption(pipeline_options=options)
    )
    return DocumentConverter(format_options={InputFormat.PDF: format_option})


def _convert(
    pdf,
    *,
    out_dir,
    span,
    device,
    settings,
    formulas=True,
    report=None,
    structure=None,
):
    """Markdown for `span` of `pdf`, with its pictures written to `out_dir`.

    The default seam: `extract_pdf(convert=...)` replaces this whole call,
    so a test never loads a model. It does not replace `resolve_device`,
    which runs first either way and needs docling importable -- a test on
    a base install has to stub that too.

    Returns `(markdown, formula count, warnings)`. A stub may return the
    Markdown alone; `extract_pdf` accepts either. `report` is the caller's
    dict for what the conversion found out: the path of docling's own
    snapshot of the document, under `snapshot`, which renderer painted the
    page images, under `render`, and with `structure` (a `StructureRequest`)
    what the region-role pass did, under `structure`.
    """
    report = {} if report is None else report
    # docling-parse paints a JBIG2-masked image unmasked (docling issue
    # #4329), and the models then read a smear; such a file's page images
    # come from pypdfium2 instead. Decided per document, from the bytes.
    jbig2 = pdf_render.has_jbig2_mask(pdf)
    report["render"] = RENDER_PDFIUM_PAGE_IMAGE if jbig2 else RENDER_DOCLING_PARSE
    converter = _converter(device, settings, pdfium_page_images=jbig2)
    # No selection is docling's own default (every page): its `convert` is
    # validated strictly and refuses `page_range=None` (docling 2.129).
    result = converter.convert(
        str(pdf), **({"page_range": span} if span is not None else {})
    )
    # docling's document as it came back, before the formula markers and
    # the heading levels below change it.
    snapshot = Path(out_dir) / SNAPSHOT
    _write_snapshot(result.document, snapshot)
    report["snapshot"] = snapshot
    return _document_markdown(
        result.document,
        pdf,
        out_dir=out_dir,
        span=span,
        formulas=formulas,
        report=report,
        structure=structure,
    )


def _document_markdown(
    document, pdf, *, out_dir, span, formulas=True, report=None, structure=None
):
    """Markdown for a converted `document`, pages joined by `PAGE_BREAK`.

    Everything `_convert` does after docling has read the pages, apart so
    that a test can hand it a real `DoclingDocument` without loading a
    model. Same return and `report` as `_convert`.
    """
    report = {} if report is None else report
    # The region roles, after the snapshot (which stays docling's own) and
    # before anything reads a label: the formula markers and the heading
    # levels below must see the corrected items, and a heading the model
    # made gets its level from `pdf_headings` like any other.
    if structure is not None:
        try:
            report["structure"] = _decide_structure(document, pdf, out_dir, structure)
        except BaseException:
            # The extraction fails with it; the manifest says which part.
            from .decisions import STATUS_FAILED

            report["structure_status"] = STATUS_FAILED
            raise
    # Which pages the engine read any text on, from the document's own
    # text, before the formula markers below give empty items text: the
    # Markdown cannot say, because an empty table still exports its pipes
    # (Codex re-verify 260924).
    report["text_pages"] = _text_pages(document)
    # Before the export: each undecoded formula is given a marker as its
    # text, so the serializer writes the marker where the equation stands
    # and the picture can only land at its own item.
    regions = pdf_formula.mark(document) if formulas else []
    # And the headings' levels, which docling does not give: numbering and
    # the glyphs under each heading decide them before the export.
    pdf_headings.assign(document, pdf)
    images = Path(out_dir) / IMAGE_DIR
    exported, report["unplaced"], figures = _export_pages(document, images, span)
    # Each named figure is shown at its share of the page's width, so the
    # pixels the render step draws decide sharpness, never size.
    report["figures"] = figures
    exported = pdf_figures.add_widths(exported, figures)
    markdown = pdf_headings.promote(_relative_images(exported, images))
    if not formulas:
        return markdown, 0, []
    return pdf_formula.apply(
        markdown,
        regions,
        pdf,
        out_dir,
        neighbours=pdf_formula.neighbours(document),
    )


def _export_pages(document, images, span):
    """`(Markdown, unplaced item count, figure records)`: one segment per
    page, joined by `PAGE_BREAK`.

    Page by page, from each item's own page (docling's `page_no` filter,
    which reads `prov[0].page_no`, so an item whose provenance runs over
    two pages is written once, under the first), because docling's own
    page breaks are written only between items: a page with no item at
    all gets none, and every page after it was numbered one short (Codex
    review 260924). Here every page of the converted run has a segment,
    empty or not, so `_number_pages` counts right and `_selected_only`
    keeps the right pages. The document is not changed, so the formula
    markers and the heading levels are exactly as they were.

    An item with no page (none from a PDF conversion so far) would be
    dropped by the page filter; such items are written once, after the
    last page, under `UNPLACED_MARKER`, and counted for the operator
    (Codex re-verify 260924: falling back to docling's breaks for them
    brought the misnumbering back). Only a document with no pages at all
    keeps docling's own breaks.

    The pictures are written once, by docling-core's `_with_pictures_refs`
    (the step `export_to_markdown(image_dir=...)` runs, private): calling
    the public export per page would deep-copy the whole document once per
    page. Pinned in tests/test_pdf_ocr_replace_layer.py.

    Each picture written then gets its stable name (`pdf_figures.
    name_pictures`, on the copy, by the item's own reference), so the
    Markdown names `figures/p0003-01.png` and not docling's pixels; the
    records say where each one stands, for the render step.
    """
    from docling_core.types.doc import BoundingBox, DocItem, ProvenanceItem
    from docling_core.types.doc.base import ImageRefMode

    numbers = sorted(int(number) for number in (document.pages or {}))
    if not numbers:
        # Nothing to number by: docling's own breaks, as before.
        markdown = document.export_to_markdown(
            page_break_placeholder=PAGE_BREAK,
            image_mode=ImageRefMode.REFERENCED,
            image_dir=images,
        )
        return markdown, 0, []
    first = span[0] if span else 1
    last = min(span[1], numbers[-1]) if span else numbers[-1]
    # A copy with the pictures written out; ours to change.
    referenced = document._with_pictures_refs(image_dir=Path(images), page_no=None)
    figures = pdf_figures.name_pictures(referenced, Path(images).parent)
    # On a line of its own, as docling writes its own breaks: a page that
    # opens with a heading must still start its line with `#` for
    # `pdf_headings.promote`, which runs before the pages are numbered.
    markdown = f"\n\n{PAGE_BREAK}\n\n".join(
        referenced.export_to_markdown(
            image_mode=ImageRefMode.REFERENCED, page_no=number
        )
        for number in range(first, last + 1)
    )
    unplaced = [
        item
        for item, _level in referenced.iterate_items()
        if isinstance(item, DocItem) and not item.prov
    ]
    if unplaced:
        # Given a page that is not in the document, on the copy, so the
        # same serializer writes them, in reading order, in one segment.
        beyond = max(numbers[-1], last) + 1
        nowhere = BoundingBox(l=0, t=0, r=0, b=0)
        for item in unplaced:
            item.prov = [ProvenanceItem(page_no=beyond, bbox=nowhere, charspan=(0, 0))]
        tail = referenced.export_to_markdown(
            image_mode=ImageRefMode.REFERENCED, page_no=beyond
        )
        markdown = f"{markdown}\n\n{UNPLACED_MARKER}\n\n{tail}"
    return markdown, len(unplaced), figures


def _text_pages(document):
    """The pages on which the document holds any text, from 1.

    A text item with non-blank text, or a table with a cell that has one;
    every page an item's provenance names counts, so a paragraph that runs
    onto the next page counts as read there too. Pictures are not text.
    """
    from docling_core.types.doc import TableItem, TextItem

    pages = set()
    for item, _level in document.iterate_items():
        if not getattr(item, "prov", None):
            continue
        if isinstance(item, TextItem):
            has_text = bool((item.text or "").strip())
        elif isinstance(item, TableItem):
            has_text = any((cell.text or "").strip() for cell in item.data.table_cells)
        else:
            continue
        if has_text:
            pages.update(prov.page_no for prov in item.prov)
    return sorted(pages)


def _selected_pages(ranges, examined):
    """The page numbers `text_layer_report` examined, from 1.

    It walks the document in page order and counts the pages inside the
    selection, so they are the first `examined` pages of the selection in
    ascending order (a selection reaching past the end counts short).
    """
    if not ranges:
        return list(range(1, examined + 1))
    wanted = sorted({page for start, end in ranges for page in range(start, end + 1)})
    return wanted[:examined]


def _record_failed_extraction(bundle, settings, page_range, *, reason, empty):
    """The manifest's `extraction` block for an extraction that stopped.

    Replaces the block rather than merging into it: a block left from an
    earlier, completed extraction would otherwise still describe a text
    this bundle no longer holds (Codex review 260924). It keeps what was
    asked for, why it stopped and which pages came back empty; the same
    lines go to the limitations, and are listed as this extraction's own
    so the next one takes them back.
    """
    limitations = [OCR_REPLACE_EMPTY.format(page=page) for page in empty]
    limitations.append(reason)
    data = bundle.read_manifest()
    data["extraction"] = {
        "status": "failed",
        "failure": reason,
        "parser": PARSER,
        "ocr": settings.ocr,
        "ocr_engine_requested": settings.ocr_engine,
        "ocr_mode": settings.ocr_mode,
        "ocr_replace_layer": settings.ocr_replace_layer,
        "ocr_lang": list(settings.ocr_lang) or None,
        "table_mode": settings.table_mode,
        "formula_images": settings.formula_images,
        "page_range": page_range,
        "empty_pages": list(empty),
        "limitations": limitations,
    }
    bundle.write_manifest(data)
    bundle.add_limitations(limitations)


def _pages_read(text_pages, text):
    """The pages the engine read any text on, as a set.

    `text_pages` is the document's own answer (`_document_markdown`); a
    converter seam that does not give it is answered from the numbered
    Markdown's page markers.
    """
    if text_pages is not None:
        return set(text_pages)
    blank, _any = blank_pages(text)
    return {int(n) for n in PAGE_MARKER.findall(numbered_pages(text))} - set(blank)


def _replaced_layer_lines(empty):
    """One OCR_REPLACE_EMPTY line per page, the first ten, then a count."""
    lines = [OCR_REPLACE_EMPTY.format(page=page) for page in empty[:10]]
    if len(empty) > 10:
        lines.append(OCR_REPLACE_EMPTY_MORE.format(count=len(empty) - 10))
    return lines


def _decide_structure(document, pdf, out_dir, structure):
    """Run the region-role pass on `document` in place; return its summary.

    The overlay is written before the changes are applied and again after,
    so a failure in between still leaves the answers on disk.
    """
    from . import decisions

    pages = sorted(int(number) for number in (document.pages or {}))
    wanted = getattr(structure, "pages", None)
    if wanted:
        pages = [page for page in pages if page in wanted]
    overlay = decisions.decide_roles(
        structure.ask,
        document,
        pdf,
        pages,
        budget=decisions.Budget.for_pages(len(pages)),
        model=structure.model,
    )
    path = Path(out_dir) / decisions.OVERLAY_FILE
    overlay.write(path)
    applied = decisions.apply(document, overlay)
    overlay.write(path)

    def numbers(test):
        return [page for page, entry in sorted(overlay.pages.items()) if test(entry)]

    return {
        **overlay.totals,
        "status": overlay.status(),
        "applied": applied.count,
        "transitions": dict(applied.transitions),
        "high_change": [
            {"page": page, "changed": entry["changed"], "asked": entry["asked"]}
            for page, entry in sorted(overlay.pages.items())
            if entry.get("high_change")
        ],
        "unasked_page_numbers": numbers(lambda e: e.get("status") == "unasked"),
        "failed_page_numbers": numbers(lambda e: e.get("status") == "failed_apply"),
        "overlay": path,
    }


class StructureRequest:
    """The region-role pass (`--img-model`) as the extraction sees it.

    `ask(prompt, schema, image_png, deadline=None)` is the model call (the
    parsed answer, carrying `model` and `usage`; a question still retried
    at `deadline` ends as `decisions.AskFailed`); `vision()` is the endpoint's verdict on
    image input ('verified', 'unsupported', 'deferred'), asked only when an
    extraction is about to run, so a reused bundle pays for no probe.
    `rev` is the prompt/policy revision the answers are asked under; `base`
    the address the model is asked at (identity, with the model); `source`
    where the choice came from (cli, provider); `translator` the instance
    whose meter the pass's requests are counted on.
    """

    def __init__(
        self, model, ask, vision, rev, *, base=None, source=None, translator=None
    ):
        self.model = model
        self.ask = ask
        self.vision = vision
        self.rev = rev
        self.base = base
        self.source = source
        self.translator = translator
        self.pages = None
        self.verdict = None

    def where(self):
        return self.base or "the run's endpoint"


class _Answer(dict):
    """A structured answer, with the call's model and usage beside it."""

    model = None
    usage = None


def _soft_failures():
    """The model call's failures that are about one request, not the run."""
    from book_maker.classifier import NoBackend
    from book_maker.structured import StructuredJSONFailed

    soft = [StructuredJSONFailed, NoBackend]
    try:
        from book_maker.translator.vision import QuestionTimedOut, VisionRequestFailed

        soft.extend((VisionRequestFailed, QuestionTimedOut))
    except ImportError:
        pass
    return tuple(soft)


def _candidates(schema):
    """`{id: allowed answers}` from the pass's own per-id enum schema."""
    properties = ((schema or {}).get("schema") or {}).get("properties") or {}
    return {key: tuple(entry.get("enum") or ()) for key, entry in properties.items()}


def _structure_ask(choice, options=None, *, translator=None):
    """The `StructureRequest` for the image endpoint `choice`.

    `choice` is `book_maker.endpoints.resolve_image_endpoint`'s answer: the
    model, the address and key it is asked at, where it came from. The
    translator is built for it from the translation options (endpoint,
    key, prices, `--no-thinking`; extras on the run's own address) unless
    one is given, and every question goes through a `Classifier` over it:
    the pass's questions are image questions, answered by the structured
    channel once the endpoint has read the probe image.
    """
    from book_maker.classifier import Classifier, Question
    from book_maker.endpoints import build_translator

    from . import decisions

    if translator is None:
        translator = build_translator(choice, options, "English")
    classifier = Classifier(
        translator,
        choice.model,
        prefer=("schema",),
        source=choice.source,
        base=choice.api_base or None,
        separate=True,
    )
    schema_backend = classifier.backend("schema")
    soft = _soft_failures()

    def ask(prompt, schema, image_png, deadline=None):
        from book_maker.loader.helper import _is_retryable
        from book_maker.redaction import redact

        question = Question(
            prompt=prompt,
            schema=schema,
            candidates=_candidates(schema),
            image_png=image_png,
            abstain=decisions.ABSTAIN,
            deadline=deadline,
        )
        try:
            answer = classifier.ask(question)
        except soft as err:
            raise decisions.AskFailed(f"{type(err).__name__}: {err}")
        except Exception as err:
            # Weather the translator waited out until the pass's deadline:
            # this question is lost, the run is not. A fatal error (auth,
            # a 400) was never retried and still ends the run.
            if (
                deadline is not None
                and time.monotonic() >= deadline
                and _is_retryable(err)
            ):
                raise decisions.AskFailed(
                    STRUCTURE_DETAIL_DEADLINE.format(
                        error=f"{type(err).__name__}: {redact(err)}"
                    )
                ) from err
            raise
        # The pass lints the reply itself (`decisions.parse_reply`: unknown
        # ids are protocol violations, out-of-set values invalid), so it is
        # handed the reply as it came, not the classifier's filtered values.
        if not isinstance(answer.raw, dict):
            raise decisions.ReplyRejected("the reply is not a JSON object")
        reply = _Answer(answer.raw)
        reply.model = choice.model
        if answer.usage is not None:
            reply.usage = {
                name: answer.usage.get(name)
                for name in ("prompt_tokens", "completion_tokens")
            }
        return reply

    def vision():
        if schema_backend is None:
            return "unsupported"
        return schema_backend.vision()

    return StructureRequest(
        choice.model,
        ask,
        vision,
        f"{decisions.PROMPT_REV}/{decisions.POLICY_REV}",
        base=choice.api_base or None,
        source=choice.source,
        translator=translator,
    )


def image_request(image_options, translation_options):
    """The route's `StructureRequest`, or None when image steps are off.

    `image_options` carries `img_model`, `img_base_url`, `img_key`;
    `translation_options` is the translation command line as the CLI
    parses it (the run's endpoint, key and `--provider` entry), and is not
    changed. Resolved before a page is read: a model at an endpoint of
    another format, or one with no key, is refused here, in the words the
    operator needs. Nothing of the pass is built without a choice.
    """
    import argparse

    from book_maker import cli
    from book_maker.endpoints import resolve_image_endpoint, run_choice

    options = argparse.Namespace(**vars(translation_options))
    try:
        names, api_format, env_keys = cli.resolve_endpoint(options)
        provider = getattr(options, "provider_route", None)
        run = run_choice(names[0] if names else "", options.api_base, None, api_format)
        if resolve_image_endpoint(image_options, run, provider, with_key=False) is None:
            return None
        key = cli.resolve_api_key(api_format, options.key, options.api_base, env_keys)
        run = run_choice(names[0] if names else "", options.api_base, key, api_format)
        choice = resolve_image_endpoint(image_options, run, provider)
    except SystemExit as err:
        raise PipelineError(str(err), stage=STAGE)
    return _structure_ask(choice, options)


def _structure_lines(model, summary, bundle):
    """`(applied line, partial line or None, high-change lines)`.

    All of them go to the terminal; the partial and high-change lines are
    also the manifest's limitations.
    """
    overlay = Path(summary["overlay"])
    try:
        shown = overlay.resolve().relative_to(bundle.root.resolve()).as_posix()
    except ValueError:
        shown = str(overlay)
    applied = STRUCTURE_APPLIED.format(
        accepted=summary["applied"],
        asked=summary["asked"],
        model=model,
        kept=summary["kept"],
        rejected=summary["rejected"],
        high_change=summary.get("high_change_pages", 0),
        path=shown,
    )
    detail = []

    def pages(numbers):
        return ", ".join(str(n) for n in numbers)

    if summary["unasked_page_numbers"]:
        detail.append(
            STRUCTURE_DETAIL_BUDGET.format(
                bound=summary.get("budget_exhausted") or "call",
                pages=pages(summary["unasked_page_numbers"]),
            )
        )
    if summary["failed_page_numbers"]:
        detail.append(
            STRUCTURE_DETAIL_FAILED.format(pages=pages(summary["failed_page_numbers"]))
        )
    if summary["rejected"]:
        detail.append(STRUCTURE_DETAIL_REJECTED.format(count=summary["rejected"]))
    if summary["unanswered"]:
        detail.append(STRUCTURE_DETAIL_UNANSWERED.format(count=summary["unanswered"]))
    partial = STRUCTURE_PARTIAL.format(detail="; ".join(detail)) if detail else None
    high = [STRUCTURE_HIGH_CHANGE.format(**page) for page in summary["high_change"]]
    return applied, partial, high


# Every collection whose items can carry a picture (`FloatingItem.image`),
# and the page images. Their `uri` is the picture itself as a base64 data
# URI; the snapshot keeps the picture's size, dpi and mimetype and drops
# the bytes.
_PICTURE_BYTES = {
    name: {"__all__": {"image": {"uri"}}}
    for name in (
        "pictures",
        "tables",
        "key_value_items",
        "form_items",
        "field_regions",
        "field_items",
        "pages",
        # A code item is a FloatingItem too; this pipeline gives it no
        # image, but the exclusion costs nothing and outlives that.
        "texts",
    )
}


def _write_snapshot(document, path):
    """docling's document as JSON, without the pictures' bytes.

    `export_to_dict()` (and `save_as_json` in PLACEHOLDER mode, which is
    the same call on the same object, measured 260923) writes every
    picture as a base64 data URI, and building that for a long illustrated
    book costs memory the conversion already needed. The dump excludes
    the bytes instead -- `model_dump` does not touch the document, so the
    Markdown export afterwards still writes the picture files -- and is
    streamed to the file rather than built as one string. The pictures'
    `uri` is absent, so the file does not load back as a DoclingDocument
    as it stands; nothing in the pipeline reads it.
    """
    data = document.model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude=_PICTURE_BYTES
    )
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)


def _relative_images(markdown, image_dir):
    """Picture references as paths beside `source.md`, not absolute ones.

    docling writes the directory it was given into every reference. A
    bundle has to survive being moved, and Pandoc resolves the reference
    against the Markdown file, so the directory is taken back off.
    """
    prefix = str(Path(image_dir)) + "/"

    def rewrite(match):
        target = match.group(2)
        if target.startswith(prefix):
            target = f"{IMAGE_DIR}/{target[len(prefix):]}"
        return f"{match.group(1)}{target}{match.group(3)}"

    return IMAGE_REF.sub(rewrite, markdown)


def _span(ranges):
    """The one run of pages a converter can be asked for, `(first, last)`."""
    if not ranges:
        return None
    return min(start for start, _ in ranges), max(end for _, end in ranges)


def _number_pages(markdown, first_page):
    """The export's page breaks as the numbered markers the pipeline reads.

    docling puts a break between two pages and never before the first, so
    the first page's marker is prepended and each break becomes the marker
    of the page that follows it.
    """
    number = first_page or 1
    out = [f"<!-- page {number} -->", ""]
    for segment in markdown.split(PAGE_BREAK):
        if out[-1] != "":
            out.append("")
        out.append(segment.strip("\n"))
        number += 1
        out.extend(["", f"<!-- page {number} -->", ""])
    # The loop leaves a marker for a page that does not exist.
    del out[-3:]
    return "\n".join(out).rstrip("\n") + "\n"


def _selected_only(markdown, ranges):
    """The Markdown with the pages outside a gapped selection removed."""
    parts = PAGE_MARKER.split(markdown)
    kept = [parts[0].strip("\n")] if parts[0].strip() else []
    for number, body in zip(parts[1::2], parts[2::2]):
        page = int(number)
        if any(start <= page <= end for start, end in ranges):
            kept.append(f"<!-- page {page} -->\n{body.strip(chr(10))}")
    return "\n\n".join(kept).rstrip("\n") + "\n"


def extract_pdf(
    bundle,
    pdf_path,
    *,
    pandoc,
    device="auto",
    page_range=None,
    ocr=False,
    ocr_lang=None,
    formula_images=True,
    settings=None,
    convert=None,
    progress=True,
    structure=None,
):
    """Convert one PDF to Markdown in `bundle`, locally.

    docling's models read it. With `ocr` the pages that have no text layer
    are read by the OCR models as well; without it such a page is refused
    rather than skipped, because a page nobody can read is not a page that
    was translated.

    `settings` (an `ExtractionSettings`) replaces `ocr`, `ocr_lang` and
    `formula_images` when given; the stage passes the one it compared the
    bundle against, so what runs is what was checked.

    `structure` (a `StructureRequest`, from `--img-model`) runs the
    region-role pass inside the conversion when the endpoint can see a
    page image; when it cannot, the extraction goes on with the
    detector's labels and says so.
    """
    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise PipelineError(f"no PDF at {pdf}", stage=STAGE)
    converter = convert if convert is not None else _convert
    ranges = parse_pages(page_range)
    pages = one_based_pages(page_range)
    if settings is None:
        settings = ExtractionSettings(
            ocr=bool(ocr),
            ocr_lang=tuple(parse_ocr_lang(ocr_lang) or ()),
            formula_images=bool(formula_images),
        )
    ocr = settings.ocr
    languages = list(settings.ocr_lang) or None

    # Before the bundle is created, because it can refuse: an unusable
    # device, or no parser installed at all, must not leave a half-made
    # bundle directory behind for the next run to puzzle over.
    resolved, message = resolve_device(device)
    bundle.create()
    print(message)
    # A new extraction replaces the last one, and so do the limitations it
    # recorded: a line about the old languages or headings would otherwise
    # outlive the text it described. Only the lines the extraction listed
    # as its own go; what other stages recorded stays.
    previous = (bundle.read_manifest().get("extraction") or {}).get("limitations")
    bundle.drop_limitations(previous or [])
    # And the figures drawn from the last one: their files are about to be
    # replaced, so their record goes with them.
    pdf_figures.forget(bundle)

    bundle.set_stage(STAGE, "running", parser=PARSER, device=resolved)
    staging = bundle.work_file("extraction")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        # Asked before the models are started, because it decides how they
        # are used: a page the PDF cannot spell out has to be read by the
        # OCR models -- and without them, refused rather than skipped.
        report = text_layer_report(pdf, page_range)
        missing, examined = report
        # Pages whose only text is an invisible layer under a scan: usable
        # text, so not refused, but the operator is told, because with
        # OCR on the models read the picture and replace that layer.
        invisible = list(getattr(report, "invisible", ()))
        if missing and not ocr:
            raise PipelineError(
                OCR_REQUIRED.format(
                    count=len(missing),
                    total=examined,
                    pages=", ".join(str(n) for n in missing),
                ),
                stage=STAGE,
            )
    except (PipelineError, KeyboardInterrupt):
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise

    # Whether the structure model can see a page, asked before the models
    # start: an endpoint that cannot is told about once, up front, and the
    # extraction goes on without the pass rather than failing after it.
    active = None
    structure_note = None
    if structure is not None:
        try:
            verdict = structure.vision()
            structure.verdict = verdict
        except (PipelineError, KeyboardInterrupt):
            bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
            raise
        except Exception as err:
            bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
            raise PipelineError(
                STRUCTURE_FAILED.format(
                    model=structure.model, detail=f"{type(err).__name__}: {err}"
                ),
                stage=STAGE,
            )
        if verdict == "verified":
            active = structure
            if len(ranges or ()) > 1:
                active.pages = {
                    page for start, end in ranges for page in range(start, end + 1)
                }
        else:
            from book_maker.endpoints import IMG_ENDPOINT_UNVERIFIED

            structure_note = IMG_ENDPOINT_UNVERIFIED.format(
                model=structure.model, base=structure.where(), verdict=verdict
            )
            print(structure_note)

    if missing:
        print(SCANNED_PAGES.format(count=len(missing), total=examined))
    invisible_note = None
    if invisible:
        invisible_note = INVISIBLE_TEXT_LAYER.format(
            count=len(invisible), total=examined
        )
        print(invisible_note)
    # Replacing the embedded layer is the operator's explicit choice
    # (--ocr-replace-layer), said once before the models start.
    replace_layer = settings.ocr_replace_layer
    if replace_layer:
        print(OCR_REPLACING_LAYER)
    # The models are about to read these pages in whatever languages they
    # were given; an operator who gave none is told which. An invisible
    # layer is read again too when OCR is on (measured 260923: the Chinese
    # scan read in the engine's default languages lost half its text), and
    # every page is when the layer is replaced (ocrmac's default on
    # Chinese: Han CER 0.291 -> 0.494).
    if ocr and (missing or invisible or replace_layer) and not languages:
        print(OCR_LANG_DEFAULT)

    # A converter reads one run of pages. A selection with a gap in it is
    # read as the run that covers it and trimmed afterwards, so the flag
    # keeps working -- at the cost of reading the pages in the gap, which
    # the operator is told about rather than charged for silently.
    span = _span(ranges)
    gapped = bool(ranges) and len(ranges) > 1
    if gapped:
        print(PAGES_SPAN_CONVERTED.format(span=f"{span[0]}-{span[1]}"))

    scope = (PAGE_SCOPE if examined == 1 else PAGES_SCOPE).format(count=examined)
    engine = (ENGINE_OCR if ocr else ENGINE_LAYOUT).format(device=resolved)
    line = ProgressLine(
        EXTRACT_PROGRESS_LABEL.format(scope=scope, engine=engine),
        enabled=progress,
    )
    # The parser's last words, for the failure message: a conversion that
    # dies says what it said before it died.
    transcript = deque(maxlen=LOG_TAIL_LINES)
    # What the conversion found out about itself: the snapshot's path from
    # the converter, the engine docling chose from its log.
    found = {}

    def note(text):
        for raw in (text or "").splitlines():
            entry = raw.strip()
            if entry:
                transcript.append(entry)
                line.note(entry)
                chosen = AUTO_OCR_SELECTED.search(entry)
                if chosen and "ocr_engine" not in found:
                    found["ocr_engine"] = chosen.group(1)

    finished = False
    # Started before the models are loaded: on a first run they are
    # downloaded too, and the operator is owed the same sign of life
    # there as during the conversion itself.
    line.start()
    try:
        with ticking(line):
            try:
                # Only a pass that will run is handed on: a converter
                # without the pass is called exactly as before.
                extra = {"structure": active} if active is not None else {}
                with contextlib.redirect_stdout(_Sink(note)), _docling_log(note):
                    produced = converter(
                        pdf,
                        out_dir=staging,
                        span=span,
                        device=resolved,
                        settings=settings,
                        formulas=settings.formula_images,
                        report=found,
                        **extra,
                    )
                # A stub seam returns the Markdown alone; the real
                # converter also reports what it did with the formulas.
                if isinstance(produced, str):
                    markdown, formulas, formula_warnings = produced, 0, []
                else:
                    markdown, formulas, formula_warnings = produced
                finished = True
            except PipelineError:
                raise
            except Exception as err:
                raise PipelineError(
                    BACKEND_FAILED.format(detail=_failure_detail(err, transcript)),
                    stage=STAGE,
                )
    except (PipelineError, KeyboardInterrupt):
        if found.get("structure_status") is not None:
            bundle.update_manifest(
                extraction={"structure_status": found["structure_status"]}
            )
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise
    finally:
        line.finish(
            EXTRACT_DONE.format(scope=scope, engine=engine, elapsed=int(line.elapsed()))
            if finished
            else None
        )

    # The engine that read the pages: docling's own choice under `auto`,
    # which it names in its log, else the one asked for. Said whenever OCR
    # was on, because the engine decides which languages were readable.
    ocr_engine = None
    engine_line = None
    if ocr:
        ocr_engine = (
            found.get("ocr_engine")
            if settings.ocr_engine == "auto"
            else settings.ocr_engine
        )
        engine_line = OCR_ENGINE_USED.format(
            engine=ocr_engine or settings.ocr_engine,
            chosen=OCR_ENGINE_CHOSEN if settings.ocr_engine == "auto" else "",
            languages=(
                OCR_LANGUAGES_GIVEN.format(languages=",".join(languages))
                if languages
                else OCR_LANGUAGES_DEFAULT
            ),
        )
        print(engine_line)
    snapshot = found.get("snapshot")
    render = found.get("render")
    if render == RENDER_PDFIUM_PAGE_IMAGE:
        print(JBIG2_MASK_RENDER)
    unplaced_note = (
        PAGE_MAP_UNPLACED.format(n=found["unplaced"]) if found.get("unplaced") else None
    )
    if unplaced_note is not None:
        print(unplaced_note)
    structure_summary = found.get("structure")
    structure_partial = None
    structure_high = []
    if structure_summary is not None:
        applied_line, structure_partial, structure_high = _structure_lines(
            active.model, structure_summary, bundle
        )
        print(applied_line)
        if structure_partial is not None:
            print(structure_partial)
        for line in structure_high:
            print(line)

    try:
        # Asked of what the parser returned, before the page markers are
        # added: once they are in, a conversion that produced nothing is a
        # document full of HTML comments, which is not blank, and the
        # importer's own "no content" refusal never fires. Reporting
        # `completed` over an empty book is the silent failure this
        # pipeline refuses.
        #
        # Only when no page needed OCR. When some did, `check_recognised_text`
        # below owns the refusal and says the more useful thing -- that the
        # OCR pass itself came back with pictures only. Saying "rerun with
        # --pdf-ocr" to somebody who just ran it would be worse than saying
        # nothing.
        text = _number_pages(markdown, first_selected_page(page_range))
        if gapped:
            text = _selected_only(text, ranges)
        # docling reads some glyphs as C0 control characters. They are not
        # text, XML forbids them, and a model shown one writes it back as a
        # `\u0000` escape Pandoc takes for raw TeX: removed here, said once,
        # and kept in the limitations (skill field test 260925).
        text, removed, control_pages = strip_control_characters(text)
        control_note = None
        if removed:
            control_note = CONTROL_CHARACTERS_REMOVED.format(
                count=removed, pages=", ".join(control_pages)
            )
            print(control_note)
        # With --ocr-replace-layer a page that carried a layer and came
        # back empty is empty: the layer is not used in its place (owner
        # ruling 260923, no silent fallback). Said page by page; nothing
        # read anywhere stops the run here, before a translation is paid.
        replaced_empty = []
        if replace_layer:
            selected = _selected_pages(ranges, examined)
            read = _pages_read(found.get("text_pages"), text)
            replaced_empty = [
                page for page in selected if page not in missing and page not in read
            ]
            for warning in _replaced_layer_lines(replaced_empty):
                print(warning)
            if not read & set(selected):
                _record_failed_extraction(
                    bundle,
                    settings,
                    page_range,
                    reason=OCR_REPLACE_ALL_EMPTY,
                    empty=replaced_empty,
                )
                raise PipelineError(OCR_REPLACE_ALL_EMPTY, stage=STAGE)
        if not missing and not _prose(markdown):
            raise PipelineError(EXTRACTION_EMPTY, stage=STAGE)
        source = staging / "source.md"
        source.write_text(text, encoding="utf-8")
        silent = check_recognised_text(source, missing)
        first = first_selected_page(page_range)
        headed = heading_for_top(text, first, pdf.stem)
        heading_note = None
        if headed is not None:
            text, heading_text = headed
            source.write_text(text, encoding="utf-8")
            heading_note = (
                SELECTION_HEADING_ADDED.format(page=first)
                if first and first >= 2
                else TITLE_HEADING_ADDED.format(title=heading_text)
            )
            print(heading_note)
        for warning in formula_warnings:
            print(warning)
        if formulas:
            print(FORMULA_IMAGES.format(count=formulas))
        dense = dense_pages(text)
        for number, chars in dense:
            print(PAGE_TOO_DENSE.format(page=number, chars=chars))
        # The figures this Markdown names, and where each stands on its
        # page: what the render step draws from. Written for every
        # extraction, none included -- its absence marks a bundle made
        # before figures were drawn (`pdf_figures.render_figures`).
        pdf_figures.write_records(
            staging / pdf_figures.FIGURES_FILE,
            [
                dict(
                    record,
                    fallback=(staging / record["fallback"])
                    .relative_to(bundle.root)
                    .as_posix(),
                )
                for record in pdf_figures.referenced(found.get("figures") or [], text)
            ],
        )
        pdf_figures.clear_assets(bundle)
        report = import_markdown(
            bundle, source, pandoc=pandoc, origin=pdf, stage=STAGE, kind="pdf"
        )
    except (PipelineError, KeyboardInterrupt):
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise

    _write_provenance(
        bundle,
        pdf,
        resolved,
        device,
        pages,
        page_range,
        scanned=missing,
        examined=examined,
        selected=_selected_pages(ranges, examined),
        settings=settings,
        ocr_engine=ocr_engine,
        raw_document=(
            Path(snapshot).resolve().relative_to(bundle.root).as_posix()
            if snapshot
            else None
        ),
        formulas=formulas,
        render=render,
        structure=structure_summary,
        image=structure,
    )
    limitations = [
        "Extraction reading order, headings and diacritics are not verified "
        "by this pipeline; inspect source.md before translating."
    ]
    if silent:
        from .messages import OCR_EMPTY_PAGES

        limitations.append(
            OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent))
        )
    # Every page, not the capped terminal list: the manifest is the record.
    limitations.extend(OCR_REPLACE_EMPTY.format(page=page) for page in replaced_empty)
    if engine_line is not None and not languages:
        limitations.append(engine_line)
    if invisible_note is not None:
        limitations.append(invisible_note)
    if render == RENDER_PDFIUM_PAGE_IMAGE:
        limitations.append(JBIG2_MASK_RENDER)
    if unplaced_note is not None:
        limitations.append(unplaced_note)
    if control_note is not None:
        limitations.append(control_note)
    if heading_note is not None:
        limitations.append(heading_note)
    for number, chars in dense:
        limitations.append(PAGE_TOO_DENSE.format(page=number, chars=chars))
    # A formula that could not be placed is a gap in the book, and the
    # terminal line scrolls away; the manifest keeps it.
    limitations.extend(formula_warnings)
    # So is a structure pass that did not run, or ran only in part.
    for line in (structure_note, structure_partial, *structure_high):
        if line is not None:
            limitations.append(line)
    bundle.add_limitations(limitations)
    # What this extraction added, so the next one can take it back.
    bundle.update_manifest(extraction={"limitations": limitations})
    return report


@contextlib.contextmanager
def _docling_log(note):
    """docling's log lines, fed to `note` while a conversion runs.

    docling reports through `logging`, not stdout, so without this the
    progress line and a failure message never see what it said -- among
    it, which OCR engine its auto setting picked. INFO is let through for
    the duration; what the terminal showed before (warnings and up, with
    the handlers already configured) is passed on unchanged, so the extra
    lines reach the progress line only.
    """
    logger = logging.getLogger("docling")
    handler = _LogFeed(note, logger.getEffectiveLevel(), logger.propagate)
    level, propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


class _LogFeed(logging.Handler):
    """One docling log record to `note`, and on to the root when it was due."""

    def __init__(self, note, passthrough, propagate):
        super().__init__(level=logging.INFO)
        self._note = note
        self._passthrough = passthrough
        self._propagate = propagate

    def emit(self, record):
        try:
            self._note(record.getMessage())
        except Exception:
            self.handleError(record)
        if self._propagate and record.levelno >= self._passthrough:
            logging.getLogger().callHandlers(record)


class _Sink:
    """stdout during a conversion, fed to the progress line one line at a time."""

    def __init__(self, note):
        self._note = note
        self._buffer = ""

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            head, _, self._buffer = self._buffer.partition("\n")
            self._note(head)
        return len(text)

    def flush(self):
        if self._buffer:
            self._note(self._buffer)
            self._buffer = ""

    def isatty(self):
        return False


def _failure_detail(err, transcript):
    """Why the conversion died: the exception, and what was logged before it."""
    detail = f"{type(err).__name__}: {err}".strip()
    tail = [line for line in transcript if line]
    if tail:
        detail = f"{detail} (last log line: {tail[-1]})"
    return detail


def _write_provenance(
    bundle,
    pdf,
    resolved,
    requested,
    pages,
    page_range,
    *,
    scanned=(),
    examined=0,
    selected=None,
    settings=None,
    ocr_engine=None,
    raw_document=None,
    formulas=0,
    render=None,
    structure=None,
    image=None,
):
    settings = settings or ExtractionSettings()
    ocr = settings.ocr
    ocr_lang = list(settings.ocr_lang) or None
    formula_images = settings.formula_images
    # What was asked for and what ran; `ExtractionSettings.from_manifest`
    # reads the asked-for half back on a rerun.
    engine = {
        "ocr_engine_requested": settings.ocr_engine,
        "ocr_engine": ocr_engine,
        "ocr_mode": settings.ocr_mode,
        # `ocr_mode` full_page, said plainly for a reader of the manifest;
        # derived, so not read back as a setting.
        "ocr_replace_layer": settings.ocr_replace_layer,
        "table_mode": settings.table_mode,
    }
    # The structure model asked for (identity, read back by
    # `from_manifest`), and whether its pass ran; the pass's counts beside.
    roles = {
        "structure": settings.structure,
        "structure_rev": settings.structure_rev if settings.structure else None,
        "structure_base": settings.structure_base if settings.structure else None,
        # Where the choice came from (cli, provider) and the endpoint's
        # answer to the probe image: provenance, not identity.
        "img_source": getattr(image, "source", None),
        "img_verdict": getattr(image, "verdict", None),
        "structure_applied": structure is not None,
        # complete / partial (decisions.Overlay.status), or not_run when
        # asked for and the endpoint could not see a page; only complete
        # satisfies a rerun that asks for structure (stages.already_prepared).
        "structure_status": (
            structure["status"]
            if structure is not None
            else ("not_run" if settings.structure else None)
        ),
        "structure_totals": (
            {
                key: value
                for key, value in structure.items()
                if key not in ("overlay", "status")
            }
            if structure is not None
            else None
        ),
    }
    version = _installed_version()
    bundle.work.mkdir(parents=True, exist_ok=True)
    bundle.work_file(EXTRACTION_JOB).write_text(
        json.dumps(
            {
                "parser": PARSER,
                "pdf": str(pdf),
                "pdf_sha256": sha256_file(pdf),
                "device": resolved,
                "device_requested": requested,
                "pages": pages,
                "ocr": ocr,
                "ocr_lang": ocr_lang,
                "formula_images": formula_images,
                **engine,
                **roles,
                "raw_document": raw_document,
                "version": version,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Replaces the block rather than merging into it: a stopped attempt's
    # `status`, `failure` and `empty_pages` (`_record_failed_extraction`)
    # would otherwise sit beside a completed stage (Codex re-verify 260924).
    # Nothing written earlier in this extraction is lost: the limitations
    # are listed after this, and `structure_status` is in `roles`.
    data = bundle.read_manifest()
    data["extraction"] = {
        "provider": PARSER,
        "version": version,
        "device": resolved,
        "device_requested": requested or "auto",
        "picture_description": False,
        # Pages the PDF itself could not spell out, and which therefore
        # had to be read by the OCR models.
        "pages_without_text_layer": list(scanned),
        "pages_examined": examined,
        "ocr": ocr,
        # Every selected page when the layer was replaced.
        "pages_read_by_ocr": (
            list(selected or [])
            if settings.ocr_replace_layer
            else (list(scanned) if ocr else [])
        ),
        # The languages the models were told to read, as given
        # (`--ocr-lang`); None means the engine's own default.
        "ocr_lang": ocr_lang,
        # Whether display formulas were kept as pictures, and how many
        # were; a rerun that changes the setting extracts again.
        "formula_images": formula_images,
        "formula_image_count": formulas,
        # The OCR engine asked for, the one that ran (docling's choice
        # under `auto`; None without OCR), and the OCR and table modes.
        **engine,
        # The region-role pass: the model asked for, its revision,
        # whether it ran, and what it did (overlay: decisions.json).
        **roles,
        # docling's document before our changes, bundle-relative.
        "raw_document": raw_document,
        # Which renderer painted the page images the models read:
        # docling-parse, or pypdfium2 for a PDF with JBIG2 image masks.
        # Derived from the file, so provenance, not a setting.
        "render_backend": render,
        "pdf": pdf.name,
        "pdf_sha256": sha256_file(pdf),
        "page_range": page_range,
        "page_range_sent": pages,
        "page_numbering": "1-based input, 1-based request",
        # Nothing was bought: the local parser has no provider charge.
        "cost_cents": None,
    }
    bundle.write_manifest(data)
    bundle.set_stage(STAGE, "completed", parser=PARSER, device=resolved)


def _installed_version():
    try:
        from importlib.metadata import version

        return version("docling")
    except Exception:
        return None
