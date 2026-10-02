"""The EPUB's table of contents, read out of the file that was produced.

Every assertion here is made against the navigation document inside a real
Pandoc-built EPUB -- found the way a reading system finds it, through
`container.xml` and the package manifest -- and never against the command
line that asked for it. A correct `--toc-depth` on the argv is not a table
of contents; the entries, their order, their nesting and the anchors they
resolve to are.
"""

import posixpath
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    pandoc_or_skip,
    register_fake_format,
)

from book_maker.pipeline import epub_export, epub_nav  # noqa: E402
from book_maker.pipeline.bundle import Bundle, sha256_file  # noqa: E402
from book_maker.pipeline.epub_export import export_epub, validate_epub  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import NAV_INVALID  # noqa: E402
from book_maker.pipeline.preflight import inspect  # noqa: E402
from book_maker.pipeline.translate import translate_bundle  # noqa: E402

OPTIONS = ["--api_format", "faketest", "--language", "zh-hans"]

XHTML = "{http://www.w3.org/1999/xhtml}"
OPS = "{http://www.idpf.org/2007/ops}"
OPF = "{http://www.idpf.org/2007/opf}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def fake_format(monkeypatch):
    return register_fake_format(monkeypatch)


# --------------------------------------------------------------------------
# Reading the book back, independently of the code under test
# --------------------------------------------------------------------------
def nav_document(epub_path):
    """`(member name, XML text)` for the declared navigation document.

    Written out here rather than reused from `epub_nav` on purpose: a test
    that found the nav with the same function it is checking would agree
    with a broken implementation about where the nav is not.
    """
    with zipfile.ZipFile(epub_path) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        package = container.find(f".//{CONTAINER}rootfile").get("full-path")
        opf = ElementTree.fromstring(archive.read(package))
        for item in opf.iter(f"{OPF}item"):
            if "nav" in (item.get("properties") or "").split():
                name = posixpath.normpath(
                    posixpath.join(posixpath.dirname(package), item.get("href"))
                )
                return name, archive.read(name).decode("utf-8")
    raise AssertionError(f"{epub_path} declares no navigation document")


def toc_entries(epub_path):
    """`(depth, text, href)` for the `epub:type="toc"` entries, in order."""
    _, xml = nav_document(epub_path)
    root = ElementTree.fromstring(xml)
    toc = next(
        nav
        for nav in root.iter(f"{XHTML}nav")
        if "toc" in (nav.get(f"{OPS}type") or "").split()
    )
    entries = []

    def walk(ordered, depth):
        for item in ordered.findall(f"{XHTML}li"):
            anchor = item.find(f"{XHTML}a")
            entries.append(
                (
                    depth,
                    " ".join("".join(anchor.itertext()).split()),
                    anchor.get("href"),
                )
            )
            for nested in item.findall(f"{XHTML}ol"):
                walk(nested, depth + 1)

    walk(toc.find(f"{XHTML}ol"), 0)
    return entries


def landmark_entries(epub_path):
    _, xml = nav_document(epub_path)
    root = ElementTree.fromstring(xml)
    for nav in root.iter(f"{XHTML}nav"):
        if "landmarks" in (nav.get(f"{OPS}type") or "").split():
            return [a.get("href") for a in nav.iter(f"{XHTML}a")]
    return []


def anchor_exists(epub_path, nav_name, href):
    """Whether `href`, relative to the nav document, lands on real content."""
    path, _, fragment = unquote(href).partition("#")
    target = (
        posixpath.normpath(posixpath.join(posixpath.dirname(nav_name), path))
        if path
        else nav_name
    )
    with zipfile.ZipFile(epub_path) as archive:
        if target not in archive.namelist():
            return False
        if not fragment:
            return True
        root = ElementTree.fromstring(archive.read(target))
        return any(element.get("id") == fragment for element in root.iter())


