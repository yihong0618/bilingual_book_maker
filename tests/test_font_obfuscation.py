"""Obfuscated fonts are unscrambled on read.

`META-INF/encryption.xml` is a manifest-less OCF member, so ebooklib never
carries it into the output — which is the policy we want, since the output
declares no encryption of any kind. That only stays *correct* if the fonts
it described are plain by then. Otherwise the translated book ships a
scrambled font with nothing left to explain it.

The reference obfuscation below is written out independently of the
implementation on purpose: a test that reuses the module's own XOR would
pass on any key it computed, right or wrong.
"""

import hashlib
import re
import zipfile

import pytest
from ebooklib import epub

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.font_obfuscation import (
    ENCRYPTION_PATH,
    deobfuscate_fonts,
    reobfuscate_written_epub,
)

IDPF_ALGORITHM = "http://www.idpf.org/2008/embedding"
ADOBE_ALGORITHM = "http://ns.adobe.com/pdf/enc#RC"

IDENTIFIER = "urn:uuid:0d6e2b1f-4c1a-4f7a-9a0e-2f4f7d8a1b2c"

LONG_FONT = bytes(range(256)) * 8  # 2048 bytes, past both key windows
SHORT_FONT = bytes(range(256)) + bytes(range(44))  # 300 bytes, inside both


class SilentModel:
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"<T>{text}</T>"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


# ------------------------------------------- an independent reference impl


def _idpf_key(identifier):
    stripped = "".join(identifier.split())
    return hashlib.sha1(stripped.encode("utf-8")).digest()


def _adobe_key(identifier):
    hexed = "".join(identifier.split())
    hexed = hexed.replace("urn:uuid:", "").replace("-", "")
    return bytes.fromhex(hexed)


def _scramble(data, key, window):
    out = bytearray(data)
    for index in range(min(window, len(out))):
        out[index] ^= key[index % len(key)]
    return bytes(out)


def _obfuscate(data, algorithm, identifier):
    if algorithm == IDPF_ALGORITHM:
        return _scramble(data, _idpf_key(identifier), 1040)
    try:
        key = _adobe_key(identifier)
    except ValueError:
        return data  # no UUID, no key — the fixture ships the font as it is
    return _scramble(data, key, 1024)


# ------------------------------------------------------------- the fixture


ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="{algorithm}"/>
    <enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


def _write_source(path, font, algorithm, identifier=IDENTIFIER):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Obfuscated font fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>Body text</p></body></html>"
    book.add_item(chapter)
    book.add_item(
        epub.EpubItem(
            uid="font",
            file_name="fonts/obfuscated.otf",
            media_type="application/vnd.ms-opentype",
            content=font,
        )
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)

    font_member = "EPUB/fonts/obfuscated.otf"
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    _obfuscate(font, algorithm, identifier)
                    if info.filename == font_member
                    else source.read(info.filename)
                ),
            )
        target.writestr(
            "META-INF/encryption.xml",
            ENCRYPTION.format(algorithm=algorithm, uri=font_member),
        )
        # a signature must survive the same drop, and change nothing
        target.writestr("META-INF/signatures.xml", "<signatures/>")
    replacement.replace(path)
    return path


def _load(path):
    return EPUBBookLoader(
        str(path), SilentModel, key="", resume=False, language="zh-hans"
    )


# --------------------------------------------------------------- the cases


@pytest.mark.parametrize("algorithm", [IDPF_ALGORITHM, ADOBE_ALGORITHM])
@pytest.mark.parametrize("font", [LONG_FONT, SHORT_FONT], ids=["long", "short"])
def test_the_font_is_plain_once_the_book_is_loaded(tmp_path, algorithm, font):
    path = _write_source(tmp_path / "book.epub", font, algorithm)
    loader = _load(path)
    assert loader.origin_book.get_item_with_id("font").content == font


