"""Checks on a finished translation that are not about batch alignment.

`Base._check_batch` decides whether a reply can be paired with what was sent
at all. What is here judges a reply that did pair, and only warns: nothing in
this module raises, retries or changes a translation.

`suspected_echo` (T3 of the 260925 skill field test): a block that came back
byte-identical to its source was accepted silently. Seen on the PDF route with
a zh-hans target: short ancient-Greek quotations, each right after the book's
own English rendering, returned as the Greek unchanged. Keeping such a quote
can be right, so the check names the block and leaves the choice to the
operator (lead 260925 with astra consult, rescoped to detection only;
docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md).

It is a script heuristic, not language identification: no model, no named
entity recognition. The question it answers is "is this identical text
mostly written in a script the target language does not use?" An en->fr
echo, or a source already in the target's script, is never flagged.
"""

import re
import unicodedata

from book_maker.markdown_spans import CODE_SPAN, MATH_SPANS
from book_maker.utils import language_code

# The least number of letters, after protected content is removed, that
# counts as prose worth warning about. A chosen margin, not a measurement:
# long enough to leave out a name ("Plato", "ΣΩΚΡΑΤΗΣ") or a one-word term,
# short enough to catch a one-line quotation.
MIN_PROSE_LETTERS = 12

# The share of those letters that must be in a script the target does not
# use. Chosen, not measured: a clear majority, so a Chinese sentence carrying
# a Latin name or two is still "in the target's script".
MIN_FOREIGN_SHARE = 0.6

# More than this share of the cased words capitalised reads as a list of
# names or a title, which may rightly stay as it is. Chosen, not measured.
MAX_CAPITALISED_SHARE = 0.5

# What is never judged: code, maths, URLs, markup and identifiers. Removed
# before letters are counted, so a block that is nothing but these says
# nothing. The one caller (the reading edition) judges the source block as
# written and the reply after its placeholders are restored, and a fenced
# code block is never sent, so neither placeholders nor fences reach here.
# The text is whitespace-collapsed first, so a code span needs no DOTALL.
_PROTECTED = [
    CODE_SPAN,
    *(re.compile(pattern, flags) for pattern, flags in MATH_SPANS),
    re.compile(r"\]\([^)\s]*(?:\s+\"[^\"]*\")?\)"),  # a link's target, not its text
    re.compile(r"<(?:https?://|mailto:)[^>\s]+>"),  # autolinks
    re.compile(r"(?:https?://|www\.)\S+"),  # bare URLs
    re.compile(r"<[A-Za-z/!?][^>]*>"),  # html/xml tags
    re.compile(r"\{#[^}]*\}"),  # heading identifiers
    # Identifiers: snake_case, letters mixed with digits, camelCase, dotted.
    re.compile(r"\b\w*_\w*\b"),
    re.compile(r"\b(?=\w*\d)(?=\w*[^\W\d_])\w+\b"),
    re.compile(r"\b[a-z]+[A-Z]\w*\b"),
    re.compile(r"\b\w+(?:\.\w+){2,}\b"),
]

_WORD = re.compile(r"[^\W\d_]+")

# The first words of a character's Unicode name that belong to one script.
# Anything not listed is its name's first word (BENGALI, TAMIL, ...), which
# is what the table below names those scripts by.
_SCRIPT_PREFIXES = (
    ("CJK", "HAN"),
    ("IDEOGRAPHIC", "HAN"),
    ("HIRAGANA", "KANA"),
    ("KATAKANA", "KANA"),
    ("HALFWIDTH KATAKANA", "KANA"),
    ("HANGUL", "HANGUL"),
    ("HALFWIDTH HANGUL", "HANGUL"),
    ("FULLWIDTH LATIN", "LATIN"),
)

_LATIN = frozenset({"LATIN"})
_CYRILLIC = frozenset({"CYRILLIC"})
_ARABIC = frozenset({"ARABIC"})
_DEVANAGARI = frozenset({"DEVANAGARI"})

# Target language (primary subtag) -> the scripts its prose is written in.
# A language missing here is one whose script this check cannot tell, and it
# is never flagged.
TARGET_SCRIPTS = {
    **{
        code: _LATIN
        for code in (
            "af az br bs ca cs cy da de en es et eu fi fo fr ga gl ha haw hr "
            "ht hu id is it jw la lb ln lt lv mg mi ms mt nl nn no oc pl pt "
            "ro sk sl sn so sq su sv sw tk tl tr uz vi yo"
        ).split()
    },
    **{code: _CYRILLIC for code in "ba be bg kk mk mn ru tg tt uk".split()},
    "sr": _CYRILLIC | _LATIN,
    "zh": frozenset({"HAN", "BOPOMOFO"}),
    "yue": frozenset({"HAN"}),
    "ja": frozenset({"HAN", "KANA"}),
    "ko": frozenset({"HANGUL", "HAN"}),
    "el": frozenset({"GREEK"}),
    **{code: _ARABIC for code in "ar fa ps sd ug ur".split()},
    **{code: frozenset({"HEBREW"}) for code in "he yi".split()},
    **{code: _DEVANAGARI for code in "hi mr ne sa".split()},
    "bn": frozenset({"BENGALI"}),
    "as": frozenset({"BENGALI"}),
    "pa": frozenset({"GURMUKHI"}),
    "gu": frozenset({"GUJARATI"}),
    "ta": frozenset({"TAMIL"}),
    "te": frozenset({"TELUGU"}),
    "kn": frozenset({"KANNADA"}),
    "ml": frozenset({"MALAYALAM"}),
    "si": frozenset({"SINHALA"}),
    "th": frozenset({"THAI"}),
    "lo": frozenset({"LAO"}),
    "km": frozenset({"KHMER"}),
    "my": frozenset({"MYANMAR"}),
    "bo": frozenset({"TIBETAN"}),
    "ka": frozenset({"GEORGIAN"}),
    "hy": frozenset({"ARMENIAN"}),
    "am": frozenset({"ETHIOPIC"}),
}