# --------------------------------------------------------------------------
# Bundles
# --------------------------------------------------------------------------
def exported(tmp_path, pandoc, bilingual, *, name="bundle"):
    """A bundle whose bilingual Markdown is `bilingual`, exported.

    Exporting an edited `book_bilingual.md` is a supported route of its own,
    and it is the one that lets a heading shape be stated exactly.
    """
    bundle = Bundle(tmp_path / name).create()
    bundle.bilingual_markdown.write_text(bilingual, encoding="utf-8")
    export_epub(bundle, pandoc=pandoc, title="A Book", language="zh-hans")
    return bundle


SIX_LEVELS = """# Book Title

Opening prose.

## Chapter One

Prose under one.

### Section 1.1

Prose.

#### Part 1.1.1

Prose.

##### Piece 1.1.1.1

Prose.

###### Grain 1.1.1.1.1

Prose.

## Chapter Two

Prose under two.
"""


# --------------------------------------------------------------------------
# The outline, carried whole
# --------------------------------------------------------------------------
def test_every_heading_level_reaches_the_table_of_contents(tmp_path, pandoc):
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    entries = toc_entries(bundle.epub)
    assert [(depth, text) for depth, text, _ in entries] == [
        (0, "Book Title"),
        (1, "Chapter One"),
        (2, "Section 1.1"),
        (3, "Part 1.1.1"),
        (4, "Piece 1.1.1.1"),
        (5, "Grain 1.1.1.1.1"),
        (1, "Chapter Two"),
    ]
    # H6 is the point: the writer's own default stops at three.
    assert max(depth for depth, _, _ in entries) == 5

    nav_name, _ = nav_document(bundle.epub)
    for _, text, href in entries:
        assert anchor_exists(bundle.epub, nav_name, href), f"{text} -> {href}"


def test_the_navigation_is_found_through_the_package_not_by_its_name(tmp_path, pandoc):
    """container.xml -> the OPF -> the item whose properties include `nav`."""
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    name, xml = nav_document(bundle.epub)
    assert 'epub:type="toc"' in xml
    with zipfile.ZipFile(bundle.epub) as archive:
        opf = next(n for n in archive.namelist() if n.endswith(".opf"))
        package = ElementTree.fromstring(archive.read(opf))
    declared = [
        item.get("href")
        for item in package.iter(f"{OPF}item")
        if "nav" in (item.get("properties") or "").split()
    ]
    assert len(declared) == 1
    assert name.endswith(declared[0])


def test_skipped_levels_attach_to_the_nearest_shallower_heading(tmp_path, pandoc):
    text = (
        "# One\n\na\n\n### Three Under One\n\nb\n\n## Two After Three\n\nc\n\n"
        "###### Six Under Two\n\nd\n\n## Another Two\n\ne\n"
    )
    bundle = exported(tmp_path, pandoc, text)
    assert [(d, t) for d, t, _ in toc_entries(bundle.epub)] == [
        (0, "One"),
        (1, "Three Under One"),
        (1, "Two After Three"),
        (2, "Six Under Two"),
        (1, "Another Two"),
    ]


def test_repeated_headings_get_entries_of_their_own(tmp_path, pandoc):
    text = "# Book\n\na\n\n## Notes\n\nb\n\n## Notes\n\nc\n\n## Notes\n\nd\n"
    bundle = exported(tmp_path, pandoc, text)
    entries = toc_entries(bundle.epub)
    assert [t for _, t, _ in entries] == ["Book", "Notes", "Notes", "Notes"]
    targets = [href for _, _, href in entries]
    assert len(set(targets)) == len(targets), targets
    nav_name, _ = nav_document(bundle.epub)
    for href in targets:
        assert anchor_exists(bundle.epub, nav_name, href), href


def test_explicit_identifiers_are_the_ones_the_contents_point_at(tmp_path, pandoc):
    text = (
        "# Book {#the-book}\n\nSee [later](#way-down).\n\n"
        "## Chapter {#way-down}\n\nb\n"
    )
    bundle = exported(tmp_path, pandoc, text)
    fragments = [href.partition("#")[2] for _, _, href in toc_entries(bundle.epub)]
    assert fragments == ["the-book", "way-down"]


