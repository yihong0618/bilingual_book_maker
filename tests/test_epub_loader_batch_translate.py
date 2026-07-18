from bs4 import BeautifulSoup as bs

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import EPUBBookLoaderHelper


class FakeTranslateModel:
    TRANSLATION_ERROR_MARKER = "[Translation failed for this paragraph]"

    def __init__(self):
        self.received = None

    def translate_list(self, text_list):
        self.received = list(text_list)
        return [f"translated:{t}" for t in text_list]


def _make_loader(translate_model):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = translate_model
    loader.exclude_translate_tags = "sup,code"
    loader.translation_style = ""
    loader.helper = EPUBBookLoaderHelper(translate_model, 1, "", False)
    return loader


def test_deal_old_acc_sends_plain_text_not_html_tags():
    # Regression test: a nav/TOC-style paragraph with class/id attributes
    # used to be stringified (tags and all) before being sent to the
    # translation model, leaking raw HTML into the translated output.
    soup = bs(
        '<p class="toccn" id="chapter_2">Chapter 2: SWITZERLAND: Happiness Is Boredom</p>',
        "html.parser",
    )
    p = soup.find("p")
    translate_model = FakeTranslateModel()
    loader = _make_loader(translate_model)

    loader._deal_old_acc([p], False)

    assert translate_model.received == ["Chapter 2: SWITZERLAND: Happiness Is Boredom"]

    inserted = soup.find_all("p")[-1]
    assert "<" not in inserted.get_text()
    assert inserted.get_text() == (
        "translated:Chapter 2: SWITZERLAND: Happiness Is Boredom"
    )
