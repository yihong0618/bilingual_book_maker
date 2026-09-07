"""The file says what the translation was, to a machine.

The disclosure tests cover the one line a *reader* is told. This covers the
other half: `bbm_translation_metadata.json` and the user glossary beside it
— what somebody reads when they want to know what could have gone wrong
with a translation they were handed.

Owner ruling, 260906: the record says nothing about the operator. Four keys
at most — `generator`, `model`, `date`, and the checksum of the embedded
glossary — and no command line, no build, no endpoint host, no route, no
languages. The key set is asserted exactly, so a fifth key is a decision
somebody makes on purpose rather than a line that slips in.

Two things are load-bearing throughout and are asserted rather than assumed:
nothing secret reaches the zip, and nothing a previous run wrote survives
into the next one's output.
"""

import json
import os
import subprocess
import sys
import zipfile
from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
from ebooklib import epub

from book_maker import translation_metadata as tmeta
from book_maker.loader.disclosure import CREDIT_CLASS, CREDIT_PREFIX, is_our_colophon
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.utils import language_code

DC_NS = epub.NAMESPACES["DC"]


class StubModel:
    """A translator that knows its model and the host it talks to."""

    TRANSLATION_ERROR_MARKER = None
    model = "x/y"
    api_base = "https://api.openai.com/v1"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"T{text}"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


class ModelB(StubModel):
    model = "vendor/b"
    api_base = "https://other.example.org/v1"


def _page(uid, file_name, body, title="A page"):
    item = epub.EpubHtml(title=title, file_name=file_name, lang="en")
    item.id = uid
    item.content = (
        "<html xmlns='http://www.w3.org/1999/xhtml'>"
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    )
    return item


def _source(identifier="urn:uuid:source-1", language="en"):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Translation metadata fixture")
    book.set_language(language)
    book.add_author("A. Author")
    title = _page("titlepage", "title.xhtml", "<h1>Fixture</h1>", "Title")
    chapter = _page("chapter", "chapter.xhtml", "<p>Body text</p>", "One")
    book.add_item(title)
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", title, chapter]
    book.guide = [{"type": "title-page", "href": "title.xhtml", "title": "Title"}]
    return book


def _rebuild(
    source,
    *,
    model=StubModel,
    disclose=True,
    translation_metadata=False,
    plan_mode=False,
    context_mode="window",
    language="zh-hans",
    glossary_path=None,
):
    """A stamped book, without the cost of a translation run.

    Mirrors what `EPUBBookLoader.__init__` stores and what the CLI sets on
    the loader afterwards — plan mode in particular is set from outside.
    """
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source
    loader.language = language
    # __init__ settles the tag once and everything mechanical reads it from
    # there; a loader built past __init__ has to settle it the same way.
    loader.language_tag = language_code(language)
    loader.single_translate = False
    loader.disclose = disclose
    loader.translation_metadata = translation_metadata
    loader.plan_mode = plan_mode
    loader.context_mode = context_mode
    if glossary_path is not None:
        loader.glossary_path = glossary_path
    loader.translate_model = model() if model else None
    new_book = loader._make_new_book(source)
    # The write routes add the source's items as they finish with them; a
    # rebuild without a translation has to do the same, or there is no
    # document in the book for the credit line to land on.
    for item in source.get_items():
        if not is_our_colophon(item):
            new_book.add_item(item)
    loader._stamp_disclosure(new_book)
    return new_book


def _written(tmp_path, book, name="out.epub"):
    out = tmp_path / name
    epub.write_epub(str(out), book)
    return out


def _opf_of(path):
    with zipfile.ZipFile(path) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return archive.read(opf_name).decode("utf-8")


def _metas(book):
    """Every `bbm:` meta in the book, as {name: content}.

    Nothing writes one any more, so every use of this asserts an empty
    answer — but a book stamped by an older build arrives carrying them, and
    the rerun tests need to see them go.
    """
    found = {}
    for metas in book.metadata.values():
        if not isinstance(metas, dict):
            continue
        for entries in metas.values():
            for entry in entries:
                others = entry[1] if isinstance(entry, tuple) and len(entry) > 1 else {}
                name = (others or {}).get("name") or ""
                if name.startswith(tmeta.PREFIX):
                    found[name] = (others or {}).get("content")
    return found


def _record_of(book):
    """The parsed `bbm_translation_metadata.json` the stamp wrote, or None."""
    item = book.get_item_with_id(tmeta.TRANSLATION_METADATA_ID)
    return None if item is None else json.loads(item.content.decode("utf-8"))


