from book_maker.cli import get_book_type


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"
