import re
import zipfile

import pytest
from ebooklib import epub

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.utils import language_code


def test_epub_loader_handles_custom_metadata(tmp_path):
    source_book = epub.EpubBook()
    source_book.add_metadata("DC", "title", "Metadata Copy Test", {"id": "title-id"})
    source_book.add_metadata("DC", "creator", "Tester", {"role": "aut"})

    # Simulate a namespace that ebooklib does not recognise; the legacy approach
    # copied this verbatim and ebooklib failed while writing the book back.
    source_book.metadata["custom"] = [
        ("foo-tag", "bar-value", {"attr": "value"}),
    ]

    legacy_book = epub.EpubBook()
    legacy_book.metadata = source_book.metadata
    with pytest.raises(AttributeError):
        epub.write_epub(str(tmp_path / "legacy.epub"), legacy_book)

    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source_book
    loader.language = "zh-hans"
    loader.single_translate = False
    rebuilt_book = loader._make_new_book(source_book)

    output_path = tmp_path / "rebuilt.epub"
    epub.write_epub(str(output_path), rebuilt_book)
    assert output_path.exists()

    dc_namespace = epub.NAMESPACES["DC"]
    titles = rebuilt_book.metadata[dc_namespace]["title"]
    creators = rebuilt_book.metadata[dc_namespace]["creator"]

    assert ("Metadata Copy Test", {"id": "title-id"}) in titles
    assert ("Tester", {"role": "aut"}) in creators
    assert "custom" not in rebuilt_book.metadata


# ------------------------------------------------------- the book's language


def _language_source(*languages):
    book = epub.EpubBook()
    book.set_identifier("language-fixture")
    book.set_title("Language fixture")
    for index, code in enumerate(languages):
        if index == 0:
            book.set_language(code)
        else:
            book.add_metadata("DC", "language", code)
    return book


def _written_languages(tmp_path, source, language="zh-hans", single=False):
    """The order as a reading system sees it, read back from the OPF."""
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source
    loader.language = language
    # what __init__ settles, settled the same way; see EPUBBookLoader.__init__
    loader.language_tag = language_code(language)
    loader.single_translate = single
    rebuilt = loader._make_new_book(source)

    out = tmp_path / f"languages-{language}-{single}.epub"
    epub.write_epub(str(out), rebuilt)
    with zipfile.ZipFile(out) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".opf"))
        opf = archive.read(name).decode("utf-8")
    return re.findall(r"<dc:language>([^<]*)</dc:language>", opf), rebuilt


def test_the_target_language_is_declared_first(tmp_path):
    """A bilingual book is in both languages, but it is *made* to be read in
    the target one, and a reading system takes the first dc:language."""
    languages, rebuilt = _written_languages(tmp_path, _language_source("en"))

    assert languages == ["zh-hans", "en"]
    assert rebuilt.language == "zh-hans"


def test_a_single_translation_declares_only_the_target(tmp_path):
    languages, rebuilt = _written_languages(
        tmp_path, _language_source("en"), single=True
    )

    assert languages == ["zh-hans"]
    assert rebuilt.language == "zh-hans"


def test_the_source_languages_keep_their_order_behind_the_target(tmp_path):
    languages, _ = _written_languages(tmp_path, _language_source("en", "fr", "la"))

    assert languages == ["zh-hans", "en", "fr", "la"]


def test_a_target_the_source_already_declares_is_not_declared_twice(tmp_path):
    languages, _ = _written_languages(tmp_path, _language_source("en", "zh-hans"))

    assert languages == ["zh-hans", "en"]


def test_an_untaggable_language_leaves_the_source_declaration_alone(tmp_path):
    """--language may carry prompt wording no tag can be made from; nothing
    is stamped then, here as everywhere else."""
    languages, rebuilt = _written_languages(
        tmp_path, _language_source("en"), language="whatever the model calls it"
    )

    assert languages == ["en"]
    assert rebuilt.language == "en"
