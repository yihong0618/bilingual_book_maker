"""Run one BBM Markdown translation over a bundle, through the real CLI.

Nothing about endpoints, keys, compatibility rules or provider construction
is reimplemented here: `book_maker.cli.main` is called with the arguments the
operator wrote after `--`, plus the book this bundle holds, plus a loader
subclass that writes the reading edition and a completion record.

Two things are decided here rather than there:

* **Identity.** What was translated is the source text, the assets beside
  it, and every setting that changes what the model is asked -- including
  the contents of a prompt or glossary file, not its path. Resuming with a
  different identity would pair new source blocks with old translations, so
  it is refused instead.
* **Completion.** The Markdown loader saves progress and exits zero when it
  is interrupted, so an exit status cannot be the proof a book was
  finished. The loader writes a record after the file is on disk, and that
  record is what this module believes.
"""

import contextlib
import hashlib
import io
import json
import re
import shlex
from pathlib import Path

from .bundle import TRANSLATE_RESULT, TRANSLATE_STATE, sha256_file, sha256_text
from .errors import PipelineError
from .messages import (
    BILINGUAL_EDITED,
    BILINGUAL_EDITED_EXPORT,
    BILINGUAL_MARKDOWN_SAVED,
    SETTINGS_CHANGED,
    STAGE_COMPLETE,
    TRANSLATION_REUSED,
)
from .preflight import inspect
from .reading_edition import reading_edition_loader_class

STAGE = "translate"

# Bumped when the rendered layout changes, so a bundle translated by an
# older formatter is not resumed into a newer one.
FORMATTER_VERSION = 2

# Owned by the harness: the book is the bundle's, the resume decision is
# made from the bundle's state, and the deliverable is bilingual.
FORBIDDEN_OPTIONS = {
    "book_name": ("--book_name", "the bundle names its own source file"),
    "resume": ("--resume", "the bundle decides whether its saved state can be resumed"),
    "single_translate": (
        "--single_translate",
        "the bundle's deliverable is bilingual Markdown",
    ),
    "batch_flag": ("--batch", "a batch-API run produces no book on this path"),
    "batch_use_flag": (
        "--batch-use",
        "a batch-API run produces no book on this path",
    ),
    "retranslate": ("--retranslate", "it edits an existing translated epub"),
    "plan_dry_run": ("--plan-dry-run", "it writes a plan instead of a book"),
}

# Namespace fields whose value may carry a credential. Their contents are
# hashed into the identity and never written down: a key by any of its
# spellings (the legacy flags all arrive here as `key`), a header block that
# is where an Authorization line goes, a body block a gateway may want the
# key in, and a proxy whose URL can carry user:password.
SECRET_FIELDS = ("key", "extra_headers", "extra_body", "proxy")

# Any other value that turns out to be a URL with credentials in it --
# `--api_base https://user:secret@gateway/v1` is a real way to configure a
# private endpoint, and it must not end up in a file.
CREDENTIALED_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/@\s]*:[^/@\s]*@")

# Namespace fields naming a file whose *contents* decide what the model is
# asked. Editing the file changes nothing on the command line.
FILE_FIELDS = ("prompt_arg", "glossary_path")

# Namespace fields that say nothing about what the model is asked: the two
# the harness sets for itself, and the record of which spelling of the
# glossary flag was typed. `--glossary` and `--terminology` are one flag
# under two names, so a bundle finished under one word must be reused under
# the other rather than translated -- and paid for -- a second time.
# The sidecar endpoints say nothing about the translation either, typed or
# not: the image model shapes the extraction (whose settings have their own
# identity, and whose output is hashed above as `source`), and the Markdown
# run has no classification step.
SIDECAR_FIELDS = (
    "img_model",
    "img_base_url",
    "img_key",
    "classify_model",
    "classify_base_url",
    "classify_key",
    "classify_min_confidence",
    "plan_classify_model",
)
# How sharp the PDF route draws its figures (`pdf_figures.FigurePolicy`):
# the pixels only, never a word the model is asked, and a change redraws
# the figures without translating again (packet Q, owner 260925).
ROUTE_FIELDS = ("figure_policy", "pdf_image_dpi")
IGNORED_FIELDS = (
    ("book_name", "resume", "glossary_flag") + SIDECAR_FIELDS + ROUTE_FIELDS
)


_DEFAULTS = None


def _parser_defaults():
    """`{field: value}` of a namespace parsed from no options at all."""
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = dict(vars(parse_bbm_options([])))
    return _DEFAULTS