def test_the_written_book_declares_its_fonts_and_drops_the_signature(tmp_path):
    """The output declares the obfuscation it applies — and only that.

    An earlier revision shipped the font in the clear and no declaration;
    that undid the publisher's licence compliance, so the font now goes out
    obfuscated with an `encryption.xml` describing it. A signature still
    goes: it asserted the integrity of a file that no longer exists.
    """
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert ENCRYPTION_PATH in names
    assert "META-INF/signatures.xml" not in names


def test_a_book_with_no_encryption_declaration_is_untouched(tmp_path):
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    # rebuild without the declaration; the font stays scrambled and nothing
    # in the code may decide to unscramble it anyway
    plain = tmp_path / "plain.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(plain, "w") as target:
        for info in source.infolist():
            if info.filename == "META-INF/encryption.xml":
                continue
            target.writestr(info, source.read(info.filename))

    loader = _load(plain)
    scrambled = _obfuscate(LONG_FONT, IDPF_ALGORITHM, IDENTIFIER)
    assert loader.origin_book.get_item_with_id("font").content == scrambled


def test_an_unresolvable_reference_is_reported_not_swallowed(tmp_path):
    """A URI naming nothing in the manifest leaves a scrambled font behind;
    the caller has to be able to say so."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    member = "META-INF/encryption.xml"
    replacement = tmp_path / "broken.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    ENCRYPTION.format(
                        algorithm=IDPF_ALGORITHM, uri="EPUB/fonts/absent.otf"
                    ).encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )

    book = epub.read_epub(str(replacement))
    restored, unresolved = deobfuscate_fonts(book, str(replacement))
    assert restored == []
    assert unresolved == ["EPUB/fonts/absent.otf"]


def test_an_identifier_that_is_not_a_uuid_defeats_the_adobe_key(tmp_path):
    """Adobe's key *is* the UUID; there is no key to derive without one, so
    the font is reported unresolved rather than mangled further."""
    path = _write_source(
        tmp_path / "book.epub", LONG_FONT, ADOBE_ALGORITHM, identifier="not-a-uuid"
    )
    book = epub.read_epub(str(path))
    restored, unresolved = deobfuscate_fonts(book, str(path))
    assert restored == []
    assert unresolved == ["EPUB/fonts/obfuscated.otf"]


# ---------------------------- finding 2: which identifier holds the key


UNIQUE_ID = "urn:uuid:44444444-4444-4444-8444-444444444444"
OTHER_ID = "urn:uuid:99999999-9999-4999-8999-999999999999"


def _two_identifier_source(tmp_path, algorithm):
    """A book whose `unique-identifier` is the *first* dc:identifier.

    ebooklib sets `book.uid` from the last identified dc:identifier it
    reads, not the one the package names, so a book like this hands the
    wrong key to anything that trusts `uid`.
    """
    path = _write_source(tmp_path / "two-ids.epub", LONG_FONT, algorithm, UNIQUE_ID)

    with zipfile.ZipFile(path) as archive:
        member = next(n for n in archive.namelist() if n.endswith(".opf"))
        opf = archive.read(member).decode("utf-8")
    opf = opf.replace(
        "</metadata>",
        f'<dc:identifier id="isbn">{OTHER_ID}</dc:identifier></metadata>',
        1,
    )
    replacement = path.with_suffix(".fixed.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    opf.encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)
    return path


@pytest.mark.parametrize("algorithm", [IDPF_ALGORITHM, ADOBE_ALGORITHM])
def test_the_key_comes_from_the_identifier_the_package_names(tmp_path, algorithm):
    """Finding 2: the key is the *unique-identifier*, not whichever
    dc:identifier ebooklib happened to read last."""
    path = _two_identifier_source(tmp_path, algorithm)

    book = epub.read_epub(str(path))
    assert book.uid == OTHER_ID, "the fixture must actually mislead ebooklib"

    restored, unresolved = deobfuscate_fonts(book, str(path))

    assert unresolved == []
    assert [font.uri for font in restored] == ["EPUB/fonts/obfuscated.otf"]
    assert book.get_item_with_id("font").content == LONG_FONT


# ----------------------------------- the round trip: obfuscated in, out


def _opf_of(path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return name, archive.read(name).decode("utf-8")


def _output_identifier(path):
    """The identifier the *written* package names, read back independently."""
    import re

    _, opf = _opf_of(path)
    named = re.search(r'<package[^>]*\bunique-identifier="([^"]+)"', opf).group(1)
    return re.search(
        rf'<dc:identifier[^>]*\bid="{named}"[^>]*>([^<]*)</dc:identifier>', opf
    ).group(1)


def _declared_algorithms(path):
    with zipfile.ZipFile(path) as archive:
        if ENCRYPTION_PATH not in archive.namelist():
            return {}
        declaration = archive.read(ENCRYPTION_PATH).decode("utf-8")
    import re

    return dict(
        zip(
            re.findall(r'CipherReference URI="([^"]+)"', declaration),
            re.findall(r'EncryptionMethod Algorithm="([^"]+)"', declaration),
        )
    )


@pytest.mark.parametrize("algorithm", [IDPF_ALGORITHM, ADOBE_ALGORITHM])
@pytest.mark.parametrize("font", [LONG_FONT, SHORT_FONT], ids=["long", "short"])
def test_an_obfuscated_font_comes_out_obfuscated(tmp_path, algorithm, font):
    """A foundry licence that allowed the source to embed the font allowed it
    *obfuscated*. Shipping it in the clear undoes the publisher's compliance,
    so the output re-obfuscates under its own identifier."""
    path = _write_source(tmp_path / "book.epub", font, algorithm)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    member = "EPUB/fonts/obfuscated.otf"

    assert _declared_algorithms(output) == {member: algorithm}

    with zipfile.ZipFile(output) as archive:
        shipped = archive.read(member)
    assert shipped != font, "the font must not ship in the clear"

    # the same algorithm, keyed on the output book's own identifier,
    # must give the plain font back exactly
    assert _obfuscate(shipped, algorithm, _output_identifier(output)) == font


def test_the_output_is_keyed_on_its_own_identifier_not_the_sources(tmp_path):
    """The translation has its own identity (`derive_translation_identity`),
    and the OCF key is the identifier of the book the font sits in."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        shipped = archive.read("EPUB/fonts/obfuscated.otf")

    assert _output_identifier(output) != IDENTIFIER
    assert _obfuscate(shipped, IDPF_ALGORITHM, IDENTIFIER) != LONG_FONT


