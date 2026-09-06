"""What the run *was*, in the few facts a file can carry about its maker.

The disclosure module says, to a reader, that the book is a machine
translation. This says the same thing to a machine, and says more of it:
which build of the tool ran, which model, against which host, with which
command. A reader never sees any of it, and that is the point: the person
who has to answer "where did this file come from, six months later" is not
reading the closing note, they are running a script over a directory of
epubs.

It is written twice, in two deliberately different shapes.

- **`bbm_translation_metadata.json` is the record.** It carries the full fact set,
  and it is the copy that survives: a manifest item is copied across a
  conversion, and it says whose it is from the inside (`generator`), so it
  is still identifiable when everything around it has been rewritten.
- **The `bbm:` metas are a marker, not a copy of the record.** Any editor
  strips them in a keystroke, so a rich meta set buys nothing that the
  record does not already buy. Three of them: the tool and its build, the
  model, and the date — what a person peering into the OPF wants to see,
  and no more. The two forms are *not* mirrors, and nothing should be
  written that assumes they are.

Three rules shape every field here.

- **Nothing is invented.** A fact that cannot be established is omitted,
  never guessed: no endpoint meta beats an endpoint meta naming a host the
  run never contacted.
- **Nothing secret travels.** The command line is recorded because the shape
  of a run is what makes it reproducible, but a key, a prompt and anything
  shaped like a bearer token are taken out of it first — and then the whole
  line goes through `redaction.redact`, which knows the values this run was
  actually handed. The produced zip must not contain them in any member.
- **Nothing derived travels either.** A glossary the *user* supplied is
  embedded verbatim, because it is an instruction the translation obeyed and
  a reviewer needs to see it. A glossary the tool derived by itself is not
  recorded at all: it is an artifact of the run, it changes between runs of
  the same command, and stamping it would state that the user asked for
  something they did not.
"""

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from book_maker.redaction import redact

# Every meta we write is `<meta name="bbm:…" content="…"/>` — the EPUB 2
# form, because it is the one every reading system, every library tool and
# every `unzip -p … | grep` already understands. The prefix is what makes an
# entry ours, and it is the whole ownership test: a previous run's entries
# are stripped on sight (see `disclosure.is_prior_disclosure`).
PREFIX = "bbm:"

# The three, and only three, metas a run writes. The first is the marker —
# the name says the tool, the content says which build of it — and it is
# always written, because it is what makes the package recognisably ours
# at a glance. The other two are the facts a casual inspector is actually
# looking for. Everything else the run knows is in `bbm_translation_metadata.json`.
MARKER_META = "bbm:bilingual_book_maker"
MODEL_META = "bbm:model"
DATE_META = "bbm:date"

# Older builds wrote a meta per fact. None of them is written any more, and
# a rerun strips every `bbm:` name whatever it is (see
# `disclosure.is_prior_disclosure`), so only the one that a previous run's
# *evidence* hangs off is still named here: the glossary carries no marker
# of its own, so a book stamped by such a build vouches for its embedded
# glossary in this meta and nowhere else.
LEGACY_GLOSSARY_SHA_META = "bbm:glossary-sha256"

# The glossary lands in the manifest, not the spine: it is evidence about the
# translation, not a page of the book.
GLOSSARY_ID = "bbm-glossary"
GLOSSARY_STEM = "bbm_glossary"
GLOSSARY_FILE = f"{GLOSSARY_STEM}.txt"
GLOSSARY_MEDIA_TYPE = "text/plain"

# The whole fact set, in a file — the durable half: a Calibre conversion
# rewrites the package document and drops every `bbm:` meta with it, while a
# manifest item it does not understand is copied across. The metas are a
# marker beside it, not a second copy of it; both are built from one
# `TranslationMetadata` and one clock, so the facts they do share cannot disagree.
TRANSLATION_METADATA_ID = "bbm-translation-metadata"
TRANSLATION_METADATA_STEM = "bbm_translation_metadata"
TRANSLATION_METADATA_FILE = f"{TRANSLATION_METADATA_STEM}.json"
TRANSLATION_METADATA_MEDIA_TYPE = "application/json"

# What makes the file ours, from the inside — the ownership rule the colophon
# has always used, and the only one that still holds after a conversion has
# thrown the metas away. It is also what vouches for the embedded glossary,
# which is the user's file byte for byte and can carry no marker: the record
# names its checksum, and the record says whose it is.
RECORD_MARK_KEY = "generator"
RECORD_MARK = "bilingual_book_maker translation metadata record"

# The record's key for that checksum, read back by the rerun path.
GLOSSARY_SHA_KEY = "glossary-sha256"

