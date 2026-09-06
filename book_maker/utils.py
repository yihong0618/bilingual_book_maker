import re
from dataclasses import dataclass

import tiktoken

# Borrowed from : https://github.com/openai/whisper
LANGUAGES = {
    "en": "english",
    "zh-hans": "simplified chinese",
    # Whisper's list paired `zh` with "simplified chinese" too, which made
    # the two tags share one name: the reverse map is last-wins, so the name
    # resolved to `zh` and a bare `--language zh-hans` stamped `zh`. `zh` is
    # the macro-language, so it takes the macro-language's name and the
    # script subtags keep theirs. Every pair in these tables round-trips
    # (`tests/test_language_tag_and_name.py::TestEveryPairRoundTrips`).
    "zh": "chinese",
    "zh-hant": "traditional chinese",
    "zh-yue": "cantonese",
    "de": "german",
    "es": "spanish",
    "ru": "russian",
    "ko": "korean",
    "fr": "french",
    "ja": "japanese",
    "pt": "portuguese",
    "tr": "turkish",
    "pl": "polish",
    "ca": "catalan",
    "nl": "dutch",
    "ar": "arabic",
    "sv": "swedish",
    "it": "italian",
    "id": "indonesian",
    "hi": "hindi",
    "fi": "finnish",
    "vi": "vietnamese",
    "he": "hebrew",
    "uk": "ukrainian",
    "el": "greek",
    "ms": "malay",
    "cs": "czech",
    "ro": "romanian",
    "da": "danish",
    "hu": "hungarian",
    "ta": "tamil",
    "no": "norwegian",
    "th": "thai",
    "ur": "urdu",
    "hr": "croatian",
    "bg": "bulgarian",
    "lt": "lithuanian",
    "la": "latin",
    "mi": "maori",
    "ml": "malayalam",
    "cy": "welsh",
    "sk": "slovak",
    "te": "telugu",
    "fa": "persian",
    "lv": "latvian",
    "bn": "bengali",
    "sr": "serbian",
    "az": "azerbaijani",
    "sl": "slovenian",
    "kn": "kannada",
    "et": "estonian",
    "mk": "macedonian",
    "br": "breton",
    "eu": "basque",
    "is": "icelandic",
    "hy": "armenian",
    "ne": "nepali",
    "mn": "mongolian",
    "bs": "bosnian",
    "kk": "kazakh",
    "sq": "albanian",
    "sw": "swahili",
    "gl": "galician",
    "mr": "marathi",
    "pa": "punjabi",
    "si": "sinhala",
    "km": "khmer",
    "sn": "shona",
    "yo": "yoruba",
    "so": "somali",
    "af": "afrikaans",
    "oc": "occitan",
    "ka": "georgian",
    "be": "belarusian",
    "tg": "tajik",
    "sd": "sindhi",
    "gu": "gujarati",
    "am": "amharic",
    "yi": "yiddish",
    "lo": "lao",
    "uz": "uzbek",
    "fo": "faroese",
    "ht": "haitian creole",
    "ps": "pashto",
    "tk": "turkmen",
    "nn": "nynorsk",
    "mt": "maltese",
    "sa": "sanskrit",
    "lb": "luxembourgish",
    "my": "myanmar",
    "bo": "tibetan",
    "tl": "tagalog",
    "mg": "malagasy",
    "as": "assamese",
    "tt": "tatar",
    "haw": "hawaiian",
    "ln": "lingala",
    "ha": "hausa",
    "ba": "bashkir",
    "jw": "javanese",
    "su": "sundanese",
    # --- added beyond whisper's list -------------------------------------
    # Everything below is appended, never interleaved: the reverse map is
    # built last-wins, so an entry that repeated a name already above would
    # silently move that name's code and change the tag stamped on books
    # already being translated. Every name here is unique for that reason.
    #
    # Regional variants people actually ask for. Each needs its own English
    # name for the same reason: "zh-tw": "traditional chinese" would have
    # taken "traditional chinese" away from zh-hant.
    "en-gb": "british english",
    "en-us": "american english",
    "pt-br": "brazilian portuguese",
    "pt-pt": "european portuguese",
    "es-419": "latin american spanish",
    "es-mx": "mexican spanish",
    "fr-ca": "canadian french",
    "zh-cn": "mainland chinese",
    "zh-tw": "taiwan mandarin",
    "zh-hk": "hong kong chinese",
    "nb": "norwegian bokmal",
    # Europe
    "ga": "irish",
    "gd": "scottish gaelic",
    "kw": "cornish",
    "gv": "manx",
    "sco": "scots",
    "fy": "western frisian",
    "nds": "low german",
    "gsw": "swiss german",
    "wa": "walloon",
    "ast": "asturian",
    "an": "aragonese",
    "co": "corsican",
    "sc": "sardinian",
    "scn": "sicilian",
    "fur": "friulian",
    "rm": "romansh",
    "se": "northern sami",
    "kl": "greenlandic",
    "eo": "esperanto",
    "ia": "interlingua",
    "grc": "ancient greek",
    "cu": "church slavonic",
    # Caucasus, Central Asia, Siberia
    "ab": "abkhaz",
    "os": "ossetian",
    "ce": "chechen",
    "cv": "chuvash",
    "sah": "yakut",
    "udm": "udmurt",
    "kv": "komi",
    "myv": "erzya",
    "ky": "kyrgyz",
    "ug": "uyghur",
    # West and South Asia
    "ku": "kurdish",
    "ckb": "central kurdish",
    "arc": "aramaic",
    "syr": "syriac",
    "dv": "dhivehi",
    "or": "odia",
    "mai": "maithili",
    "bho": "bhojpuri",
    "doi": "dogri",
    "ks": "kashmiri",
    "sat": "santali",
    "mni": "manipuri",
    "kok": "konkani",
    "brx": "bodo",
    "new": "newari",
    "pi": "pali",
    "dz": "dzongkha",
    "shn": "shan",
    # Southeast Asia and the Pacific
    "fil": "filipino",
    "ceb": "cebuano",
    "ilo": "ilocano",
    "hil": "hiligaynon",
    "war": "waray",
    "pam": "kapampangan",
    "bcl": "bikol",
    "mad": "madurese",
    "ban": "balinese",
    "ace": "acehnese",
    "bug": "buginese",
    "min": "minangkabau",
    "sm": "samoan",
    "to": "tongan",
    "fj": "fijian",
    "ty": "tahitian",
    "ch": "chamorro",
    "bi": "bislama",
    "tpi": "tok pisin",
    # Africa
    "ig": "igbo",
    "zu": "zulu",
    "xh": "xhosa",
    "st": "southern sotho",
    "nso": "northern sotho",
    "tn": "tswana",
    "ts": "tsonga",
    "ve": "venda",
    "ss": "swati",
    "nr": "southern ndebele",
    "ny": "chichewa",
    "rw": "kinyarwanda",
    "rn": "kirundi",
    "lg": "ganda",
    "wo": "wolof",
    "ff": "fulah",
    "ak": "akan",
    "ee": "ewe",
    "bm": "bambara",
    "ti": "tigrinya",
    "om": "oromo",
    "aa": "afar",
    "sg": "sango",
    "kg": "kongo",
    "lu": "luba-katanga",
    "umb": "umbundu",
    "kam": "kamba",
    "luo": "luo",
    "ki": "kikuyu",
    "mer": "meru",
    "nyn": "nyankole",
    # The Americas
    "ay": "aymara",
    "qu": "quechua",
    "gn": "guarani",
    "chr": "cherokee",
    "nv": "navajo",
    "iu": "inuktitut",
    "cr": "cree",
    "oj": "ojibwe",
    "pap": "papiamento",
    # --- appended 260906, with the zh pair above --------------------------
    # Same rule as the block before it: appended, never interleaved, every
    # name distinct from every other. Chinese varieties first, because the
    # `zh` fix above is what brought this block about — a book asked for in
    # "Chinese" now says so, and the ones that are not Mandarin say that.
    "lzh": "literary chinese",
    "wuu": "wu chinese",
    "nan": "hokkien",
    "hak": "hakka",
    "zh-sg": "singapore chinese",
    # Regional variants of the big European languages, alongside the ones
    # already listed above (en-gb/en-us, pt-br/pt-pt, es-419/es-mx, fr-ca).
    "en-au": "australian english",
    "en-ca": "canadian english",
    "en-in": "indian english",
    "de-at": "austrian german",
    "de-ch": "swiss standard german",
    "es-es": "european spanish",
    "es-ar": "argentine spanish",
    # "flemish" is already an accepted spelling of `nl`, so this row cannot
    # take that name without moving it.
    "nl-be": "belgian dutch",
    # Script subtags, the other half of what the zh fix was about.
    "sr-latn": "serbian latin",
    "sr-cyrl": "serbian cyrillic",
    # Arabic varieties written as books, under their own ISO codes rather
    # than as regions of `ar`.
    "arz": "egyptian arabic",
    "ary": "moroccan arabic",
    "apc": "levantine arabic",
    "kab": "kabyle",
    "tzm": "central atlas tamazight",
    # Historical and literary languages: what a book translator is actually
    # asked for that a speech-recognition list never carried.
    "ang": "old english",
    "enm": "middle english",
    "fro": "old french",
    "goh": "old high german",
    "non": "old norse",
    "got": "gothic",
    "sga": "old irish",
    "xcl": "classical armenian",
    "pal": "middle persian",
    "cop": "coptic",
    "egy": "ancient egyptian",
    "akk": "akkadian",
    "sux": "sumerian",
    "hit": "hittite",
    # Regional languages of Europe with their own literatures.
    "hsb": "upper sorbian",
    "dsb": "lower sorbian",
    "csb": "kashubian",
    "szl": "silesian",
    "rue": "rusyn",
    "lij": "ligurian",
    "vec": "venetian",
    "nap": "neapolitan",
    "lmo": "lombard",
    "pms": "piedmontese",
    "mwl": "mirandese",
    "lad": "ladino",
    "lld": "ladin",
    # The Americas and the creoles.
    "nah": "nahuatl",
    "yua": "yucatec maya",
    "arn": "mapudungun",
    "srn": "sranan tongo",
    "jam": "jamaican patois",
    # Caucasus and Siberia, beside the ones listed above.
    "av": "avar",
    "lez": "lezgian",
    "kbd": "kabardian",
    "tyv": "tuvan",
    # Constructed, beside esperanto and interlingua above.
    "ie": "interlingue",
    "io": "ido",
    "vo": "volapuk",
    "jbo": "lojban",
}

