"""What a validator makes of the books this branch produces.

The unit tests above assert that particular strings land in the OPF. This
one asks the only question that matters to a reading system: is the result
a valid EPUB. Three fixtures, each exercising one thing this branch changed
— the carried `prefix` declaration, the de-obfuscated font, and the copied
rights metadata — are built, translated by a stand-in model and validated.

FATAL and ERROR fail. WARNING is printed, not failed: several are inherent
to what ebooklib emits and are not this branch's to fix.
"""

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from ebooklib import epub

from book_maker.loader.disclosure import CREDIT_CLASS
from book_maker.loader.epub_loader import EPUBBookLoader

REPO = Path(__file__).resolve().parent.parent

# What a translated animal_farm.epub scores: 6 distinct ERROR messages (8
# occurrences), 0 warnings. Measured 260903 both at the parent commit and
# with this slice applied — the same six either way, so neither the calibre
# drop nor the disclosure changes the number. All six are the source's own
# EPUB 2 shape surviving into an EPUB 3 package: four RSC-005 over
# `opf:scheme` / `opf:role` / `opf:file-as` attributes, one RSC-005 for the
# missing `nav` property, one OPF-014 for an undeclared `svg` property.
# Fixing those is explicitly out of this slice. A ratchet: may fall, never
# rise.
ANIMAL_FARM_ERRORS = 6

# Captured at import, before any loader has run. `EPUBBookLoader.__init__`
# installs a replacement `_load_spine` on the ebooklib *class* when a book
# trips issue #71, and never puts it back — and ebooklib's own version is
# what parses the NCX into `book.toc`. So in a process where any book has
# tripped that path, every EPUB 2 book read afterwards arrives with an empty
# table of contents, and writes an empty <navMap> (RSC-005). That is a
# pre-existing landmine, not this branch's; this test refuses to inherit it.
PRISTINE_LOAD_SPINE = epub.EpubReader._load_spine

EPUBCHECK_HOME = "https://github.com/w3c/epubcheck/releases"


class StandInModel:
    """Translates without a network, and marks what it touched."""

    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"翻訳 {text}"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


# ------------------------------------------------------------ finding the tool


def _epubcheck_command():
    """The argv prefix that runs epubcheck, or None if this machine has none.

    Three ways it can be present, in order of how deliberate they are: an
    explicit `EPUBCHECK_JAR`, a launcher on PATH, and the jar the repo keeps
    under `tools/` for the corpus gate.
    """
    override = os.environ.get("EPUBCHECK_JAR")
    if override:
        if not Path(override).exists():
            pytest.fail(f"EPUBCHECK_JAR={override} does not exist")
        return _java_run(override)

    launcher = shutil.which("epubcheck")
    if launcher:
        return [launcher]

    bundled = sorted((REPO / "tools").glob("epubcheck*/epubcheck.jar"))
    if bundled:
        return _java_run(str(bundled[-1]))
    return None


def _java_run(jar):
    """A jar needs a JVM; without one this machine has no checker."""
    java = shutil.which("java")
    return [java, "-jar", jar] if java else None


@pytest.fixture(scope="module")
def epubcheck():
    command = _epubcheck_command()
    if command is None:
        pytest.skip(
            "epubcheck was not found: set EPUBCHECK_JAR to a jar, put an "
            "`epubcheck` launcher on PATH, or unzip a release from "
            f"{EPUBCHECK_HOME} under {REPO / 'tools'}"
        )
    return command


