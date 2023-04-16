from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.txt_loader import TXTBookLoader
from book_maker.loader.srt_loader import SRTBookLoader

BOOK_LOADER_DICT = {
    "epub": EPUBBookLoader,
    "txt": TXTBookLoader,
    "srt": SRTBookLoader,
    # TODO add more here
}