def test_formatted_and_setext_headings_keep_their_reading_text(tmp_path, pandoc):
    text = (
        "Setext Book\n===========\n\na\n\n"
        "Setext Chapter\n--------------\n\nb\n\n"
        "### *Slanted* and **bold** and `code`\n\nc\n"
    )
    bundle = exported(tmp_path, pandoc, text)
    assert [(d, t) for d, t, _ in toc_entries(bundle.epub)] == [
        (0, "Setext Book"),
        (1, "Setext Chapter"),
        (2, "Slanted and bold and code"),
    ]


def test_a_book_with_no_headings_still_has_a_contents_entry(tmp_path, pandoc):
    text = "Just prose, and a second paragraph.\n\nNo heading anywhere.\n"
    bundle = exported(tmp_path, pandoc, text)
    entries = toc_entries(bundle.epub)
    assert len(entries) == 1
    depth, label, href = entries[0]
    assert depth == 0
    assert label == "A Book"  # the book-level entry, from its title
    nav_name, _ = nav_document(bundle.epub)
    assert anchor_exists(bundle.epub, nav_name, href)


def test_the_landmarks_pointer_to_the_contents_is_not_counted_as_a_chapter(
    tmp_path, pandoc
):
    """`<a href="#toc" epub:type="toc">` lives in landmarks, not in the TOC."""
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    assert "#toc" in landmark_entries(bundle.epub)
    assert "#toc" not in [href for _, _, href in toc_entries(bundle.epub)]
    assert len(toc_entries(bundle.epub)) == 7


# --------------------------------------------------------------------------
# The translated book
# --------------------------------------------------------------------------
def test_a_translated_heading_is_not_a_second_contents_entry(
    tmp_path, pandoc, fake_format
):
    """One entry per source heading; the translation is ordinary prose."""
    bundle = Bundle(tmp_path / "bundle").create()
    bundle.source.write_text(SIX_LEVELS, encoding="utf-8")
    bundle.set_stage("import", "completed")
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc, title="A Book")

    entries = toc_entries(bundle.epub)
    assert [t for _, t, _ in entries] == [
        "Book Title",
        "Chapter One",
        "Section 1.1",
        "Part 1.1.1",
        "Piece 1.1.1.1",
        "Grain 1.1.1.1.1",
        "Chapter Two",
    ]
    # The translations are in the book, and in none of the entries.
    _, xml = nav_document(bundle.epub)
    assert "译:" not in xml
    with zipfile.ZipFile(bundle.epub) as archive:
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml") and "nav" not in name
        )
    assert "译:Book Title" in body


# --------------------------------------------------------------------------
# What is refused, and what survives the refusal
# --------------------------------------------------------------------------
def outline_of(bundle, pandoc):
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    return inspect(text, root=bundle.root, pandoc=pandoc).outline


