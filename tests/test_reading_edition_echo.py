"""Echoed blocks on the reading edition: named once, and in the manifest.

PIN (lead 260925 with astra consult, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md,
section "T3, open"; rescoped to detection only): a block the model returns
identical to its source, in a script the target does not use, is kept as it
came back, and the run says which `source.md` lines they are -- one warning
at the end of the run, and the same line as a manifest limitation. A rerun
replaces the previous run's line rather than piling a second one on.

Real Pandoc and the real CLI; only the model is fixed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_fixture,
)

from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.importer import import_markdown  # noqa: E402
from book_maker.pipeline.messages import ECHO_UNRESOLVED  # noqa: E402
from book_maker.pipeline.reading_edition import echo_warning  # noqa: E402
from book_maker.pipeline.translate import translate_bundle  # noqa: E402

GREEK_1 = "Πατήρ ού φόβον ένθρώσκει, Πειθώ δ' έπιχεύει."
GREEK_2 = "καί μ' οΰτι μελιγλώσσοις πειθοΰς έπαοιδαΐσιν θέλξει."

# Line numbers matter: the warning names them.
BOOK = f"""# Oracles

The Father does not thrust in fear, but rather pours in Persuasion.

{GREEK_1}

Plato

not with the honey-tongued spells of Persuasion shall he charm me.

{GREEK_2}
"""
GREEK_1_LINE = 5
GREEK_2_LINE = 11


class EchoFake(FakeTranslator):
    """Translates like the shared fake, but hands back Greek and names as sent."""

    instances = []
    fail_after = None
    echo = True

    def _answer(self, text):
        stripped = text.strip()
        if type(self).echo and (stripped in (GREEK_1, GREEK_2) or stripped == "Plato"):
            return stripped
        return super()._answer(text)


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def echo_format(monkeypatch):
    EchoFake.echo = True
    return register_fake_format(monkeypatch, "echofake", EchoFake)


def prepared(tmp_path, pandoc, text=BOOK):
    book = write_fixture(tmp_path / "src", text)
    bundle = Bundle(tmp_path / "bundle").create()
    import_markdown(bundle, book, pandoc=pandoc)
    return bundle


def options(fmt):
    return ["--api_format", fmt, "--language", "zh-hans"]


def expected_warning():
    return ECHO_UNRESOLVED.format(
        count=2, blocks=f"line {GREEK_1_LINE}, line {GREEK_2_LINE}"
    )


def test_echoed_blocks_are_named_once_by_source_line(
    tmp_path, pandoc, echo_format, capsys
):
    bundle = prepared(tmp_path, pandoc)
    source_lines = bundle.source.read_text(encoding="utf-8").splitlines()
    assert source_lines[GREEK_1_LINE - 1] == GREEK_1
    assert source_lines[GREEK_2_LINE - 1] == GREEK_2

    translate_bundle(bundle, options(echo_format), pandoc=pandoc)

    out = capsys.readouterr().out
    # "Plato" came back unchanged too, and is rightly not named.
    assert out.count("came back identical to the original") == 1
    assert expected_warning() in out
    # Kept as the model returned it: the check never rewrites a translation.
    bilingual = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert f'lang="zh-hans"}}\n{GREEK_1}\n:::' in bilingual


def test_echoed_blocks_are_a_manifest_limitation(tmp_path, pandoc, echo_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, options(echo_format), pandoc=pandoc)

    manifest = bundle.read_manifest()
    assert expected_warning() in manifest["limitations"]
    assert manifest["translation"]["limitations"] == [expected_warning()]


def test_a_rerun_without_echoes_drops_the_previous_line(
    tmp_path, pandoc, echo_format, capsys
):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, options(echo_format), pandoc=pandoc)
    assert expected_warning() in bundle.read_manifest()["limitations"]

    # Deleting the bilingual file is how a translation is redone on purpose.
    bundle.bilingual_markdown.unlink()
    EchoFake.echo = False
    capsys.readouterr()
    translate_bundle(bundle, options(echo_format), pandoc=pandoc)

    assert "came back identical" not in capsys.readouterr().out
    manifest = bundle.read_manifest()
    assert expected_warning() not in manifest["limitations"]
    assert manifest["translation"]["limitations"] == []


def test_a_book_with_no_echo_prints_nothing(tmp_path, pandoc, echo_format, capsys):
    EchoFake.echo = False
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, options(echo_format), pandoc=pandoc)
    assert "came back identical" not in capsys.readouterr().out
    assert not any(
        "came back identical" in line for line in bundle.read_manifest()["limitations"]
    )


def test_the_warning_names_ten_lines_then_counts_the_rest():
    warning = echo_warning(list(range(1, 14)))
    assert warning == ECHO_UNRESOLVED.format(
        count=13,
        blocks=", ".join(f"line {n}" for n in range(1, 11)) + " and 3 more",
    )
    assert echo_warning([]) is None
