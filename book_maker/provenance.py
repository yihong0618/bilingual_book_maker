"""What the run *was*, in the few facts a file can carry about its maker.

The disclosure module says, to a reader, that the book is a machine
translation. This says the same thing to a machine, and says more of it:
which build of the tool ran, which model, against which host, with which
command. A reader never sees any of it — it is `<meta>` in the package
document — and that is the point: the person who has to answer "where did
this file come from, six months later" is not reading the closing note,
they are running a script over a directory of epubs.

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

# Every provenance meta is `<meta name="bbm:…" content="…"/>` — the EPUB 2
# form, because it is the one every reading system, every library tool and
# every `unzip -p … | grep` already understands. The prefix is what makes an
# entry ours, and it is the whole ownership test: a previous run's entries
# are stripped on sight (see `disclosure.is_prior_disclosure`).
PREFIX = "bbm:"

COMMIT_META = "bbm:commit"
MODEL_META = "bbm:model"
ENDPOINT_META = "bbm:endpoint"
ROUTE_META = "bbm:route"
ARGS_META = "bbm:args"
SOURCE_LANG_META = "bbm:source-lang"
TARGET_LANG_META = "bbm:target-lang"
GLOSSARY_SHA_META = "bbm:glossary-sha256"
PROVENANCE_SHA_META = "bbm:provenance-sha256"

# The glossary lands in the manifest, not the spine: it is evidence about the
# translation, not a page of the book.
GLOSSARY_ID = "bbm-glossary"
GLOSSARY_STEM = "bbm_glossary"
GLOSSARY_FILE = f"{GLOSSARY_STEM}.txt"
GLOSSARY_MEDIA_TYPE = "text/plain"

# The same facts as the metas, in a file. The metas are the readable form and
# the file is the durable one: a Calibre conversion rewrites the package
# document and drops every `bbm:` meta with it, while a manifest item it does
# not understand is copied across. Neither is the original — they are written
# together from one `Provenance`, so they cannot disagree.
PROVENANCE_ID = "bbm-provenance"
PROVENANCE_STEM = "bbm_provenance"
PROVENANCE_FILE = f"{PROVENANCE_STEM}.json"
PROVENANCE_MEDIA_TYPE = "application/json"

# What makes the file ours. The glossary is the user's, so it can only be
# vouched for from outside (a `bbm:glossary-sha256` naming its bytes); this
# one we write, so it also says so itself — which is the ownership rule the
# colophon has always used, and the only one that still holds after a
# conversion has thrown the metas away.
RECORD_MARK_KEY = "generator"
RECORD_MARK = "bilingual_book_maker provenance record"

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
# rerun line; `test_provenance.py` fails if the two drift apart. `--prompt` is
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
# masked.
_EMBEDDED_TOKEN = re.compile(
    "(?:"
    + "|".join(
        re.escape(prefix) for prefix in TOKEN_PREFIXES if prefix.lower() != "bearer "
    )
    + r")[A-Za-z0-9_\-.]{4,}"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer[ \t]+[A-Za-z0-9._\-]{4,}")
_URL_USERINFO = re.compile(r"(?<=://)[^/@\s]{1,128}@")


def mask_embedded_secrets(text):
    """`text` with credential-shaped substrings replaced by the mask."""
    text = _URL_USERINFO.sub(f"{MASK}@", text)
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


@dataclass(frozen=True)
class Provenance:
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

    def metas(self):
        """`(name, content)` for every meta this run has something to say in.

        A fact with no value is left out rather than written empty: an absent
        `bbm:endpoint` says "this route has no endpoint", and
        `bbm:endpoint=""` says nothing at all while looking like it does.
        """
        candidates = (
            (COMMIT_META, self.commit),
            (MODEL_META, self.model),
            (ENDPOINT_META, self.endpoint),
            (ROUTE_META, self.route),
            (ARGS_META, self.args),
            (SOURCE_LANG_META, self.source_language),
            (TARGET_LANG_META, self.target_language),
            (GLOSSARY_SHA_META, self.glossary_sha256),
        )
        return [(name, str(value)) for name, value in candidates if value]

    def record(self, metas=None):
        """The same facts as `metas()`, as the bytes of `bbm_provenance.json`.

        Keys are the meta names with the `bbm:` prefix dropped, so the two
        forms are a literal mirror of each other and a test can say so in one
        line; the same omission rule applies, so a key present in the file is
        a fact this run actually had. `metas` may be passed in to guarantee
        both forms are built from one list rather than two calls.

        The one key that is not a meta is `generator`, which is what makes the
        file recognisable as ours after a conversion has dropped the metas.
        `bbm:provenance-sha256` is the one meta that is not a key: it names
        these bytes, so it cannot be inside them.
        """
        body = {RECORD_MARK_KEY: RECORD_MARK}
        for name, content in self.metas() if metas is None else metas:
            if name == PROVENANCE_SHA_META:
                continue
            body[name[len(PREFIX) :]] = content
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