# ISO 15924 script subtag (lowercased) -> the scripts `_script` names. A
# script subtag in the target tag decides on its own: `az-Cyrl` is written
# in Cyrillic whatever `az` alone would be, `sr-Latn` in Latin only. A
# 4-letter subtag not listed here is a script this check cannot tell, and
# nothing is flagged (lead 260925, Codex review 01a0dc74,
# docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md).
SCRIPT_SUBTAGS = {
    "latn": _LATIN,
    "cyrl": _CYRILLIC,
    "arab": _ARABIC,
    "deva": _DEVANAGARI,
    "hans": frozenset({"HAN"}),
    "hant": frozenset({"HAN"}),
    "hani": frozenset({"HAN"}),
    "bopo": frozenset({"BOPOMOFO"}),
    "jpan": frozenset({"HAN", "KANA"}),
    "hira": frozenset({"KANA"}),
    "kana": frozenset({"KANA"}),
    "hrkt": frozenset({"KANA"}),
    "kore": frozenset({"HANGUL", "HAN"}),
    "hang": frozenset({"HANGUL"}),
    "grek": frozenset({"GREEK"}),
    "hebr": frozenset({"HEBREW"}),
    "beng": frozenset({"BENGALI"}),
    "guru": frozenset({"GURMUKHI"}),
    "gujr": frozenset({"GUJARATI"}),
    "taml": frozenset({"TAMIL"}),
    "telu": frozenset({"TELUGU"}),
    "knda": frozenset({"KANNADA"}),
    "mlym": frozenset({"MALAYALAM"}),
    "sinh": frozenset({"SINHALA"}),
    "thai": frozenset({"THAI"}),
    "laoo": frozenset({"LAO"}),
    "khmr": frozenset({"KHMER"}),
    "mymr": frozenset({"MYANMAR"}),
    "tibt": frozenset({"TIBETAN"}),
    "geor": frozenset({"GEORGIAN"}),
    "armn": frozenset({"ARMENIAN"}),
    "ethi": frozenset({"ETHIOPIC"}),
}


def _script_subtag(subtags):
    """The tag's script subtag, lowercased, or None.

    In a BCP 47 tag it follows the language, or a 3-letter extended language
    subtag: `zh-Hans`, `zh-yue-Hant`. A region or variant ends the search.
    """
    for subtag in subtags[1:3]:
        if len(subtag) == 4 and subtag.isascii() and subtag.isalpha():
            return subtag.lower()
        if not (len(subtag) == 3 and subtag.isascii() and subtag.isalpha()):
            return None
    return None


def _normalized(text):
    """NFC, whitespace runs collapsed to one space, ends stripped."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()


def _script(char):
    name = unicodedata.name(char, "")
    for prefix, script in _SCRIPT_PREFIXES:
        if name.startswith(prefix):
            return script
    return name.split(" ", 1)[0]


def target_scripts(target_language):
    """The scripts `target_language` is written in, or None when unknown.

    Takes what `--language` takes: a tag (`zh-hans`, `pt-BR`) or a name the
    tables know (`simplified chinese`). A script subtag wins over the
    language (`az-Cyrl` is Cyrillic); an unknown one is None.
    """
    tag = language_code(target_language)
    if not tag:
        return None
    subtags = tag.split("-")
    script = _script_subtag(subtags)
    if script is not None:
        return SCRIPT_SUBTAGS.get(script)
    return TARGET_SCRIPTS.get(subtags[0].lower())


def _unprotected(text):
    for pattern in _PROTECTED:
        text = pattern.sub(" ", text)
    return text


def _names_only(text):
    """Most of the cased words are capitalised: names, or a title."""
    cased = [
        word for word in _WORD.findall(text) if word[0].isupper() or word[0].islower()
    ]
    if not cased:
        return False
    capitalised = sum(1 for word in cased if word[0].isupper())
    return capitalised / len(cased) > MAX_CAPITALISED_SHARE


def suspected_echo(source, translation, target_language):
    """Whether `translation` looks like `source` handed back untranslated.

    True only when the two are identical (NFC, whitespace collapsed) and what
    remains once protected content is removed is prose: at least
    `MIN_PROSE_LETTERS` letters, not mostly capitalised words, with at least
    `MIN_FOREIGN_SHARE` of the letters in a script the target language does
    not use. A target whose script is unknown is never flagged.
    """
    original = _normalized(source)
    if not original or original != _normalized(translation):
        return False
    expected = target_scripts(target_language)
    if not expected:
        return False
    remainder = _unprotected(original)
    letters = [char for char in remainder if char.isalpha()]
    if len(letters) < MIN_PROSE_LETTERS:
        return False
    if _names_only(remainder):
        return False
    foreign = sum(1 for char in letters if _script(char) not in expected)
    return foreign / len(letters) >= MIN_FOREIGN_SHARE