def test_the_rewritten_archive_still_opens_as_an_epub(tmp_path):
    """OCF requires `mimetype` first and uncompressed; the rewrite that adds
    encryption.xml must not disturb that."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()

    with zipfile.ZipFile(tmp_path / "book_bilingual.epub") as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.testzip() is None


def test_a_book_with_no_obfuscated_fonts_is_not_rewritten(tmp_path):
    """Nothing to declare, so no declaration and no second pass over the zip."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    plain = tmp_path / "plain.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(plain, "w") as target:
        for info in source.infolist():
            if info.filename == "META-INF/encryption.xml":
                continue
            target.writestr(info, source.read(info.filename))

    loader = _load(plain)
    loader.quiet = True
    loader.make_bilingual_book()

    output = tmp_path / "plain_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        assert ENCRYPTION_PATH not in archive.namelist()


def test_re_obfuscating_nothing_leaves_the_file_untouched(tmp_path):
    """The rewrite is skipped entirely, not performed with an empty list.

    Byte equality alone would not show that: the rewrite replaces the file
    through `os.replace`, so the inode is what says whether it ran.
    """
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    before = path.read_bytes()
    inode = path.stat().st_ino

    assert reobfuscate_written_epub(str(path), []) == []

    assert path.read_bytes() == before
    assert path.stat().st_ino == inode, "the archive was rewritten anyway"


