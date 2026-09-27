"""`make_book.py --to-epub`: a PDF in, a bilingual EPUB beside it.

The same three stages the staged harness runs, in one command, over a bundle
that stays on disk. Nothing is reimplemented here: `prepare` extracts,
`translate_bundle` runs the real translation CLI over the bundle's Markdown,
`export_epub` packages it, and the resume rules are the bundle's own -- a
second run over a finished bundle re-uses the extraction and the translated
batches rather than paying for them again.

What this module owns is the two decisions the harness leaves to the
operator: where the bundle goes (`<name>_book/` beside the PDF, kept, so the
Markdown can be edited and the run resumed) and where the book goes
(`<name>_bilingual.epub` beside the PDF, the name every other BBM route
writes). The copy is made after the export has validated its navigation, so
the file next to the PDF is never a half-built one.
"""

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from .bundle import Bundle, parse_ocr_lang, parse_pages
from .epub_export import export_epub
from .errors import PipelineError
from .messages import PANDOC_ON_PATH, PANDOC_REQUIRED, TO_EPUB_BUNDLE, TO_EPUB_COPY
from .pdf_figures import FIGURE_POLICY_DEFAULT, render_figures
from .pdf_settings import check_ocr_engine
from .preflight import find_pandoc
from .stages import device_for, prepare
from .translate import (
    BilingualEdited,
    check_options,
    parse_bbm_options,
    translate_bundle,
)

BUNDLE_SUFFIX = "_book"
EPUB_SUFFIX = "_bilingual.epub"

# Options the pipeline answers for itself. Everything else the operator
# typed is a translation option and is handed to the translate stage
# untouched -- the model, the key, the language, --test, --use_context, the
# prompt, all of it.
OWNED_OPTIONS = (
    "--to-epub",
    "--pdf-ocr",
    "--with-ocr",
    "--no-gpu",
    "--no-formula-images",
    "--ocr-replace-layer",
)
OWNED_VALUE_OPTIONS = (
    "--book_name",
    "--ocr-lang",
    "--ocr-engine",
    "--pages",
    "--device",
    # The image endpoint: resolved here, for the extraction. The inner
    # translation run has no image step, and would warn that nothing uses it.
    "--img-model",
    "--img-base-url",
    "--img-key",
    # How sharp the figures are drawn: the pixels, never the translation.
    # No main-CLI flag yet (packet Q); owned here so that when it comes it
    # can never reach the inner run.
    "--figure-policy",
    "--pdf-image-dpi",
)


def translation_argv(argv):
    """`argv` without the options this route owns.

    `--book_name` goes because the bundle names its own source file (the
    extracted Markdown, not the PDF); the others because they are this
    route's own switches and the translation CLI has never heard of them.
    """
    kept = []
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in OWNED_OPTIONS:
            continue
        if token in OWNED_VALUE_OPTIONS:
            skip = True
            continue
        if any(token.startswith(f"{name}=") for name in OWNED_VALUE_OPTIONS):
            continue
        kept.append(token)
    return kept


def _print_image_usage(structure):
    """`Image model (<model> at <base>): <usage>`, once the pass has run.

    The pass's requests are metered on a translator of their own, so they
    are reported apart from the translation's (which the inner run prints).
    Nothing is printed when no request was made (a reused bundle).
    """
    from .messages import IMAGE_MODEL_USAGE

    usage = getattr(structure.translator, "usage", None)
    summary = usage.summary() if usage is not None else None
    if summary:
        print(
            IMAGE_MODEL_USAGE.format(
                model=structure.model, base=structure.where(), summary=summary
            )
        )


def selection_stem(pdf_path, pages=None):
    """`<stem>` for the whole PDF, `<stem>_pages-6-7` for a page selection.

    A selection gets its own bundle and its own book: a chapter run must
    not overwrite the whole-book run beside it, and a rerun with the same
    selection must find its own extraction and translation to resume.
    """
    stem = Path(pdf_path).stem
    if pages:
        return f"{stem}_pages-{''.join(str(pages).split())}"
    return stem


def bundle_path(pdf_path, pages=None):
    pdf = Path(pdf_path)
    return pdf.parent / f"{selection_stem(pdf, pages)}{BUNDLE_SUFFIX}"


def epub_path(pdf_path, pages=None):
    pdf = Path(pdf_path)
    return pdf.parent / f"{selection_stem(pdf, pages)}{EPUB_SUFFIX}"