# MARC relator "bkp" — book producer. The `trl` contributor says what did the
# translating; this one says what built the file, and carries the build.
PRODUCER_ID = "bbm-bkp"
PRODUCER_ROLE = "bkp"

# The distribution name in pyproject.toml, for the pip-install case where
# there is no checkout to ask.
DISTRIBUTION = "bbook-maker"
UNKNOWN = "unknown"

MASK = "<redacted>"


# ------------------------------------------------------------ which build ran


@lru_cache(maxsize=1)
def tool_commit():
    """The build of this tool that produced the file.

    A checkout answers with its short commit, which is the only identifier
    that pins the code exactly. Asked with the *package's own* directory as
    the working directory, and only believed when the repository it finds is
    the one this package lives in — an installed copy sitting inside some
    unrelated project's tree would otherwise report that project's HEAD, a
    hash that names the wrong source entirely.

    A pip install has no checkout, so it answers with its version. Neither
    available (a vendored copy, a zipapp, no git on PATH) is `unknown`:
    saying nothing about the build is a fact, and a wrong hash is not.

    Cached because it shells out and the answer cannot change inside one run;
    `tool_commit.cache_clear()` is the test hook.
    """
    package = Path(__file__).resolve().parent
    top = _git(package, "rev-parse", "--show-toplevel")
    if top:
        try:
            same = Path(top).resolve() == package.parent.resolve()
        except OSError:
            same = False
        if same:
            commit = _git(package, "rev-parse", "--short", "HEAD")
            if commit:
                return commit

    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(DISTRIBUTION)
    except (ImportError, PackageNotFoundError, ValueError, OSError):
        pass
    return UNKNOWN


def _git(cwd, *args):
    """`git …` in `cwd`, or None for anything that is not a clean answer.

    Every failure mode is the same answer: no git on PATH, not a repository,
    a repository with no commit yet, a hung index lock. None of them is worth
    a traceback in the middle of writing a book.
    """
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


# --------------------------------------------------------------- which host


def endpoint_host(api_base):
    """The host `api_base` points at, and nothing else about it.

    A path says which API version the run happened to use, a port and a
    userinfo say things about the operator's network and credentials, and
    none of the three is what "where did this text come from" means. So
    `https://user:pw@api.openai.com:443/v1/chat` records as `api.openai.com`.

    A bare host with no scheme (`api.openai.com/v1`) is accepted too: the
    flag takes what the endpoint's own documentation printed, and that is
    not always a URL.
    """
    if not api_base:
        return None
    text = str(api_base).strip()
    if not text:
        return None
    parts = urlsplit(text)
    if not parts.netloc:
        # No scheme, so urlsplit put the whole thing in `path`. Re-read it as
        # an authority.
        parts = urlsplit("//" + text.lstrip("/"))
    try:
        host = parts.hostname
    except ValueError:
        # An authority urlsplit will parse but not decompose (a malformed
        # IPv6 literal). Recording nothing beats recording the brackets.
        return None
    return host or None


# ---------------------------------------------------------- which command


# Long options whose *value* is a credential. Mirrors
# `epub_loader.KEY_FLAG_ENV`, which exists for the same reason on the printed
# rerun line; `test_translation_metadata.py` fails if the two drift apart. `--prompt` is
# here for a different reason: it is not a secret, it is the instruction the
# translation was given, and it can be a paragraph of text or a path into
# someone's home directory. Neither belongs in a file that gets sent around.
SECRET_VALUE_FLAGS = frozenset(
    {
        "--key",
        "--api_key",
        "--openai_key",
        "--caiyun_key",
        "--deepl_key",
        "--claude_key",
        "--gemini_key",
        "--groq_key",
        "--xai_key",
        "--orcarouter_key",
        "--qwen_key",
        "--extra_headers",
        "--prompt",
    }
)

# Short options carrying the same. `-k` is not defined by this CLI today; it
# is the spelling a key flag would take if one ever were, and costs nothing
# to refuse in advance.
SECRET_SHORT_FLAGS = frozenset({"-k"})

# What a credential looks like when it arrives somewhere no flag explains —
# a positional, an `--extra_body` field, a value the parser never saw. Only
# known credential prefixes, never a length or entropy rule: `claude-haiku-
# 4-5-20251001` is twenty-five opaque-looking characters and is a model id,
# and masking it would make the record useless at exactly the moment it
# matters.
TOKEN_PREFIXES = (
    "sk-",
    "sk_",
    "pk-",
    "gsk_",
    "xai-",
    "ghp_",
    "gho_",
    "github_pat_",
    "hf_",
    "AIza",
    "Bearer ",
    "bearer ",
)