# language code lookup by name, with a few language aliases
TO_LANGUAGE_CODE = {
    **{language: code for code, language in LANGUAGES.items()},
    "burmese": "my",
    "valencian": "ca",
    "flemish": "nl",
    "haitian": "ht",
    "letzeburgesch": "lb",
    "pushto": "ps",
    "panjabi": "pa",
    "moldavian": "ro",
    "moldovan": "ro",
    "sinhalese": "si",
    "castilian": "es",
    # --- added with the entries above ------------------------------------
    # Aliases only: a spelling people type that is not the name the table
    # prints back. None of these may repeat a name in LANGUAGES, or it would
    # take that name's code away from the entry that owns it.
    "farsi": "fa",
    # "Mandarin" names a spoken variety and states no script, so it resolves
    # to the macro-language rather than guessing a script subtag — the same
    # ruling as `zh` above. Ask for a script with `zh-hans` / `zh-hant`.
    "mandarin": "zh",
    "mandarin chinese": "zh",
    "brazilian": "pt-br",
    "bokmal": "nb",
    "oriya": "or",
    "uighur": "ug",
    "kirghiz": "ky",
    "sorani": "ckb",
    "kalaallisut": "kl",
    "chewa": "ny",
    "nyanja": "ny",
    "sesotho": "st",
    "setswana": "tn",
    "isizulu": "zu",
    "isixhosa": "xh",
    "twi": "ak",
    "scots gaelic": "gd",
    "irish gaelic": "ga",
    "frisian": "fy",
    "maldivian": "dv",
    "divehi": "dv",
    # --- appended 260906, with the entries above -------------------------
    "standard chinese": "zh",
    "putonghua": "zh",
    "classical chinese": "lzh",
    "min nan": "nan",
    "taiwanese hokkien": "nan",
    "shanghainese": "wuu",
    "norwegian nynorsk": "nn",
    "modern standard arabic": "ar",
    "old church slavonic": "cu",
    "jamaican creole": "jam",
    "judeo-spanish": "lad",
    "swiss high german": "de-ch",
    "bahasa indonesia": "id",
    "bahasa melayu": "ms",
    "pilipino": "fil",
}