def pdf_to_epub(
    pdf_path,
    argv,
    *,
    device=None,
    pdf_ocr=False,
    ocr_lang=None,
    pages=None,
    formula_images=True,
    ocr_replace_layer=False,
    ocr_engine="auto",
    img_model=None,
    img_base_url=None,
    img_key=None,
    quiet=False,
    pandoc=None,
    figure_policy=FIGURE_POLICY_DEFAULT,
    prepare_stage=prepare,
    figure_stage=render_figures,
    translate_stage=translate_bundle,
    export_stage=export_epub,
):
    """Extract, translate and export one PDF. Returns the EPUB beside it.

    The stage functions are parameters so the routing can be tested without
    a PDF parser, a model or Pandoc; the defaults are the real stages and
    nothing else is injectable.
    """
    pdf = Path(pdf_path)
    # All resolved before the PDF is opened: an unusable Pandoc, a
    # translation option or a page selection that does not parse must not
    # cost an extraction.
    try:
        executable = find_pandoc(pandoc)
    except PipelineError as err:
        if err.detail != PANDOC_REQUIRED:
            raise  # too old: the message already names the fix
        raise PipelineError(PANDOC_ON_PATH)
    options = check_options(translation_argv(argv))
    parse_pages(pages)  # a selection that does not parse is refused here too
    parse_ocr_lang(ocr_lang)  # and an empty language list
    if pdf_ocr:
        # and an OCR engine this install cannot run (auto never is)
        check_ocr_engine(ocr_engine)
    # The image model (`--img-model`, else the provider entry's img_model,
    # else off) is resolved here, before a page is read: an endpoint that
    # cannot take it, or one with no key, is refused now. Nothing of the
    # pass is imported, built or probed when there is no choice.
    structure = None
    translation = parse_bbm_options(translation_argv(argv))
    image = SimpleNamespace(
        img_model=img_model, img_base_url=img_base_url, img_key=img_key
    )
    if (img_model or "").strip() or getattr(translation, "provider", None):
        from .docling_parser import image_request

        structure = image_request(image, translation)

    bundle = Bundle(bundle_path(pdf, pages)).create()
    print(TO_EPUB_BUNDLE.format(path=bundle.root))
    prepare_stage(
        bundle,
        pdf,
        pandoc=executable,
        device=device_for(device),
        pages=pages,
        ocr=pdf_ocr,
        ocr_lang=ocr_lang,
        formula_images=formula_images,
        progress=not quiet,
        structure=structure,
        # Handed on only when asked for, as the stage's seams take it.
        **({"ocr_replace_layer": True} if ocr_replace_layer else {}),
        **({"ocr_engine": ocr_engine} if (ocr_engine or "auto") != "auto" else {}),
    )
    if structure is not None and structure.translator is not None:
        _print_image_usage(structure)
    # The figures, drawn at the policy asked for: after an extraction, and
    # on a rerun that reused one, so another policy redraws them (and only
    # them) before the book is built. The export below always rebuilds the
    # EPUB, so the new pixels reach it and the copy beside the PDF.
    figure_stage(bundle, pdf, figure_policy)
    destination = epub_path(pdf, pages)
    try:
        translate_stage(bundle, options, pandoc=executable)
    except BilingualEdited as edited:
        # The translate stage refuses a hand-edited book_bilingual.md
        # without knowing this route's copy; the route's refusal names the
        # export and then the copy over `destination`, since the harness
        # export writes nothing beside the PDF (lead 260925, Codex review
        # 01a0dc74, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md).
        raise edited.naming_copy(destination) from None
    built = export_stage(bundle, pandoc=executable)

    # Only now, with a validated book in the bundle: a copy made from a
    # failed export would put a broken EPUB under the name a reader opens.
    # Through a sibling and a rename: a copy that dies halfway must not
    # leave a truncated file under the name a reader opens, and a previous
    # good book under that name survives until the new one is complete.
    # The sibling's name is this invocation's own (`mkstemp`), so two runs
    # writing the same book do not share, or delete, each other's partial
    # file (Codex review 01a0dc74).
    partial = None
    try:
        handle, name = tempfile.mkstemp(
            dir=destination.parent, prefix=f"{destination.name}.", suffix=".part"
        )
        os.close(handle)
        partial = Path(name)
        shutil.copyfile(built, partial)
        # mkstemp makes the file owner-only; the book keeps the bundle
        # EPUB's permissions, as a plain copy would.
        shutil.copymode(built, partial)
        os.replace(partial, destination)
    except OSError as err:
        if partial is not None:
            partial.unlink(missing_ok=True)
        raise PipelineError(f"could not save {destination}: {err}", stage="export")
    print(TO_EPUB_COPY.format(path=destination))
    return destination