def _credit_of(book):
    """The text of the one credit line, or None."""
    import re

    pattern = re.compile(rf'<p class="{CREDIT_CLASS}"[^>]*>([^<]*)</p>'.encode("utf-8"))
    for item in book.get_items():
        content = getattr(item, "content", None) or b""
        if isinstance(content, str):
            content = content.encode("utf-8")
        found = pattern.findall(content)
        if found:
            return found[0].decode("utf-8")
    return None


def _translate_file(path, model=StubModel, glossary_path=None, **kwargs):
    loader = EPUBBookLoader(
        str(path), model, key="", resume=False, language="zh-hans", **kwargs
    )
    loader.quiet = True
    if glossary_path is not None:
        # The glossary is a separate piece of work; the loader carries the
        # path the user named, whichever branch put it there.
        loader.glossary_path = glossary_path
    loader.make_bilingual_book()
    return path.with_name(f"{path.stem}_bilingual.epub")


GLOSSARY = "sett: 獾穴\nbrock: 獾\n".encode("utf-8")


# ------------------------------------------------------ what gets recorded


def test_the_record_carries_exactly_the_allowed_keys(tmp_path):
    """The owner decision, pinned: four keys and never a fifth. This test is
    meant to fail loudly the day somebody adds one — that is what it is
    for."""
    terms = tmp_path / "terms.txt"
    terms.write_bytes(GLOSSARY)

    record = _record_of(
        _rebuild(_source(), translation_metadata=True, glossary_path=str(terms))
    )

    assert set(record) == {
        tmeta.RECORD_MARK_KEY,
        "model",
        "date",
        tmeta.GLOSSARY_SHA_KEY,
    }
    assert set(record) == set(tmeta.RECORD_KEYS)
    assert record == {
        tmeta.RECORD_MARK_KEY: tmeta.RECORD_MARK,
        "model": "x/y",
        "date": date.today().isoformat(),
        tmeta.GLOSSARY_SHA_KEY: sha256(GLOSSARY).hexdigest(),
    }


def test_nothing_that_identifies_the_operator_is_recorded(tmp_path, monkeypatch):
    """The whole point of the 260906 slim: the command that ran, the build
    that ran it, the host it talked to and the route it took are facts about
    a person and a machine, not about the translation."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_book.py",
            "--book_name",
            "b.epub",
            "--api_base",
            "https://gw.example/v1",
        ],
    )

    book = _rebuild(_source(language="fr"), translation_metadata=True)
    body = json.dumps(_record_of(book))

    for gone in ("commit", "args", "endpoint", "route", "source-lang", "target-lang"):
        assert gone not in _record_of(book), gone
    assert "make_book.py" not in body
    assert "gw.example" not in body
    assert "api.openai.com" not in body
    assert "StubModel" not in body
    assert "zh-hans" not in body and "fr" not in body


def test_the_record_reaches_the_written_file(tmp_path):
    output = _written(tmp_path, _rebuild(_source(), translation_metadata=True))

    with zipfile.ZipFile(output) as archive:
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    assert record["model"] == "x/y"
    assert record["date"] == date.today().isoformat()


def test_the_record_says_which_service_ran_when_there_is_no_model():
    """A route with no model of its own still answers: `model_id` falls back
    to the service the run was selected by."""

    class Modelless(StubModel):
        model = None
        api_base = None

    record = _record_of(_rebuild(_source(), model=Modelless, translation_metadata=True))

    assert record["model"] == "Modelless"


def test_an_absent_fact_is_left_out_of_the_file_rather_than_written_empty():
    """`"model": null` reads like a finding. An absent key does not."""
    record = json.loads(tmeta.TranslationMetadata().record())

    assert record == {tmeta.RECORD_MARK_KEY: tmeta.RECORD_MARK}
    assert None not in record.values() and "" not in record.values()


def test_a_run_with_no_date_records_none():
    """The omission rule reaches the date too: `record()` never reaches for
    a clock of its own, so a caller that hands it nothing gets nothing
    rather than today."""
    bare = tmeta.TranslationMetadata(model="x/y")

    assert "date" not in json.loads(bare.record())


def test_the_package_document_says_nothing_at_all(tmp_path):
    """Zero `bbm:` metas, and no contributor of ours beside them: the
    package metadata is the source's and nothing else."""
    book = _rebuild(_source(), translation_metadata=True)
    opf = _opf_of(_written(tmp_path, book))

    assert _metas(book) == {}
    assert "bbm:" not in opf
    assert "<dc:contributor" not in opf
    assert "<dc:description" not in opf