def parse_bbm_options(bbm_options):
    """The trailing options as the real CLI parses them.

    Everything this module needs to know -- which language, which endpoint,
    whether another workflow was asked for, what the identity of the run is
    -- comes from here rather than from a grammar of our own. It is also
    where a typo is caught: `run` must not pay for a PDF extraction and then
    find out that the translation command line never parsed.
    """
    from book_maker.cli import parse_args
    from book_maker.legacy_cli import translate_legacy_argv

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            legacy = translate_legacy_argv(list(bbm_options))
            return parse_args(legacy.argv)
    except SystemExit:
        detail = buffer.getvalue().strip().splitlines()
        raise PipelineError(
            detail[-1] if detail else "the translation options did not parse",
            stage=STAGE,
        )


def check_options(bbm_options):
    """Refuse trailing options the harness owns or cannot deliver.

    Checked against the parsed result, not against the tokens: `--batch`
    reaches the namespace as `batch_flag` however it was spelled, and a
    legacy alias that sets the book name is the same refusal as
    `--book_name`.

    The pairs the inner run would refuse are refused here too, in the CLI's
    own words. The inner run only reaches its own check after the PDF has
    been extracted, and it leaves behind an exit status the harness can only
    report as "stopped before finishing" -- so the operator would pay a
    minute of Java and OCR to be told something that was true of the command
    line they typed.
    """
    from book_maker.cli import (
        PARALLEL_SESSION_REFUSAL,
        compat_stops,
        parallel_session_conflict,
    )

    options = parse_bbm_options(bbm_options)
    for field, (flag, why) in FORBIDDEN_OPTIONS.items():
        if getattr(options, field, None):
            raise PipelineError(f"{flag} cannot be passed through: {why}", stage=STAGE)
    if parallel_session_conflict(options):
        raise PipelineError(PARALLEL_SESSION_REFUSAL, stage=STAGE)
    if getattr(options, "plan_classify", None) == "agent":
        raise PipelineError(
            "--plan-classify agent cannot be passed through: it stops before "
            "translating and hands off a plan",
            stage=STAGE,
        )
    # The compatibility table's stop rows, for the Markdown book the inner
    # run translates (packet F, after the port's Codex review: the route
    # diverts before `check_compatibility`, so a stop there came after the
    # extraction was paid for).
    stops = compat_stops(options, "md")
    if stops:
        raise PipelineError(" ".join(stops), stage=STAGE)
    return list(bbm_options)


def option_identity(options):
    """The parsed settings, as a list safe to hash and to write down.

    Built from the namespace so that last-value wins exactly as it does in
    the run itself: `--language zh-hans --language ja` is a Japanese book,
    and must not share an identity with the reverse order. A credential
    becomes a hash of itself; a prompt or glossary file becomes a hash of
    what is in it.
    """
    identity = []
    defaults = _parser_defaults()
    for name, value in sorted(vars(options).items()):
        if name in IGNORED_FIELDS:
            continue
        # A value the parser would have supplied anyway says nothing about
        # the run, so it is not written down: a flag added later, at its
        # default, then leaves every finished bundle's identity alone. Before
        # 260924 the whole namespace went in, and each new flag (packets F,
        # G, H that day) re-fingerprinted every bundle -- a completed one
        # translated and paid for again, an interrupted one refused with
        # SETTINGS_CHANGED (Codex, packet H). The branch was unreleased; the
        # bundles written before this change are the owner's own and match
        # no longer, once. A field the parser does not know is kept.
        if name in defaults and value == defaults[name]:
            continue
        if name in SECRET_FIELDS or (
            isinstance(value, str) and CREDENTIALED_URL.match(value)
        ):
            identity.append(
                (name, f"secret:{sha256_text(str(value))}" if value else "unset")
            )
            continue
        if name in FILE_FIELDS and value:
            path = Path(str(value))
            if path.is_file():
                identity.append((name, f"file:{sha256_file(path)}"))
            else:
                identity.append((name, f"literal:{sha256_text(str(value))}"))
            continue
        identity.append(
            (
                name,
                (
                    value
                    if isinstance(value, (bool, int, float))
                    else str(value) if value is not None else None
                ),
            )
        )
    if getattr(options, "provider", None):
        # A named endpoint is shorthand for an address and a model list that
        # live in a file; the file's contents belong to the identity, and
        # its contents do not belong in the manifest.
        identity.append(("provider_config", _provider_config_digest()))
    return identity


def _provider_config_digest():
    from book_maker import provider_loader

    digest = hashlib.sha256()
    for path in (
        provider_loader.EXAMPLE_CONFIG_PATH,
        provider_loader.GLOBAL_CONFIG_PATH,
        Path.cwd() / provider_loader.LOCAL_CONFIG_FILENAME,
    ):
        digest.update(str(path).encode("utf-8"))
        digest.update(
            sha256_file(path).encode("ascii") if Path(path).is_file() else b"-"
        )
    return digest.hexdigest()


