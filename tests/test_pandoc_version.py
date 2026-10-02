"""The Pandoc release gate: refused before anything is paid for.

PIN (owner-facing CI fix, 2026-09-21, docs/260921-fix-PANDOC_MIN_VERSION_CI.md):
Pandoc 3.1.12 is the first release whose EPUB contents point at headings
(text/ch001.xhtml#chapter-one); 3.1.11 and older point at the chapter
file, which `epub_nav.validate_navigation` refuses after the translation
was paid for. Ubuntu 24.04 apt ships 3.1.3 and Debian 13 ships 3.1.11, so
the gate has to name the download page, not the package manager.
"""

import os
import stat
import sys

import pytest

from book_maker.pipeline import to_epub
from book_maker.pipeline.errors import PipelineError
from book_maker.pipeline.messages import (
    PANDOC_ON_PATH,
    PANDOC_REQUIRED,
    PANDOC_TOO_OLD,
)
from book_maker.pipeline.preflight import find_pandoc


def fake_pandoc(tmp_path, first_line):
    """An executable that answers --version with the given first line."""
    if sys.platform == "win32":
        pytest.skip("the fake executable is a shell script")
    script = tmp_path / "pandoc"
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{first_line}' 'Features: +server'\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.parametrize("line", ["pandoc 3.1.3", "pandoc 3.1.11.1", "pandoc 2.19.2"])
def test_a_pandoc_older_than_3_1_12_is_refused_by_name(tmp_path, line):
    script = fake_pandoc(tmp_path, line)
    with pytest.raises(PipelineError) as refused:
        find_pandoc(str(script))
    assert refused.value.detail == PANDOC_TOO_OLD.format(found=line)
    assert "3.1.12 or newer" in refused.value.detail


@pytest.mark.parametrize(
    "line", ["pandoc 3.1.12", "pandoc 3.2", "pandoc 3.11", "pandoc-3.6 3.6"]
)
def test_pandoc_3_1_12_and_newer_are_accepted(tmp_path, line):
    script = fake_pandoc(tmp_path, line)
    assert find_pandoc(str(script)) == str(script)


def test_an_unparseable_version_line_is_refused_not_guessed(tmp_path):
    script = fake_pandoc(tmp_path, "not a pandoc")
    with pytest.raises(PipelineError) as refused:
        find_pandoc(str(script))
    assert refused.value.detail == PANDOC_TOO_OLD.format(found="not a pandoc")


def test_the_gate_reads_the_pandoc_found_on_path(tmp_path, monkeypatch):
    fake_pandoc(tmp_path, "pandoc 3.1.3")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(PipelineError) as refused:
        find_pandoc()
    assert "3.1.3 is too old" in refused.value.detail


def test_the_main_cli_route_keeps_the_too_old_message(tmp_path, monkeypatch):
    # to_epub rewords only the missing-Pandoc refusal (the main CLI has no
    # --pandoc flag); a too-old Pandoc already names its fix and passes through.
    script = fake_pandoc(tmp_path, "pandoc 3.1.3")
    calls = []

    def real(explicit=None):
        calls.append(explicit)
        return find_pandoc(str(script))

    monkeypatch.setattr(to_epub, "find_pandoc", real)
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(str(tmp_path / "missing.pdf"), [], pandoc=None)
    assert refused.value.detail == PANDOC_TOO_OLD.format(found="pandoc 3.1.3")
    assert refused.value.detail != PANDOC_ON_PATH
    assert calls == [None]


def test_a_too_old_pandoc_names_path_first_and_the_harness_flag_as_such():
    # PIN (packet H, 260924; docs/260923-docs-WIKI_MODERNIZE.md "Findings
    # for the owner" item 3): the refusal reaches the main CLI's --to-epub
    # route too, which has no --pandoc flag, so the fix it names first is
    # PATH and --pandoc is offered as the harness's
    text = PANDOC_TOO_OLD.format(found="pandoc 3.1.3")
    assert "put a Pandoc ≥ 3.1.12 first on PATH" in text
    assert "(or run the harness tools/pdf_to_book.py with --pandoc PATH)" in text
    assert "or provide --pandoc PATH" not in text


def test_the_main_cli_route_still_rewords_a_missing_pandoc(tmp_path, monkeypatch):
    def missing(explicit=None):
        raise PipelineError(PANDOC_REQUIRED)

    monkeypatch.setattr(to_epub, "find_pandoc", missing)
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(str(tmp_path / "missing.pdf"), [], pandoc=None)
    assert refused.value.detail == PANDOC_ON_PATH
