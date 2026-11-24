"""Generate missing EPUB navigation while preserving targets and resources."""

from io import BytesIO
from pathlib import PurePosixPath
import posixpath
import zipfile
import pytest
from bs4 import BeautifulSoup
from ebooklib import epub
from book_maker.loader.helper import rebase_ncx_srcs, resolve_ncx_toc
from test_epubcheck import PRISTINE_LOAD_SPINE, _assert_valid, epubcheck
from test_epub_loader_correctness import _make_loader, _replace_epub_member


def _nav_items(package):
    return [
        item
        for item in package.find_all("item")
        if "nav" in (item.get("properties") or "").split()
    ]


@pytest.mark.parametrize("chapter_name", ["one.xhtml", "nav.xhtml"])
def test_missing_navigation_is_generated_without_overwriting_content(
    tmp_path, monkeypatch, chapter_name
):
    loader, output = _make_loader(tmp_path, monkeypatch, [(chapter_name, ["Source"])])
    assert not any(isinstance(item, epub.EpubNav) for item in loader.origin_book.items)
    # The minimal fixture has no NCX either; supply a chapter TOC to exercise
    # navigation generation independently of EbookLib's legacy TOC reader.
    loader.origin_book.toc = (loader.origin_book.get_item_with_href(chapter_name),)

    loader.make_bilingual_book()

    with zipfile.ZipFile(output) as archive:
        package_name = next(
            name for name in archive.namelist() if name.endswith(".opf")
        )
        package = BeautifulSoup(archive.read(package_name), "xml")
        nav_items = _nav_items(package)
        assert len(nav_items) == 1
        nav_name = str(PurePosixPath(package_name).parent / nav_items[0]["href"])
        navigation = BeautifulSoup(archive.read(nav_name), "xml")
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert len(package.find_all("item", attrs={"href": chapter_name})) == 1
    assert navigation.title.get_text() == "Correctness baseline"
    assert navigation.html["lang"] == "zh-hans"
    assert navigation.find("nav").find("a")["href"] == chapter_name


def _write_ncx_fixture(
    tmp_path, monkeypatch, chapter_name, ncx_name, title_id=None, with_nav=False
):
    monkeypatch.setattr(epub.EpubReader, "_load_spine", PRISTINE_LOAD_SPINE)
    source = tmp_path / "book.epub"
    book = epub.EpubBook()
    book.set_identifier("ncx-fixture")
    book.set_title("Navigation fixture")
    book.set_language("en")
    if title_id:
        book.metadata[epub.NAMESPACES["DC"]]["title"] = [
            ("Navigation fixture", {"id": title_id})
        ]
    chapter = epub.EpubHtml(title="Chapter", file_name=chapter_name, lang="en")
    chapter.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title>'
        '</head><body><p id="one">Source text.</p></body></html>'
    )
    book.add_item(chapter)
    book.add_item(epub.EpubNcx(file_name=ncx_name))
    if with_nav:
        book.add_item(epub.EpubNav())
    book.toc = [epub.Link(chapter_name + "#one", "Chapter", "chapter-link")]
    book.spine = [chapter]
    epub.write_epub(str(source), book)
    # EbookLib writes OPF-relative links into an NCX at any location. Make
    # the source's NCX relative to its actual location before reading it.
    # Generate the unrebased NCX directly: an earlier loader may already
    # have installed its process-wide rebasing writer hook.
    raw = epub.EpubWriter(str(source), book)._get_ncx()
    _replace_epub_member(
        source, "EPUB/" + ncx_name, rebase_ncx_srcs(raw, ncx_name).decode()
    )
    loader, output = _make_loader(tmp_path, monkeypatch, [])
    loader.disclose = False
    loader.quiet = True
    return loader, output