# ------------------------------------------------------------ nothing secret


SECRET_KEY = "TESTSECRET123"
SECRET_PROMPT = "SECRETPROMPTX"


def test_no_member_of_the_zip_carries_the_key_or_the_prompt(tmp_path, monkeypatch):
    """The load-bearing assertion of this module: a key and a prompt must not
    leave the machine inside a book that gets emailed to a publisher.

    Most of the answer is now structural — the command line is not recorded
    at all — but the model id still is, on the page and in the record, and a
    model id is a string this process was handed. `redact` knows the values
    it was given, so one arriving through that door is masked rather than
    written.
    """
    from book_maker import redaction

    class LeakyModel(StubModel):
        model = f"vendor/{SECRET_KEY}"

    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", "--key", SECRET_KEY, "--prompt", SECRET_PROMPT],
    )
    redaction.remember(SECRET_KEY, SECRET_PROMPT)
    try:
        book = _rebuild(_source(), model=LeakyModel, translation_metadata=True)
        output = _written(tmp_path, book)
        with zipfile.ZipFile(output) as archive:
            members = archive.namelist()
            # the sweep is only meaningful while the record is actually in
            # there
            assert any(
                m.endswith(tmeta.TRANSLATION_METADATA_FILE) for m in members
            ), members
            for member in members:
                body = archive.read(member)
                assert SECRET_KEY.encode() not in body, member
                assert SECRET_PROMPT.encode() not in body, member
            record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    finally:
        redaction.forget_all()

    # what is left in its place, still recognisably a model id
    assert redaction.MASK in record["model"]
    assert record["model"].startswith("vendor/")


def test_the_visible_line_is_redacted_too(tmp_path):
    """The record is not the only place the model id is printed."""
    from book_maker import redaction

    class LeakyModel(StubModel):
        model = f"vendor/{SECRET_KEY}"

    redaction.remember(SECRET_KEY)
    try:
        credit = _credit_of(_rebuild(_source(), model=LeakyModel))
    finally:
        redaction.forget_all()

    assert SECRET_KEY not in credit
    # escaped, because the mask goes onto a page: `<redacted>` written raw
    # would be markup
    assert "&lt;redacted&gt;" in credit


def test_a_key_shaped_glossary_is_still_swept(tmp_path):
    """The glossary is the user's file, embedded verbatim — which is exactly
    why the sweep runs over every member of the zip and not only the
    record."""
    from book_maker import redaction

    terms = tmp_path / "terms.txt"
    terms.write_text(f"note: {SECRET_KEY}\n", encoding="utf-8")
    redaction.remember(SECRET_KEY)
    try:
        book = _rebuild(_source(), translation_metadata=True, glossary_path=str(terms))
        # The glossary travels verbatim by design, so this is the one member
        # that legitimately carries what the user put in it. Asserted rather
        # than swept, so the exception is a decision on the record.
        item = book.get_item_with_id(tmeta.GLOSSARY_ID)
        assert SECRET_KEY.encode() in item.content
    finally:
        redaction.forget_all()


# ---------------------------------------------------------------- the gating


@pytest.mark.parametrize(
    "kwargs,recorded",
    [
        ({}, False),
        ({"translation_metadata": True}, True),
        ({"plan_mode": True}, True),
        ({"context_mode": "session"}, True),
        # the switch that turns off everything the file says about the run
        ({"translation_metadata": True, "disclose": False}, False),
        ({"plan_mode": True, "disclose": False}, False),
    ],
)
def test_who_gets_the_machine_record(kwargs, recorded):
    book = _rebuild(_source(), **kwargs)

    assert (_record_of(book) is not None) is recorded


def test_an_implicit_session_route_gets_the_record_too():
    """The codex route keeps a session without `--use_context session` ever
    being typed; its runs earn the record the same way an explicit session
    run does."""

    class AlwaysSession(StubModel):
        SESSION_CONTEXT_ALWAYS_ON = True

    assert _record_of(_rebuild(_source(), model=AlwaysSession)) is not None


def test_a_legacy_run_still_gets_the_reader_facing_disclosure():
    """Only the machine half is opt-in. The credit line is not: nothing
    about `--translation-metadata` changes what a reader is told."""
    book = _rebuild(_source())

    assert _credit_of(book) == f"{CREDIT_PREFIX}x/y, {date.today().year}."
    assert _record_of(book) is None


