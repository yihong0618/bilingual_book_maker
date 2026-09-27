#!/usr/bin/env python3
"""Staged harness for the PDF/Markdown -> bilingual Markdown + EPUB bundle.

Development tooling, not a stable interface: `make_book.py --to-epub` runs
the same stages in one go, and this file is where they can be run one at a
time. Each stage writes its artifacts into one bundle directory and records
what it did in the manifest, so a failure downstream never costs the work
upstream.

    tools/pdf_to_book.py import  INPUT.md  --output BUNDLE
    tools/pdf_to_book.py extract INPUT.pdf --output BUNDLE
    tools/pdf_to_book.py translate BUNDLE -- --model gpt-5-mini --language zh-hans
    tools/pdf_to_book.py export  BUNDLE
    tools/pdf_to_book.py run INPUT --output BUNDLE -- <translation options>

Everything after a bare `--` is handed to the existing BBM translation
command line untouched, except for the few options this harness owns.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from book_maker.pipeline import messages  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.epub_export import export_epub  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.importer import import_markdown  # noqa: E402
from book_maker.pipeline.pdf_figures import (  # noqa: E402
    FIGURE_POLICY_DEFAULT,
    parse_figure_policy,
    parse_pdf_image_dpi_policy,
    render_figures,
)
from book_maker.pipeline.pdf_settings import OCR_ENGINES  # noqa: E402
from book_maker.pipeline.preflight import find_pandoc  # noqa: E402
from book_maker.pipeline.stages import (  # noqa: E402
    MARKDOWN_SUFFIXES,
    PDF_PARSER,
    PDF_SUFFIXES,
    already_prepared,
    check_pdf_options,
    prepare,
    source_kind,
)
from book_maker.pipeline.translate import check_options, translate_bundle  # noqa: E402

__all__ = [
    "MARKDOWN_SUFFIXES",
    "PDF_PARSER",
    "PDF_SUFFIXES",
    "already_prepared",
    "check_pdf_options",
    "main",
    "prepare",
    "source_kind",
    "split_trailing",
]


def split_trailing(argv):
    """`(harness arguments, BBM options)` around the first bare `--`.

    argparse cannot be trusted with this: REMAINDER swallows the wrong
    tokens as soon as an option before it takes a value.
    """
    argv = list(argv)
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pdf_to_book.py",
        description=messages.DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("--pandoc", default=None, help=messages.HELP_PANDOC)
    sub = parser.add_subparsers(dest="command", required=True)

    importer = sub.add_parser("import", help=messages.HELP_IMPORT, allow_abbrev=False)
    importer.add_argument("input", help=messages.HELP_INPUT)
    importer.add_argument("--output", required=True, help=messages.HELP_OUTPUT)

    extract = sub.add_parser("extract", help=messages.HELP_EXTRACT, allow_abbrev=False)
    extract.add_argument("input", help=messages.HELP_INPUT)
    extract.add_argument("--output", required=True, help=messages.HELP_OUTPUT)
    _add_pdf_options(extract)

    translate = sub.add_parser(
        "translate", help=messages.HELP_TRANSLATE, allow_abbrev=False
    )
    translate.add_argument("bundle", help=messages.HELP_BUNDLE)

    export = sub.add_parser("export", help=messages.HELP_EXPORT, allow_abbrev=False)
    export.add_argument("bundle", help=messages.HELP_BUNDLE)
    export.add_argument("--title", default=None, help=messages.HELP_TITLE)
    export.add_argument("--language", default=None, help=messages.HELP_LANGUAGE)
    # Not given: the figures stay as drawn. Given: they are drawn again at
    # that policy first, when it differs from the one they were drawn at.
    _add_figure_policy(export, default=None)

    run = sub.add_parser("run", help=messages.HELP_RUN, allow_abbrev=False)
    run.add_argument("input", help=messages.HELP_INPUT)
    run.add_argument("--output", required=True, help=messages.HELP_OUTPUT)
    _add_pdf_options(run)
    run.add_argument("--title", default=None, help=messages.HELP_TITLE)
    run.add_argument("--language", default=None, help=messages.HELP_LANGUAGE)
    return parser


def _add_pdf_options(parser):
    """The options that only mean something when the input is a PDF.

    None has a value on a Markdown input -- there is nothing to run OCR
    on and nothing to page through -- so typing one there is refused rather
    than ignored.
    """
    parser.add_argument("--pdf-ocr", action="store_true", help=messages.HELP_PDF_OCR)
    parser.add_argument(
        "--ocr-replace-layer",
        action="store_true",
        help=messages.HELP_OCR_REPLACE_LAYER,
    )
    parser.add_argument(
        "--device",
        default=None,
        choices=("auto", "cpu", "cuda", "mps", "xpu"),
        help=messages.HELP_DEVICE,
    )
    parser.add_argument("--ocr-lang", default=None, help=messages.HELP_OCR_LANG)
    parser.add_argument(
        "--ocr-engine",
        dest="ocr_engine",
        default="auto",
        choices=OCR_ENGINES,
        help=messages.HELP_OCR_ENGINE,
    )
    parser.add_argument("--pages", default=None, help=messages.HELP_PAGES)
    parser.add_argument(
        "--no-formula-images",
        dest="formula_images",
        action="store_false",
        help=messages.HELP_FORMULA_IMAGES,
    )
    from book_maker.endpoints import HELP_IMG_BASE_URL, HELP_IMG_KEY, HELP_IMG_MODEL

    parser.add_argument(
        "--img-model",
        dest="img_model",
        default=None,
        metavar="MODEL",
        help=HELP_IMG_MODEL,
    )
    parser.add_argument(
        "--img-base-url",
        dest="img_base_url",
        default=None,
        metavar="URL",
        help=HELP_IMG_BASE_URL,
    )
    parser.add_argument(
        "--img-key", dest="img_key", default=None, metavar="KEY", help=HELP_IMG_KEY
    )
    _add_figure_policy(parser, default=FIGURE_POLICY_DEFAULT)


def _add_figure_policy(parser, *, default):
    """`--figure-policy KIND:VALUE`: a developer option of this harness.

    How sharp the PDF's figures are drawn (`pdf_figures.FigurePolicy`);
    no main-CLI flag yet, its name and meaning follow the eval (packet Q).
    """
    parser.add_argument(
        "--figure-policy",
        dest="figure_policy",
        default=default,
        type=parse_figure_policy,
        metavar="KIND:VALUE",
        help="developer option: how sharp figures are drawn, one of "
        "dpi:N, page-width:PX, figure-px:PX",
    )
    # The main CLI's spelling, as a shorthand for dpi:N; the same value.
    parser.add_argument(
        "--pdf-image-dpi",
        dest="figure_policy",
        default=argparse.SUPPRESS,
        type=parse_pdf_image_dpi_policy,
        metavar="N",
        help="shorthand for --figure-policy dpi:N (72 to 600)",
    )


def structure_request(options, trailing):
    """`{"structure": request}` when an image model is chosen, else `{}`.

    `--img-model`, else the `--provider` entry's img_model among the
    translation options (`run`'s trailing options; `extract` has none, so
    the default endpoint and its key variable), else off. Nothing of the
    pass is loaded without a choice.
    """
    from book_maker.pipeline.translate import parse_bbm_options

    translation = parse_bbm_options(trailing)
    if not (getattr(options, "img_model", None) or "").strip() and not getattr(
        translation, "provider", None
    ):
        return {}
    from book_maker.pipeline.docling_parser import image_request

    request = image_request(options, translation)
    return {"structure": request} if request is not None else {}


def _extracted_pdf(bundle):
    """The PDF this bundle was extracted from, the same bytes, or a refusal."""
    from book_maker.pipeline.bundle import sha256_file

    source = bundle.read_manifest().get("source") or {}
    origin = source.get("origin")
    if source.get("kind") != "pdf" or not origin:
        raise PipelineError(
            "--figure-policy needs a bundle extracted from a PDF", stage="export"
        )
    pdf = Path(origin)
    if not pdf.is_file() or sha256_file(pdf) != source.get("origin_sha256"):
        raise PipelineError(
            f"the PDF this bundle was extracted from is not at {pdf} as it "
            f"was; the figures cannot be drawn again",
            stage="export",
        )
    return pdf


def main(argv=None):
    own, trailing = split_trailing(sys.argv[1:] if argv is None else list(argv))
    options = build_parser().parse_args(own)
    command = options.command
    if trailing and command not in ("translate", "run"):
        print(
            messages.STAGE_FAILED.format(
                stage=command,
                detail=f"{messages.HELP_TRAILING} They belong to translate or run.",
            )
        )
        return 2

    try:
        # Resolved first, every time: the EPUB dependency must fail before
        # a PDF is submitted or a model is called, not after.
        pandoc = find_pandoc(options.pandoc)

        if command == "import":
            if source_kind(options.input) != "markdown":
                raise PipelineError(
                    f"{options.input} is not a Markdown file; use the extract "
                    f"command for a PDF",
                    stage="import",
                )
            bundle = Bundle(options.output).create()
            import_markdown(bundle, options.input, pandoc=pandoc)
        elif command == "extract":
            if source_kind(options.input) != "pdf":
                raise PipelineError(
                    f"{options.input} is not a PDF; use the import command",
                    stage="extract",
                )
            device = check_pdf_options("pdf", options)
            structure = structure_request(options, [])
            bundle = Bundle(options.output).create()
            prepare(
                bundle,
                options.input,
                pandoc=pandoc,
                device=device,
                pages=options.pages,
                ocr=options.pdf_ocr,
                ocr_lang=options.ocr_lang,
                formula_images=options.formula_images,
                ocr_replace_layer=options.ocr_replace_layer,
                ocr_engine=options.ocr_engine,
                **structure,
            )
            render_figures(bundle, options.input, options.figure_policy)
        elif command == "translate":
            bundle = Bundle(options.bundle)
            translate_bundle(bundle, check_options(trailing), pandoc=pandoc)
        elif command == "export":
            bundle = Bundle(options.bundle)
            if options.figure_policy is not None:
                render_figures(bundle, _extracted_pdf(bundle), options.figure_policy)
            # The bundle's own EPUB only: nothing beside the PDF is written,
            # even for a `--to-epub` bundle (lead 260925, Codex review
            # 01a0dc74: which file there is the route's copy cannot be told
            # safely from a bundle). The route's refusal names the copy step.
            export_epub(
                bundle,
                pandoc=pandoc,
                title=options.title,
                language=options.language,
            )
            print(messages.STAGE_COMPLETE.format(stage="export"))
        elif command == "run":
            # The translation command line is validated first: `run` must
            # not pay for an extraction and then refuse the options that
            # were going to translate it.
            bbm_options = check_options(trailing)
            device = check_pdf_options(source_kind(options.input), options)
            structure = structure_request(options, trailing)
            bundle = Bundle(options.output).create()
            prepare(
                bundle,
                options.input,
                pandoc=pandoc,
                device=device,
                pages=options.pages,
                ocr=options.pdf_ocr,
                ocr_lang=options.ocr_lang,
                formula_images=options.formula_images,
                ocr_replace_layer=options.ocr_replace_layer,
                ocr_engine=options.ocr_engine,
                **structure,
            )
            render_figures(bundle, options.input, options.figure_policy)
            translate_bundle(bundle, bbm_options, pandoc=pandoc)
            export_epub(
                bundle,
                pandoc=pandoc,
                title=options.title,
                language=options.language,
            )
            print(messages.STAGE_COMPLETE.format(stage="export"))
    except PipelineError as err:
        print(
            messages.STAGE_FAILED.format(stage=err.stage or command, detail=err.detail)
        )
        return 1
    except KeyboardInterrupt:
        print(messages.STAGE_FAILED.format(stage=command, detail="interrupted"))
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