@pytest.mark.parametrize(
    "chapter_name,title_id",
    [
        ("chapter.xhtml", "nav"),
        ("Nav.xhtml", None),
        ("NAV-2.XHTML", "nav"),
    ],
)
def test_generated_navigation_reserves_metadata_ids_and_case_equivalent_names(
    tmp_path, monkeypatch, chapter_name, title_id
):
    loader, output = _write_ncx_fixture(
        tmp_path, monkeypatch, chapter_name, "toc.ncx", title_id
    )
    loader.make_bilingual_book()

    with zipfile.ZipFile(output) as archive:
        names = [name.casefold() for name in archive.namelist()]
        package = BeautifulSoup(archive.read("EPUB/content.opf"), "xml")
    assert len(names) == len(set(names))
    assert len(_nav_items(package)) == 1
    ids = [element["id"] for element in package.find_all(attrs={"id": True})]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "ncx_name,chapter_name",
    [
        ("toc.ncx", "Text/chapter.xhtml"),
        ("Text/toc.ncx", "Text/chapter.xhtml"),
        ("Navigation/toc.ncx", "Text/chapter.xhtml"),
    ],
)
def test_generated_navigation_resolves_the_selected_ncx_directory(
    tmp_path, monkeypatch, ncx_name, chapter_name
):
    loader, output = _write_ncx_fixture(tmp_path, monkeypatch, chapter_name, ncx_name)
    original_href = loader.origin_book.toc[0].href

    loader.make_bilingual_book()

    with zipfile.ZipFile(output) as archive:
        nav = BeautifulSoup(archive.read("EPUB/nav.xhtml"), "xml")
        ncx = BeautifulSoup(archive.read("EPUB/" + ncx_name), "xml")
    assert nav.find("a")["href"] == chapter_name + "#one"
    assert ncx.find("content")["src"] == (
        posixpath.relpath(chapter_name, posixpath.dirname(ncx_name)) + "#one"
    )
    assert loader.origin_book.toc[0].href == original_href


def test_source_navigation_is_kept_and_not_rebased(tmp_path, monkeypatch):
    # With a nav in the source, EbookLib reads the TOC from it, already
    # package-relative: nothing is generated and nothing is resolved twice.
    loader, output = _write_ncx_fixture(
        tmp_path,
        monkeypatch,
        "Text/chapter.xhtml",
        "Navigation/toc.ncx",
        with_nav=True,
    )

    loader.make_bilingual_book()

    with zipfile.ZipFile(output) as archive:
        package = BeautifulSoup(archive.read("EPUB/content.opf"), "xml")
        nav_items = _nav_items(package)
        assert len(nav_items) == 1
        nav = BeautifulSoup(archive.read("EPUB/" + nav_items[0]["href"]), "xml")
        ncx = BeautifulSoup(archive.read("EPUB/Navigation/toc.ncx"), "xml")
    assert nav.find("a")["href"] == "Text/chapter.xhtml#one"
    assert ncx.find("content")["src"] == "../Text/chapter.xhtml#one"


def test_ncx_is_resolved_against_the_book_being_rebuilt(tmp_path, monkeypatch):
    # --retranslate rebuilds a previous output, whose NCX need not sit where
    # the source's does: the TOC read from it is resolved against its own NCX.
    (tmp_path / "source").mkdir()
    (tmp_path / "output").mkdir()
    loader, _ = _write_ncx_fixture(
        tmp_path / "source", monkeypatch, "Text/chapter.xhtml", "Navigation/toc.ncx"
    )
    _write_ncx_fixture(
        tmp_path / "output", monkeypatch, "Text/chapter.xhtml", "toc.ncx"
    )
    rebuilt = tmp_path / "output" / "book.epub"

    new_book = loader._make_new_book(epub.read_epub(str(rebuilt)), str(rebuilt))

    assert new_book.toc[0].href == "Text/chapter.xhtml#one"