def test_the_loader_takes_the_flag(tmp_path):
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())

    loader = EPUBBookLoader(
        str(source),
        StubModel,
        key="",
        resume=False,
        language="zh-hans",
        translation_metadata=True,
    )

    assert loader.translation_metadata is True


def test_the_flag_is_off_by_default(tmp_path):
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())

    loader = EPUBBookLoader(
        str(source), StubModel, key="", resume=False, language="zh-hans"
    )

    assert loader.translation_metadata is False


# --------------------------------------------------------------- the glossary


def _with_glossary(tmp_path, **kwargs):
    path = tmp_path / "terms.txt"
    path.write_bytes(GLOSSARY)
    return _rebuild(
        _source(), translation_metadata=True, glossary_path=str(path), **kwargs
    )


def test_a_user_glossary_travels_verbatim_with_its_checksum(tmp_path):
    """The verbatim pins are the thing that can make a translation say what
    the source does not, so the reviewer gets the bytes, not a summary."""
    book = _with_glossary(tmp_path)

    item = book.get_item_with_id(tmeta.GLOSSARY_ID)
    assert item is not None
    assert item.content == GLOSSARY
    assert item.file_name == tmeta.GLOSSARY_FILE
    assert item.media_type == tmeta.GLOSSARY_MEDIA_TYPE
    # vouched for in the record, which is what a rerun reads back to know
    # the file is a previous run's and not the book's own
    assert _record_of(book)[tmeta.GLOSSARY_SHA_KEY] == sha256(GLOSSARY).hexdigest()


def test_the_glossary_is_in_the_manifest_and_not_in_the_spine(tmp_path):
    book = _with_glossary(tmp_path)

    assert tmeta.GLOSSARY_ID not in [
        getattr(entry, "id", None) for entry in book.spine if not isinstance(entry, str)
    ]
    opf = _opf_of(_written(tmp_path, book))
    assert f'href="{tmeta.GLOSSARY_FILE}"' in opf
    assert f'idref="{tmeta.GLOSSARY_ID}"' not in opf


def test_no_glossary_means_no_item_and_no_checksum():
    book = _rebuild(_source(), translation_metadata=True)

    assert book.get_item_with_id(tmeta.GLOSSARY_ID) is None
    assert tmeta.GLOSSARY_SHA_KEY not in _record_of(book)


def test_a_derived_glossary_is_not_recorded(tmp_path):
    """A glossary the tool built for itself is an artifact of the run: it
    changes between runs of the same command, and stamping it would say the
    user asked for something they did not. Only `glossary_path` — the file a
    person named — is ever embedded."""
    book = _rebuild(_source(), translation_metadata=True)
    # what an auto-derived glossary looks like on the loader: terms, and no
    # file anybody asked for. Applied to a rebuild that already ran, so the
    # assertion is about `_run_translation_metadata` reading only the path.
    assert tmeta.GLOSSARY_SHA_KEY not in _record_of(book)
    assert book.get_item_with_id(tmeta.GLOSSARY_ID) is None

    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = _source()
    loader.language = "zh-hans"
    loader.translation_metadata = True
    loader.translate_model = StubModel()
    loader.glossary = {"sett": "獾穴"}
    loader.glossary_auto = True

    assert loader._run_translation_metadata().glossary_bytes is None


def test_an_unreadable_glossary_costs_a_warning_not_the_book(tmp_path, capsys):
    book = _rebuild(
        _source(),
        translation_metadata=True,
        glossary_path=str(tmp_path / "missing.txt"),
    )

    assert book.get_item_with_id(tmeta.GLOSSARY_ID) is None
    assert tmeta.GLOSSARY_SHA_KEY not in _record_of(book)
    assert "glossary could not be read" in " ".join(capsys.readouterr().out.split())
    # the rest of the record is written all the same
    assert _record_of(book)["model"] == "x/y"


def test_the_glossary_name_is_allocated_against_the_book(tmp_path):
    """A book that already ships `bbm_glossary.txt` keeps it; ours takes the
    next name."""
    source = _source()
    source.add_item(
        epub.EpubItem(
            uid=tmeta.GLOSSARY_ID,
            file_name=tmeta.GLOSSARY_FILE,
            media_type="text/plain",
            content=b"the book's own file",
        )
    )
    book_path = tmp_path / "book.epub"
    epub.write_epub(str(book_path), source)
    terms = tmp_path / "terms.txt"
    terms.write_bytes(GLOSSARY)

    output = _translate_file(
        book_path, translation_metadata=True, glossary_path=str(terms)
    )

    with zipfile.ZipFile(output) as archive:
        assert archive.read(f"EPUB/{tmeta.GLOSSARY_FILE}") == b"the book's own file"
        assert archive.read(f"EPUB/{tmeta.GLOSSARY_STEM}-2.txt") == GLOSSARY
    opf = _opf_of(output)
    assert f'id="{tmeta.GLOSSARY_ID}-2"' in opf