def mutate(source, destination, changes):
    """A copy of `source` with members replaced (str) or dropped (None)."""
    with zipfile.ZipFile(source) as archive:
        members = [
            (info.filename, archive.read(info.filename)) for info in archive.infolist()
        ]
    with zipfile.ZipFile(destination, "w") as out:
        for name, data in members:
            if name in changes:
                if changes[name] is None:
                    continue
                data = changes[name].encode("utf-8")
            out.writestr(name, data)
    return destination


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("drop-nav-file", "which is not in the package"),
        ("undeclare-nav", "declares no navigation document"),
        ("no-toc-nav", 'no nav element with epub:type="toc"'),
        ("empty-toc", "has no entries"),
        ("dangling-file", "which is not in the package"),
        ("dangling-anchor", "has no such anchor"),
        ("remote-entry", "points outside the package"),
        ("unlabelled-entry", "has no label"),
        ("dropped-heading", "heading(s) and the table of contents"),
        ("reordered-headings", "where the heading reads"),
        ("flattened-nesting", "deep where the heading is"),
        ("rewritten-anchor", "rather than at the heading"),
        ("no-container-rootfile", "names no package document"),
    ],
)
def test_a_book_whose_contents_do_not_lead_anywhere_is_refused(
    tmp_path, pandoc, damage, expected
):
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    outline = outline_of(bundle, pandoc)
    name, xml = nav_document(bundle.epub)

    changes = {}
    if damage == "drop-nav-file":
        changes[name] = None
    elif damage == "undeclare-nav":
        with zipfile.ZipFile(bundle.epub) as archive:
            opf = next(n for n in archive.namelist() if n.endswith(".opf"))
            text = archive.read(opf).decode("utf-8")
        changes[opf] = text.replace(' properties="nav"', "")
    elif damage == "no-toc-nav":
        changes[name] = xml.replace('epub:type="toc"', 'epub:type="bodymatter"')
    elif damage == "empty-toc":
        changes[name] = _replace_toc_list(xml, '<ol class="toc"></ol>')
    elif damage == "dangling-file":
        changes[name] = xml.replace("text/ch001.xhtml", "text/gone.xhtml")
    elif damage == "dangling-anchor":
        changes[name] = xml.replace("#chapter-two", "#chapter-nowhere")
    elif damage == "remote-entry":
        changes[name] = xml.replace(
            'href="text/ch001.xhtml#book-title"',
            'href="https://example.com/book.xhtml#book-title"',
        )
    elif damage == "unlabelled-entry":
        changes[name] = xml.replace(">Chapter Two</a>", "></a>")
    elif damage == "dropped-heading":
        changes[name] = xml.replace(
            '<li id="toc-li-7"><a href="text/ch003.xhtml#chapter-two">'
            "Chapter Two</a></li>",
            "",
        )
    elif damage == "reordered-headings":
        changes[name] = xml.replace(">Chapter One<", ">ZZZ<")
    elif damage == "flattened-nesting":
        # The same entries, in the same order, pointing at the same real
        # anchors -- with the hierarchy thrown away.
        changes[name] = _replace_toc_list(
            xml,
            '<ol class="toc">'
            + "".join(
                f'<li><a href="{href}">{label}</a></li>'
                for _, label, href in toc_entries(bundle.epub)
            )
            + "</ol>",
        )
    elif damage == "rewritten-anchor":
        # An anchor that exists, in the right file, on the wrong heading.
        changes[name] = xml.replace(
            'href="text/ch002.xhtml#chapter-one"',
            'href="text/ch002.xhtml#section-1.1"',
        )
    elif damage == "no-container-rootfile":
        changes["META-INF/container.xml"] = (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'version="1.0"><rootfiles/></container>'
        )

    candidate = mutate(bundle.epub, tmp_path / "candidate.epub", changes)
    with pytest.raises(PipelineError) as refused:
        validate_epub(candidate, expected_images=[], expected_outline=outline)
    assert refused.value.detail.startswith(NAV_INVALID), refused.value.detail
    assert expected in refused.value.detail


def _replace_toc_list(xml, replacement):
    """Swap the contents list of the `toc` nav for `replacement`."""
    start = xml.index('<ol class="toc">')
    end = xml.index("</nav>", start)
    return xml[:start] + replacement + xml[end:]


def test_the_untouched_book_passes_the_same_check(tmp_path, pandoc):
    """The refusals above are about the damage, not about the checker."""
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    outline = outline_of(bundle, pandoc)
    assert (
        validate_epub(bundle.epub, expected_images=[], expected_outline=outline)
        == bundle.epub
    )


def test_a_candidate_with_broken_navigation_never_replaces_the_book(
    tmp_path, pandoc, monkeypatch
):
    """The book on disk is only ever replaced by one that navigates."""
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    before = sha256_file(bundle.epub)
    good = bundle.epub.read_bytes()

    real_run_tool = epub_export.run_tool

    def break_the_nav(argv, *args, **kwargs):
        result = real_run_tool(argv, *args, **kwargs)
        target = Path(argv[argv.index("-o") + 1])
        if target.suffix == ".part":
            name, xml = nav_document(target)
            mutate(target, target, {name: xml.replace("#chapter-two", "#gone")})
        return result

    monkeypatch.setattr(epub_export, "run_tool", break_the_nav)
    with pytest.raises(PipelineError) as refused:
        export_epub(bundle, pandoc=pandoc, title="A Book", language="zh-hans")
    assert refused.value.detail.startswith(NAV_INVALID)
    assert sha256_file(bundle.epub) == before
    assert bundle.epub.read_bytes() == good
    assert not bundle.work_file("book_bilingual.epub.part").exists()


