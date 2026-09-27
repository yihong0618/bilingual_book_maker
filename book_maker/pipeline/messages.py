"""Operator-facing text for the Markdown/EPUB bundle pipeline.

Every string a person reads on this path lives here, verbatim, so the
wording is owned in one place rather than scattered through the stages.
"""

STAGE_COMPLETE = "Stage complete: {stage}"
BILINGUAL_MARKDOWN_SAVED = "Bilingual Markdown saved: {path}"
BILINGUAL_EPUB_SAVED = "Bilingual EPUB saved: {path}"
STAGE_FAILED = "{stage} failed: {detail}"
PRESERVED_WITHOUT_TRANSLATION = "Preserved without translation: {kind} ({count})."
UNSUPPORTED_STRUCTURE = (
    "Unsupported Markdown structure: {kind} at {location}. "
    "Normalize the source before translation."
)
SETTINGS_CHANGED = (
    "Source or translation settings changed; start a new translation bundle."
)
# A hand-edited book_bilingual.md is refused, never overwritten. Every path
# is shell-quoted so the command can be pasted. The harness export rebuilds
# only the bundle's own EPUB and writes nothing beside the PDF (lead 260925,
# Codex review 01a0dc74, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md), so
# the `--to-epub` route's refusal names both steps: {bundle} is the bundle,
# {bundle_epub} the EPUB the export writes in it, {copy} the file beside the
# PDF this run would have written. The lead's text, verbatim.
BILINGUAL_EDITED = (
    "Bilingual Markdown was edited; rebuild the EPUB from it with: "
    "python tools/pdf_to_book.py export {bundle}, then copy {bundle_epub} "
    "over {copy}."
)
# The same refusal from the staged harness (`translate`, `run`), where there
# is no copy beside a PDF: only the export.
BILINGUAL_EDITED_EXPORT = (
    "Bilingual Markdown was edited; rebuild the EPUB from it with: "
    "python tools/pdf_to_book.py export {bundle}"
)
SUBMISSION_UNKNOWN = (
    "Extraction submission outcome is unknown; do not resubmit until the "
    "provider job is checked."
)
# T3 of the 260925 skill field test: a block the model handed back unchanged,
# in a script the target does not use (`translation_checks.suspected_echo`).
# The lead's text, verbatim; {blocks} is "line N" of source.md, the first
# ten, then "and K more".
ECHO_UNRESOLVED = (
    "Warning: {count} block(s) came back identical to the original: {blocks}. "
    "A quotation the book already renders nearby may rightly stay; otherwise "
    "translate it in book_bilingual.md and export the bundle."
)
PANDOC_REQUIRED = (
    "Pandoc is required for EPUB export. Install it or provide --pandoc PATH."
)
# Pandoc 3.1.12 (February 2024) is the first release whose EPUB contents
# point at the headings (text/ch001.xhtml#chapter-one); earlier ones point
# at the files, which the navigation check refuses after the translation
# was paid for. Ubuntu 24.04 and Debian 13 apt ship older releases, so the
# message names the download page rather than the package manager.
PANDOC_MIN_VERSION = (3, 1, 12)
PANDOC_TOO_OLD = (
    "{found} is too old for EPUB export; Pandoc 3.1.12 or newer is required "
    "(its table of contents points at headings, older releases point at "
    "files). Install a current release from https://pandoc.org/installing.html "
    "and put a Pandoc ≥ 3.1.12 first on PATH (or run the harness "
    "tools/pdf_to_book.py with --pandoc PATH)."
)
NAV_INVALID = "EPUB navigation is invalid: "
PDF_OPTIONS_INERT = (
    "--pdf-ocr, --ocr-replace-layer, --ocr-engine, --device, --ocr-lang, "
    "--pages and --img-model apply only to PDF input."
)
DEVICE_SELECTED = "PDF extraction device: {device}."
DEVICE_CPU_FALLBACK = "PDF extraction device: cpu (no supported accelerator detected)."
# Two refusals, not one. docling reports both of these as the same
# unavailable-device error, but they need opposite answers from the
# operator: one is a reinstall, the other is a machine that has no such
# accelerator and never will.
DEVICE_NO_CUDA_BUILD = (
    "--device cuda was asked for, but the installed PyTorch is a CPU-only "
    "build. Reinstall through the CUDA route; see docs/installation-pdf.md."
)
DEVICE_UNAVAILABLE = (
    "--device {device} was asked for, but this machine has no {device} "
    "accelerator available. Use --device cpu to run on the processor, or "
    "--device auto to take whatever is here."
)
# The install line names the checkout route FIRST and on purpose. The
# published bbook_maker carries no `pdf` extra yet, and pip answers a
# missing extra with a warning and a successful install of the release
# without it -- so an operator told to run that command would land back
# on this very message, having changed nothing. Naming it as the thing
# that does not work is what breaks the loop.
PDF_ROUTE_NOT_INSTALLED = (
    "reading a PDF needs the pdf extra, which is not installed. From a "
    'checkout: pip install ".[pdf]" (on Linux without an NVIDIA GPU add '
    "--extra-index-url https://download.pytorch.org/whl/cpu, or the extra "
    "downloads about 3 GB of CUDA there). The published package does not "
    'carry this route yet, so pip install "bbook_maker[pdf]" will only warn '
    "about the unknown extra and install the release without it. Every case, "
    "per platform, in docs/installation-pdf.md. Detail: {err}"
)
PDFIUM_UNUSABLE = (
    "pypdfium2 is installed but unusable (no PdfDocument); reinstall it with "
    "pip install --force-reinstall pypdfium2"
)
BACKEND_FAILED = "PDF extraction failed: {detail}"
SCANNED_PAGES = (
    "{count} of {total} selected pages have no text layer; they are read by "
    "the OCR models."
)
INVISIBLE_TEXT_LAYER = (
    "{count} of {total} selected pages carry only an invisible OCR text layer "
    "(a scanned book with recognised text underneath); that layer is kept as "
    "the page's text (with --pdf-ocr, possibly mixed with what the OCR engine "
    "reads) unless --ocr-replace-layer is given with --pdf-ocr, which replaces "
    "it with a fresh OCR reading of the page image."
)
OCR_REQUIRED = (
    "{count} of {total} selected pages have no text layer (page(s) {pages}); "
    "rerun with --pdf-ocr to read them with the OCR models."
)
EXTRACTION_EMPTY = (
    "The parser returned no text for this PDF; there is nothing to translate. "
    "If its pages are scans, rerun with --pdf-ocr."
)
OCR_EMPTY = (
    "The parser produced no text for a document whose pages have no text "
    "layer; the OCR pass returned pictures only."
)
OCR_LANG_DEFAULT = (
    "No --ocr-lang given: the OCR engine reads its own default languages, "
    "which may not be the pages'; the line after extraction names the engine "
    "and languages it used. Check source.md before translating."
)
# `--ocr-replace-layer` (packet G; owner ruling 260923, docs/260923-docs-
# OWNER_RULINGS_OCR_PROMPT_LAYER_WIKI.md section 6): replacing an embedded
# text layer with fresh OCR is explicit, and a replacement that read nothing
# is said page by page, never papered over with the layer. The lead's text,
# verbatim, except `--with-ocr` (a retired spelling since 260921) is
# written as the current `--pdf-ocr`.
OCR_REPLACE_NEEDS_OCR = (
    "--ocr-replace-layer re-reads pages that already carry a text layer with "
    "the OCR engine, so it needs --pdf-ocr."
)
OCR_REPLACING_LAYER = (
    "OCR: replacing the embedded text layer on every page (--ocr-replace-layer)"
)
OCR_REPLACE_EMPTY = (
    "page {page}: the OCR engine read nothing where the PDF carried a text "
    "layer; with --ocr-replace-layer the layer is not used, so the page is "
    "empty. Rerun without the flag to keep the layer, or with --ocr-lang for "
    "the page's language."
)
# Not the lead's text: the tail of a long list of empty pages, and the stop
# when nothing selected was read (the lead asked for one sentence, same advice).
OCR_REPLACE_EMPTY_MORE = "... and {count} more"
OCR_REPLACE_ALL_EMPTY = (
    "The OCR engine read nothing on any selected page, and with "
    "--ocr-replace-layer the PDF's text layer is not used, so there is nothing "
    "to translate; rerun without the flag to keep the layer, or with "
    "--ocr-lang for the pages' language."
)
# The lead's text (Codex re-verify 260924, finding 2), verbatim.
PAGE_MAP_UNPLACED = (
    "{n} item(s) carry no page number; they are placed after the last page "
    "in source.md."
)
OCR_ENGINE_USED = "OCR engine: {engine}{chosen}, languages: {languages}."
OCR_ENGINE_CHOSEN = " (docling's choice on this install)"
# `--ocr-engine` (packet K): an engine named by the operator that this
# install cannot run is refused before any page is read, with the line that
# installs it; `auto` is never refused (it takes whatever is installed).
OCR_ENGINE_MISSING = (
    "--ocr-engine {engine} was asked for, but it is not installed here. "
    "{install} Or leave out --ocr-engine: auto takes the first engine "
    "installed."
)
# The pdf extra carries rapidocr with onnxruntime, and ocrmac on macOS (owner
# 250925), so a fresh install has both; the lines name the packages for an
# install that lost them.
OCR_ENGINE_INSTALL = {
    "rapidocr": (
        "The pdf extra brings it with onnxruntime; if it is missing here, "
        'reinstall the extra (pip install ".[pdf]") or pip install rapidocr '
        "onnxruntime."
    ),
    "easyocr": (
        "Install it with pip install easyocr (it downloads its models on first " "use)."
    ),
    "ocrmac": (
        "The pdf extra brings it on macOS; if it is missing here: pip install "
        "ocrmac (nothing to download)."
    ),
    "tesseract": "Install tesseract and its language data, then put it on PATH.",
}
OCR_ENGINE_NOT_MACOS = (
    "--ocr-engine ocrmac is Apple's Vision framework, which exists only on "
    "macOS; on this system choose rapidocr, easyocr or tesseract, or leave "
    "out --ocr-engine."
)
OCR_LANGUAGES_GIVEN = "{languages}"  # comma-joined, as given
OCR_LANGUAGES_DEFAULT = "the engine's defaults"
EXTRACTION_REUSED_OTHER_RUNTIME = (
    "Reusing the extraction made with docling {old_version} on {old_device}; "
    "this run would use docling {new_version} on {new_device}. Delete the "
    "bundle directory to extract again."
)
OCR_LANG_EMPTY = "--ocr-lang needs at least one language code, for example ch_sim,en"
# docling issue #4329: docling-parse paints a JBIG2-masked image unmasked.
JBIG2_MASK_RENDER = (
    "The PDF carries JBIG2 image masks, which docling-parse renders wrongly "
    "(docling issue #4329); page images are rendered by pypdfium2 instead, "
    "the text layer still by docling-parse."
)
OCR_EMPTY_PAGES = (
    "Warning: no text was recognised on page(s) {pages}; check source.md "
    "before translating."
)
SELECTION_HEADING_ADDED = (
    "Page {page}: the selection starts inside a section, so a heading "
    '"Page {page}" was added above its prose; the table of contents needs '
    "one there. Rename it in source.md before translating if you like."
)
TITLE_HEADING_ADDED = (
    "The document does not open with a top-level heading, so a heading "
    '"{title}" was added above its text; the table of contents needs one '
    "there. Rename it in source.md before translating if you like."
)
PAGES_SPAN_CONVERTED = (
    "The page selection is not one run of pages, so pages {span} were read "
    "and the ones outside the selection dropped afterwards; a single range "
    "reads fewer pages."
)
# Display formulas. docling finds the equation and does not read it, so
# the region is cropped from the page and kept as a picture; the wording
# says plainly that the equations are not translated, because a reader of
# a bilingual maths book will notice and should not have to guess why.
FORMULA_IMAGES = (
    "Display formulas kept as images: {count}. The parser does not read "
    "equations, so each one is cropped from the page; the prose around them "
    "is translated, the equations are not."
)
FORMULA_REGION_OVERSIZE = (
    "Warning: a formula region on page {page} covers {share}% of the page, "
    "which is a layout mistake rather than an equation; it was left as a "
    "placeholder instead of replacing the page with a picture of itself."
)
FORMULA_UNPLACEABLE = (
    "Warning: a formula on page {page} has no usable position on the page, "
    "so its placeholder is left where it is."
)
FORMULA_NO_POSITION = (
    "Warning: undecoded formula {number} (counting in reading order) carries "
    "no page position in the parser's output, so it is left as a placeholder."
)
FORMULA_NOT_EXPORTED = (
    "Warning: undecoded formula {number} on page {page} did not appear in the "
    "parser's Markdown, so its picture was not placed."
)
# Figures, drawn from the PDF by our own renderer at a chosen resolution
# (`pdf_figures`, packet Q, owner 260925); docling's 72 DPI pictures are
# the fallback.
FIGURES_DRAWN = "Figures: {count} drawn at {policy}, {size}."
FIGURES_REDRAWN = (
    "Figures redrawn at {policy} (they were {old}); source.md and the "
    "translation are kept."
)
FIGURE_RENDER_FAILED = (
    "Figure {id} on page {page} could not be drawn at {policy} ({err}); "
    "it keeps docling's 72 DPI picture."
)
# Codex astra follow-ups (260925): the megapixel ceiling, a lone embedded
# picture kept at its own resolution, a fallback that is not there.
FIGURE_CAPPED = "Figure {id} on page {page} would be {mp:.1f} MP at {policy}; drawn at {dpi} DPI instead, the {cap:.1f} MP most e-readers accept."
FIGURES_NATIVE = "{count} of them kept at the resolution of the picture embedded in the PDF, below {policy}: more pixels would add none of its detail."
FIGURE_FALLBACK_MISSING = "Figure {id} on page {page} could not be drawn ({err}), and docling's picture for it ({path}) is missing too; delete the bundle directory to extract again."
# A bundle whose source.md names drawn figures must carry their record,
# a record for every named figure (Codex review 260925): without it a
# changed policy would draw nothing and look like success. {problem} is
# one of the three FIGURE_RECORD_* fragments below.
FIGURE_RECORD_BROKEN = "source.md names {count} drawn figure(s), but .work/extraction/figures.json {problem}; the figures cannot be drawn. Delete the bundle directory to extract again."
FIGURE_RECORD_MISSING = "is missing"
FIGURE_RECORD_UNREADABLE = "cannot be read"
FIGURE_RECORD_UNLISTED = "does not list {ids}"
# A recorded figure the operator removed from source.md (lead 260925: the
# route tells operators to edit source.md before translating): skipped,
# its file left alone, said once per run.
FIGURES_UNREFERENCED = "{count} figure(s) in .work/extraction/figures.json are no longer named in source.md and were not drawn: {ids}."
# `--pdf-image-dpi` (owner 260925: the PDF's real physical DPI, default
# 200; {default} is formatted from `pdf_figures.FIGURE_POLICY_DEFAULT`).
FIGURE_DPI_BODY = (
    "how sharp the figures are, in dots per inch of the PDF's own page "
    "size. Default {default}: sharp on a tablet or a high-density "
    "e-reader; 150 for a smaller book, 300 for figures with tiny labels. "
    "Changing it on a rerun redraws the figures only; the extraction and "
    "the translation are kept. Formulas keep their own resolution."
)
HELP_PDF_IMAGE_DPI_CLI = "PDF only, with --to-epub: " + FIGURE_DPI_BODY
PDF_IMAGE_DPI_INVALID = "must be a whole number from 72 to 600"
FIGURES_LEGACY = (
    "This bundle was made before figures were drawn by their own "
    "renderer, so its figures stay at 72 DPI. Delete the bundle directory "
    "to extract again with sharp figures."
)
# docling reads some glyphs (a minus, an approximately-equal sign) as C0
# control characters; the extraction removes them from source.md (skill
# field test 260925, lead's text).
CONTROL_CHARACTERS_REMOVED = (
    "Extraction: removed {count} control character(s) that docling read from "
    "glyphs on page(s) {pages}; they are not text and would break the EPUB."
)
PAGE_TOO_DENSE = (
    "Warning: page {page} extracted {chars} characters, several times what a "
    "printed page holds; inspect source.md before translating."
)

