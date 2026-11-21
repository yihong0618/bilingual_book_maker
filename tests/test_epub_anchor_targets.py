"""Bilingual copies must not duplicate source navigation targets."""

import pytest
from bs4 import BeautifulSoup
from book_maker.loader.helper import EPUBBookLoaderHelper


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize(
    "markup,attribute",
    [
        ('<p xml:id="target">Source</p>', "xml:id"),
        ('<a name="target" href="#other">Source</a>', "name"),
        (
            '<h:a xmlns:h="http://www.w3.org/1999/xhtml" id="target" '
            'name="target" href="#target">Source</h:a>',
            "name",
        ),
    ],
)
def test_translation_keeps_only_one_navigation_target(markup, attribute, single):
    soup = BeautifulSoup(f"<body>{markup}</body>", "html.parser")
    source = soup.body.find(recursive=False)
    helper = EPUBBookLoaderHelper(None, 1, "", False, language="zh-hans")

    translation = helper.insert_trans(source, "译文", single_translate=single)

    assert len(soup.find_all(attrs={attribute: "target"})) == 1
    assert translation.get(attribute) == ("target" if single else None)
