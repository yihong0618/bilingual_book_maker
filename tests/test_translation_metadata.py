"""The file says which run made it, to a machine.

The disclosure tests cover what a *reader* is told. This covers the other
half: `bbm_translation_metadata.json`, the three `bbm:` metas beside it, the
book-producer credit and the embedded user glossary — the record someone
runs a script over six months later when they need to know which build,
which model and which command produced a directory full of epubs.

The two forms are deliberately different shapes and the tests keep them
apart: the file carries the whole fact set, the metas carry a marker, the
model and the date. Asserting a fact of the record therefore means reading
the record.

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
from book_maker.loader.disclosure import COLOPHON_FILE, COLOPHON_ID, TOOL_NAME
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


def _source(identifier="urn:uuid:source-1", language="en"):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Translation metadata fixture")
    book.set_language(language)
    book.add_author("A. Author")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang=language)
    chapter.content = "<html><body><p>Body text</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
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
    api_base=None,
    source_lang="auto",
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
    loader._api_base = api_base
    loader._source_lang = source_lang
    if glossary_path is not None:
        loader.glossary_path = glossary_path
    loader.translate_model = model() if model else None
    new_book = loader._make_new_book(source)
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
    """Every `bbm:` meta in the book, as {name: content}."""
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
    """The parsed `bbm_translation_metadata.json` the stamp wrote, or None.

    Where every fact but the marker, the model and the date now lives, so
    this — not `_metas` — is what a test asking "what did the run record"
    reads.
    """
    item = book.get_item_with_id(tmeta.TRANSLATION_METADATA_ID)
    return None if item is None else json.loads(item.content.decode("utf-8"))


def _recorded_args(book):
    return _record_of(book)["args"]


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


# ------------------------------------------------------ what gets recorded


def test_the_record_says_which_build_model_and_host_made_it(tmp_path, monkeypatch):
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")

    record = _record_of(_rebuild(_source(), translation_metadata=True))

    assert record["commit"] == "deadbee"
    assert record["model"] == "x/y"
    # host only: no scheme, no path, no port, no credentials
    assert record["endpoint"] == "api.openai.com"
    assert record["target-lang"] == "zh-hans"
    assert record["source-lang"] == "en"
    assert record["route"] == "StubModel"


def test_the_package_carries_three_metas_and_no_more(tmp_path, monkeypatch):
    """A `bbm:` meta is stripped by any editor that touches the package
    document, so a meta per fact would be an audit trail that evaporates on
    contact. Three: the tool and its build, the model, the date. Everything
    else is in the file, which a conversion copies across."""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")

    metas = _metas(_rebuild(_source(), translation_metadata=True))

    assert metas == {
        tmeta.MARKER_META: "deadbee",
        tmeta.MODEL_META: "x/y",
        tmeta.DATE_META: date.today().isoformat(),
    }


def test_no_other_bbm_name_reaches_a_fresh_books_package_document(
    tmp_path, monkeypatch
):
    """Swept over the written OPF rather than over the in-memory book: the
    assertion is about what leaves the machine, and a name that only the
    writer adds would pass a book-level check."""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")

    opf = _opf_of(_written(tmp_path, _rebuild(_source(), translation_metadata=True)))

    assert opf.count('name="bbm:') == 3
    assert '<meta name="bbm:bilingual_book_maker" content="deadbee"/>' in opf
    assert '<meta name="bbm:model" content="x/y"/>' in opf
    assert f'<meta name="bbm:date" content="{date.today().isoformat()}"/>' in opf


def test_the_record_reaches_the_written_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")

    output = _written(tmp_path, _rebuild(_source(), translation_metadata=True))

    with zipfile.ZipFile(output) as archive:
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    assert record["commit"] == "deadbee"
    assert record["model"] == "x/y"
    assert record["endpoint"] == "api.openai.com"


def test_the_producer_credit_names_the_build(tmp_path, monkeypatch):
    """A second contributor, `bkp`, beside the `trl` one: what translated the
    text and what built the file are different claims."""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")

    opf = _opf_of(_written(tmp_path, _rebuild(_source(), translation_metadata=True)))

    assert f">{TOOL_NAME} deadbee</dc:contributor>" in opf
    assert f'<dc:contributor id="{tmeta.PRODUCER_ID}">' in opf
    assert (
        f'<meta refines="#{tmeta.PRODUCER_ID}" property="role" '
        'scheme="marc:relators">bkp</meta>' in opf
    )


def test_a_build_that_cannot_be_named_still_credits_the_tool(monkeypatch):
    monkeypatch.setattr(tmeta, "tool_commit", lambda: tmeta.UNKNOWN)

    book = _rebuild(_source(), translation_metadata=True)
    credits = [value for value, _ in book.get_metadata("DC", "contributor")]

    # the marker is always written, build or no build
    assert _metas(book)[tmeta.MARKER_META] == tmeta.UNKNOWN
    assert _record_of(book)["commit"] == tmeta.UNKNOWN
    # the bare name, not "bilingual_book_maker unknown"
    assert credits.count(TOOL_NAME) == 2


def test_the_marker_is_written_even_when_nothing_else_is_known(tmp_path, monkeypatch):
    """The marker is the one meta that is never conditional: it is what makes
    the package recognisably this tool's at a glance, and a run that cannot
    name its build says `unknown` rather than saying nothing. (A route with
    no model id of its own still fills `bbm:model` — `model_id` falls back to
    the service name — so the modelless case is reached here directly.)"""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")

    assert dict(tmeta.TranslationMetadata(commit="deadbee").metas()) == {
        tmeta.MARKER_META: "deadbee"
    }
    assert dict(tmeta.TranslationMetadata().metas()) == {
        tmeta.MARKER_META: tmeta.UNKNOWN
    }

    # and the modelless route, end to end, names its service there
    class Modelless(StubModel):
        model = None
        api_base = None

    metas = _metas(_rebuild(_source(), model=Modelless, translation_metadata=True))
    assert metas[tmeta.MARKER_META] == "deadbee"
    assert metas[tmeta.MODEL_META] == "Modelless"


def test_the_endpoint_is_omitted_when_the_route_has_no_endpoint():
    """Google Translate takes no key, no model and no base. Recording an
    empty endpoint would look like an answer; recording none is the answer."""

    class Modelless(StubModel):
        model = None
        api_base = None

    record = _record_of(_rebuild(_source(), model=Modelless, translation_metadata=True))

    assert "endpoint" not in record
    # what did answer is still on the record
    assert record["route"] == "Modelless"
    assert record["commit"]


def test_the_flag_supplies_the_host_when_the_translator_does_not():
    class NoBase(StubModel):
        api_base = None

    record = _record_of(
        _rebuild(
            _source(),
            model=NoBase,
            translation_metadata=True,
            api_base="https://127.0.0.1:8765/v1",
        )
    )

    assert record["endpoint"] == "127.0.0.1"


@pytest.mark.parametrize(
    "given,expected",
    [
        ("https://api.openai.com/v1", "api.openai.com"),
        ("https://api.openai.com/v1/chat", "api.openai.com"),
        ("https://user:pw@api.openai.com:8443/v1", "api.openai.com"),
        ("http://127.0.0.1:8765/v1", "127.0.0.1"),
        ("api.openai.com/v1", "api.openai.com"),
        ("https://API.OpenAI.com/v1", "api.openai.com"),
        ("", None),
        (None, None),
        ("   ", None),
    ],
)
def test_only_the_host_survives_the_endpoint(given, expected):
    assert tmeta.endpoint_host(given) == expected


# ------------------------------------------------------- the source language


def test_the_flagged_source_language_wins_over_the_books_own():
    record = _record_of(
        _rebuild(_source(language="fr"), translation_metadata=True, source_lang="de")
    )

    assert record["source-lang"] == "de"


def test_auto_is_not_a_source_language():
    """`--source_lang auto` is the default and states nothing; the book's own
    declaration is a fact and stands in for it."""
    record = _record_of(
        _rebuild(_source(language="fr"), translation_metadata=True, source_lang="auto")
    )

    assert record["source-lang"] == "fr"


def test_a_book_that_declares_no_language_gets_no_source_key():
    source = _source()
    source.metadata[DC_NS].pop("language", None)

    record = _record_of(_rebuild(source, translation_metadata=True))

    assert "source-lang" not in record
    assert record["target-lang"] == "zh-hans"


def test_the_translation_carries_both_languages():
    """A bilingual book legitimately has two `dc:language`s. The target comes
    first — a reading system takes the first as the book's own — and the
    source stays behind it, where a library still finds it."""
    book = _rebuild(_source(language="en"), translation_metadata=True)

    languages = [value for value, _ in book.get_metadata("DC", "language")]

    assert languages[0] == "zh-hans"
    assert "en" in languages[1:]


# ---------------------------------------------------------- the command line


def test_the_command_is_recorded_in_the_shape_it_was_run(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", "--book_name", "b.epub", "--test", "--accumulated_num", "12"],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "--book_name b.epub" in args
    assert "--test" in args
    assert "--accumulated_num 12" in args


SECRET_KEY = "TESTSECRET123"
SECRET_PROMPT = "SECRETPROMPTX"


@pytest.mark.parametrize(
    "argv",
    [
        ["make_book.py", "--key", SECRET_KEY, "--prompt", SECRET_PROMPT],
        ["make_book.py", f"--key={SECRET_KEY}", f"--prompt={SECRET_PROMPT}"],
        # argparse accepts any unambiguous abbreviation, and so must this
        ["make_book.py", "--ke", SECRET_KEY, "--promp", SECRET_PROMPT],
    ],
)
def test_no_member_of_the_zip_carries_the_key_or_the_prompt(
    tmp_path, monkeypatch, argv
):
    """The load-bearing assertion of this whole module: whatever else the
    record says, a key and a prompt must not leave the machine inside a book
    that gets emailed to a publisher."""
    monkeypatch.setattr(sys, "argv", argv)

    output = _written(tmp_path, _rebuild(_source(), translation_metadata=True))

    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        # the record file repeats the command line, so the sweep below is
        # only meaningful while it is actually in there
        assert any(
            m.endswith(tmeta.TRANSLATION_METADATA_FILE) for m in members
        ), members
        for member in members:
            body = archive.read(member)
            assert SECRET_KEY.encode() not in body, member
            assert SECRET_PROMPT.encode() not in body, member
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    # what is left in their place, in the record that carries the command
    assert tmeta.MASK in record["args"]


def test_a_token_shaped_value_goes_even_where_no_flag_explains_it(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--extra_body", "sk-loose12345"])

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "sk-loose" not in args
    assert tmeta.MASK in args


def test_a_key_nested_inside_a_recordable_value_is_masked(monkeypatch):
    """codex review 260905 (P1): a credential *inside* a valid argument — an
    `--extra_body` field, a URL's userinfo — starts with no token prefix and
    announces no flag, so both earlier nets walked straight past it."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_book.py",
            "--extra_body",
            '{"api_key": "sk-secondary-secret99"}',
            "--api_base",
            "https://user:hunter2secret@gateway.example/v1",
        ],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "sk-secondary-secret99" not in args
    assert "hunter2secret" not in args
    # the shape still reads: the field name and the host are the record
    assert "api_key" in args
    assert "gateway.example/v1" in args


