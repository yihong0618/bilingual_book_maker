"""What the translation *was*, in the few facts that concern a reader.

The disclosure module says, on one line inside the book, that the text was
machine translated. This says the same thing to a machine, and says the
little more of it that a person checking a translation needs: which model
did the work, when, and — if the run was pinned to a glossary — which
verbatim substitutions it was told to make.

It says nothing about the operator. Owner ruling, 260906: a book that
leaves this machine must not be traceable back to the person who ran it, so
the command line, the build of the tool, the endpoint it talked to and the
route it took are not recorded anywhere. What is left is the shortlist of
things that can have gone wrong with the *translation* — the model, the
date, the pins — and nothing that identifies who asked for it.

Two shapes carry it.

- **`bbm_translation_metadata.json` is the record.** Four keys at most, and
  the first of them is `generator`: not a fact about the run but the string
  that makes the file recognisably this tool's from the inside, so a rerun
  can find and replace its own record after a conversion has rewritten
  everything around it.
- **The user's glossary rides beside it, verbatim**, as a manifest item the
  record names by checksum. It is the instruction the translation obeyed,
  it is the thing that can make a translation say what the source does not,
  and a reviewer needs the bytes rather than a summary of them.

Two rules shape every field.

- **Nothing is invented.** A fact that cannot be established is omitted,
  never guessed, and never written empty: `"model": null` reads like a
  finding, an absent key does not.
- **Nothing derived travels.** A glossary the *user* supplied is embedded,
  because it is an instruction the translation obeyed. A glossary the tool
  derived by itself is not recorded at all: it is an artifact of the run,
  it changes between runs of the same command, and stamping it would state
  that the user asked for something they did not.

Nothing here writes a package meta or a `dc:` entry any more. `bbm:` metas,
the `trl` and `bkp` contributor credits and the `dc:description` stamp were
all dropped in the same ruling; what remains of them is the recognition
side, in `book_maker.loader.disclosure`, so a rerun still strips what an
older build left behind.
"""

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from book_maker.redaction import redact

# The prefix an older build's package metas carried. Nothing writes one any
# more; the name survives because a rerun still strips every `bbm:` meta it
# finds in a book stamped before this ruling (see
# `disclosure.is_prior_disclosure`).
PREFIX = "bbm:"

# The one meta of that shape still *read*: an embedded glossary carries no
# marker of its own, so on a book stamped by an older build this is the only
# thing saying the file was ours to drop.
LEGACY_GLOSSARY_SHA_META = "bbm:glossary-sha256"

# The glossary lands in the manifest, not the spine: it is evidence about the
# translation, not a page of the book.
GLOSSARY_ID = "bbm-glossary"
GLOSSARY_STEM = "bbm_glossary"
GLOSSARY_FILE = f"{GLOSSARY_STEM}.txt"
GLOSSARY_MEDIA_TYPE = "text/plain"

# The record itself. A manifest item a converter does not understand is
# copied across; a package document is rewritten. That is why the durable
# half is a file.
TRANSLATION_METADATA_ID = "bbm-translation-metadata"
TRANSLATION_METADATA_STEM = "bbm_translation_metadata"
TRANSLATION_METADATA_FILE = f"{TRANSLATION_METADATA_STEM}.json"
TRANSLATION_METADATA_MEDIA_TYPE = "application/json"

# What makes the file ours, from the inside. Recognition, not tracing: it
# names the tool and no build, no host and no command, and it is the only
# thing a rerun has to go on once a conversion has dropped everything else.
RECORD_MARK_KEY = "generator"
RECORD_MARK = "bilingual_book_maker translation metadata record"

# The record's key for the glossary's checksum, read back by the rerun path.
GLOSSARY_SHA_KEY = "glossary-sha256"

# The whole allowed key set, pinned here and in `test_translation_metadata`
# so that adding a fifth key is a decision somebody makes on purpose rather
# than a line that slips in. Owner ruling, 260906.
RECORD_KEYS = frozenset({RECORD_MARK_KEY, "model", "date", GLOSSARY_SHA_KEY})


def _iso_date(when):
    """`when` as `YYYY-MM-DD`, or None when the run has no date to give.

    Never `date.today()`: the date this records is the one the credit line
    prints, handed in by the caller that already settled it. A second clock
    here would be a second answer, and a book written a second either side
    of midnight would say two different days about one run.
    """
    if when is None:
        return None
    isoformat = getattr(when, "isoformat", None)
    return isoformat() if callable(isoformat) else str(when)


@dataclass(frozen=True)
class TranslationMetadata:
    """Everything the package will say about the translation that made it.

    Built once, just before the book is written, and then only appended to a
    manifest — the same read-everything-then-commit discipline the credit
    line follows, so a failure here cannot leave a book carrying half a
    record.
    """

    model: str | None = None
    # The user's glossary, verbatim. `None` covers both "no glossary" and
    # "a glossary this tool derived for itself", which is not the user's
    # instruction and is deliberately not recorded.
    glossary_bytes: bytes | None = None

    @property
    def glossary_sha256(self):
        if self.glossary_bytes is None:
            return None
        return sha256(self.glossary_bytes).hexdigest()

    def record(self, when=None):
        """The fact set, as the bytes of `bbm_translation_metadata.json`.

        Four keys at most and never a fifth: `generator`, which says whose
        the file is; the model; the date; and the checksum of the embedded
        glossary. Nothing about the operator, the machine or the command.

        The omission rule is unchanged: a key present in the file is a fact
        this run actually had.

        Passed through `redact` on the way out as a last net. Nothing here
        should ever carry a credential — no field takes one — but the model
        id is a string this process was handed, and the cost of being sure
        is one pass over a few hundred bytes.
        """
        facts = (
            ("model", self.model),
            ("date", _iso_date(when)),
            (GLOSSARY_SHA_KEY, self.glossary_sha256),
        )
        body = {RECORD_MARK_KEY: RECORD_MARK}
        for key, value in facts:
            if value:
                body[key] = str(value)
        text = json.dumps(body, ensure_ascii=False, indent=2)
        return redact(text).encode("utf-8") + b"\n"


def read_glossary(path):
    """The user's glossary file, as bytes, or None with a warning.

    Read here rather than at write time so the whole record is settled before
    anything is added to the book. A glossary that cannot be read is a real
    loss — it is the one instruction a reviewer cannot reconstruct — so it is
    said out loud rather than silently dropped, and the book is still
    written.
    """
    if not path:
        return None
    try:
        return Path(str(path)).read_bytes()
    except OSError as e:
        from rich import print as rich_print
        from rich.markup import escape

        rich_print(
            "[bold yellow]Warning: the glossary could not be read for the "
            f"file's record ({type(e).__name__}: {escape(str(e))}); the book "
            "is written without a copy of it.[/bold yellow]"
        )
        return None
