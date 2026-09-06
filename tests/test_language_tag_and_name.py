"""`--language` states a tag, a name, or both.

One string used to drive two unrelated things: the prose the model reads
("translate into simplified chinese") and the machinery around it — the
structured field name, the `lang=` stamp on the markup, the first
`dc:language` of the output. Matching a typed name back to a tag works for
the languages the tables know and silently produces nothing for the ones
they miss, which is every small language and every free-typed name.

`--language TAG:NAME` states both halves instead of guessing one from the
other. This file pins the split at every level it exists at: the parse, the
field name, the stamp on the written book, the refusal of a half-written
value, and the one line a run says when a name matched nothing.

The wire-level half — what the endpoint actually receives — lives in
tests/test_prompt_capture_server.py, where a real subprocess talks to a real
local server.
"""

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from ebooklib import epub

from book_maker.cli import language_guidance, source_evidence
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    batch_translation_schema,
    single_field_name,
    single_translation_schema,
)
from book_maker.utils import (
    LANGUAGES,
    TO_LANGUAGE_CODE,
    language_code,
    parse_language_spec,
)

REPO = Path(__file__).resolve().parent.parent
HERMETIC = Path(__file__).resolve().parent / "hermetic"
DOC = REPO / "docs" / "languages.md"


# ------------------------------------------------------------ the parse


class TestTheParse:
    def test_a_colon_states_both_halves(self):
        spec = parse_language_spec("zh-hant:Traditional Chinese")

        assert spec.tag == "zh-hant"
        assert spec.name == "Traditional Chinese"
        assert spec.pinned is True

    def test_only_the_first_colon_separates(self):
        """A name may contain one; a tag may not."""
        spec = parse_language_spec("nan:Hokkien: the Taiwanese variety")

        assert spec.tag == "nan"
        assert spec.name == "Hokkien: the Taiwanese variety"

    def test_the_halves_are_stripped(self):
        spec = parse_language_spec("  zh-hant : Traditional Chinese  ")

        assert (spec.tag, spec.name) == ("zh-hant", "Traditional Chinese")

    @pytest.mark.parametrize("value", [":Traditional Chinese", "zh-hant:", ":", " : "])
    def test_a_half_written_value_is_refused(self, value):
        with pytest.raises(ValueError):
            parse_language_spec(value)

    def test_a_bare_tag_keeps_itself_as_the_tag(self):
        """Retires the pin that read `spec.tag == "zh"` here.

        The parse used to resolve the tag to its English name and match that
        name back to a tag. A name lookup returns one tag, so with `zh` and
        `zh-hans` both named "simplified chinese" the trip home landed on
        `zh` and a default `--language zh-hans` run stamped `zh`. The table
        no longer shares that name, and the parse no longer takes the trip:
        a tag the table knows is already the answer.
        """
        spec = parse_language_spec("zh-hans")

        assert spec.name == "simplified chinese"
        assert spec.tag == "zh-hans"
        assert spec.pinned is False
        assert spec.known is True

    def test_the_operators_own_casing_survives(self):
        """The table is asked for prose, never for the tag to stamp."""
        spec = parse_language_spec("pt-BR")

        assert spec.tag == "pt-BR"
        assert spec.name == "brazilian portuguese"
        assert spec.known is True

    def test_a_bare_name_resolves_to_its_own_tag(self):
        """Retires the pin that read `spec.tag == "zh"` here too: the name
        now belongs to `zh-hans` alone, and `zh` answers to "chinese"."""
        spec = parse_language_spec("Simplified Chinese")

        assert spec.name == "Simplified Chinese"
        assert spec.tag == "zh-hans"
        assert spec.pinned is False
        assert spec.known is True

    def test_the_macro_language_has_a_name_of_its_own(self):
        """A bare `zh` asks for "chinese", not for a script it never named —
        so its prose and its structured field say `chinese`."""
        assert parse_language_spec("zh").name == "chinese"
        assert parse_language_spec("Chinese").tag == "zh"

    def test_free_text_travels_as_the_name_and_stamps_nothing(self):
        spec = parse_language_spec("whatever the model calls it")

        assert spec.name == "whatever the model calls it"
        assert spec.tag is None
        assert spec.known is False


# ------------------------------------------------------- the pairs hold