def test_a_secret_named_field_is_masked_whatever_its_value_looks_like(
    monkeypatch,
):
    """Reverify finding 260906: `{"api_key": "secondary-secret"}` carries no
    token prefix for the shape net; the field *name* is the evidence."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", "--extra_body", '{"api_key": "secondary-secret"}'],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "secondary-secret" not in args
    assert "api_key" in args


def test_a_quote_inside_a_secret_value_does_not_leak_its_tail(monkeypatch):
    """Reverify round 2, 260906: the field regex was quote-blind, so an
    apostrophe inside a double-quoted JSON value ended the match early and
    `secondary-secret` survived. A value that parses as JSON is now walked
    as JSON, not matched by regex."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_book.py",
            "--extra_body",
            '{"api_key": "prefix\'secondary-secret", "note": "kept"}',
        ],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "secondary-secret" not in args
    assert "api_key" in args
    assert "kept" in args  # non-secret fields still travel


def test_an_escaped_quote_inside_a_secret_value_does_not_leak(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", "--extra_body", '{"token": "a\\"b-secondary-secret"}'],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "secondary-secret" not in args


def test_a_joined_flag_with_a_json_value_is_walked_too(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", '--extra_body={"password": "pre\'fix-secret"}'],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "fix-secret" not in args
    assert "--extra_body" in args