def test_a_shallow_table_of_contents_is_not_a_publishable_book(
    tmp_path, pandoc, monkeypatch
):
    """Pandoc's own default depth of 3 drops H4 and below, silently."""
    bundle = exported(tmp_path, pandoc, SIX_LEVELS)
    before = sha256_file(bundle.epub)

    monkeypatch.setattr(epub_export, "MAX_HEADING_LEVEL", 3)
    with pytest.raises(PipelineError) as refused:
        export_epub(bundle, pandoc=pandoc, title="A Book", language="zh-hans")
    assert refused.value.detail.startswith(NAV_INVALID)
    assert "7 heading(s)" in refused.value.detail
    assert sha256_file(bundle.epub) == before


# --------------------------------------------------------------------------
# The nesting rule, on its own
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "levels,depths",
    [
        ([1, 2, 3, 4, 5, 6], [0, 1, 2, 3, 4, 5]),
        ([1, 3, 2, 6, 2], [0, 1, 1, 2, 1]),
        ([2, 2, 2], [0, 0, 0]),
        ([3, 1, 2], [0, 0, 1]),
        ([1, 6, 6, 2], [0, 1, 1, 1]),
        ([], []),
    ],
)
def test_headings_nest_under_the_nearest_shallower_predecessor(levels, depths):
    outline = [(level, f"h{index}", "") for index, level in enumerate(levels)]
    assert [depth for depth, _, _ in epub_nav.nest(outline)] == depths


@pytest.mark.parametrize(
    "source,labels,anchors",
    [
        (
            "Title\n=====\n\nText.\n\n## Next\n\n[Title](#title) and [Next](#next).\n",
            ["Title", "Next"],
            ["title", "next"],
        ),
        ('# "Hello"\n\nText.\n', ["“Hello”"], ["hello"]),
        (
            "# Chapter {#custom .unnumbered}\n\nText.\n\n## Next\n\n[Chapter](#custom).\n",
            ["Chapter", "Next"],
            ["custom", "next"],
        ),
    ],
)
def test_heading_shapes_survive_translation_then_navigation(
    tmp_path, pandoc, monkeypatch, source, labels, anchors
):
    from pipeline_helpers import write_fixture
    from book_maker.pipeline.importer import import_markdown
    from book_maker.pipeline.preflight import parse_markdown

    register_fake_format(monkeypatch)
    original = write_fixture(tmp_path / "src", source)
    bundle = Bundle(tmp_path / "translated").create()
    import_markdown(bundle, original, pandoc=pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)
    entries = toc_entries(bundle.epub)
    assert [label for _, label, _ in entries] == labels
    assert [href.partition("#")[2] for _, _, href in entries] == anchors
    ast = parse_markdown(pandoc, bundle.bilingual_markdown.read_text())
    divs = [b for b in ast["blocks"] if b["t"] == "Div"]
    assert not any(b["t"] == "Header" for div in divs for b in div["c"][1])
    assert bundle.source.read_text() == source


def test_source_div_is_refused_before_translation_but_exports_with_outline(
    tmp_path, pandoc, monkeypatch
):
    from pipeline_helpers import FakeTranslator, write_fixture
    from book_maker.pipeline.importer import import_markdown

    source = "::: {.section}\n# Within\n\nText.\n:::\n\n# Outside\n\nText.\n"
    register_fake_format(monkeypatch)
    original = write_fixture(tmp_path / "src", source)
    bundle = Bundle(tmp_path / "translated").create()
    import_markdown(bundle, original, pandoc=pandoc)
    with pytest.raises(PipelineError, match="source fenced div"):
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    assert not FakeTranslator.instances
    result = exported(tmp_path, pandoc, source)
    assert [label for _, label, _ in toc_entries(result.epub)] == ["Within", "Outside"]
