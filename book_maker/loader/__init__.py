from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.txt_loader import TXTBookLoader

BOOK_LOADER_DICT = {
    "epub": EPUBBookLoader,
    "txt": TXTBookLoader
    # TODO add more here
}