class TestEveryPairRoundTrips:
    """Each tag owns a name, and that name answers with the same tag.

    The `zh-hans` bug was not one bad row, it was a shape the tables allow:
    two tags sharing an English name, with the reverse map keeping only the
    last one. Nothing in the code notices — the name resolves, a tag comes
    back, and it is the wrong tag. So the property is pinned for the whole
    table rather than for the row that was found by hand.

    There is no allowlist. A pair that cannot round-trip does not belong in
    `LANGUAGES`: an entry needing a second spelling puts that spelling in
    `TO_LANGUAGE_CODE`, which is where aliases live and where being
    many-to-one is the point.
    """

    def test_no_two_tags_share_a_name(self):
        shared = {}
        for tag, name in LANGUAGES.items():
            shared.setdefault(name, []).append(tag)

        assert not {n: t for n, t in shared.items() if len(t) > 1}

    def test_every_name_answers_with_its_own_tag(self):
        wrong = {
            tag: (name, language_code(name))
            for tag, name in LANGUAGES.items()
            if language_code(name) != tag
        }

        assert not wrong

    def test_no_alias_takes_a_printed_name_away(self):
        """`TO_LANGUAGE_CODE` is built name-first, then aliases are laid over
        it: an alias repeating a printed name would silently move that name's
        tag, which is the same failure one table over."""
        printed = set(LANGUAGES.values())
        aliases = {k: v for k, v in TO_LANGUAGE_CODE.items() if k not in printed}

        assert not (set(aliases) & printed)

    def test_the_chinese_pairs_are_the_owner_ruling(self):
        assert LANGUAGES["zh"] == "chinese"
        assert LANGUAGES["zh-hans"] == "simplified chinese"
        assert LANGUAGES["zh-hant"] == "traditional chinese"
        assert language_code("chinese") == "zh"
        assert language_code("simplified chinese") == "zh-hans"
        assert language_code("traditional chinese") == "zh-hant"


class TestTheSourceEvidence:
    """`--source_lang` is the only place the source is stated now."""

    def test_auto_states_nothing(self):
        assert source_evidence("auto") is None
        assert source_evidence("AUTO") is None
        assert source_evidence("") is None
        assert source_evidence(None) is None

    def test_a_code_is_spelled_out_for_the_prompt(self):
        assert source_evidence("en") == "english"

    def test_anything_else_travels_as_written(self):
        assert source_evidence("Middle English") == "Middle English"


# ------------------------------------------------------- the field name


def _route(language, field_tag=None):
    route = ChatGPTAPI.__new__(ChatGPTAPI)
    route.language = language
    route.language_field_tag = field_tag
    return route


class TestTheFieldName:
    def test_a_pinned_tag_names_the_field(self):
        route = _route("Traditional Chinese", "zh-hant")

        assert single_field_name(route.field_language) == "zh_hant_translation"
        assert batch_field_name(route.field_language) == "zh_hant_paragraphs"

    def test_a_bare_language_still_names_it_from_the_prose(self):
        route = _route("traditional chinese")

        assert (
            single_field_name(route.field_language) == "traditional_chinese_translation"
        )

    def test_the_descriptions_keep_the_name(self):
        """The field is named for the tag; what the model is *told* is the
        name, in the description beside it."""
        schema = single_translation_schema("Traditional Chinese", "zh-hant")
        field = schema["schema"]["properties"]["zh_hant_translation"]

        assert schema["name"] == "zh_hant_translation"
        assert "Traditional Chinese" in field["description"]
        assert "zh-hant" not in field["description"]

    def test_the_batch_schema_splits_the_same_way(self):
        schema = batch_translation_schema("Traditional Chinese", 3, None, "zh-hant")
        array = schema["schema"]["properties"]["zh_hant_paragraphs"]

        assert schema["name"] == "zh_hant_paragraphs"
        assert "zh_hant_translation" in array["items"]["properties"]
        assert "Traditional Chinese" in array["description"]


# ------------------------------------------------- what an engine is asked

from book_maker.translator.deepl_translator import DeepL, deepl_target  # noqa: E402
from book_maker.translator.google_translator import Google, google_target  # noqa: E402