# ebooklib stamps dcterms:modified from the wall clock to the second, so two
# builds straddling a second differ in content.opf (flaked on CI 260926).
_MODIFIED = re.compile(rb'(<meta property="dcterms:modified">)[^<]*(</meta>)')


def _unstamped(payload):
    return _MODIFIED.sub(rb"\1\2", payload)


def test_every_other_member_survives_the_rewrite(tmp_path):
    """Only the fonts change; the member list and every other payload are
    exactly what ebooklib wrote."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()
    output = tmp_path / "book_bilingual.epub"

    with zipfile.ZipFile(output) as archive:
        after = {n: _unstamped(archive.read(n)) for n in archive.namelist()}

    # rebuild the same book without the re-obfuscation step to compare
    loader2 = _load(path)
    loader2.quiet = True
    loader2._reobfuscate_written = lambda *args, **kwargs: None
    loader2.make_bilingual_book()
    with zipfile.ZipFile(output) as archive:
        before = {n: _unstamped(archive.read(n)) for n in archive.namelist()}

    assert set(after) - set(before) == {ENCRYPTION_PATH}
    changed = {n for n in before if before[n] != after.get(n)}
    assert changed == {"EPUB/fonts/obfuscated.otf"}


# ------------------------------------------------ the other write routes


def test_the_single_translation_route_re_obfuscates(tmp_path):
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.single_translate = True
    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    assert _declared_algorithms(output) == {"EPUB/fonts/obfuscated.otf": IDPF_ALGORITHM}
    with zipfile.ZipFile(output) as archive:
        shipped = archive.read("EPUB/fonts/obfuscated.otf")
    assert _obfuscate(shipped, IDPF_ALGORITHM, _output_identifier(output)) == LONG_FONT


def test_the_recovery_save_re_obfuscates(tmp_path):
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()
    loader.p_to_save = loader.p_to_save[:1]

    loader._save_temp_book()

    output = tmp_path / "book_bilingual_temp.epub"
    assert _declared_algorithms(output) == {"EPUB/fonts/obfuscated.otf": IDPF_ALGORITHM}
    with zipfile.ZipFile(output) as archive:
        shipped = archive.read("EPUB/fonts/obfuscated.otf")
    assert _obfuscate(shipped, IDPF_ALGORITHM, _output_identifier(output)) == LONG_FONT


def test_the_retranslate_route_re_obfuscates(tmp_path):
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    first = _load(path)
    first.quiet = True
    first.make_bilingual_book()
    once = tmp_path / "book_bilingual.epub"

    loader = _load(path)
    loader.quiet = True
    loader.retranslate = [str(once), "chapter.xhtml", "Body text", "Body text"]
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()

    assert _declared_algorithms(once) == {"EPUB/fonts/obfuscated.otf": IDPF_ALGORITHM}
    with zipfile.ZipFile(once) as archive:
        shipped = archive.read("EPUB/fonts/obfuscated.otf")
    assert _obfuscate(shipped, IDPF_ALGORITHM, _output_identifier(once)) == LONG_FONT


def test_translating_an_output_again_rekeys_the_font(tmp_path):
    """Each generation is keyed on its own identifier: the second book's
    font must unscramble with the second book's key, not the first's."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    first = _load(path)
    first.quiet = True
    first.make_bilingual_book()
    once = tmp_path / "book_bilingual.epub"

    second = _load(once)
    second.quiet = True
    second.make_bilingual_book()
    twice = tmp_path / "book_bilingual_bilingual.epub"

    assert _output_identifier(twice) != _output_identifier(once)
    with zipfile.ZipFile(twice) as archive:
        shipped = archive.read("EPUB/fonts/obfuscated.otf")
    assert _obfuscate(shipped, IDPF_ALGORITHM, _output_identifier(twice)) == LONG_FONT