def test_a_books_own_glossary_file_is_never_taken_for_ours(tmp_path):
    """Ownership is the checksum something recognisably ours vouches for,
    not the file name — a book shipping its own `bbm_glossary.txt` keeps it
    through a translation."""
    source = _source()
    source.add_item(
        epub.EpubItem(
            uid="theirs",
            file_name=tmeta.GLOSSARY_FILE,
            media_type="text/plain",
            content=b"the book's own file",
        )
    )
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), source)

    output = _translate_file(path)

    with zipfile.ZipFile(output) as archive:
        assert b"the book's own file" in archive.read(f"EPUB/{tmeta.GLOSSARY_FILE}")


# ------------------------------------------------------- the record file


def test_the_record_file_vouches_for_itself():
    """Nothing in the package names its checksum. It does not need one: the
    mark inside the file says whose it is after a conversion has thrown the
    package document away, which is the whole reason the file exists."""
    book = _rebuild(_source(), translation_metadata=True)

    item = book.get_item_with_id(tmeta.TRANSLATION_METADATA_ID)
    assert item.media_type == tmeta.TRANSLATION_METADATA_MEDIA_TYPE
    assert item.file_name == tmeta.TRANSLATION_METADATA_FILE
    assert _record_of(book)[tmeta.RECORD_MARK_KEY] == tmeta.RECORD_MARK


def test_the_record_file_is_in_the_manifest_and_not_in_the_spine(tmp_path):
    book = _rebuild(_source(), translation_metadata=True)

    assert tmeta.TRANSLATION_METADATA_ID not in [
        getattr(entry, "id", None) for entry in book.spine if not isinstance(entry, str)
    ]
    opf = _opf_of(_written(tmp_path, book))
    assert f'href="{tmeta.TRANSLATION_METADATA_FILE}"' in opf
    assert f'media-type="{tmeta.TRANSLATION_METADATA_MEDIA_TYPE}"' in opf
    assert f'idref="{tmeta.TRANSLATION_METADATA_ID}"' not in opf


def test_a_run_that_records_nothing_writes_no_record_file():
    book = _rebuild(_source())

    assert book.get_item_with_id(tmeta.TRANSLATION_METADATA_ID) is None


def test_the_record_file_name_is_allocated_against_the_book(tmp_path):
    """A book that already ships `bbm_translation_metadata.json` keeps it;
    ours takes the next name, the way the glossary does."""
    source = _source()
    source.add_item(
        epub.EpubItem(
            uid=tmeta.TRANSLATION_METADATA_ID,
            file_name=tmeta.TRANSLATION_METADATA_FILE,
            media_type=tmeta.TRANSLATION_METADATA_MEDIA_TYPE,
            content=b'{"theirs": true}',
        )
    )
    book_path = tmp_path / "book.epub"
    epub.write_epub(str(book_path), source)

    output = _translate_file(book_path, translation_metadata=True)

    with zipfile.ZipFile(output) as archive:
        assert (
            archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}")
            == b'{"theirs": true}'
        )
        ours = json.loads(
            archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_STEM}-2.json")
        )
    assert ours[tmeta.RECORD_MARK_KEY] == tmeta.RECORD_MARK
    assert f'id="{tmeta.TRANSLATION_METADATA_ID}-2"' in _opf_of(output)


def test_a_books_own_translation_metadata_file_is_never_taken_for_ours(tmp_path):
    """The marker inside is the whole ownership test, and a stranger's file
    does not carry it, so a book shipping its own
    `bbm_translation_metadata.json` keeps it through a translation — even one
    that writes no record of its own."""
    source = _source()
    source.add_item(
        epub.EpubItem(
            uid="theirs",
            file_name=tmeta.TRANSLATION_METADATA_FILE,
            media_type=tmeta.TRANSLATION_METADATA_MEDIA_TYPE,
            content=b"not even json",
        )
    )
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), source)

    output = _translate_file(path)

    with zipfile.ZipFile(output) as archive:
        assert (
            archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}") == b"not even json"
        )


