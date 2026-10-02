"""Package the bundle's bilingual Markdown as EPUB3, with Pandoc.

Pandoc reads the Markdown, embeds the images, writes the XHTML and builds
the navigation; this module decides what to ask it for, checks that the file
it produced is actually a book -- a navigable one, with every heading of the
source in its table of contents -- and only then lets it replace the one that
was there before. It makes no model or OCR request of any kind.
"""

import os
import re
import shutil
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path, PurePosixPath

from .bundle import sha256_file
from .epub_nav import validate_navigation
from .errors import PipelineError
from .messages import BILINGUAL_EPUB_SAVED
from .preflight import (
    MARKDOWN_FORMAT,
    find_pandoc,
    inspect,
    pandoc_version,
    run_tool,
)

STAGE = "export"

CSS_SOURCE = Path(__file__).with_name("epub.css")
# Marks an image standing alone in its paragraph as a figure, for the CSS
# above to centre (figures lost their <figure> wrapper, and its centring,
# when their alt text went: packet Q, 260925).
FIGURE_FILTER = Path(__file__).with_name("epub_figures.lua")

# Markdown's deepest heading, and therefore the depth the table of contents
# has to carry.
MAX_HEADING_LEVEL = 6

# Pandoc reports an unreachable image on stderr and still exits 0, leaving a
# book with a hole in it. These are the phrases that mean exactly that.
FETCH_FAILURES = (
    "Could not fetch resource",
    "Could not find image",
    "Could not load",
    "Failed to load",
    "File not found",
)


def export_epub(bundle, *, pandoc=None, title=None, language=None, author=None):
    """Build `book_bilingual.epub` from `book_bilingual.md`. Returns its path."""
    executable = find_pandoc(pandoc)
    source = bundle.bilingual_markdown
    if not source.is_file():
        raise PipelineError(
            f"no bilingual Markdown at {source}; translate the bundle first",
            stage=STAGE,
        )
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise PipelineError(f"{source} is empty", stage=STAGE)

    report = inspect(text, root=bundle.root, pandoc=executable)
    report.raise_if_problems(STAGE)

    manifest = bundle.read_manifest()
    title = title or _title_from(manifest, bundle)
    language = language or _language_from(manifest)
    author = author or (manifest.get("source") or {}).get("author")
    if not language:
        raise PipelineError(
            "the target language tag is unknown; pass --language",
            stage=STAGE,
        )
    if manifest.get("translation", {}).get("sample"):
        # A partial translation must not be mistaken for the book.
        title = f"{title} (sample)"

    css = bundle.work_file("epub.css")
    css.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CSS_SOURCE, css)

    # One book-title H1 with chapter H2s under it splits at H2; anything
    # else splits at its own top level. A headingless manuscript stays one
    # document, which is correct and worth saying out loud.
    split_level = (
        2 if report.top_level_headings == 1 and report.second_level_headings else 1
    )

    pandoc_input = _pandoc_input(bundle, text)
    target = bundle.work_file("book_bilingual.epub.part")
    if target.exists():
        target.unlink()
    argv = [
        executable,
        str(pandoc_input),
        "-f",
        MARKDOWN_FORMAT,
        "-t",
        "epub3",
        "--standalone",
        "--toc",
        # Every level Markdown has. The default of 3 drops an H4 and
        # everything under it from the contents without saying so, and a
        # heading a reader cannot navigate to is a heading the book lost.
        f"--toc-depth={MAX_HEADING_LEVEL}",
        f"--split-level={split_level}",
        "--css",
        str(css),
        "--lua-filter",
        str(FIGURE_FILTER),
        "--resource-path",
        str(bundle.root),
        "--metadata",
        f"title={title}",
        "--metadata",
        f"lang={language}",
        "-o",
        str(target),
    ]
    if author:
        argv += ["--metadata", f"author={author}"]

    result = run_tool(argv)
    warnings = [
        line
        for line in (result.stderr or "").splitlines()
        if any(phrase in line for phrase in FETCH_FAILURES)
    ]
    if warnings:
        target.unlink(missing_ok=True)
        raise PipelineError(
            "pandoc could not read a resource:\n" + "\n".join(warnings), stage=STAGE
        )

    try:
        validate_epub(
            target, expected_images=report.images, expected_outline=report.outline
        )
    except PipelineError:
        # The book that is already there stays; only the candidate goes.
        target.unlink(missing_ok=True)
        raise

    # Only now does the previous, valid book go away.
    os.replace(target, bundle.epub)

    bundle.add_limitations(report.preserved_lines())
    bundle.update_manifest(
        outputs={
            "epub": {
                "path": bundle.epub.name,
                "sha256": sha256_file(bundle.epub),
                "split_level": split_level,
                "title": title,
                "language": language,
                "author": author,
                "pandoc": pandoc_version(executable),
            }
        }
    )
    bundle.set_stage(STAGE, "completed", output=bundle.epub.name)
    print(BILINGUAL_EPUB_SAVED.format(path=bundle.epub))
    return bundle.epub


