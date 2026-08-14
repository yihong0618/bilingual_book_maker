from unittest.mock import MagicMock

import pytest

from book_maker.translator import MODEL_DICT
from book_maker.translator.google_v3_translator import GoogleV3


def make_translator(language="simplified chinese", source_lang="en"):
    return GoogleV3("", language, source_lang=source_lang)


def test_googlev3_registered_in_model_dict():
    assert MODEL_DICT["googlev3"] is GoogleV3


def test_language_codes_normalized_for_v3():
    t = make_translator(language="simplified chinese")
    assert t.target_lang == "zh-CN"
    t = make_translator(language="traditional chinese")
    assert t.target_lang == "zh-TW"
    t = make_translator(language="ja")
    assert t.target_lang == "ja"


def test_glossary_requires_explicit_source_lang():
    t = make_translator(source_lang="auto")
    with pytest.raises(Exception, match="source language"):
        t.set_config(project_id="p", glossary_id="g")


def test_set_config_defaults_location():
    t = make_translator()
    t.set_config(project_id="p", location=None, glossary_id="g")
    assert t.location == "us-central1"
    assert t.glossary_id == "g"


def make_mocked_client(t, glossary=False):
    t.client = MagicMock()
    t.parent = "projects/p/locations/us-central1"
    if glossary:
        t.glossary_config = MagicMock()
    response = MagicMock()
    response.translations = [MagicMock(translated_text="plain")]
    response.glossary_translations = [MagicMock(translated_text="glossary")]
    t.client.translate_text.return_value = response
    return t.client


def test_translate_uses_glossary_translation():
    t = make_translator()
    client = make_mocked_client(t, glossary=True)
    assert t.translate("hello") == "glossary"
    request = client.translate_text.call_args.kwargs["request"]
    assert request["glossary_config"] is t.glossary_config
    assert request["source_language_code"] == "en"
    assert request["target_language_code"] == "zh-CN"
    assert request["contents"] == ["hello"]


def test_translate_without_glossary():
    t = make_translator()
    client = make_mocked_client(t, glossary=False)
    assert t.translate("hello") == "plain"
    request = client.translate_text.call_args.kwargs["request"]
    assert "glossary_config" not in request


def test_translate_omits_source_lang_when_auto():
    t = make_translator(source_lang="auto")
    client = make_mocked_client(t)
    t.translate("hello")
    request = client.translate_text.call_args.kwargs["request"]
    assert "source_language_code" not in request