def test_the_record_is_still_ours_after_a_conversion_drops_the_package(tmp_path):
    """The case the file exists for. A converter rewrites the package
    document and copies the files across, so the marker inside the record is
    the only thing left saying whose it is; a rerun must still replace it
    rather than shipping two.

    The filename plus the `generator` string is now the *only* recogniser —
    there is no meta beside it any more — which is exactly what this pins.
    """
    source = _source()
    stale = tmeta.TranslationMetadata(model="old/model").record(date(2025, 1, 1))
    source.add_item(
        epub.EpubItem(
            uid="converted-record",
            file_name=tmeta.TRANSLATION_METADATA_FILE,
            media_type=tmeta.TRANSLATION_METADATA_MEDIA_TYPE,
            content=stale,
        )
    )
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), source)

    output = _translate_file(path, translation_metadata=True)

    with zipfile.ZipFile(output) as archive:
        members = [
            m for m in archive.namelist() if tmeta.TRANSLATION_METADATA_STEM in m
        ]
        assert members == [f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"]
        record = json.loads(archive.read(members[0]))
    assert record["model"] == "x/y" and "old/model" not in json.dumps(record)


# ------------------------------------------------------------- rerunning it


def test_a_second_run_leaves_none_of_the_first_runs_record(tmp_path):
    """A book translated by model A and then by model B must not claim both
    models — or carry both glossaries."""
    glossary_one = tmp_path / "one.txt"
    glossary_one.write_bytes(b"first: run\n")

    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    loader = EPUBBookLoader(
        str(source),
        StubModel,
        key="",
        resume=False,
        language="zh-hans",
        translation_metadata=True,
    )
    loader.quiet = True
    loader.glossary_path = str(glossary_one)
    loader.make_bilingual_book()
    once = source.with_name("book_bilingual.epub")

    twice_loader = EPUBBookLoader(
        str(once),
        ModelB,
        key="",
        resume=False,
        language="zh-hans",
        translation_metadata=True,
    )
    twice_loader.quiet = True
    twice_loader.make_bilingual_book()
    twice = once.with_name("book_bilingual_bilingual.epub")

    with zipfile.ZipFile(twice) as archive:
        members = archive.namelist()
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
        title = archive.read("EPUB/title.xhtml").decode("utf-8")

    assert record["model"] == "vendor/b"
    assert "x/y" not in json.dumps(record)
    # the first run's glossary is gone, item and checksum both
    assert not [m for m in members if "bbm_glossary" in m]
    assert tmeta.GLOSSARY_SHA_KEY not in record
    # and the record file is replaced, not accumulated
    assert [m for m in members if tmeta.TRANSLATION_METADATA_STEM in m] == [
        f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"
    ]
    # one line in front of the reader, naming this run
    assert title.count(CREDIT_CLASS) == 1
    assert "vendor/b" in title and "x/y" not in title


# The metas an older build wrote, one per fact. A book stamped by such a
# build is out in the world and a rerun has to clear the whole shape.
OLD_SHAPE_METAS = {
    "bbm:bilingual_book_maker": "aaaaaaa",
    "bbm:commit": "aaaaaaa",
    "bbm:model": "old/model",
    "bbm:date": "2025-01-01",
    "bbm:endpoint": "old.example.org",
    "bbm:route": "OldRoute",
    "bbm:args": "make_book.py --first-run",
    "bbm:source-lang": "en",
    "bbm:target-lang": "ja",
    "bbm:glossary-sha256": sha256(GLOSSARY).hexdigest(),
    "bbm:translation-metadata-sha256": "0" * 64,
}


def _old_shape_book(tmp_path):
    """A book on disk carrying the meta-per-fact shape, the two contributor
    credits, the description, the closing page, its glossary and its record —
    everything a build before this ruling left behind."""
    source = _source()
    for name, content in OLD_SHAPE_METAS.items():
        source.add_metadata("OPF", "meta", None, {"name": name, "content": content})
    source.add_metadata("DC", "contributor", "bilingual_book_maker", {"id": "bbm-trl"})
    source.add_metadata(
        None,
        "meta",
        "trl",
        {"refines": "#bbm-trl", "property": "role", "scheme": "marc:relators"},
    )
    source.add_metadata(
        "DC", "contributor", "bilingual_book_maker aaaaaaa", {"id": "bbm-bkp"}
    )
    source.add_metadata(
        None,
        "meta",
        "bkp",
        {"refines": "#bbm-bkp", "property": "role", "scheme": "marc:relators"},
    )
    source.add_metadata(
        "DC",
        "description",
        "AI translation (old/model, 2025).\n"
        "Original text unaltered; translation quality not verified.",
    )
    colophon = epub.EpubHtml(
        title="Translation note", file_name="bbm_translation_note.xhtml", lang="en"
    )
    colophon.id = "bbm-translation-note"
    colophon.content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n  <head>\n'
        "    <title>Translation note</title>\n"
        '    <meta name="generator" content="bilingual_book_maker translation '
        'note"/>\n  </head>\n  <body>\n    <h1>Translation Credits</h1>\n'
        "    <p>Model: old/model</p>\n  </body>\n</html>\n"
    ).encode("utf-8")
    source.add_item(colophon)
    source.spine.append(colophon)
    source.add_item(
        epub.EpubItem(
            uid=tmeta.GLOSSARY_ID,
            file_name=tmeta.GLOSSARY_FILE,
            media_type=tmeta.GLOSSARY_MEDIA_TYPE,
            content=GLOSSARY,
        )
    )
    source.add_item(
        epub.EpubItem(
            uid=tmeta.TRANSLATION_METADATA_ID,
            file_name=tmeta.TRANSLATION_METADATA_FILE,
            media_type=tmeta.TRANSLATION_METADATA_MEDIA_TYPE,
            content=json.dumps(
                {
                    tmeta.RECORD_MARK_KEY: tmeta.RECORD_MARK,
                    "commit": "aaaaaaa",
                    "model": "old/model",
                    "endpoint": "old.example.org",
                    "args": "make_book.py --first-run",
                    tmeta.GLOSSARY_SHA_KEY: sha256(GLOSSARY).hexdigest(),
                }
            ).encode("utf-8"),
        )
    )
    path = tmp_path / "old.epub"
    epub.write_epub(str(path), source)
    return path