def validate_epub(path, *, expected_images=(), expected_outline=None):
    """Refuse anything that is not a readable, navigable EPUB3 container.

    Cheap structural checks only -- a real EPUBCheck run and a reader pass
    are separate work -- but every one of them has a failure mode behind it:
    a truncated zip, a missing container, an XHTML file Pandoc wrote from
    broken markup, images that never made it in, and a table of contents
    that does not lead to the book.

    `expected_outline` is `preflight.Report.outline` for the document that
    was converted. It is optional only so that the container checks can be
    exercised on a package with no Markdown behind it; the export path
    always supplies it, and `None` still requires a working nav.
    """
    if not path.is_file() or path.stat().st_size == 0:
        raise PipelineError(f"pandoc wrote no EPUB at {path}", stage=STAGE)
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            if broken:
                raise PipelineError(f"corrupt EPUB entry: {broken}", stage=STAGE)
            names = archive.namelist()
            if "mimetype" not in names:
                raise PipelineError("EPUB has no mimetype entry", stage=STAGE)
            mimetype = archive.read("mimetype").decode("ascii", "replace").strip()
            if mimetype != "application/epub+zip":
                raise PipelineError(f"EPUB mimetype is {mimetype!r}", stage=STAGE)
            if "META-INF/container.xml" not in names:
                raise PipelineError("EPUB has no META-INF/container.xml", stage=STAGE)
            documents = [n for n in names if n.lower().endswith((".xhtml", ".html"))]
            if not documents:
                raise PipelineError("EPUB contains no content document", stage=STAGE)
            for name in documents + ["META-INF/container.xml"]:
                data = archive.read(name)
                try:
                    ElementTree.fromstring(data)
                except ElementTree.ParseError as err:
                    raise PipelineError(
                        f"EPUB document {name} is not well-formed XML: {err}",
                        stage=STAGE,
                    )
            if not any(n.lower().endswith(".opf") for n in names):
                raise PipelineError("EPUB has no package document", stage=STAGE)
            _check_images(archive, documents, expected_images)
            validate_navigation(archive, expected_outline or [], stage=STAGE)
    except zipfile.BadZipFile as err:
        raise PipelineError(f"pandoc produced an unreadable EPUB: {err}", stage=STAGE)
    return path


def _check_images(archive, documents, expected_images):
    """Every `img` in the book points at a resource the book contains.

    Pandoc renames embedded media (`plate.png` becomes `EPUB/media/file0.png`),
    so comparing file names proves nothing. What has to hold is that each
    `src` resolves inside the archive, that the resource has bytes, and that
    the book ends up with as many distinct images as the Markdown named --
    the failure this guards against is one silently left out.
    """
    entries = {name: info for name, info in zip(archive.namelist(), archive.infolist())}
    referenced = set()
    for document in documents:
        root = PurePosixPath(document).parent
        try:
            tree = ElementTree.fromstring(archive.read(document))
        except ElementTree.ParseError:
            continue  # already reported by the caller's well-formedness pass
        for element in tree.iter():
            if not element.tag.endswith("img"):
                continue
            src = element.get("src")
            if not src:
                raise PipelineError("EPUB has an image with no source", stage=STAGE)
            if "://" in src:
                raise PipelineError(
                    f"EPUB image {src} is a remote reference", stage=STAGE
                )
            resolved = str(PurePosixPath(os.path.normpath(str(root / src))))
            if resolved not in entries:
                raise PipelineError(
                    f"EPUB image {src} is not in the package", stage=STAGE
                )
            if entries[resolved].file_size == 0:
                raise PipelineError(f"EPUB image {src} is empty", stage=STAGE)
            referenced.add(resolved)
    wanted = len(set(expected_images))
    if len(referenced) != wanted:
        raise PipelineError(
            f"the Markdown names {wanted} image(s) and the EPUB carries "
            f"{len(referenced)}",
            stage=STAGE,
        )