# Region roles (`--img-model`, packet F; `--structure-model` in packet E2):
# a vision model re-names the layout detector's regions before the export.
# The sentences below are the lead's (packet E2, 260923), verbatim; an
# endpoint that cannot see a page and one of another format are said by
# `book_maker.endpoints` (IMG_ENDPOINT_UNVERIFIED, IMG_ENDPOINT_UNSUPPORTED).
STRUCTURE_APPLIED = (
    "Region roles: {accepted} of {asked} asked items changed by {model} "
    "({kept} kept, {rejected} rejected, {high_change} pages with many changes); "
    "overlay at {path}."
)
STRUCTURE_PARTIAL = "Region roles: {detail}; the detector's own labels stand there."
# The lead's text, verbatim (ruling 260923): a page on which most asked
# items changed keeps its changes and is called out instead.
STRUCTURE_HIGH_CHANGE = (
    "Region roles: {changed} of {asked} asked items on page {page} changed; "
    "read that page in source.md before translating."
)
# The `{detail}` of STRUCTURE_PARTIAL, one clause per kind, joined by "; ".
STRUCTURE_DETAIL_BUDGET = (
    "the {bound} budget ran out, so page(s) {pages} were not asked"
)
STRUCTURE_DETAIL_REJECTED = "{count} answer(s) rejected"
STRUCTURE_DETAIL_UNANSWERED = "{count} region(s) not answered"
STRUCTURE_DETAIL_FAILED = "page(s) {pages} could not be changed safely"
# Not the lead's text (Codex follow-up 260923): a question the endpoint
# kept failing until the pass's time budget ran out, and a bundle whose
# pass was not complete, which a run asking for one extracts again.
STRUCTURE_DETAIL_DEADLINE = (
    "the max_seconds budget ran out while the endpoint was retried: {error}"
)
STRUCTURE_NOT_REUSED = (
    "Extracting again: the bundle's region-role pass is {status}; this run "
    "asks for --img-model {model}, which only a complete pass satisfies."
)
# Not the lead's text: an error asking the endpoint about images (an
# authentication failure, say), which ends the run like any other.
STRUCTURE_FAILED = "--img-model {model} failed: {detail}"
# Not the lead's text: the image model's own bill, printed after the
# extraction when a second translator asked the pages (packet F).
IMAGE_MODEL_USAGE = "Image model ({model} at {base}): {summary}"