def test_a_token_prefix_inside_a_word_is_not_a_token(monkeypatch):
    """Reverify finding 260906: `desk-notes.epub` contains `sk-notes` and
    the unanchored pass recorded the book argument as `de<redacted>` —
    corrupting exactly the nonsecret shape the record exists to keep."""
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--book_name", "desk-notes.epub"])

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "desk-notes.epub" in args
    assert tmeta.MASK not in args


def test_a_bearer_value_nested_in_a_field_is_masked(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", "--extra_body", '{"auth": "Bearer abc123def456"}'],
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "abc123def456" not in args


def test_a_model_id_is_not_mistaken_for_a_token(monkeypatch):
    """Opaque and long is not the test — a model id is both, and masking it
    would empty the record at the moment it matters."""
    monkeypatch.setattr(
        sys, "argv", ["make_book.py", "--model", "claude-haiku-4-5-20251001"]
    )

    args = _recorded_args(_rebuild(_source(), translation_metadata=True))

    assert "claude-haiku-4-5-20251001" in args


def test_a_registered_key_is_masked_wherever_it_appears(monkeypatch):
    """The last net: `redact` knows the values this process was handed, so a
    key in a position no flag rule looked at still does not travel."""
    from book_maker import redaction

    monkeypatch.setattr(
        sys, "argv", ["make_book.py", "--api_base", "https://h/ABCDEFGHIJ"]
    )
    redaction.remember("ABCDEFGHIJ")
    try:
        args = _recorded_args(_rebuild(_source(), translation_metadata=True))
    finally:
        redaction.forget_all()

    assert "ABCDEFGHIJ" not in args


def test_every_key_flag_the_rerun_line_knows_is_a_key_flag_here_too():
    """`epub_loader.KEY_FLAG_ENV` and `SECRET_VALUE_FLAGS` are two lists of
    the same thing — every flag whose value is a credential. A flag added to
    one and not the other is a key printed, or written into a book."""
    from book_maker.loader.epub_loader import KEY_FLAG_ENV

    for flag in KEY_FLAG_ENV:
        assert tmeta.is_secret_flag(flag), flag


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
    metas = _metas(_rebuild(_source(), **kwargs))

    assert bool(metas) is recorded


def test_an_implicit_session_route_gets_the_record_too():
    """codex review 260905 (P2): the codex route keeps a session without
    `--use_context session` ever being typed; its runs earn the record the
    same way an explicit session run does."""

    class AlwaysSession(StubModel):
        SESSION_CONTEXT_ALWAYS_ON = True

    assert _metas(_rebuild(_source(), model=AlwaysSession))


def test_a_record_that_fails_halfway_leaves_nothing_behind(tmp_path, monkeypatch):
    """The machine half is written under the same all-or-nothing rule as the
    rest of the stamp: the caller is entitled to write the book after a
    failure, and a package carrying half a record is worse than one carrying
    none."""
    from book_maker.loader import disclosure

    terms = tmp_path / "terms.txt"
    terms.write_bytes(GLOSSARY)

    def boom(*args, **kwargs):
        raise RuntimeError("cannot build the note")

    monkeypatch.setattr(disclosure, "build_colophon", boom)
    book = _rebuild(_source(), disclose=False)
    before = len(book.spine)

    with pytest.raises(RuntimeError):
        disclosure.stamp_disclosure(
            book,
            "x/y",
            "zh-hans",
            translation_metadata=tmeta.TranslationMetadata(
                commit="deadbee", glossary_bytes=GLOSSARY
            ),
        )

    assert not _metas(book)
    assert not book.get_metadata("DC", "contributor")
    assert book.get_item_with_id(tmeta.GLOSSARY_ID) is None
    assert len(book.spine) == before


def test_a_legacy_run_still_gets_the_reader_facing_disclosure():
    """Only the machine half is opt-in. The colophon and the credit are not:
    nothing about `--translation-metadata` changes what a reader is told."""
    book = _rebuild(_source())

    assert book.get_item_with_id(COLOPHON_ID) is not None
    assert book.get_metadata("DC", "contributor")
    assert not _metas(book)


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


GLOSSARY = "sett: 獾穴\nbrock: 獾\n".encode("utf-8")


def _with_glossary(tmp_path, **kwargs):
    path = tmp_path / "terms.txt"
    path.write_bytes(GLOSSARY)
    return _rebuild(
        _source(), translation_metadata=True, glossary_path=str(path), **kwargs
    )


def test_a_user_glossary_travels_verbatim_with_its_checksum(tmp_path):
    """The instruction the translation obeyed is evidence, so the reviewer
    gets the bytes, not a summary of them."""
    book = _with_glossary(tmp_path)

    item = book.get_item_with_id(tmeta.GLOSSARY_ID)
    assert item is not None
    assert item.content == GLOSSARY
    assert item.file_name == tmeta.GLOSSARY_FILE
    assert item.media_type == tmeta.GLOSSARY_MEDIA_TYPE
    # vouched for in the record, which is what a rerun reads back to know
    # the file is a previous run's and not the book's own
    assert _record_of(book)[tmeta.GLOSSARY_SHA_KEY] == sha256(GLOSSARY).hexdigest()
    # never as a meta: the metas are the marker, not the record
    assert tmeta.LEGACY_GLOSSARY_SHA_META not in _metas(book)


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
    person named — is ever embedded, so a run holding a derived glossary and
    no path records nothing."""
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = _source()
    loader.language = "zh-hans"
    loader.single_translate = False
    loader.disclose = True
    loader.translation_metadata = True
    loader.plan_mode = False
    loader.context_mode = "window"
    loader._api_base = None
    loader._source_lang = "auto"
    loader.translate_model = StubModel()
    # what an auto-derived glossary looks like on the loader: terms, and no
    # file anybody asked for
    loader.glossary = {"sett": "獾穴"}
    loader.glossary_auto = True
    book = loader._make_new_book(loader.origin_book)
    loader._stamp_disclosure(book)

    assert tmeta.GLOSSARY_SHA_KEY not in _record_of(book)
    assert book.get_item_with_id(tmeta.GLOSSARY_ID) is None
    assert "獾穴" not in _opf_of(_written(tmp_path, book))


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
    assert _metas(book)[tmeta.MODEL_META] == "x/y"


def test_the_glossary_name_is_allocated_against_the_book(tmp_path):
    """A book that already ships `bbm_glossary.txt` keeps it; ours takes the
    next name, the way the colophon does."""
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


# ------------------------------------------------------- the record file


def test_the_record_file_carries_the_whole_fact_set(tmp_path, monkeypatch):
    """The two forms were once a literal mirror — every key a meta with the
    `bbm:` prefix dropped — and this test said so. That contract is retired:
    the metas are a three-entry marker and the file is the record, because a
    meta set is stripped by the first editor to open the package and a
    manifest item is not. So the file is asserted whole, on its own terms,
    and the three facts it shares with the metas are checked for agreement
    rather than for identity."""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "deadbee")
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--book_name", "b.epub"])
    terms = tmp_path / "terms.txt"
    terms.write_bytes(GLOSSARY)

    book = _rebuild(
        _source(language="en"),
        translation_metadata=True,
        glossary_path=str(terms),
    )
    record = _record_of(book)
    metas = _metas(book)

    assert record == {
        tmeta.RECORD_MARK_KEY: tmeta.RECORD_MARK,
        "commit": "deadbee",
        "model": "x/y",
        "date": date.today().isoformat(),
        "endpoint": "api.openai.com",
        "route": "StubModel",
        "args": "make_book.py --book_name b.epub",
        "source-lang": "en",
        "target-lang": "zh-hans",
        tmeta.GLOSSARY_SHA_KEY: sha256(GLOSSARY).hexdigest(),
    }
    # what the marker half says, it says the same way
    assert metas[tmeta.MARKER_META] == record["commit"]
    assert metas[tmeta.MODEL_META] == record["model"]
    assert metas[tmeta.DATE_META] == record["date"]


def test_the_record_file_vouches_for_itself():
    """Nothing in the package names its checksum any more. It does not need
    one: a `bbm:translation-metadata-sha256` meta could only guarantee the bytes while
    the metas survived, and the mark inside the file says whose it is after
    they have not."""
    book = _rebuild(_source(), translation_metadata=True)

    item = book.get_item_with_id(tmeta.TRANSLATION_METADATA_ID)
    assert item.media_type == tmeta.TRANSLATION_METADATA_MEDIA_TYPE
    assert item.file_name == tmeta.TRANSLATION_METADATA_FILE
    assert _record_of(book)[tmeta.RECORD_MARK_KEY] == tmeta.RECORD_MARK
    assert not [name for name in _metas(book) if "sha256" in name]


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
    assert not _metas(book)


def test_an_absent_fact_is_left_out_of_the_file_rather_than_written_empty():
    """`"endpoint": null` reads like a finding. An absent key does not."""

    class Modelless(StubModel):
        api_base = None

    book = _rebuild(_source(), model=Modelless, translation_metadata=True)

    record = _record_of(book)
    assert "endpoint" not in record
    assert None not in record.values() and "" not in record.values()
    # the model was known, so the marker half still names it
    assert _metas(book)[tmeta.MODEL_META] == record["model"]


def test_the_record_file_carries_the_glossary_checksum(tmp_path):
    book = _with_glossary(tmp_path)

    assert _record_of(book)[tmeta.GLOSSARY_SHA_KEY] == sha256(GLOSSARY).hexdigest()


def test_the_record_file_name_is_allocated_against_the_book(tmp_path):
    """A book that already ships `bbm_translation_metadata.json` keeps it; ours takes
    the next name, the way the colophon and the glossary do."""
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
    does not carry it, so a book shipping its own `bbm_translation_metadata.json` keeps
    it through a translation — even one that writes no record of its own."""
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


def test_the_record_is_still_ours_after_a_conversion_drops_the_metas(tmp_path):
    """The case the file exists for. A converter rewrites the package
    document — every `bbm:` meta with it — and copies the files across, so
    the marker inside the record is the only thing left saying whose it is;
    a rerun must still replace it rather than shipping two."""
    source = _source()
    stale = tmeta.TranslationMetadata(commit="aaaaaaa", model="old/model").record()
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


def test_a_second_run_leaves_none_of_the_first_runs_record(tmp_path, monkeypatch):
    """Findings the disclosure module already pins, applied to the machine
    half: a book translated by model A and then by model B must not claim
    both builds, both models, both hosts — or carry both glossaries."""
    glossary_one = tmp_path / "one.txt"
    glossary_one.write_bytes(b"first: run\n")
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "aaaaaaa")
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--first-run"])

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

    monkeypatch.setattr(tmeta, "tool_commit", lambda: "bbbbbbb")
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--second-run"])
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

    opf = _opf_of(twice)
    with zipfile.ZipFile(twice) as archive:
        members = archive.namelist()

    with zipfile.ZipFile(twice) as archive:
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))

    # the marker half: one of each, this run's answers only
    assert opf.count(f'name="{tmeta.MARKER_META}"') == 1
    assert opf.count(f'name="{tmeta.MODEL_META}"') == 1
    assert "bbbbbbb" in opf and "aaaaaaa" not in opf
    assert "vendor/b" in opf and "x/y" not in opf
    assert opf.count(f'id="{tmeta.PRODUCER_ID}"') == 1
    # the record half, where the rest of the facts live now
    assert record["commit"] == "bbbbbbb" and record["model"] == "vendor/b"
    assert record["endpoint"] == "other.example.org"
    assert "--second-run" in record["args"] and "--first-run" not in record["args"]
    assert "api.openai.com" not in json.dumps(record)
    # the first run's glossary is gone, item and checksum both
    assert not [m for m in members if "bbm_glossary" in m]
    assert tmeta.GLOSSARY_SHA_KEY not in record
    # and the record file is replaced, not accumulated
    assert [m for m in members if tmeta.TRANSLATION_METADATA_STEM in m] == [
        f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"
    ]