LEADING_COMMENT = re.compile(r"^<!--.*-->$")
FIRST_HEADING = re.compile(r"^#{1,6}\s+\S")
# A line that is front matter rather than prose: an image on its own, a
# fenced-div control line (the translation blocks), or a short banner --
# a publisher's name, an affiliation, a series line. Measured on the first
# real paper: OpenDataLoader put the logo and "NVIDIA" above the title.
FRONT_MATTER_LINE = re.compile(r"^(?:!\[[^\]]*\]\([^)]*\)|:::.*)$")
FRONT_MATTER_MAX_CHARS = 80
# A Setext underline: the short line above it is a heading, not a banner,
# and a heading is never moved. (A horizontal rule looks the same and is
# treated the same: conservatively, nothing above it moves.)
SETEXT_UNDERLINE = re.compile(r"^(?:=+|-+)$")
# A fenced div's opening line (`::: {.bbm-translation lang="en"}`) and its
# closing line (`:::` alone).
DIV_OPEN = re.compile(r"^:{3,}\s*(?:\{.*\}|\S+)\s*$")
DIV_CLOSE = re.compile(r"^:{3,}\s*$")


def _is_front_matter(line):
    if SETEXT_UNDERLINE.match(line):
        return False
    return bool(
        LEADING_COMMENT.match(line)
        or FRONT_MATTER_LINE.match(line)
        or len(line) <= FRONT_MATTER_MAX_CHARS
    )


def _all_front_matter(lines):
    """Whether every one of these non-blank lines is front matter.

    A line inside a fenced div goes with the div: a translation block
    belongs to the source line above it, and that line has already been
    judged. Judging the translation's own length made the outcome depend
    on the target language -- a 13-character Chinese banner whose English
    ran to 89 characters was refused (260921, a scanned book whose first
    heading the OCR had lost).
    """
    depth = 0
    for line in lines:
        if depth and DIV_CLOSE.match(line):
            depth -= 1
            continue
        if DIV_OPEN.match(line):
            depth += 1
            continue
        if depth:
            continue
        if not _is_front_matter(line):
            return False
    return True


def _pandoc_input(bundle, text):
    """The file handed to Pandoc, and why it is sometimes not the deliverable.

    Both extractors put a page marker at the top of the document, before the
    first heading, and a paper's first page puts a logo and an affiliation
    there too. Pandoc answers any content before the first heading with a
    chapter of its own, labelled in the table of contents with the book's
    title and linked to the wrong file -- a comment alone makes it a blank
    page, front matter makes it a duplicate entry the validator refuses.

    When everything above the first heading is front matter -- comments,
    images, translation blocks, short lines -- it is moved to just below the
    heading for the conversion. Nothing is dropped, `book_bilingual.md` on
    disk is not touched, and the deliverable keeps the order the extractor
    produced. Prose above the first heading (an untitled preface, an
    abstract with no heading) is not front matter and is left where it is;
    the export then fails on the navigation check, which is the honest
    outcome until such a document gets a heading of its own.
    """
    lines = text.splitlines()
    heading = next(
        (i for i, line in enumerate(lines) if FIRST_HEADING.match(line.strip())), None
    )
    if not heading:
        return bundle.bilingual_markdown
    prefix = [line.strip() for line in lines[:heading] if line.strip()]
    if not prefix or not _all_front_matter(prefix):
        return bundle.bilingual_markdown
    moved = (
        [lines[heading], ""]
        + [item for line in prefix for item in (line, "")]
        + lines[heading + 1 :]
    )
    staged = bundle.work_file("export.md")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("\n".join(moved) + "\n", encoding="utf-8")
    return staged


def _title_from(manifest, bundle):
    source = manifest.get("source") or {}
    for candidate in (source.get("title"), source.get("name")):
        if candidate:
            return str(candidate)
    return bundle.root.name


def _language_from(manifest):
    translation = manifest.get("translation") or {}
    return translation.get("language_tag") or None