def test_a_font_name_with_an_ampersand_yields_well_formed_encryption_xml():
    """Codex finding on 959c74d: the URI was pasted into the declaration
    unescaped, so `EPUB/fonts/A&B.otf` produced XML no parser accepts.

    The URI is now percent-encoded before it is escaped, so the `&` leaves
    as `%26` and the XML escape has nothing left to do — the name comes
    back through `unquote` on the read side. Still well-formed, which is
    what this test is for.
    """
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote

    from book_maker.loader.font_obfuscation import IDPF_ALGORITHM, build_encryption_xml

    xml = build_encryption_xml([("EPUB/fonts/A&B.otf", IDPF_ALGORITHM)])
    root = ET.fromstring(xml)
    uris = [
        el.get("URI")
        for el in root.iter("{http://www.w3.org/2001/04/xmlenc#}CipherReference")
    ]
    assert uris == ["EPUB/fonts/A%26B.otf"]
    assert [unquote(uri) for uri in uris] == ["EPUB/fonts/A&B.otf"]


# ------------- finding 1: the gate matches by local name, so must the read


CONTAINER_NAMESPACED_ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <EncryptedData>
    <EncryptionMethod Algorithm="{algorithm}"/>
    <CipherData><CipherReference URI="{uri}"/></CipherData>
  </EncryptedData>
</encryption>
"""

UNNAMESPACED_ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption>
  <EncryptedData>
    <EncryptionMethod Algorithm="{algorithm}"/>
    <CipherData><CipherReference URI="{uri}"/></CipherData>
  </EncryptedData>
</encryption>
"""


def _with_encryption(path, declaration):
    """Rebuild `path` with `declaration` as its encryption.xml."""
    replacement = path.with_suffix(".redeclared.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    declaration.encode("utf-8")
                    if info.filename == ENCRYPTION_PATH
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)
    return path


@pytest.mark.parametrize(
    "template",
    [CONTAINER_NAMESPACED_ENCRYPTION, UNNAMESPACED_ENCRYPTION],
    ids=["container-ns", "no-ns"],
)
def test_a_declaration_the_gate_accepted_is_read_by_the_same_rule(tmp_path, template):
    """The DRM gate matches EncryptedData by local name; the read path used
    to require the xmlenc namespace. A producer who wrote the elements in
    the container namespace (or none) cleared the gate as font obfuscation
    and then had *nothing* restored and nothing reported — the output
    shipped a scrambled font with no declaration left to explain it."""
    from book_maker.loader.rights import check_epub

    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    _with_encryption(
        path, template.format(algorithm=IDPF_ALGORITHM, uri="EPUB/fonts/obfuscated.otf")
    )

    assert check_epub(str(path)) == "ok", "the fixture must clear the gate"

    book = epub.read_epub(str(path))
    restored, unresolved = deobfuscate_fonts(book, str(path))

    assert unresolved == []
    assert [font.uri for font in restored] == ["EPUB/fonts/obfuscated.otf"]
    assert book.get_item_with_id("font").content == LONG_FONT