def _findings(command, path):
    """Every FATAL/ERROR/WARNING epubcheck reports, or a loud failure.

    Read from `--json`, never scraped: a checker that cannot start prints a
    stack trace and no findings, which a scraper would read as a clean book.
    """
    result = subprocess.run(
        [*command, "--quiet", "--json", "-", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        pytest.fail(
            f"epubcheck delivered no JSON report for {path} (exit "
            f"{result.returncode}):\n{(result.stderr or result.stdout)[:800]}"
        )
    if not (report.get("checker") or {}).get("checkerVersion"):
        pytest.fail(f"epubcheck report for {path} has no checker block")
    messages = report.get("messages")
    if not isinstance(messages, list):
        pytest.fail(f"epubcheck report for {path} has no messages array")
    if not messages and not (report.get("items") or []):
        pytest.fail(f"epubcheck examined nothing in {path} — unreadable file?")

    findings = []
    for message in messages:
        severity = message.get("severity")
        if severity not in ("FATAL", "ERROR", "WARNING"):
            continue
        locations = message.get("locations") or []
        where = locations[0].get("path") if locations else "?"
        findings.append(
            (severity, f'{message.get("ID")} [{where}]: {message.get("message")}')
        )
    return findings


def _assert_valid(command, path, label):
    findings = _findings(command, path)
    for severity, text in findings:
        if severity == "WARNING":
            print(f"epubcheck WARNING on {label}: {text}")
    fatal = [text for severity, text in findings if severity != "WARNING"]
    assert not fatal, f"epubcheck rejected {label}:\n" + "\n".join(fatal)


# ---------------------------------------------------------------- the fixtures


def _rewrite_member(path, member, content):
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    content.encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)


def _opf_member(path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return name, archive.read(name).decode("utf-8")


def _base_book(identifier, extra_items=()):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Validation fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = (
        "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>One</title>"
        "</head><body><h1>One</h1><p>The first paragraph.</p>"
        "<p>The second paragraph.</p></body></html>"
    )
    book.add_item(chapter)
    for item in extra_items:
        book.add_item(item)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    return book


def _translate(path, glossary_path=None, **kwargs):
    loader = EPUBBookLoader(
        str(path), StandInModel, key="", resume=False, language="japanese", **kwargs
    )
    loader.quiet = True
    if glossary_path is not None:
        loader.glossary_path = glossary_path
    loader.make_bilingual_book()
    return path.with_name(f"{path.stem}_bilingual.epub")


@pytest.fixture
def tdm_book(tmp_path):
    """Rights metadata under a prefix the source declares."""
    path = tmp_path / "tdm.epub"
    epub.write_epub(
        str(path), _base_book("urn:uuid:11111111-1111-4111-8111-111111111111")
    )
    member, opf = _opf_member(path)
    opf = opf.replace(
        'prefix="rendition: http://www.idpf.org/vocab/rendition/#"',
        'prefix="rendition: http://www.idpf.org/vocab/rendition/# '
        'tdm: http://www.w3.org/ns/tdmrep#"',
        1,
    )
    opf = opf.replace(
        "</metadata>",
        '<meta property="tdm:reservation">1</meta>'
        '<meta property="tdm:policy">https://example.org/policy.json</meta>'
        "</metadata>",
        1,
    )
    _rewrite_member(path, member, opf)
    return _translate(path)


@pytest.fixture
def font_book(tmp_path):
    """An obfuscated font whose declaration is dropped from the output."""
    identifier = "urn:uuid:22222222-2222-4222-8222-222222222222"
    font = bytes(range(256)) * 8
    path = tmp_path / "font.epub"
    epub.write_epub(
        str(path),
        _base_book(
            identifier,
            [
                epub.EpubItem(
                    uid="font",
                    file_name="fonts/obfuscated.otf",
                    media_type="application/vnd.ms-opentype",
                    content=font,
                )
            ],
        ),
    )

    key = hashlib.sha1(identifier.encode("utf-8")).digest()
    scrambled = bytearray(font)
    for index in range(min(1040, len(scrambled))):
        scrambled[index] ^= key[index % len(key)]

    member = "EPUB/fonts/obfuscated.otf"
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    bytes(scrambled)
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )
        target.writestr(
            "META-INF/encryption.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>'
            '<enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>'
            f'<enc:CipherData><enc:CipherReference URI="{member}"/></enc:CipherData>'
            "</enc:EncryptedData></encryption>",
        )
    replacement.replace(path)
    return _translate(path)


@pytest.fixture
def rights_book(tmp_path):
    """Plain book carrying dc:rights."""
    path = tmp_path / "rights.epub"
    book = _base_book("urn:uuid:33333333-3333-4333-8333-333333333333")
    book.add_metadata("DC", "rights", "Public domain in the United States.")
    epub.write_epub(str(path), book)
    return _translate(path)


@pytest.fixture
def fixed_layout_book(tmp_path):
    """A pre-paginated package: every spine document owes page dimensions.

    The nav is kept out of the spine and the one chapter carries a viewport,
    so the source itself is a clean fixed-layout book and anything epubcheck
    then says is about what the translation added.
    """
    path = tmp_path / "fixed.epub"
    book = _base_book("urn:uuid:44444444-4444-4444-8444-444444444444")
    book.add_metadata(None, "meta", "pre-paginated", {"property": "rendition:layout"})
    book.spine = [book.get_item_with_href("chapter.xhtml")]
    epub.write_epub(str(path), book)
    # The viewport goes in after the write: ebooklib regenerates the head of
    # an EpubHtml it created, so a meta set on `content` never reaches the
    # file.
    member = "EPUB/chapter.xhtml"
    with zipfile.ZipFile(path) as archive:
        chapter = archive.read(member).decode("utf-8")
    _rewrite_member(
        path,
        member,
        chapter.replace(
            "<head>",
            '<head><meta name="viewport" content="width=1200, height=1600"/>',
            1,
        ),
    )
    return _translate(path)


# ------------------------------------------------------------------ the cases


def test_a_fixed_layout_book_validates(epubcheck, fixed_layout_book):
    """A pre-paginated package requires page dimensions of every spine
    document (epubcheck HTM-046), and a page appended for the translation
    had none to give — nine of the 45 books in `epub-sample` are
    pre-paginated, so this was a real refusal, not a hypothetical one.

    Since the 260906 slim there is no appended page: the credit line goes
    inside a document the book already laid out, so it inherits that
    document's declaration and the whole question goes away. Pinned here
    because the fixture is the only pre-paginated book in the suite.
    """
    _assert_valid(epubcheck, fixed_layout_book, "the fixed-layout book")

    marker = CREDIT_CLASS.encode("utf-8")
    with zipfile.ZipFile(fixed_layout_book) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        opf = archive.read(opf_name).decode("utf-8")
        carrying = [name for name in archive.namelist() if marker in archive.read(name)]
    assert carrying == ["EPUB/chapter.xhtml"]
    # no page was added, so nothing new is in the spine
    assert opf.count("<itemref") == 1


def test_a_carried_prefix_declaration_validates(epubcheck, tdm_book):
    _assert_valid(epubcheck, tdm_book, "the tdm-prefix book")


def test_a_reobfuscated_font_validates(epubcheck, font_book):
    """The font goes out obfuscated again, under the output's own identifier,
    with the OCF declaration that describes it — and epubcheck reads the
    declaration, the manifest and the archive as consistent."""
    _assert_valid(epubcheck, font_book, "the obfuscated-font book")
    with zipfile.ZipFile(font_book) as archive:
        names = archive.namelist()
        assert "META-INF/encryption.xml" in names
        declaration = archive.read("META-INF/encryption.xml").decode("utf-8")
        assert "http://www.idpf.org/2008/embedding" in declaration
        assert "EPUB/fonts/obfuscated.otf" in declaration
        # OCF: mimetype first and stored, after the post-write rewrite
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED


def test_a_round_tripped_book_validates(epubcheck, font_book, tmp_path):
    """Translating an output again: its fonts are unscrambled with its own
    key and scrambled with the next book's. Both books must validate."""
    again = _translate(font_book)

    _assert_valid(epubcheck, again, "the twice-translated obfuscated-font book")
    with zipfile.ZipFile(again) as archive:
        assert "META-INF/encryption.xml" in archive.namelist()


def test_copied_rights_metadata_validates(epubcheck, rights_book):
    _assert_valid(epubcheck, rights_book, "the dc:rights book")


@pytest.fixture
def translation_metadata_book(tmp_path):
    """A book carrying the machine record, glossary and all.

    Three things here are new shapes in the package and none is exercised
    anywhere else: EPUB 2 `<meta name= content=>` entries in an EPUB 3
    package, and a `text/plain` and an `application/json` resource in the
    manifest that no content document references.
    """
    path = tmp_path / "translation_metadata.epub"
    epub.write_epub(
        str(path), _base_book("urn:uuid:55555555-5555-4555-8555-555555555555")
    )
    glossary = tmp_path / "terms.txt"
    glossary.write_text("sett: badger set\n", encoding="utf-8")
    return _translate(path, glossary_path=str(glossary), translation_metadata=True)


def test_the_machine_record_validates(epubcheck, translation_metadata_book):
    _assert_valid(epubcheck, translation_metadata_book, "the translation metadata book")

    with zipfile.ZipFile(translation_metadata_book) as archive:
        names = archive.namelist()
        opf_name = next(n for n in names if n.endswith(".opf"))
        opf = archive.read(opf_name).decode("utf-8")
    # nothing of ours in the package document any more (owner ruling,
    # 260906): the record is a manifest item and the reader gets one line
    assert "bbm:" not in opf
    assert "<dc:contributor" not in opf
    assert "EPUB/bbm_glossary.txt" in names
    assert "EPUB/bbm_translation_metadata.json" in names
    # in the manifest, never in the spine
    assert 'idref="bbm-glossary"' not in opf
    assert 'idref="bbm-translation-metadata"' not in opf


@pytest.mark.parametrize("fixture", ["tdm_book", "font_book", "rights_book"])
def test_every_output_carries_the_credit_line(request, fixture):
    """The one visible mark ships in every book, and ships valid — the three
    fixtures above are checked by epubcheck with it in place. Exactly one
    document carries it: a second would be a book claiming two translations.
    """
    output = request.getfixturevalue(fixture)
    marker = CREDIT_CLASS.encode("utf-8")
    with zipfile.ZipFile(output) as archive:
        carrying = [name for name in archive.namelist() if marker in archive.read(name)]
        occurrences = sum(
            archive.read(name).count(marker) for name in archive.namelist()
        )
    assert len(carrying) == 1, carrying
    assert occurrences == 1


@pytest.fixture
def pristine_reader(monkeypatch):
    """Read books with ebooklib's own spine loader, whatever ran before."""
    monkeypatch.setattr(epub.EpubReader, "_load_spine", PRISTINE_LOAD_SPINE)


def test_a_real_calibre_book_does_not_get_worse(epubcheck, pristine_reader, tmp_path):
    """animal_farm.epub is calibre output: an EPUB 2 package full of records
    of the file calibre built. Translating it must not add findings of our
    own on top of the ones its own shape already causes.

    Copied into tmp_path first — a translation must never be written beside
    the fixture it came from.
    """
    source = tmp_path / "animal_farm.epub"
    shutil.copy(REPO / "test_books" / "animal_farm.epub", source)

    output = _translate(source)

    errors = [
        text for severity, text in _findings(epubcheck, output) if severity != "WARNING"
    ]
    assert len(errors) <= ANIMAL_FARM_ERRORS, "\n".join(errors)

    with zipfile.ZipFile(output) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        opf = archive.read(opf_name).decode("utf-8")
    # calibre's record of its own output is gone; what identifies the work
    # (an identifier it issued, the producer credit) is not ours to remove
    assert 'name="calibre:' not in opf
    assert "calibre: https://calibre-ebook.com" not in opf
    assert '<dc:identifier opf:scheme="calibre">' in opf