def test_a_dot_slash_package_path_is_read_like_ebooklib_reads_it(tmp_path, monkeypatch):
    loader, output = _write_ncx_fixture(
        tmp_path, monkeypatch, "Text/chapter.xhtml", "Navigation/toc.ncx"
    )
    source = tmp_path / "book.epub"
    with zipfile.ZipFile(source) as archive:
        container = archive.read("META-INF/container.xml").decode()
    _replace_epub_member(
        source,
        "META-INF/container.xml",
        container.replace('full-path="EPUB/', 'full-path="./EPUB/'),
    )
    loader, output = _make_loader(tmp_path, monkeypatch, [])
    loader.disclose = False

    loader.make_bilingual_book()

    with zipfile.ZipFile(output) as archive:
        nav = BeautifulSoup(archive.read("EPUB/nav.xhtml"), "xml")
    assert nav.find("a")["href"] == "Text/chapter.xhtml#one"


def test_generated_navigation_edge_cases_pass_epubcheck(
    tmp_path, monkeypatch, epubcheck
):
    loader, output = _write_ncx_fixture(
        tmp_path, monkeypatch, "Nav-2.xhtml", "Navigation/toc.ncx", title_id="nav"
    )
    loader.make_bilingual_book()

    _assert_valid(epubcheck, output, "generated navigation edge cases")


@pytest.mark.parametrize(
    "ncx_name,href,expected",
    [
        ("toc.ncx", "../Text/a.xhtml#one", "../Text/a.xhtml#one"),
        ("Navigation/toc.ncx", "../../Text/a.xhtml", "../Text/a.xhtml"),
        ("Navigation/toc.ncx", "../a.xhtml?edition=1#one", "a.xhtml?edition=1#one"),
        ("Navigation/toc.ncx", "#one", "Navigation/toc.ncx#one"),
        (
            "Navigation/toc.ncx",
            "https://example.org/a#one",
            "https://example.org/a#one",
        ),
        ("Navigation/toc.ncx", "//example.org/a#one", "//example.org/a#one"),
        ("Navigation/toc.ncx", "/a.xhtml#one", "/a.xhtml#one"),
        ("My%20Book/toc.ncx", "ch%201.xhtml#one", "My%20Book/ch%201.xhtml#one"),
    ],
)
def test_ncx_resolution_preserves_parent_paths_and_url_parts(ncx_name, href, expected):
    original = epub.Link(href, "Chapter", "chapter")

    resolved = resolve_ncx_toc([original], ncx_name)[0]

    assert resolved.href == expected
    assert original.href == href
    assert resolved is not original


def test_generated_navigation_uses_the_package_selected_by_the_reader(
    tmp_path, monkeypatch
):
    archives = []
    for folder_name, prefix in [("first", "A"), ("last", "B")]:
        folder = tmp_path / folder_name
        folder.mkdir()
        _write_ncx_fixture(
            folder, monkeypatch, f"{prefix}/chapter.xhtml", f"{prefix}/toc.ncx"
        )
        archives.append((folder_name, (folder / "book.epub").read_bytes()))
    source = tmp_path / "book.epub"
    with zipfile.ZipFile(source, "w") as combined:
        combined.writestr("mimetype", "application/epub+zip")
        combined.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            '<rootfiles><rootfile full-path="first/content.opf" media-type="application/oebps-package+xml"/>'
            '<rootfile full-path="last/content.opf" media-type="application/oebps-package+xml"/>'
            '<rootfile full-path="first/content.opf" media-type="application/octet-stream"/>'
            "</rootfiles></container>",
        )
        for folder_name, content in archives:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                for name in archive.namelist():
                    if name.startswith("EPUB/"):
                        combined.writestr(
                            folder_name + "/" + name.removeprefix("EPUB/"),
                            archive.read(name),
                        )
    loader, output = _make_loader(tmp_path, monkeypatch, [])
    loader.disclose = False
    assert loader.origin_book.get_item_with_href("B/chapter.xhtml") is not None

    loader.make_bilingual_book()

    with zipfile.ZipFile(output) as archive:
        nav = BeautifulSoup(archive.read("EPUB/nav.xhtml"), "xml")
    assert nav.find("a")["href"] == "B/chapter.xhtml#one"