def test_an_oddly_namespaced_declaration_still_writes_a_conforming_one(tmp_path):
    """Reading a loose declaration must not make us write one: the output's
    encryption.xml is still xmlenc, and re-readable."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    _with_encryption(
        path,
        CONTAINER_NAMESPACED_ENCRYPTION.format(
            algorithm=IDPF_ALGORITHM, uri="EPUB/fonts/obfuscated.otf"
        ),
    )
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    member = "EPUB/fonts/obfuscated.otf"
    with zipfile.ZipFile(output) as archive:
        declaration = archive.read(ENCRYPTION_PATH)
        shipped = archive.read(member)

    import xml.etree.ElementTree as ET

    root = ET.fromstring(declaration)
    assert [
        el.get("URI")
        for el in root.iter("{http://www.w3.org/2001/04/xmlenc#}CipherReference")
    ] == [member]
    assert _obfuscate(shipped, IDPF_ALGORITHM, _output_identifier(output)) == LONG_FONT


# -------------- finding 2: no identifier is no key, not an empty-string key


def _identifierless_source(tmp_path, shape="none"):
    """A package with no usable `unique-identifier`.

    Two shapes, because they fail differently: `none` names no
    `unique-identifier` and carries no `dc:identifier` either, so ebooklib
    hands back the random uuid its `reset()` invented; `dangling` names an
    id that nothing in the package carries, so `read_package` finds no
    identifier while ebooklib happily reports the one it can see. Both are
    a book that does not say what its identifier is.
    """
    path = _write_source(tmp_path / "anonymous.epub", LONG_FONT, IDPF_ALGORITHM)
    member, opf = _opf_of(path)
    import re

    if shape == "none":
        opf = re.sub(r'\sunique-identifier="[^"]*"', "", opf, count=1)
        opf = re.sub(r"<dc:identifier[^>]*>[^<]*</dc:identifier>", "", opf)
    else:
        opf = re.sub(
            r'unique-identifier="[^"]*"', 'unique-identifier="nothing-has-this"', opf, 1
        )

    replacement = path.with_suffix(".anon.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    opf.encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)
    return path


@pytest.mark.parametrize("shape", ["none", "dangling"])
def test_a_book_with_no_identifier_leaves_its_idpf_font_alone(tmp_path, shape):
    """`sha1(b"")` is a perfectly computable key and a completely wrong one:
    the font was XORed with it, counted as restored, then re-scrambled under
    the output's identifier — a silently corrupt font and nothing said. No
    identifier means no key, exactly as it already does for Adobe.

    `book.uid` is whatever ebooklib made of the same book, and it is never
    consulted: for `none` it is a uuid ebooklib invented out of nothing,
    for `dangling` it is a real dc:identifier that the package does not
    name as its unique-identifier. Neither is the OCF key.
    """
    path = _identifierless_source(tmp_path, shape)
    book = epub.read_epub(str(path))
    assert book.uid, "the fixture must leave ebooklib with a uid to be ignored"

    scrambled = book.get_item_with_id("font").content
    restored, unresolved = deobfuscate_fonts(book, str(path))

    assert restored == []
    assert unresolved == ["EPUB/fonts/obfuscated.otf"]
    assert book.get_item_with_id("font").content == scrambled


def test_a_package_that_cannot_be_read_at_all_yields_no_key(tmp_path):
    """`read_package` answers `EMPTY_PACKAGE` for a container it could not
    parse, so `unique_identifier` is None there and there is no key to be
    had. That has to arrive as an unresolved font — not as an
    `AttributeError` from `None.split()` in the key derivation, and not as
    a key invented from somewhere else."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    book = epub.read_epub(str(path))
    assert book.uid == IDENTIFIER, "ebooklib read the identifier it must not use"

    # the same archive with no container.xml: encryption.xml is still there
    # to be read, but nothing says where the package document is
    headless = tmp_path / "headless.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(headless, "w") as target:
        for info in source.infolist():
            if info.filename == "META-INF/container.xml":
                continue
            target.writestr(info, source.read(info.filename))

    scrambled = book.get_item_with_id("font").content
    restored, unresolved = deobfuscate_fonts(book, str(headless))

    assert restored == []
    assert unresolved == ["EPUB/fonts/obfuscated.otf"]
    assert book.get_item_with_id("font").content == scrambled


# ------------- finding 3: the write path percent-encodes what the read undoes


@pytest.mark.parametrize(
    "name",
    ["A&B.otf", "a b.otf", "Old#1.otf", "a%20b.otf", "plain.otf"],
)
def test_a_font_member_name_survives_the_declaration_round_trip(name):
    from book_maker.loader.font_obfuscation import (
        IDPF_ALGORITHM as ALGORITHM,
        _declarations,
        build_encryption_xml,
    )

    member = f"EPUB/fonts/{name}"
    xml = build_encryption_xml([(member, ALGORITHM)])

    class OneMemberArchive:
        def read(self, _path):
            return xml

    assert _declarations(OneMemberArchive()) == [(ALGORITHM, member)]


