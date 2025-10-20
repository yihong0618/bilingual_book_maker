import pytest
from ebooklib import epub

from book_maker.loader.epub_loader import EPUBBookLoader


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