# The metas an older build wrote, one per fact. A book stamped by such a
# build is out in the world and a rerun has to clear the whole shape, not
# only the three names this build writes.
OLD_SHAPE_METAS = {
    "bbm:commit": "aaaaaaa",
    "bbm:model": "old/model",
    "bbm:endpoint": "old.example.org",
    "bbm:route": "OldRoute",
    "bbm:args": "make_book.py --first-run",
    "bbm:source-lang": "en",
    "bbm:target-lang": "ja",
    "bbm:glossary-sha256": sha256(GLOSSARY).hexdigest(),
    "bbm:translation-metadata-sha256": "0" * 64,
}


def _old_shape_book(tmp_path):
    """A book on disk carrying the meta-per-fact shape, its glossary and its
    record — what a build before this one left behind."""
    source = _source()
    for name, content in OLD_SHAPE_METAS.items():
        source.add_metadata("OPF", "meta", None, {"name": name, "content": content})
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
                    **{
                        name[len(tmeta.PREFIX) :]: content
                        for name, content in OLD_SHAPE_METAS.items()
                        if name != "bbm:translation-metadata-sha256"
                    },
                }
            ).encode("utf-8"),
        )
    )
    path = tmp_path / "old.epub"
    epub.write_epub(str(path), source)
    return path