def test_a_rerun_clears_every_trace_an_older_build_left(tmp_path):
    """The shape changed; the ownership rule did not. A rerun over a book
    stamped by a build that wrote a meta per fact, two contributors, a
    description and a closing page must leave none of them — and must end
    with one record and one credit line, this run's."""
    output = _translate_file(
        _old_shape_book(tmp_path), model=ModelB, translation_metadata=True
    )

    opf = _opf_of(output)
    for name in OLD_SHAPE_METAS:
        assert f'name="{name}"' not in opf, name
    assert "bbm:" not in opf
    assert "<dc:contributor" not in opf
    assert "<dc:description" not in opf
    assert "old/model" not in opf and "aaaaaaa" not in opf

    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
        title = archive.read("EPUB/title.xhtml").decode("utf-8")
    # one record, replaced rather than accumulated, and the old glossary the
    # legacy meta vouched for went with it
    assert [m for m in members if tmeta.TRANSLATION_METADATA_STEM in m] == [
        f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"
    ]
    assert not [m for m in members if tmeta.GLOSSARY_STEM in m]
    assert not [m for m in members if "translation_note" in m]
    assert record["model"] == "vendor/b"
    assert "old/model" not in json.dumps(record)
    assert title.count(CREDIT_CLASS) == 1


def test_the_legacy_glossary_meta_still_vouches_for_a_glossary(tmp_path):
    """The one meta an older build wrote that is still *read*: the embedded
    glossary carries no marker of its own, so on a book stamped before the
    record carried the checksum, `bbm:glossary-sha256` is the only thing
    saying the file was ours to drop."""
    from book_maker.loader.disclosure import prior_glossary_shas

    source = _source()
    source.add_metadata(
        "OPF",
        "meta",
        None,
        {
            "name": tmeta.LEGACY_GLOSSARY_SHA_META,
            "content": sha256(GLOSSARY).hexdigest(),
        },
    )
    path = tmp_path / "legacy.epub"
    epub.write_epub(str(path), source)

    assert prior_glossary_shas(epub.read_epub(str(path))) == {
        sha256(GLOSSARY).hexdigest()
    }