def test_the_declaration_percent_encodes_the_member_name():
    """`#` opens a fragment and a space is not legal in a URI attribute, so
    the written URI has to be encoded — `unquote` on the read side is what
    puts the name back."""
    import xml.etree.ElementTree as ET

    from book_maker.loader.font_obfuscation import (
        IDPF_ALGORITHM as ALGORITHM,
        build_encryption_xml,
    )

    xml = build_encryption_xml(
        [
            ("EPUB/fonts/Old#1.otf", ALGORITHM),
            ("EPUB/fonts/a b.otf", ALGORITHM),
            ("EPUB/fonts/a%20b.otf", ALGORITHM),
        ]
    )
    root = ET.fromstring(xml)
    assert [
        el.get("URI")
        for el in root.iter("{http://www.w3.org/2001/04/xmlenc#}CipherReference")
    ] == [
        "EPUB/fonts/Old%231.otf",
        "EPUB/fonts/a%20b.otf",
        "EPUB/fonts/a%2520b.otf",
    ]


# ------- codex review: the reference is the resource's own, not any nested one


KEYINFO_ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="{algorithm}"/>
    <enc:KeyInfo>
      <enc:EncryptedKey>
        <enc:CipherData>
          <enc:CipherReference URI="EPUB/keys/wrapped.bin"/>
        </enc:CipherData>
      </enc:EncryptedKey>
    </enc:KeyInfo>
    <enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""

NESTED_ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="{algorithm}"/>
    <enc:KeyInfo>
      <enc:EncryptedData>
        <enc:EncryptionMethod Algorithm="{algorithm}"/>
        <enc:CipherData>
          <enc:CipherReference URI="EPUB/fonts/inner.otf"/>
        </enc:CipherData>
      </enc:EncryptedData>
    </enc:KeyInfo>
    <enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


def _declarations_of(text):
    from book_maker.loader.font_obfuscation import _declarations

    class OneMemberArchive:
        def read(self, _path):
            return text.encode("utf-8")

    return _declarations(OneMemberArchive())


def test_the_reference_read_is_the_resources_own_cipher_data():
    """A `KeyInfo/EncryptedKey` may carry a `CipherReference` of its own,
    and it appears first. Searching the whole subtree picked *that* — so
    the font was looked up under the wrapped key's name, went unresolved,
    and the key blob would have been unscrambled in its place had it been
    a manifest item. The reference is the one in the resource's own
    `CipherData`, nothing else."""
    declarations = _declarations_of(
        KEYINFO_ENCRYPTION.format(
            algorithm=IDPF_ALGORITHM, uri="EPUB/fonts/obfuscated.otf"
        )
    )
    assert declarations == [(IDPF_ALGORITHM, "EPUB/fonts/obfuscated.otf")]


def test_a_nested_encrypted_data_is_declared_once_and_only_for_itself():
    """The inner entry is its own declaration; the outer one must not
    borrow its reference and report it twice."""
    declarations = _declarations_of(
        NESTED_ENCRYPTION.format(
            algorithm=IDPF_ALGORITHM, uri="EPUB/fonts/obfuscated.otf"
        )
    )
    assert declarations == [
        (IDPF_ALGORITHM, "EPUB/fonts/obfuscated.otf"),
        (IDPF_ALGORITHM, "EPUB/fonts/inner.otf"),
    ]


def test_an_entry_with_no_cipher_data_of_its_own_declares_nothing():
    """No `CipherData` child names no resource, so there is nothing to
    restore and nothing to invent from elsewhere in the entry."""
    declarations = _declarations_of("""<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="%s"/>
    <enc:KeyInfo><enc:CipherReference URI="EPUB/keys/stray.bin"/></enc:KeyInfo>
  </enc:EncryptedData>
</encryption>
""" % IDPF_ALGORITHM)
    assert declarations == []