class TestTheEnginesSpellItTheirOwnWay:
    """The MT routes put a code in an outbound request, and the vendors do
    not agree with BCP-47 or with each other: Google wants `zh-CN`, DeepL
    has one `zh` and no script subtag at all. Renaming `zh` moved what
    `TO_LANGUAGE_CODE["simplified chinese"]` answers, which is the value
    both of them read — so each vendor's spelling is made in that vendor's
    own module, and the shared table stays the tag a book is stamped with.

    These routes are handed `LanguageSpec.name`, never the tag (`cli.py`
    passes `target.name` to the model), so the name is what is pinned here.
    """

    def test_google_asks_for_the_script_google_knows(self):
        assert google_target("simplified chinese") == "zh-CN"
        assert google_target("traditional chinese") == "zh-TW"
        assert google_target("chinese") == "zh-CN"

    def test_google_folds_a_regional_variant_it_has_no_code_for(self):
        assert google_target("british english") == "en"
        assert google_target("brazilian portuguese") == "pt"
        assert google_target("norwegian") == "no"

    def test_google_leaves_anything_else_as_the_table_has_it(self):
        assert google_target("japanese") == "ja"
        assert google_target("ja") == "ja"
        assert google_target("Klingon") == "Klingon"

    def test_the_default_language_still_reaches_google_as_a_code(self):
        """The canary: `zh-hans` is the default, and a `tl=` the endpoint
        does not know is a book that comes back untranslated."""
        assert "tl=zh-CN" in Google("", "simplified chinese").api_url

    def test_deepl_gets_the_one_zh_it_supports(self):
        """DeepL's allowlist is its own target list — `zh-hans` is not on
        it, so without this mapping the default `--language` would raise."""
        assert deepl_target("simplified chinese") == "zh"
        assert DeepL("k", "simplified chinese").language == "zh"

    def test_deepl_keeps_its_own_casing_of_a_regional_code(self):
        assert deepl_target("brazilian portuguese") == "pt-BR"
        assert deepl_target("british english") == "en-GB"

    def test_deepl_matches_a_name_whatever_its_case(self):
        """`--help` advertises Title-cased names; the lookup used to be
        case-sensitive and answered `None` to every one of them."""
        assert deepl_target("Japanese") == "ja"
        assert DeepL("k", "Japanese").language == "ja"

    def test_deepl_still_refuses_a_language_it_does_not_have(self):
        with pytest.raises(Exception, match="DeepL do not support"):
            DeepL("k", "Klingon")


# ---------------------------------------------------------- the stamp


def _stampable_book(path):
    """A book whose paragraphs declare their own language.

    `stamp_translation` only stamps what the source already stamped — a book
    that declares no languages gets no new ones — so a fixture that declares
    none could not show what the stamp carries.
    """
    book = epub.EpubBook()
    book.set_identifier("language-stamp-fixture")
    book.set_title("Stamp fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="one.xhtml", lang="en")
    chapter.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<p lang="en">The cat sat on the mat.</p>'
        '<p lang="en">The dog slept by the door.</p>'
        "</body></html>"
    )
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    book.toc = (chapter,)
    epub.write_epub(str(path), book)
    return path


def _translate(tmp_path, language):
    """One real run through the offline stand-in, and what it wrote."""
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    source = _stampable_book(tmp_path / "tiny.epub")
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(source),
            "--api_format",
            "google",
            "--language",
            language,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = tmp_path / "tiny_bilingual.epub"
    assert out.exists(), proc.stdout + proc.stderr
    with zipfile.ZipFile(out) as archive:
        opf = next(n for n in archive.namelist() if n.endswith(".opf"))
        package = archive.read(opf).decode("utf-8")
        document = archive.read(
            next(n for n in archive.namelist() if n.endswith("one.xhtml"))
        ).decode("utf-8")
    return package, document


class TestTheStamp:
    """What lands in the file is the tag, never the name a model was given.

    Two sites, both pinned because they are written by different code:
    `dc:language` in the package document (`_make_new_book`) and the `lang=`
    on each inserted paragraph (`stamp_translation`).
    """

    def test_a_pinned_tag_is_what_the_book_declares(self, tmp_path):
        package, document = _translate(tmp_path, "zh-hant:Traditional Chinese")

        declared = re.findall(r"<dc:language>([^<]*)</dc:language>", package)
        assert declared[0] == "zh-hant"
        assert "Traditional Chinese" not in package

    def test_a_pinned_tag_is_what_the_inserted_markup_carries(self, tmp_path):
        package, document = _translate(tmp_path, "zh-hant:Traditional Chinese")

        assert 'lang="zh-hant"' in document
        assert "Traditional Chinese" not in document

    def test_the_default_language_stamps_the_tag_it_was_given(self, tmp_path):
        """`zh-hans` is the default, so this is what most books declare.

        It declared `zh` until 260906: the parse resolved the tag to
        "simplified chinese" and matched that name back to `zh`, which also
        carried it. A run asked for the simplified script now says so.
        """
        package, document = _translate(tmp_path, "zh-hans")

        declared = re.findall(r"<dc:language>([^<]*)</dc:language>", package)
        assert declared[0] == "zh-hans"
        assert 'lang="zh-hans"' in document

    def test_a_name_the_tables_do_not_know_stamps_nothing(self, tmp_path):
        """The failure this flag exists to give a way out of: prose no tag
        can be made from leaves the source's own declaration standing."""
        package, document = _translate(tmp_path, "whatever the model calls it")

        declared = re.findall(r"<dc:language>([^<]*)</dc:language>", package)
        assert declared == ["en"]
        assert "whatever the model calls it" not in document