def test_a_rerun_clears_every_meta_an_older_build_wrote(tmp_path, monkeypatch):
    """The shape changed; the ownership rule did not. A rerun over a book
    stamped by a build that wrote a meta per fact must leave none of them —
    the removal is by the `bbm:` prefix, not by the list this build happens
    to write — and must end with one record, this run's."""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "bbbbbbb")
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--second-run"])

    output = _translate_file(
        _old_shape_book(tmp_path), model=ModelB, translation_metadata=True
    )

    opf = _opf_of(output)
    for name in OLD_SHAPE_METAS:
        if name != tmeta.MODEL_META:
            assert f'name="{name}"' not in opf, name
    assert opf.count('name="bbm:') == 3
    assert f'<meta name="{tmeta.MARKER_META}" content="bbbbbbb"/>' in opf
    assert f'<meta name="{tmeta.MODEL_META}" content="vendor/b"/>' in opf
    assert f'name="{tmeta.DATE_META}"' in opf
    assert "old/model" not in opf and "aaaaaaa" not in opf

    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    # one record, replaced rather than accumulated, and the old glossary the
    # legacy meta vouched for went with it
    assert [m for m in members if tmeta.TRANSLATION_METADATA_STEM in m] == [
        f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"
    ]
    assert not [m for m in members if tmeta.GLOSSARY_STEM in m]
    assert record["commit"] == "bbbbbbb" and record["model"] == "vendor/b"
    assert "old/model" not in json.dumps(record)


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


