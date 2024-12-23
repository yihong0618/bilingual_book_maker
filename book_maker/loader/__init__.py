from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.txt_loader import TXTBookLoader
from book_maker.loader.srt_loader import SRTBookLoader
from book_maker.loader.md_loader import MarkdownBookLoader

BOOK_LOADER_DICT = {
    "epub": EPUBBookLoader,
    "txt": TXTBookLoader,
    "srt": SRTBookLoader,
    "md": MarkdownBookLoader,
    # TODO add more here
}