def test_a_rerun_without_the_record_strips_the_previous_one(tmp_path):
    """Ours to rewrite means ours to remove: a plain second pass must not
    leave the first run's record standing as though it described this
    file."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source, translation_metadata=True)

    twice = _translate_file(once, model=ModelB)

    with zipfile.ZipFile(twice) as archive:
        assert not [
            m for m in archive.namelist() if tmeta.TRANSLATION_METADATA_STEM in m
        ]
        title = archive.read("EPUB/title.xhtml").decode("utf-8")
    # the reader-facing disclosure is still written
    assert title.count(CREDIT_CLASS) == 1


# ------------------------------------------------------------- one clock


def test_the_line_and_the_record_name_one_day():
    """The date is settled once, in `stamp_disclosure`, and handed to both —
    so a run that straddles midnight cannot print one day to the reader and
    record another. Injected the way the code takes it, rather than patched:
    `when` is already the seam."""
    from book_maker.loader import disclosure

    frozen = date(2026, 9, 6)
    book = _rebuild(_source(), disclose=False)

    disclosure.stamp_disclosure(
        book,
        "x/y",
        when=frozen,
        translation_metadata=tmeta.TranslationMetadata(model="x/y"),
    )

    assert _credit_of(book) == f"{CREDIT_PREFIX}x/y, {frozen.year}."
    assert _record_of(book)["date"] == frozen.isoformat()


# ------------------------------------------------------- through the CLI


REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
HERMETIC = Path(__file__).resolve().parent / "hermetic"


def _cli(tmp_path, *args):
    env = dict(os.environ)
    for name in ("BBM_API_KEY", "OPENAI_API_KEY", "BBM_OPENAI_API_KEY"):
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(src),
            "--api_format",
            "google",
            "--test",
            "--test_num",
            "1",
            "--quiet",
            *args,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc, src.with_name(f"{src.stem}_bilingual.epub")


def _credit_count(path):
    total = 0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            total += archive.read(name).count(CREDIT_CLASS.encode("utf-8"))
    return total


def test_the_flag_records_the_run_in_a_real_translation(tmp_path):
    proc, output = _cli(tmp_path, "--translation-metadata")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    opf = _opf_of(output)
    assert "bbm:" not in opf
    with zipfile.ZipFile(output) as archive:
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    assert set(record) == {tmeta.RECORD_MARK_KEY, "model", "date"}
    assert record["model"] == "google"
    assert _credit_count(output) == 1


def test_the_cli_forwards_the_glossary_into_the_record(tmp_path):
    """The unit tests hand `glossary_path` to the loader directly; only a
    real CLI run proves the flag actually reaches it."""
    terms = tmp_path / "terms.txt"
    terms.write_text("Manor Farm → 庄园农场\n", encoding="utf-8")
    proc, output = _cli(tmp_path, "--translation-metadata", "--glossary", str(terms))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    with zipfile.ZipFile(output) as z:
        record = json.loads(z.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
        embedded = [n for n in z.namelist() if tmeta.GLOSSARY_STEM in n]
        assert embedded
        body = z.read(embedded[0])
    assert "Manor Farm" in body.decode("utf-8")
    assert record[tmeta.GLOSSARY_SHA_KEY] == sha256(body).hexdigest()


def test_a_plain_legacy_run_records_nothing(tmp_path):
    """The tag-mode default. The reader still gets the line; a machine gets
    nothing it was not asked for."""
    proc, output = _cli(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    with zipfile.ZipFile(output) as archive:
        assert not [
            m for m in archive.namelist() if tmeta.TRANSLATION_METADATA_STEM in m
        ]
    assert _credit_count(output) == 1


def test_a_session_run_records_it_without_being_asked(tmp_path):
    proc, output = _cli(tmp_path, "--use_context", "session")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    with zipfile.ZipFile(output) as archive:
        assert f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}" in archive.namelist()


def test_the_switch_that_silences_the_line_silences_the_record_too(tmp_path):
    proc, output = _cli(tmp_path, "--translation-metadata", "--no_disclosure")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    with zipfile.ZipFile(output) as archive:
        assert not [
            m for m in archive.namelist() if tmeta.TRANSLATION_METADATA_STEM in m
        ]
    assert _credit_count(output) == 0


def test_the_credit_line_lands_on_the_real_books_title_page(tmp_path):
    """animal_farm.epub is calibre output with an EPUB 2 guide: the line
    goes where the guide says, once."""
    proc, output = _cli(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    with zipfile.ZipFile(output) as archive:
        carrying = [
            name
            for name in archive.namelist()
            if CREDIT_CLASS.encode("utf-8") in archive.read(name)
        ]
    assert len(carrying) == 1, carrying


def test_the_flag_says_so_when_the_book_has_nowhere_to_record_it(tmp_path):
    """Only an epub has a package document. A flag that silently does
    nothing is worse than one that is refused out loud."""
    src = tmp_path / "the_little_prince.txt"
    src.write_text("Hello there.\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(src),
            "--api_format",
            "google",
            "--test",
            "--test_num",
            "1",
            "--translation-metadata",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )

    said = " ".join(proc.stdout.split())
    assert "--translation-metadata records the run in the package document" in said
    assert "only an epub has one" in said