def test_a_rerun_without_the_record_strips_the_previous_one(tmp_path, monkeypatch):
    """Ours to rewrite means ours to remove: a plain second pass must not
    leave the first run's build, model and command standing as though they
    described this file."""
    monkeypatch.setattr(tmeta, "tool_commit", lambda: "aaaaaaa")
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source, translation_metadata=True)

    twice = _translate_file(once, model=ModelB)

    opf = _opf_of(twice)
    assert "bbm:" not in opf
    assert "aaaaaaa" not in opf
    with zipfile.ZipFile(twice) as archive:
        assert not [
            m for m in archive.namelist() if tmeta.TRANSLATION_METADATA_STEM in m
        ]
    # the reader-facing disclosure is still written
    assert COLOPHON_FILE in opf


def test_a_books_own_glossary_file_is_never_taken_for_ours(tmp_path):
    """Ownership is the checksum a `bbm:glossary-sha256` meta vouches for,
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


# -------------------------------------------------------------- the colophon


def _colophon_text(book):
    return book.get_item_with_id(COLOPHON_ID).content.decode("utf-8")


def test_the_machine_record_stays_out_of_the_readers_page(tmp_path, monkeypatch):
    """The command line is for a script, not for someone reading the end of
    a book."""
    monkeypatch.setattr(sys, "argv", ["make_book.py", "--book_name", "b.epub"])

    page = _colophon_text(_rebuild(_source(), translation_metadata=True))

    assert "--book_name" not in page
    assert "api.openai.com" not in page


def test_the_page_the_meta_and_the_record_name_one_day(tmp_path):
    """One clock. The date is settled once, in `stamp_disclosure`, and handed
    to the colophon and to both translation metadata forms — so a run that straddles
    midnight cannot print one day to the reader and record another. Injected
    here the way the code takes it, rather than patched: `when` is already
    the seam."""
    frozen = date(2026, 9, 6)
    book = _rebuild(_source(), disclose=False)

    from book_maker.loader import disclosure

    disclosure.stamp_disclosure(
        book,
        "x/y",
        "zh-hans",
        when=frozen,
        translation_metadata=tmeta.TranslationMetadata(commit="deadbee", model="x/y"),
    )

    assert f"<p>Date: {frozen.isoformat()}</p>" in _colophon_text(book)
    assert _metas(book)[tmeta.DATE_META] == frozen.isoformat()
    assert _record_of(book)["date"] == frozen.isoformat()


def test_a_run_with_no_date_records_none(tmp_path):
    """The omission rule reaches the date too: `metas()` and `record()` never
    reach for a clock of their own, so a caller that hands them nothing gets
    nothing rather than today."""
    bare = tmeta.TranslationMetadata(commit="deadbee", model="x/y")

    assert dict(bare.metas()) == {
        tmeta.MARKER_META: "deadbee",
        tmeta.MODEL_META: "x/y",
    }
    assert "date" not in json.loads(bare.record())


# ---------------------------------------------------------- the build helper


def test_a_checkout_answers_with_its_commit(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    package = repo / "book_maker"
    package.mkdir(parents=True)
    (package / "translation_metadata.py").write_text("", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["add", "-A"],
        ["commit", "-qm", "one"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    expected = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    monkeypatch.setattr(tmeta, "__file__", str(package / "translation_metadata.py"))
    tmeta.tool_commit.cache_clear()
    try:
        assert tmeta.tool_commit() == expected
    finally:
        tmeta.tool_commit.cache_clear()


def test_a_checkout_of_something_else_is_not_this_build(tmp_path, monkeypatch):
    """An installed copy sitting inside an unrelated project's tree must not
    report that project's HEAD — a hash naming the wrong source is worse
    than no hash."""
    repo = tmp_path / "repo"
    elsewhere = repo / "vendor" / "site-packages" / "book_maker"
    elsewhere.mkdir(parents=True)
    (repo / "README").write_text("someone else's project", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["add", "-A"],
        ["commit", "-qm", "one"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    monkeypatch.setattr(tmeta, "__file__", str(elsewhere / "translation_metadata.py"))
    monkeypatch.setattr(tmeta, "DISTRIBUTION", "no-such-distribution-xyz")
    tmeta.tool_commit.cache_clear()
    try:
        assert tmeta.tool_commit() == tmeta.UNKNOWN
    finally:
        tmeta.tool_commit.cache_clear()


def test_no_checkout_falls_back_to_the_installed_version(tmp_path, monkeypatch):
    """A pip install has no git to ask, so it answers with its version.
    Stood in for by a distribution that is certainly installed here — the
    suite runs from a checkout, where `bbook-maker` itself need not be."""
    from importlib.metadata import version

    monkeypatch.setattr(tmeta, "_git", lambda *a, **k: None)
    monkeypatch.setattr(tmeta, "DISTRIBUTION", "pytest")
    tmeta.tool_commit.cache_clear()
    try:
        assert tmeta.tool_commit() == version("pytest")
    finally:
        tmeta.tool_commit.cache_clear()


def test_neither_available_is_unknown(monkeypatch):
    monkeypatch.setattr(tmeta, "_git", lambda *a, **k: None)
    monkeypatch.setattr(tmeta, "DISTRIBUTION", "no-such-distribution-xyz")
    tmeta.tool_commit.cache_clear()
    try:
        assert tmeta.tool_commit() == tmeta.UNKNOWN
    finally:
        tmeta.tool_commit.cache_clear()


def test_a_missing_git_is_not_an_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)

    assert tmeta._git(".", "rev-parse") is None


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


def test_the_flag_records_the_run_in_a_real_translation(tmp_path):
    proc, output = _cli(tmp_path, "--translation-metadata")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    opf = _opf_of(output)
    assert f'<meta name="{tmeta.MARKER_META}"' in opf
    # the marker half and nothing else: the run's facts are in the file
    assert opf.count('name="bbm:') == 3
    with zipfile.ZipFile(output) as archive:
        record = json.loads(archive.read(f"EPUB/{tmeta.TRANSLATION_METADATA_FILE}"))
    assert record["route"] == "google"
    assert "--translation-metadata" in record["args"]
    # a route with no endpoint of its own records none
    assert "endpoint" not in record


def test_the_cli_forwards_the_glossary_into_the_record(tmp_path):
    """codex review 260905 (P2): the unit tests hand `glossary_path` to the
    loader directly; only a real CLI run proves the flag actually reaches
    it. Without the wiring, `--glossary … --translation-metadata` recorded a run with
    no glossary at all."""
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
    """The tag-mode default. The reader still gets the note; a machine gets
    nothing it was not asked for."""
    proc, output = _cli(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    opf = _opf_of(output)
    assert "bbm:" not in opf
    assert COLOPHON_FILE in opf


def test_a_session_run_records_it_without_being_asked(tmp_path):
    proc, output = _cli(tmp_path, "--use_context", "session")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f'<meta name="{tmeta.MARKER_META}"' in _opf_of(output)


def test_the_switch_that_silences_the_note_silences_the_record_too(tmp_path):
    proc, output = _cli(tmp_path, "--translation-metadata", "--no_disclosure")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    opf = _opf_of(output)
    assert "bbm:" not in opf
    assert COLOPHON_FILE not in opf


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