# ---------------------------------------------------------- the refusal


def _cli(*args):
    """The parser's answer, with nothing else in the way: no book is named,
    so a value that survives the parse dies one line later."""
    import os

    env = dict(os.environ)
    env["COLUMNS"] = "200"
    return subprocess.run(
        [sys.executable, "make_book.py", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


class TestAHalfWrittenValueIsRefused:
    @pytest.mark.parametrize("value", [":Traditional Chinese", "zh-hant:"])
    def test_it_exits_two_with_one_line(self, value):
        proc = _cli("--language", value)

        assert proc.returncode == 2
        errors = [
            line
            for line in proc.stderr.splitlines()
            if line.startswith("make_book.py: error:")
        ]
        assert errors == [
            "make_book.py: error: argument --language: --language TAG:NAME "
            "needs both halves, as in 'zh-hant:Traditional Chinese'"
        ]


# -------------------------------------------------------- the narration


class TestTheGuidanceLine:
    """Said once, before anything is paid for, and only when it is true."""

    LINE = (
        "Note: --language Klingon matched no known language tag, so nothing "
        "is stamped on the output markup. Use the tag (--language zh-hant) "
        'or state both (--language "zh-hant:Traditional Chinese"); the tags '
        "are listed in docs/languages.md."
    )

    def test_free_text_that_matched_nothing_is_narrated_once(self):
        proc = _cli("--language", "Klingon")
        out = " ".join(proc.stdout.split())

        assert out.count("matched no known language tag") == 1
        assert self.LINE in out

    def test_a_tag_says_nothing(self):
        proc = _cli("--language", "zh-hant")

        assert "matched no known language tag" not in proc.stdout

    def test_a_stated_pair_says_nothing(self):
        proc = _cli("--language", "kok:Konkani")

        assert "matched no known language tag" not in proc.stdout

    def test_a_name_the_tables_know_says_nothing(self):
        proc = _cli("--language", "Traditional Chinese")

        assert "matched no known language tag" not in proc.stdout

    def test_the_rule_itself(self):
        assert language_guidance(parse_language_spec("zh-hant")) is None
        assert language_guidance(parse_language_spec("nan:Hokkien")) is None
        assert language_guidance(parse_language_spec("Klingon")) is not None


# --------------------------------------------------------- the doc guard


def _alias_rows():
    """Names accepted that are not the name a tag prints back."""
    printed = set(LANGUAGES.values())
    return {k: v for k, v in TO_LANGUAGE_CODE.items() if k not in printed}


class TestTheLanguageDocIsInStep:
    """docs/languages.md is the address the guidance line sends people to.

    A table that has grown past it sends them to a page that does not list
    the language they were told to look up, which is worse than no page.
    """

    def test_every_tag_is_listed(self):
        text = DOC.read_text(encoding="utf-8")
        missing = [
            f"{tag} ({name})"
            for tag, name in LANGUAGES.items()
            if f"| `{tag}` | {name} |" not in text
        ]

        assert not missing, f"not in docs/languages.md: {', '.join(missing)}"

    def test_every_other_accepted_spelling_is_listed(self):
        text = DOC.read_text(encoding="utf-8")
        missing = [
            alias
            for alias, tag in _alias_rows().items()
            if f"| `{alias}` | `{tag}` |" not in text
        ]

        assert not missing, f"not in docs/languages.md: {', '.join(missing)}"

    def test_the_doc_lists_nothing_the_tables_dropped(self):
        """The other direction: a row left behind by a removed entry."""
        text = DOC.read_text(encoding="utf-8")
        rows = re.findall(r"^\| `([^`]+)` \| ", text, flags=re.MULTILINE)

        assert len(rows) == len(LANGUAGES) + len(_alias_rows())
        assert set(rows) == set(LANGUAGES) | set(_alias_rows())

    def test_the_intro_names_both_halves_and_the_pair_form(self):
        """The lead-written intro: the load-bearing statements stay."""
        text = DOC.read_text(encoding="utf-8")

        assert "PLACEHOLDER" not in text
        assert "TAG:NAME" in text
        assert "zh_hant_translation" in text
        assert "--source_lang" in text