# A language tag as `lang=` accepts one: "zh-hans", "ja", "pt-BR".
LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*")


def language_code(value):
    """The tag `lang=` may carry for a `--language` value, or None.

    Prose reaches here as often as a tag does — the prompt is asked for
    "simplified chinese", which written into `xml:lang` is a value
    validators reject. A wording the table knows becomes its code; a value
    it does not know is kept only when it already reads as a tag, and
    anything else stamps nothing.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    known = TO_LANGUAGE_CODE.get(text.lower())
    if known:
        return known
    if LANGUAGE_TAG.fullmatch(text):
        return text
    return None


@dataclass(frozen=True)
class LanguageSpec:
    """What `--language` asked for, split in two.

    `tag` drives everything mechanical — the structured field name, the
    `lang`/`xml:lang` stamp on the markup the loader writes, the first
    `dc:language` of the output, the translation metadata record. `name` drives every
    piece of prose: `{language}` in the prompt template, the schema field
    descriptions, what the run prints.

    `pinned` says the operator wrote the tag themselves (`TAG:NAME`), which
    is the only case where the tag overrides the field name derived from the
    prose — a bare value keeps the field name every earlier run produced.
    `known` says a bare value was found in the tables; a bare value that was
    not is what the run narrates once, at the top.
    """

    name: str
    tag: str | None
    pinned: bool
    known: bool


def parse_language_spec(value):
    """Split ``--language`` into a `LanguageSpec`.

    ``--language zh-hant:Traditional Chinese`` states both halves: the tag
    the output is stamped with and the name the model is asked for. It is
    the escape hatch for a language the tables miss, where matching a typed
    name against them would either fail or land on the wrong tag.

    A bare value that the tag table knows keeps *itself* as the tag and
    takes the table's English name for the prose. It is deliberately not
    round-tripped through that name and back: a name lookup can only return
    one tag, so any two tags sharing a name family put one of them on the
    wrong stamp (`zh-hans` stamped `zh` until 260906). Anything else travels
    as both the name and — when it reads as a tag — the stamp.

    Only the first colon separates, so a name may contain one. Raises
    ``ValueError`` when a colon is there with nothing on one side of it —
    the CLI turns that into a one-line argparse error.
    """
    text = (value or "").strip()
    if ":" in text:
        tag, _, name = text.partition(":")
        tag, name = tag.strip(), name.strip()
        if not tag or not name:
            raise ValueError(
                "--language TAG:NAME needs both halves, "
                "as in 'zh-hant:Traditional Chinese'"
            )
        return LanguageSpec(name=name, tag=tag, pinned=True, known=True)
    if not text:
        raise ValueError("--language needs a language")
    key = text.lower()
    if key in LANGUAGES:
        # A typed tag is already the answer; the table is asked only for the
        # prose. The operator's own casing survives, so `pt-BR` stamps
        # `pt-BR` rather than being folded to the table's key.
        return LanguageSpec(name=LANGUAGES[key], tag=text, pinned=False, known=True)
    return LanguageSpec(
        name=text,
        tag=language_code(text),
        pinned=False,
        known=key in TO_LANGUAGE_CODE,
    )


def prompt_config_to_kwargs(prompt_config):
    prompt_config = prompt_config or {}
    return dict(
        prompt_template=prompt_config.get("user", None),
        prompt_sys_msg=prompt_config.get("system", None),
        # A style the user has fixed. When present it replaces what a handoff
        # report would otherwise observe, and stops that section being asked
        # for at all.
        style_note=prompt_config.get("style", None),
    )


# ref: https://platform.openai.com/docs/guides/chat/introduction
def num_tokens_from_text(text, model="gpt-3.5-turbo-0301"):
    messages = (
        {
            "role": "user",
            "content": text,
        },
    )

    """Returns the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    if model == "gpt-3.5-turbo-0301":  # note: future models may deviate from this
        num_tokens = 0
        for message in messages:
            num_tokens += (
                4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            )
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens
    else:
        raise NotImplementedError(
            f"""num_tokens_from_messages() is not presently implemented for model {model}.
  See https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to tokens."""
        )
