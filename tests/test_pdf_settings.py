"""The extraction settings: the one typed value an extraction is asked for
and a bundle is compared against. No docling here; the converter half is
in tests/test_docling_adapter.py."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.pdf_settings import (  # noqa: E402
    OCR_ENGINES,
    OCR_MODES,
    TABLE_MODES,
    ExtractionSettings,
)

DEFAULT_IDENTITY = {
    "ocr": False,
    "ocr_engine": "auto",
    "ocr_mode": "default",
    "ocr_lang": None,
    "table_mode": "accurate",
    "formula_images": True,
    "structure": None,
    "structure_rev": None,
    "structure_base": None,
}


def test_the_defaults_are_today_s_run():
    assert ExtractionSettings().identity() == DEFAULT_IDENTITY


def test_ocr_fields_are_inert_without_ocr():
    # OCR settings changed nothing when OCR was off, so they must not make
    # a bundle made without OCR look different.
    off = ExtractionSettings(
        ocr=False, ocr_engine="easyocr", ocr_mode="full_page", ocr_lang=("ja",)
    )
    assert off.identity() == DEFAULT_IDENTITY
    on = ExtractionSettings(
        ocr=True, ocr_engine="easyocr", ocr_mode="full_page", ocr_lang=("ja",)
    )
    assert on.identity() == {
        **DEFAULT_IDENTITY,
        "ocr": True,
        "ocr_engine": "easyocr",
        "ocr_mode": "full_page",
        "ocr_lang": ["ja"],
    }


def test_no_languages_is_none_in_the_identity():
    assert ExtractionSettings(ocr=True).identity()["ocr_lang"] is None


def test_a_list_of_languages_is_held_as_a_tuple():
    settings = ExtractionSettings(ocr=True, ocr_lang=["ch", "en"])
    assert settings.ocr_lang == ("ch", "en")
    assert settings == ExtractionSettings(ocr=True, ocr_lang=("ch", "en"))


def test_an_empty_manifest_is_the_defaults():
    assert ExtractionSettings.from_manifest({}) == ExtractionSettings()
    assert ExtractionSettings.from_manifest(None) == ExtractionSettings()


def test_a_manifest_round_trips_through_the_requested_engine():
    extraction = {
        "ocr": True,
        # what docling resolved `auto` to is not what was asked for
        "ocr_engine": "rapidocr",
        "ocr_engine_requested": "auto",
        "ocr_mode": "full_page",
        "ocr_lang": ["ch"],
        "table_mode": "fast",
        "formula_images": False,
    }
    assert ExtractionSettings.from_manifest(extraction) == ExtractionSettings(
        ocr=True,
        ocr_engine="auto",
        ocr_mode="full_page",
        ocr_lang=("ch",),
        table_mode="fast",
        formula_images=False,
    )


@pytest.mark.parametrize(
    "field,value,allowed",
    [
        ("ocr_engine", "paddle", OCR_ENGINES),
        ("ocr_mode", "everything", OCR_MODES),
        ("table_mode", "slow", TABLE_MODES),
    ],
)
def test_an_unknown_value_is_refused_naming_the_allowed_ones(field, value, allowed):
    with pytest.raises(PipelineError) as refused:
        ExtractionSettings(**{field: value})
    detail = refused.value.detail
    assert field in detail
    assert repr(value) in detail
    assert ", ".join(allowed) in detail


def test_the_settings_module_does_not_import_docling():
    import importlib

    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "docling"
        or name.startswith("docling.")
        or name.startswith("book_maker")
    }
    for name in saved:
        del sys.modules[name]
    try:
        importlib.import_module("book_maker.pipeline.pdf_settings")
        assert "docling" not in sys.modules
    finally:
        for name in [n for n in sys.modules if n.startswith("book_maker")]:
            del sys.modules[name]
        sys.modules.update(saved)