# Progress. The line is rewritten in place on a terminal and printed every
# ten seconds into a log, so it says the same thing either way: what is
# running, how long it has been running, and the last thing the parser
# said for itself.
PROGRESS_LINE = "{label}, {elapsed}s"
PROGRESS_LINE_DETAIL = "{label}, {elapsed}s - {detail}"
EXTRACT_PROGRESS_LABEL = "Extracting PDF: {scope}, {engine}"
EXTRACT_DONE = "PDF extracted: {scope}, {engine}, {elapsed}s."
# What the models are doing: laying out and reading tables on pages that
# spell themselves out, or additionally reading pages nobody typed.
ENGINE_LAYOUT = "layout and table models on {device}"
ENGINE_OCR = "OCR and layout models on {device}"
PAGE_SCOPE = "{count} page"
PAGES_SCOPE = "{count} pages"

# The main CLI's --to-epub route. It has no --pandoc flag, so the refusal
# that names one would send its operator looking for an option that is not
# there.
PANDOC_ON_PATH = (
    "Pandoc is required for --to-epub. Install it and make sure pandoc is on PATH."
)
TO_EPUB_BUNDLE = "Working bundle: {path}"
TO_EPUB_COPY = "Bilingual EPUB saved beside the PDF: {path}"
TRANSLATION_REUSED = (
    "Translation reused: {path} (same source and settings; delete it to "
    "translate again)."
)