def translation_fingerprint(bundle, bbm_options, options=None):
    """One hash over everything that decides what the translations are."""
    options = options if options is not None else parse_bbm_options(bbm_options)
    payload = {
        "formatter": FORMATTER_VERSION,
        "source": sha256_text(bundle.source.read_text(encoding="utf-8")),
        "assets": _translated_assets(bundle),
        "options": option_identity(options),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _translated_assets(bundle):
    """The asset fingerprints the translation identity holds.

    Every asset, except the figures a PDF extraction drew
    (`assets/figures/p0003-01.png`): their names are in `source.md`, which
    is hashed, and their pixels change with the figure policy alone -- a
    redraw must not translate the book again, or refuse to resume it. A
    Markdown import's own images, whatever their directory, stay hashed.
    """
    from .pdf_figures import is_drawn_figure

    kind = None
    if bundle.manifest_path.is_file():
        kind = (bundle.read_manifest().get("source") or {}).get("kind")
    return {
        path: digest
        for path, digest in bundle.asset_fingerprints().items()
        if not (kind == "pdf" and is_drawn_figure(path))
    }


def language_tag(options):
    """The normalized target tag, resolved by the CLI's own parser."""
    from book_maker.utils import parse_language_spec

    try:
        return parse_language_spec(options.language).tag
    except ValueError as err:
        raise PipelineError(f"--language is not usable: {err}", stage=STAGE)


def translate_bundle(bundle, bbm_options, *, pandoc, main=None):
    """Translate `source.md` into `book_bilingual.md`. Returns its path."""
    bbm_options = check_options(bbm_options)
    options = parse_bbm_options(bbm_options)
    manifest = bundle.read_manifest()
    stages = manifest.get("stages") or {}
    if not any(
        (stages.get(name) or {}).get("status") == "completed"
        for name in ("import", "extract")
    ):
        raise PipelineError(
            "this bundle has no completed source; import or extract first",
            stage=STAGE,
        )
    if not bundle.source.is_file():
        raise PipelineError(f"no {bundle.source.name} in the bundle", stage=STAGE)

    _refuse_to_clobber_edits(bundle, manifest)

    report = inspect(
        bundle.source.read_text(encoding="utf-8"),
        root=bundle.root,
        pandoc=pandoc,
        for_translation=True,
    )
    report.raise_if_problems(STAGE)
    for line in report.preserved_lines():
        print(line)
    bundle.add_limitations(report.preserved_lines())

    fingerprint = translation_fingerprint(bundle, bbm_options, options)
    if _already_translated(bundle, manifest, stages, fingerprint):
        # Same source, same settings, the recorded output still in place:
        # running the translation again would buy the same book twice. A
        # rerun after a failed export, or to rebuild the EPUB, lands here.
        print(TRANSLATION_REUSED.format(path=bundle.bilingual_markdown))
        return bundle.bilingual_markdown
    resume = _resume_decision(bundle, manifest, fingerprint)

    tag = language_tag(options)
    sample = bool(options.test)
    bundle.update_manifest(
        translation={
            "fingerprint": fingerprint,
            "language_tag": tag,
            "formatter": FORMATTER_VERSION,
            "options": option_identity(options),
            "sample": sample,
        }
    )
    bundle.set_stage(STAGE, "running", resumed=resume)

    completion = bundle.work_file(TRANSLATE_RESULT)
    completion.unlink(missing_ok=True)

    argv = ["--book_name", str(bundle.source)]
    if resume:
        argv.append("--resume")
    argv += bbm_options

    # Pandoc's own identifiers for the source headings, in document order:
    # the reading edition stamps them back onto the same headings, so an
    # internal link written against the source still resolves in the
    # bilingual file and in the EPUB.
    loader = reading_edition_loader_class(
        bundle,
        language_tag=tag,
        heading_ids=report.top_level_heading_ids,
        pandoc=pandoc,
    )
    runner = main
    if runner is None:
        from book_maker.cli import main as runner

    try:
        runner(argv, markdown_loader_class=loader)
    except SystemExit as err:
        # Zero included: the loader's interrupt path exits zero after
        # saving progress, and that is not a finished book.
        if not completion.is_file():
            bundle.set_stage(STAGE, "failed", exit_code=err.code)
            raise PipelineError(
                f"the translation run stopped before finishing (exit {err.code}); "
                f"rerun translate to resume",
                stage=STAGE,
            )
    except PipelineError:
        bundle.set_stage(STAGE, "failed")
        raise
    except Exception as err:
        bundle.set_stage(STAGE, "failed")
        raise PipelineError(f"{type(err).__name__}: {err}", stage=STAGE) from err

    record = _completion_record(bundle, completion)
    bundle.update_manifest(
        outputs={
            "bilingual_markdown": {
                "path": bundle.bilingual_markdown.name,
                "sha256": record["output_sha256"],
                "pairs": record.get("pairs"),
            }
        },
        translation={"sample": bool(record.get("sample")) or sample},
    )
    bundle.set_stage(
        STAGE, "completed", pairs=record.get("pairs"), batches=record.get("batches")
    )
    print(BILINGUAL_MARKDOWN_SAVED.format(path=bundle.bilingual_markdown))
    print(STAGE_COMPLETE.format(stage=STAGE))
    return bundle.bilingual_markdown


class BilingualEdited(PipelineError):
    """The refusal of a hand-edited `book_bilingual.md`.

    Its own type so the `--to-epub` route, which knows the copy beside the
    PDF it writes, can name that copy too (`naming_copy`): the harness
    export writes only inside the bundle (lead 260925, Codex review
    01a0dc74, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md). Raised as it
    is, from the staged harness, it names the export alone.
    """

    def __init__(self, bundle):
        super().__init__(
            BILINGUAL_EDITED_EXPORT.format(bundle=shlex.quote(str(bundle.root))),
            stage=STAGE,
        )
        self.bundle = bundle

    def naming_copy(self, copy):
        """The same refusal, naming the export and then the copy."""
        return PipelineError(
            BILINGUAL_EDITED.format(
                bundle=shlex.quote(str(self.bundle.root)),
                bundle_epub=shlex.quote(str(self.bundle.epub)),
                copy=shlex.quote(str(copy)),
            ),
            stage=self.stage,
        )


def _refuse_to_clobber_edits(bundle, manifest):
    """A hand-edited bilingual file is the deliverable, not scratch space."""
    if not bundle.bilingual_markdown.is_file():
        return
    recorded = ((manifest.get("outputs") or {}).get("bilingual_markdown") or {}).get(
        "sha256"
    )
    actual = sha256_file(bundle.bilingual_markdown)
    if recorded and recorded == actual:
        return
    raise BilingualEdited(bundle)


def _already_translated(bundle, manifest, stages, fingerprint):
    """Whether the bundle already holds this exact translation, finished.

    Three things must agree: the stage completed, the recorded fingerprint
    is this run's (source, options, formatter), and the bilingual file is
    the one that run wrote (`_refuse_to_clobber_edits` has already refused
    an edited one, so a hash match here means untouched). Deleting the
    bilingual file is how a translation is redone on purpose.
    """
    if (stages.get(STAGE) or {}).get("status") != "completed":
        return False
    if (manifest.get("translation") or {}).get("fingerprint") != fingerprint:
        return False
    if not bundle.bilingual_markdown.is_file():
        return False
    recorded = ((manifest.get("outputs") or {}).get("bilingual_markdown") or {}).get(
        "sha256"
    )
    return bool(recorded) and recorded == sha256_file(bundle.bilingual_markdown)


def _resume_decision(bundle, manifest, fingerprint):
    """Whether the saved batches still belong to this source and settings."""
    state = bundle.work_file(TRANSLATE_STATE)
    if not state.is_file():
        return False
    recorded = (manifest.get("translation") or {}).get("fingerprint")
    if recorded != fingerprint:
        raise PipelineError(SETTINGS_CHANGED, stage=STAGE)
    return True


def _completion_record(bundle, completion):
    if not completion.is_file():
        bundle.set_stage(STAGE, "failed", reason="no completion record")
        raise PipelineError(
            "the translation run wrote no completion record; the book was not "
            "finished",
            stage=STAGE,
        )
    record = json.loads(completion.read_text(encoding="utf-8"))
    if not record.get("completed") or record.get("untranslated_batches"):
        bundle.set_stage(STAGE, "failed", reason="incomplete translation")
        raise PipelineError(
            f"{record.get('untranslated_batches')} batches were not translated; "
            f"rerun translate to resume",
            stage=STAGE,
        )
    if not bundle.bilingual_markdown.is_file():
        bundle.set_stage(STAGE, "failed", reason="no output")
        raise PipelineError(
            "the translation run wrote no bilingual Markdown", stage=STAGE
        )
    if sha256_file(bundle.bilingual_markdown) != record["output_sha256"]:
        raise PipelineError(
            "the bilingual Markdown changed after it was written", stage=STAGE
        )
    return record