def is_secret_flag(arg):
    """Whether `arg` is an option whose value must not be recorded.

    Exact spellings are not enough: argparse accepts any unambiguous
    abbreviation, so `--openai_k sk-…` is a valid invocation an exact lookup
    would copy out verbatim. Anything that *prefixes* one of the flags above
    is therefore treated as that flag — an abbreviation ambiguous enough that
    argparse would have refused the command included, because a secret must
    not reach the file on the strength of an argument nobody accepted.
    """
    if arg in SECRET_SHORT_FLAGS:
        return True
    if not arg.startswith("--") or len(arg) < 3:
        return False
    return any(flag.startswith(arg) for flag in SECRET_VALUE_FLAGS)


def looks_like_token(arg):
    return any(arg.startswith(prefix) for prefix in TOKEN_PREFIXES)


# Secrets hiding *inside* an otherwise recordable value, where neither net
# above looks: a field in `--extra_body '{"api_key": "sk-…"}'`, credentials
# written into a URL (`https://user:secret@host/v1`), an Authorization value
# in `--extra_headers`. The whole argument is worth keeping — the field
# names and the host are the record — so only the credential substring is
# masked. The lookbehind anchors a prefix to the start of a word: without
# it `desk-notes.epub` carries `sk-notes.epub` and a harmless book argument
# came out of the record as `de<redacted>` (reverify finding, 260906).
_EMBEDDED_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(
        re.escape(prefix) for prefix in TOKEN_PREFIXES if prefix.lower() != "bearer "
    )
    + r")[A-Za-z0-9_\-.]{4,}"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._\-]{4,}")
_URL_USERINFO = re.compile(r"(?<=://)[^/@\s]{1,128}@")
# A field whose *name* announces a credential, whatever shape its value
# has: `"api_key": "secondary-secret"` has no token prefix for the shape
# net to see, and the value never passed through this process's hands for
# `redact` to know. The name is the evidence, so the name decides — and
# the name is exactly what stays in the record. An argument that actually
# parses as JSON (an `--extra_body` value) is walked as JSON, because a
# regex over string contents is quote-blind — `"prefix'secondary-secret"`
# ended the old match at the apostrophe (reverify finding, 260906). The
# regex stays as the fallback for JSON-shaped fragments inside text that
# does not parse whole, with each value matched to its own opening quote.
_SECRET_NAMES = frozenset(
    name.replace("-", "_")
    for name in (
        "api_key",
        "apikey",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "authorization",
        "auth",
    )
)
_SECRET_FIELD = re.compile(
    r"([\"'](?:api[_-]?key|apikey|key|token|access[_-]?token|refresh[_-]?token"
    r"|secret|client[_-]?secret|password|authorization|auth)[\"']\s*:\s*)"
    r"(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*')",
    re.IGNORECASE,
)