# Argument and subcommand help, as authored.
DESCRIPTION = "Create bilingual Markdown and a reflowable EPUB from PDF or Markdown."
HELP_IMPORT = "Import Markdown and its local images."
HELP_EXTRACT = "Extract Markdown and images from a PDF with docling."
HELP_PDF_OCR = (
    "Read pages that carry no text layer with the OCR models; such pages are "
    "refused without it. Off by default: a born-digital PDF is already "
    "readable, and OCR costs several times the time without changing what is "
    "read. Layout and table detection run either way."
)
# The lead's text (packet G), verbatim but for `--with-ocr` -> `--pdf-ocr`.
HELP_OCR_REPLACE_LAYER = (
    "With --pdf-ocr: run the OCR engine over every page, replacing an existing "
    "text layer instead of keeping it. Off by default: an embedded layer is "
    "kept and only pages without one are read. Measured worse than the layer "
    "on clean scans with the local engines; for a layer that is wrong "
    "(invisible garbage, the wrong language). Part of the extraction "
    "identity: toggling it extracts again."
)
HELP_DEVICE = (
    "Which processor the extraction models run on: auto (detect, falling back "
    "to the CPU), cpu, cuda, mps or xpu. CPU is fully supported and produces "
    "the same output; it is slower."
)
HELP_OCR_LANG = (
    "With --pdf-ocr: the languages the OCR engine reads on pages with no "
    "text layer (every page with --ocr-replace-layer), comma-separated, in "
    "that engine's own codes (rapidocr: ch, en, latin; easyocr: ch_sim, ja, "
    "ko; ocrmac: zh-Hans, ja-JP) or as iso: tags (iso:zh; rapidocr takes "
    "iso:zh, not ch_sim, and reads only the first language); the run names "
    "the engine and languages it used. Without it the engine reads its own "
    "default languages."
)
# `--ocr-engine` (packet K): the lead's text, verbatim, for make_book.py;
# the harness has no --to-epub, so its copy opens with its own condition.
_OCR_ENGINE_BODY = (
    "the OCR engine for pages with no text layer (every page with "
    "--ocr-replace-layer). auto (default) takes the first installed of "
    "ocrmac, rapidocr, easyocr. The pdf extra installs rapidocr with "
    "onnxruntime, models included, and on macOS also ocrmac (Apple's Vision "
    "framework); neither downloads anything, so auto reads with ocrmac on a "
    "Mac and rapidocr elsewhere. easyocr downloads its models on first use "
    "(pip install easyocr). tesseract uses the tesseract program and its "
    "language data from PATH. Language codes differ by engine; see "
    "--ocr-lang. The run names the engine it used."
)
HELP_OCR_ENGINE_CLI = "PDF only, with --to-epub --pdf-ocr: " + _OCR_ENGINE_BODY
HELP_OCR_ENGINE = "With --pdf-ocr: " + _OCR_ENGINE_BODY
HELP_FORMULA_IMAGES = (
    "Keep display formulas as the bare placeholder instead of a picture "
    "cropped from the page; the default keeps the picture."
)
HELP_PAGES = "PDF pages, numbered from 1; for example 1-20."
HELP_TRANSLATE = "Translate a prepared bundle with BBM."
HELP_EXPORT = "Build an EPUB from the bundle's bilingual Markdown without translation."
HELP_RUN = "Import or extract, translate once, and export both reading formats."
HELP_INPUT = "Source PDF or Markdown file."
HELP_BUNDLE = "Prepared book directory."
HELP_OUTPUT = "Output bundle directory."
HELP_PANDOC = "Pandoc executable; defaults to PATH lookup."
HELP_TITLE = "Book title; otherwise use source metadata or the filename."
HELP_LANGUAGE = "Target language tag for EPUB metadata."
HELP_TRAILING = "BBM translation options after --."