def _mask_json_value(node):
    if isinstance(node, dict):
        return {
            key: (
                MASK
                if isinstance(key, str)
                and key.lower().replace("-", "_") in _SECRET_NAMES
                and not isinstance(value, (dict, list))
                else _mask_json_value(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_mask_json_value(value) for value in node]
    return node


def _mask_json_part(text):
    """`text` rewritten through a real JSON walk, or None when it isn't one.

    Handles the bare object argument and the `--flag={…}` joined form; the
    prefix before the first brace is kept as typed.
    """
    prefix, brace, payload = text.partition("{")
    if not brace:
        return None
    try:
        data = json.loads(brace + payload)
    except ValueError:
        return None
    return prefix + json.dumps(_mask_json_value(data), ensure_ascii=False)


def mask_embedded_secrets(text):
    """`text` with credential-shaped substrings replaced by the mask."""
    structured = _mask_json_part(text)
    if structured is not None:
        text = structured
    text = _URL_USERINFO.sub(f"{MASK}@", text)
    text = _SECRET_FIELD.sub(rf'\1"{MASK}"', text)
    text = _BEARER_VALUE.sub(MASK, text)
    return _EMBEDDED_TOKEN.sub(MASK, text)


def sanitize_args(argv=None):
    """The run's command line, with everything that must not travel removed.

    The shape is kept whole — every flag, every path, every model id, in the
    order they were typed — because the shape is the reason to record it at
    all: someone reading the file six months later can see that the book was
    translated in test mode, at an accumulated_num of 12, against a proxy.
    Only the values that are secret or personal become `<redacted>`, so the
    line still reads as the command it was.

    Three nets, deliberately overlapping: the flag that announces a value,
    the shape of a value that announces itself, and finally `redact`, which
    knows the key strings this process was actually handed and catches one
    that arrived somewhere neither of the first two looked.
    """
    argv = list(sys.argv if argv is None else argv)
    parts = []
    pending = False
    for arg in argv:
        arg = str(arg)
        if pending:
            parts.append(MASK)
            pending = False
            continue
        flag, joined, _value = arg.partition("=")
        if is_secret_flag(flag):
            if joined:
                # `--key=sk-…`: the value never becomes its own argv entry
                parts.append(f"{flag}={MASK}")
            else:
                parts.append(arg)
                pending = True
            continue
        if looks_like_token(arg):
            parts.append(MASK)
            continue
        parts.append(arg)
    # A key flag as the very last entry leaves `pending` set and nothing to
    # mask: argparse would have refused such a command, and the flag itself
    # is already recorded.
    parts = [mask_embedded_secrets(part) for part in parts]
    return redact(" ".join(shlex.quote(part) for part in parts))


# ------------------------------------------------------------- the facts


def _iso_date(when):
    """`when` as `YYYY-MM-DD`, or None when the run has no date to give.

    Never `date.today()`: the date this records is the one the closing page
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
    """Everything the package document will say about the run that made it.

    Built once, just before the book is written, and then only appended to a
    dict and a list — the same read-everything-then-commit discipline the
    disclosure stamp follows, so a failure here cannot leave a book carrying
    half a record.
    """

    commit: str = UNKNOWN
    model: str | None = None
    endpoint: str | None = None
    route: str | None = None
    args: str = ""
    source_language: str | None = None
    target_language: str | None = None
    # The user's glossary, verbatim. `None` covers both "no glossary" and
    # "a glossary this tool derived for itself", which is not the user's
    # instruction and is deliberately not recorded.
    glossary_bytes: bytes | None = None

    @property
    def glossary_sha256(self):
        if self.glossary_bytes is None:
            return None
        return sha256(self.glossary_bytes).hexdigest()

    def metas(self, when=None):
        """`(name, content)` for the three metas a run writes.

        Deliberately not the record. A `bbm:` meta is stripped by any editor
        that touches the package document, so a full set of them would be an
        audit trail that evaporates on first contact — the audit trail is
        `record()`, which travels as a file. These three are the marker
        (always written, `unknown` build and all) and the two facts someone
        opening the OPF by hand is looking for.

        `when` is the run's date, the same `datetime.date` the closing page
        prints, so the two cannot name different days. Not knowing it leaves
        the meta out, under the omission rule the record follows: an absent
        fact says nothing, and an empty one says nothing while looking like
        it says something.
        """
        candidates = (
            (MARKER_META, self.commit or UNKNOWN),
            (MODEL_META, self.model),
            (DATE_META, _iso_date(when)),
        )
        return [(name, str(value)) for name, value in candidates if value]

    def record(self, when=None):
        """The whole fact set, as the bytes of `bbm_translation_metadata.json`.

        This is the durable half and the complete one. It used to be built
        from `metas()`, key for key, and its docstring promised the two were
        a literal mirror; that promise is retired — the metas are now a
        three-entry marker and the file is the record, so they are composed
        separately and only the facts they both name (the build, the model,
        the date) are shared, from one `TranslationMetadata` and one `when`.

        The omission rule is unchanged: a key present in the file is a fact
        this run actually had. `generator` is the one key that is not a fact
        about the run — it is what makes the file recognisable as ours after
        a conversion has dropped the metas.
        """
        facts = (
            ("commit", self.commit),
            ("model", self.model),
            ("date", _iso_date(when)),
            ("endpoint", self.endpoint),
            ("route", self.route),
            ("args", self.args),
            ("source-lang", self.source_language),
            ("target-lang", self.target_language),
            (GLOSSARY_SHA_KEY, self.glossary_sha256),
        )
        body = {RECORD_MARK_KEY: RECORD_MARK}
        for key, value in facts:
            if value:
                body[key] = str(value)
        return json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"

    def producer(self):
        """What the `bkp` contributor says: the tool, and which build of it."""
        from book_maker.loader.disclosure import TOOL_NAME

        if self.commit and self.commit != UNKNOWN:
            return f"{TOOL_NAME} {self.commit}"
        return TOOL_NAME


def read_glossary(path):
    """The user's glossary file, as bytes, or None with a warning.

    Read here rather than at write time so the whole record is settled before
    anything is added to the book. A glossary that cannot be read is a real
    loss — the audit trail is the reason the flag was given — so it is said
    out loud rather than silently dropped, and the book is still written.
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
