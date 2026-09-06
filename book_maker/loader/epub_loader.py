import builtins
import difflib
import hashlib
import json
import os
import pickle
import re
import shlex
import string
import sys
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from hashlib import sha256
from pathlib import Path
import traceback
from threading import Lock

from bs4 import BeautifulSoup as bs
from bs4 import Tag
from bs4.element import NavigableString
from ebooklib import ITEM_DOCUMENT, epub
from rich import print
from rich.markup import escape
from tqdm import tqdm

from book_maker.redaction import redact
from book_maker.session_context import handoff_path
from book_maker.utils import num_tokens_from_text, prompt_config_to_kwargs

from .base_loader import BaseBookLoader
from .helper import (
    language_tag,
    stamp_translation,
    restamp_language,
    EPUBBookLoaderHelper,
    append_inline_translation,
    backfill_toc_hrefs,
    derive_translation_identity,
    has_restricted_content_model,
    is_text_link,
    make_tag,
    not_trans,
    package_prefixes,
    rebase_ncx_srcs,
    shorter_result_link,
    strip_duplicate_ids,
    translate_list_or_singles,
)
from .disclosure import (
    entry_is_our_colophon,
    is_calibre_metadata,
    is_our_colophon,
    is_prior_disclosure,
    model_id,
    stamp_disclosure,
    tool_contributor_ids,
    translation_label,
)
from .font_obfuscation import deobfuscate_fonts, reobfuscate_written_epub
from .rights import DRM_MESSAGE, check_epub
from .plan import (
    GENERAL_GROUP_MAX_UNITS,
    PLAN_SCHEMA_VERSION,
    BookCss,
    TranslationPlan,
    UnsafeSingleTranslateError,
    file_segment_hazards,
    file_sha256,
    inline_subtree_root,
    is_fixed_layout,
    is_simple_owner,
    load_plan_overrides,
    partition_file,
    planning_settings,
    session_token_budget,
)
from ..session_context import derived_compact_budget
from .markers import (
    MARKER_OPEN,
    find_markers,
    marker_report,
    reconcile_markers,
    split_on_markers,
)
from ..translator.base_translator import BatchMismatch
from .classify import (
    PlanClassifyError,
    PlanUnresolvedError,
    build_agent_prompt,
    classify_plan,
    decide_everything,
    mode_policy,
)

# Every key-bearing CLI flag, and the environment variable it falls back to
# when it is absent. The rerun line printed at a plan handoff is rebuilt from
# argv, so a key typed on the command line would otherwise be echoed to the
# terminal, written into whatever log the run is piped to, and pasted whole
# into an agent session — three copies of a secret, on a run that spends
# nothing and looks harmless. Naming the variable rather than masking with
# `***` keeps the printed command runnable for anyone who exports it.
KEY_FLAG_ENV = {
    "--openai_key": "BBM_OPENAI_API_KEY",
    "--caiyun_key": "BBM_CAIYUN_API_KEY",
    "--deepl_key": "BBM_DEEPL_API_KEY",
    "--claude_key": "BBM_CLAUDE_API_KEY",
    "--gemini_key": "BBM_GOOGLE_GEMINI_KEY",
    "--groq_key": "BBM_GROQ_API_KEY",
    "--xai_key": "BBM_XAI_API_KEY",
    "--orcarouter_key": "BBM_ORCAROUTER_API_KEY",
    "--qwen_key": "BBM_QWEN_API_KEY",
    # --api_key belongs to --provider, whose key variable is named by the
    # provider entry rather than fixed here; the generic name is a variable
    # the user exports, which is all the placeholder has to be.
    "--api_key": "BBM_API_KEY",
    # This fork's one key flag. Which variable would have supplied it depends
    # on the endpoint (FORMAT_ENV_KEYS in cli.py) and a provider entry may
    # name its own, so the placeholder is the fallback every format accepts.
    "--key": "BBM_API_KEY",
}


def _key_flag_env(flag):
    """The env var standing in for `flag`'s value, or None if it carries no key.

    Exact spellings are not enough: argparse accepts any unambiguous
    abbreviation of a long option, so `--openai_k sk-...` is a perfectly valid
    invocation that an exact lookup would print back verbatim. Anything that
    prefixes a key flag is therefore treated as that flag. A prefix matching
    several of them is ambiguous and argparse would have rejected the command,
    but it is still redacted — a secret must not reach the terminal on the
    strength of an argument the parser refused.
    """
    env_name = KEY_FLAG_ENV.get(flag)
    if env_name is not None:
        return env_name
    if not flag.startswith("--") or len(flag) < 3:
        return None
    matches = {env for f, env in KEY_FLAG_ENV.items() if f.startswith(flag)}
    if len(matches) == 1:
        return matches.pop()
    return "BBM_API_KEY" if matches else None


def _is_extra_headers_flag(flag):
    """Whether `flag` is `--extra_headers` or an abbreviation of it.

    `--extra_headers` values are treated as secret everywhere else (the echo
    shows names only, and each value is `remember`ed), so the rerun command
    must not print them back either. Any prefix of the long option is caught,
    ambiguous ones included: on an unsupported route the value is never parsed
    or remembered, so a `remember`-based mask would miss it, and a header must
    not reach the terminal on the strength of an argument the parser refused.
    """
    return (
        flag.startswith("--") and len(flag) >= 3 and "--extra_headers".startswith(flag)
    )


def _mask_header_values(raw):
    """`raw` (a `--extra_headers` JSON value) with every value blanked.

    The header names stay — they are printed by the echo too — and only the
    values, any of which may be a credential, become the mask. A value that
    does not parse as a JSON object is replaced whole, since its shape cannot
    be trusted to hide the secret.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return json.dumps({name: "<redacted>" for name in parsed})
    return "<redacted>"


# Inline elements that mean something without holding text: a link target,
# an image, a line break. Emptying a wrapper is a reason to delete it; these
# were never wrappers.
_KEEP_WHEN_EMPTY = frozenset(
    ["img", "br", "hr", "svg", "input", "video", "audio", "object", "iframe"]
)


@dataclass(frozen=True)
class TranslationJob:
    """A stable unit of EPUB translation work.

    The positional fields are assigned before any translation starts, so API
    completion order cannot change resume/cache identity. ``node`` deliberately
    stays an implementation detail: persistence uses ``job_id`` and
    ``global_index``, never a mutable BeautifulSoup object.
    """

    job_id: str
    document_index: int
    node_index: int
    global_index: int
    source_text: str
    context_group: str
    batch_index: int
    node: object = field(repr=False, compare=False)
    # Plan mode (--plan-classify) supplies the partitioned Unit this job was
    # cut from; it owns the exact text nodes to replace, which the node alone
    # does not identify. None in tag mode.
    unit: object = field(default=None, repr=False, compare=False)


@dataclass
class ChapterTranslationPlan:
    item: object
    soup: object
    jobs: list[TranslationJob]


def check_file_filters_against(book, only_filelist, exclude_filelist):
    """Every name in --only_filelist / --exclude_filelist must be in `book`.

    Shared by the loader and the plan dry run, which has no loader: the
    answer needs the book and nothing else.
    """
    documents = sorted(item.file_name for item in book.get_items_of_type(ITEM_DOCUMENT))
    known = set(documents)
    for flag, raw in (
        ("--only_filelist", only_filelist),
        ("--exclude_filelist", exclude_filelist),
    ):
        unknown = [f for f in raw.split(",") if f and f not in known]
        if not unknown:
            continue
        lines = [
            f"[bold red]{flag} names {len(unknown)} document(s) this book "
            f"does not have: {', '.join(unknown)}.[/bold red]"
        ]
        for name in unknown:
            near = difflib.get_close_matches(name, documents, n=3, cutoff=0.5)
            if near:
                lines.append(f"  {name} — did you mean: {', '.join(near)}?")
        lines.append(f"  the book's documents: {', '.join(documents)}")
        print("\n".join(lines))
        raise SystemExit(1)


# ebooklib's own spine writer, captured before the class attribute is
# replaced. Read through `_bbm_wraps` so the capture survives a reload of
# this module: after the wrapper is installed, the class attribute *is* the
# wrapper, and capturing that would make it call itself until RecursionError.
_INSTALLED_WRITE_OPF_SPINE = epub.EpubWriter._write_opf_spine
_EBOOKLIB_WRITE_OPF_SPINE = getattr(
    _INSTALLED_WRITE_OPF_SPINE, "_bbm_wraps", _INSTALLED_WRITE_OPF_SPINE
)


def _write_opf_spine_patch(obj, root, ncx_id):
    """ebooklib's spine, plus the `properties` it has no way to write.

    A spine property is how EPUB 3 says one document is not laid out like
    the rest of the book, and the translation note needs exactly that: in a
    fixed-layout book every spine document must declare page dimensions, and
    a note about the translation has none to declare. See
    `disclosure.REFLOWABLE_PROPERTY`.

    Written as a wrapper rather than a copy of ebooklib's loop: `linear`,
    the tuple form of a spine entry and the bare-idref form are all its
    business, and duplicating them here would mean maintaining them here.
    """
    _EBOOKLIB_WRITE_OPF_SPINE(obj, root, ncx_id)

    wanted = {}
    for entry in obj.book.spine:
        item = entry[0] if isinstance(entry, tuple) else entry
        properties = getattr(item, "spine_properties", None)
        if properties:
            wanted[item.get_id()] = " ".join(properties)
    if not wanted:
        return

    spine = root.find("spine")
    if spine is None:
        return
    for itemref in spine.findall("itemref"):
        value = wanted.get(itemref.get("idref"))
        if value:
            itemref.set("properties", value)


_write_opf_spine_patch._bbm_wraps = _EBOOKLIB_WRITE_OPF_SPINE

# Installed on import, not from `EPUBBookLoader.__init__`. `stamp_disclosure`
# marks the note with `spine_properties`, and whether that reaches the file
# must not depend on whether something else happened to build a loader first
# in this process — anything that stamps a book and calls `write_epub` gets
# the same OPF.
epub.EpubWriter._write_opf_spine = _write_opf_spine_patch


class EPUBBookLoader(BaseBookLoader):
    # what `lang=` may carry for the target language; None stamps nothing
    language_tag = None

    CHECKPOINT_VERSION = 3
    CHECKPOINT_ORDER = "document"

    def __init__(
        self,
        epub_name,
        model,
        key,
        resume,
        language,
        model_api_base=None,
        is_test=False,
        test_num=5,
        prompt_config=None,
        single_translate=False,
        context_flag=False,
        context_paragraph_limit=0,
        context_mode="window",
        context_compact_at=None,
        no_context_compact=False,
        temperature=1.0,
        source_lang="auto",
        parallel_workers=1,
        disclose=True,
    ):
        # Before the translator is built and before a byte of the book is
        # read: a protected book is refused, and there is no flag that opens
        # one. builtins.print, not rich — the sentence must reach stderr
        # whole, unwrapped and unstyled, because it is the whole answer.
        if check_epub(epub_name) == "drm":
            builtins.print(DRM_MESSAGE, file=sys.stderr)
            raise SystemExit(1)

        self.epub_name = epub_name
        self.language = language
        # Whether the output says it is a machine translation. Dropping
        # calibre's record of its own file is not covered by this: that is
        # a false statement about the file, not a disclosure.
        self.disclose = disclose
        # what `lang=` may carry for that language, or None when nothing may
        self.language_tag = language_tag(language)
        self.new_epub = epub.EpubBook()
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            context_flag=context_flag,
            context_paragraph_limit=context_paragraph_limit,
            context_mode=context_mode,
            context_compact_at=context_compact_at,
            no_context_compact=no_context_compact,
            handoff_path=handoff_path(epub_name),
            temperature=temperature,
            source_lang=source_lang,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.test_num = test_num
        self.translate_tags = "p"
        self.exclude_translate_tags = "sup,code"
        self.allow_navigable_strings = False
        self.accumulated_num = 1
        # Whether `--accumulated_num` was typed at all. 1 is both "the user
        # asked for no grouping" and "the user said nothing", and plan mode's
        # session default (below) has to tell those apart.
        self.accumulated_num_given = False
        # session mode's history is re-read at the endpoint's cache rate, so
        # requests are what a run pays for; the loader needs to know which
        # mode it is in to default the plan budget accordingly.
        self.context_mode = context_mode
        self.translation_style = ""
        self.context_flag = context_flag
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
            language=self.language,
        )
        self.retranslate = None
        self.exclude_filelist = ""
        self.only_filelist = ""
        # --quiet: progress bars and per-paragraph echoes off (log files,
        # agent runs); reports and error prints stay on
        self.quiet = False
        # plan mode (--plan-classify): coverage-complete partition. The flag
        # is the switch; translate_tags is additionally set to "auto" so the
        # tag-selection paths keep their established no-match behavior.
        self.plan_mode = False
        # plan mode entered by --plan-classify auto rather than asked for. The
        # user asked for a translated book, not for a plan, so every plan
        # failure degrades to the tag-mode run they would have got anyway;
        # an explicit --plan-classify still fails loud.
        self.plan_auto = False
        self.plan_fallback_tags = "p"
        self.plan_min_coverage = 0.5
        self.poetry_group_size = 8
        # `--batch_units`: units one plan request may carry at the strict
        # degree. Below strict it is halved (see `_plan_request_cap`).
        self.batch_units = GENERAL_GROUP_MAX_UNITS
        self._misalign_recoveries = 0
        # "none" = no plan mode, the CLI's default. A caller that turns plan
        # mode on must pick all | model | agent (see .classify): there is no
        # mode where nobody decides and the code translates whatever it could
        # not rule out, so "none" inside plan mode is refused, not defaulted.
        self.plan_classify = "none"
        self.plan_classify_model = None  # user-chosen classifier; failure blocks
        self._plan_css = None
        self._plan_overrides = None
        self._plan_partitions = {}  # file_name -> (soup, FilePlan), see _plan_partition
        # once per run, not once per document — see _derive_session_compact_budget
        self._compact_budget_derived = False
        self._plan_fingerprint = None
        self._resume_plan_fingerprint = None
        # What the loaded checkpoint's translations were produced by — see
        # `_run_fingerprint`. None means a checkpoint written before runs
        # recorded it, which is warned about rather than refused.
        self._resume_run_fingerprint = None
        self._run_fingerprint_checked = False
        # kept for the fingerprint: the prompt is half of what a slot's
        # translation is, and the translator does not hand it back
        self._prompt_config = prompt_config
        self.single_translate = single_translate
        self.block_size = 1  # Default to 1 for better translation quality with delimiter-based batching
        self.sentence_mode = False
        self.batch_use_flag = False
        self.batch_flag = False
        self.parallel_workers = 1
        self.enable_parallel = False
        self._progress_lock = Lock()
        self._pending_translation_results = {}
        self._last_saved_progress = 0
        self._checkpoint_job_ids = []
        self._planned_job_ids = []
        self.set_parallel_workers(parallel_workers)

        # monkey patch for # 173
        def _write_items_patch(obj):
            for item in obj.book.get_items():
                if isinstance(item, epub.EpubNcx):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name),
                        rebase_ncx_srcs(obj._get_ncx(), item.file_name),
                    )
                elif isinstance(item, epub.EpubNav):
                    # ebooklib regenerates every EpubNav from book.toc by
                    # default, which discards translations applied to an
                    # imported nav document's content. Imported navs carry
                    # non-empty bytes; newly created navs are empty and still
                    # need ebooklib's generator.
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name),
                        item.content if item.content else obj._get_nav(item),
                    )
                elif item.manifest:
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), item.content
                    )
                else:
                    obj.out.writestr("%s" % item.file_name, item.content)

        def _check_deprecated(obj):
            pass

        epub.EpubWriter._write_items = _write_items_patch
        epub.EpubReader._check_deprecated = _check_deprecated

        try:
            self.origin_book = epub.read_epub(self.epub_name)
        except Exception:
            # tricky monkey patch for #71 if you don't know why please check the issue and ignore this
            # when upstream change will TODO fix this
            def _load_spine(obj):
                spine = obj.container.find("{%s}%s" % (epub.NAMESPACES["OPF"], "spine"))

                obj.book.spine = [
                    (t.get("idref"), t.get("linear", "yes")) for t in spine
                ]
                obj.book.set_direction(spine.get("page-progression-direction", None))

            epub.EpubReader._load_spine = _load_spine
            self.origin_book = epub.read_epub(self.epub_name)

        # ebooklib carries no META-INF member into the output, so the
        # encryption declaration that describes an obfuscated font is gone
        # by the time the book is written. Unscramble now and the drop is
        # correct; skip it and the output ships a font nothing can parse.
        # Kept for the write step: the same fonts are scrambled again once
        # the output book exists, under the output's own identifier.
        self._obfuscated_fonts, unresolved = deobfuscate_fonts(
            self.origin_book, self.epub_name
        )
        if unresolved:
            builtins.print(
                "warning: could not restore obfuscated resource(s) "
                + ", ".join(unresolved)
                + "; the translated book may carry an unusable font",
                file=sys.stderr,
            )

        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(epub_name).parent}/.{Path(epub_name).stem}.temp.bin"
        if self.resume:
            self.load_state()
        elif os.path.exists(self.bin_path):
            # Overwriting is the documented behaviour; doing it silently is
            # not. A run that meant to continue has one flag to add. Worded
            # as a prospect: a plan gate or a filter typo may still end the
            # run before the first save touches the file.
            print(
                f"[yellow]existing progress cache {self.bin_path} is ignored "
                f"and will be overwritten once translation starts; pass "
                f"--resume to continue it instead[/yellow]"
            )

    @staticmethod
    def _is_special_text(text):
        return (
            text.isdigit()
            or text.isspace()
            or is_text_link(text)
            or all(char in string.punctuation for char in text)
        )

    def _make_new_book(self, book):
        new_book = epub.EpubBook()
        # ebooklib always writes <spine toc="ncx">, so a book without an NCX
        # item leaves that reference dangling (epubcheck OPF-049). Adding the
        # item — uid "ncx", which is what the attribute names — both resolves
        # it and restores the EPUB 2 fallback table of contents.
        if not any(isinstance(item, epub.EpubNcx) for item in book.get_items()):
            new_book.add_item(epub.EpubNcx())
        # add_metadata() does not populate the scalar fields consumed by
        # ebooklib's generated navigation document.
        new_book.title = book.title
        new_book.language = book.language
        # Identity is derived from the *source* book even when rebuilding
        # from an earlier output (--retranslate), so the same source,
        # language and mode always name the same translated book.
        try:
            identifier_id = derive_translation_identity(
                new_book,
                self.origin_book,
                self.language,
                "single" if self.single_translate else "bilingual",
            )
        except Exception as e:
            # Falls back to ebooklib's fresh uuid, which is a real loss —
            # re-running the same translation then produces a book a library
            # sees as a different one — but a book with an unstable id is
            # still a book.
            print(
                "[bold yellow]Warning: could not derive a stable identifier "
                f"for the translation ({type(e).__name__}: {escape(str(e))}); "
                "this book gets a fresh one, so re-running will not produce "
                "the same identifier.[/bold yellow]"
            )
            identifier_id = None
        allowed_ns = set(epub.NAMESPACES.keys()) | set(epub.NAMESPACES.values())
        # What a previous run of this tool stamped is this tool's to rewrite:
        # stripped here and, when disclosure is on, written again from *this*
        # run's facts at write time. Left in place, a book translated twice
        # would claim both models and carry two colophons.
        try:
            prior_ids = tool_contributor_ids(book)
        except Exception as e:
            # Reads the same metadata the loop below does, and fails the same
            # way on the same malformed entry — but before the loop, where
            # nothing else would catch it. Without the ids, a previous run's
            # stamp is copied instead of replaced, so the book may end up
            # naming two translators. Said out loud, because that is a claim
            # about the file that is now wrong.
            print(
                "[bold yellow]Warning: an earlier translation stamp could not "
                f"be identified ({type(e).__name__}: {escape(str(e))}); if this "
                "book was translated before, it may now credit both runs."
                "[/bold yellow]"
            )
            prior_ids = set()
        # Entries the copy could not carry, reported once at the end rather
        # than once each: a book with a systematically odd metadata block
        # would otherwise bury its own translation under warnings.
        skipped = []

        for namespace, metas in book.metadata.items():
            # Only keep namespaces recognized by ebooklib
            if namespace not in allowed_ns:
                continue

            if isinstance(metas, dict):
                entries = []
                for name, values in metas.items():
                    for item in values:
                        try:
                            value, others = (
                                item if isinstance(item, tuple) else (item, None)
                            )
                        except Exception as e:
                            # Unpacked one at a time on purpose. Built as a
                            # generator instead, a single badly shaped value
                            # raises at the loop below and takes the rest of
                            # the namespace with it — and the namespace is
                            # where dc:language and dc:identifier live.
                            skipped.append((name, e))
                            continue
                        entries.append((name, value, others))
            else:
                try:
                    entries = list(metas)
                except Exception as e:
                    skipped.append((f"the {namespace or 'default'} namespace", e))
                    continue

            for entry in entries:
                if not entry:
                    continue

                if isinstance(entry, tuple):
                    if len(entry) == 3:
                        name, value, others = entry
                    elif len(entry) == 2:
                        name, value = entry
                        others = None
                    else:
                        continue
                else:
                    # Unexpected metadata format; skip gracefully
                    continue

                try:
                    self._copy_metadata_entry(
                        new_book,
                        namespace,
                        name,
                        value,
                        others,
                        prior_ids,
                        identifier_id,
                    )
                except Exception as e:
                    # One entry the source wrote in a shape nothing here can
                    # read. Dropping it costs a line of metadata; letting it
                    # out costs the translated book.
                    skipped.append((name, e))
        if skipped:
            shown = ", ".join(
                f"{escape(str(name))} ({type(e).__name__}: {escape(str(e))})"
                for name, e in skipped[:3]
            )
            more = f" and {len(skipped) - 3} more" if len(skipped) > 3 else ""
            print(
                f"[bold yellow]Warning: {len(skipped)} metadata entr"
                f"{'y' if len(skipped) == 1 else 'ies'} could not be copied "
                f"from the source and {'is' if len(skipped) == 1 else 'are'} "
                f"missing from the translation: {shown}{more}[/bold yellow]"
            )

        # EPUB 3 resolves a `property` like `tdm:reservation` through the
        # package's `prefix` attribute, which ebooklib's reader does not
        # read: without this the copied metas name vocabularies the book
        # never declares. `rendition` is excluded because the writer always
        # emits it — a second copy is a duplicate declaration.
        try:
            prefixes = package_prefixes(getattr(self, "epub_name", None))
        except Exception as e:
            # Read straight from the source's OPF, so a package this cannot
            # parse ends up here. The copied metas then name vocabularies
            # the output does not declare, which is a validation finding —
            # and still a book, which not writing one is not.
            print(
                "[bold yellow]Warning: the source's prefix declarations "
                f"could not be read ({type(e).__name__}: {escape(str(e))}); "
                "any metadata using them is carried without them.[/bold yellow]"
            )
            prefixes = {}
        for name, uri in prefixes.items():
            declaration = f"{name}: {uri}"
            if name in ("rendition", "calibre") or declaration in new_book.prefixes:
                continue
            new_book.prefixes.append(declaration)

        # The book is made to be read in the target language, and a reading
        # system takes the first dc:language as the book's own. A single
        # translation is only in that language; a bilingual one keeps the
        # source's languages behind it.
        tag = language_tag(self.language)
        if tag:
            dc_namespace = epub.NAMESPACES["DC"]
            source_languages = (
                []
                if self.single_translate
                else [
                    entry
                    for entry in new_book.get_metadata("DC", "language")
                    if entry[0] != tag
                ]
            )
            new_book.metadata.setdefault(dc_namespace, {})["language"] = [
                (tag, None)
            ] + source_languages
            new_book.language = tag

        # A translation is a derivative work, so it says what it derives
        # from. `uid` is the source's primary identifier — the same value
        # the identity above is derived from. Nothing is invented when the
        # source has none, and a book rebuilt from an earlier output
        # (--retranslate) already carries the pointer and keeps just one.
        source_uid = getattr(self.origin_book, "uid", None)
        if source_uid:
            existing = new_book.get_metadata("DC", "source")
            if not any(value == source_uid for value, _ in existing):
                new_book.add_metadata("DC", "source", source_uid)

        # ebooklib's _load_spine turns every child of <spine> into an
        # (idref, linear) tuple — including XML comments, which become
        # (None, None) and crash lxml at write time with "Argument must be
        # bytes or unicode" (vertically-scrollable-manga.epub keeps a
        # commented-out page-progression-direction inside its spine). An
        # entry with no idref references nothing, so dropping it loses
        # neither content nor reading order.
        new_book.spine = [
            entry
            for entry in book.spine
            if not (isinstance(entry, tuple) and not entry[0])
            and not entry_is_our_colophon(book, entry)
        ]
        new_book.toc = backfill_toc_hrefs(self._fix_toc_uids(book.toc))

        # The disclosure itself is *not* added here. It names the model the
        # run used, and with --model_list that is not settled until the last
        # paragraph is translated — so it is stamped on the finished book,
        # just before each write. See _stamp_disclosure.
        self._disclosure_language = tag or self.language
        self._disclosure_source = source_uid
        return new_book

    def _copy_metadata_entry(
        self, new_book, namespace, name, value, others, prior_ids, identifier_id
    ):
        """Carry one source metadata entry over, or decide it does not travel.

        Split out of `_make_new_book` so one unreadable entry can be caught
        and skipped there without the `try` swallowing the whole loop.
        """
        if is_prior_disclosure(name, value, others, prior_ids):
            return

        if is_calibre_metadata(namespace, name, others):
            # calibre's record describes the file calibre built — a
            # different file from this one, so it is not true of it.
            # Dropped whatever --no_disclosure says.
            return

        if name == "link":
            # ebooklib parses OPF <link rel=… href=…> into metadata but
            # writes every metadata entry back as <meta>, where rel/href are
            # not legal attributes — the book then fails validation
            # (RSC-005) over an accessibility statement it merely copied.
            # Dropping the link loses a pointer; keeping it loses the book.
            return

        if (
            identifier_id
            and name == "identifier"
            and (others or {}).get("id") == identifier_id
        ):
            # the derived identity above owns this id attribute (RSC-005
            # otherwise); the source's value stays, as a secondary
            # identifier without it
            others = {k: v for k, v in others.items() if k != "id"}

        # `others` can be {} or None
        if others:
            new_book.add_metadata(namespace, name, value, others)
        else:
            new_book.add_metadata(namespace, name, value)

    def _stamp_disclosure(self, new_book):
        """Say the file is a machine translation, on the book about to be written.

        Every write route calls this immediately before `write_epub`, which
        is the only moment the model is finally known. Doing nothing twice
        is safe: the second call finds the colophon already there.
        """
        if not getattr(self, "disclose", True):
            return
        try:
            stamp_disclosure(
                new_book,
                model_id(getattr(self, "translate_model", None)),
                getattr(self, "_disclosure_language", None) or self.language,
                source_identifier=getattr(self, "_disclosure_source", None),
                label=translation_label(getattr(self, "translate_model", None)),
            )
        except Exception as e:
            # A book that took hours and real money to translate is not
            # worth losing over the note at the end of it, so the write goes
            # ahead without it. Said out loud rather than logged quietly:
            # what is missing is the file's own statement that a machine
            # wrote it, and only the person running this can decide whether
            # that is acceptable to ship.
            print(
                "[bold yellow]Warning: this book could not be marked as a "
                f"machine translation ({type(e).__name__}: {escape(str(e))}). "
                "It is written without the translator credit, the description "
                "line and the closing translation note — nothing in the file "
                "will say it was translated by a machine.[/bold yellow]"
            )

    def _reobfuscate_written(self, path):
        """Put back the obfuscation the source shipped, on the file just written.

        A foundry licence that let the publisher embed a font let them embed
        it obfuscated; the translated edition must be no less compliant than
        the book it was made from. Runs after the write because
        `META-INF/encryption.xml` is not a manifest item and ebooklib has no
        hook for one. A source with no obfuscated fonts is not touched.
        """
        fonts = getattr(self, "_obfuscated_fonts", None)
        if fonts:
            reobfuscate_written_epub(path, fonts)

    def _fix_toc_uids(self, toc, counter=None):
        """Fix TOC items that have uid=None to prevent TypeError when writing NCX."""
        if counter is None:
            counter = [0]  # Use list to allow mutation in nested calls

        fixed_toc = []
        for item in toc:
            if isinstance(item, tuple):
                # Section with sub-items: (Section, [sub-items])
                section, sub_items = item
                if hasattr(section, "uid") and section.uid is None:
                    section.uid = f"navpoint-{counter[0]}"
                    counter[0] += 1
                fixed_sub_items = self._fix_toc_uids(sub_items, counter)
                fixed_toc.append((section, fixed_sub_items))
            elif hasattr(item, "uid"):
                # Link or EpubHtml item
                if item.uid is None:
                    item.uid = f"navpoint-{counter[0]}"
                    counter[0] += 1
                fixed_toc.append(item)
            else:
                fixed_toc.append(item)

        return fixed_toc

    def _extract_paragraph(self, p):
        for p_exclude in self.exclude_translate_tags.split(","):
            # for issue #280
            if type(p) is NavigableString:
                continue
            for pt in p.find_all(p_exclude):
                pt.extract()
        # Exclude content within specified tags from translation (e.g., code, pre)
        exclude_tags_list = [t for t in self.exclude_translate_tags.split(",") if t]
        for tag_name in exclude_tags_list:
            if type(p) is NavigableString:
                continue
            for pt in p.find_all(tag_name):
                pt.extract()
        return p

    def _is_content_only_excluded_tags(self, p):
        """Check if a paragraph contains only excluded content tags (code, pre, etc.).

        Returns True if the paragraph should be kept but not translated.
        """
        if type(p) is NavigableString:
            return False

        # Check if paragraph contains only excluded content tags
        temp_p = copy(p)
        # Remove excluded tags
        exclude_tags_list = [t for t in self.exclude_translate_tags.split(",") if t]
        for tag_name in exclude_tags_list:
            for pt in temp_p.find_all(tag_name):
                pt.extract()
        # Also remove excluded translate tags
        for tag_name in self.exclude_translate_tags.split(","):
            for pt in temp_p.find_all(tag_name):
                pt.extract()

        # If nothing meaningful remains, paragraph contains only excluded tags
        remaining_text = temp_p.get_text().strip()
        return not remaining_text or self._is_special_text(remaining_text)

    def _count_translatable_paragraphs(self, items, trans_taglist):
        """Count paragraphs that actually need translation (excluding special content)."""
        count = 0
        for i in items:
            if i.get_type() != ITEM_DOCUMENT:
                continue
            if i.file_name in self.exclude_filelist.split(","):
                continue
            if self.only_filelist and i.file_name not in self.only_filelist.split(","):
                continue

            if self._plan_mode:
                count += len(self._plan_partition(i)[1].units)
                continue

            content = i.content
            soup = bs(content, "html.parser")
            p_list = soup.findAll(trans_taglist)

            if self.allow_navigable_strings:
                p_list.extend(soup.findAll(text=True))

            for p in p_list:
                if not p.text or self._is_special_text(p.text):
                    continue
                # Skip paragraphs that only contain excluded tags
                if self._is_content_only_excluded_tags(p):
                    continue
                count += 1

        return count

    # ------------------------------------------------------------ plan mode

    @property
    def _plan_mode(self):
        # the explicit flag, not the tag string: a user-typed
        # `--translate-tags auto` must stay an ordinary (matching-nothing)
        # tag name, not a backdoor into plan mode. The CLI sets both.
        return self.plan_mode

    def _exclude_tags_tuple(self):
        return tuple(t for t in self.exclude_translate_tags.split(",") if t)

    @property
    def _session_run(self):
        """Whether this run's context is one growing history.

        `--use_context session` says so outright. The codex route is one
        without being asked: its thread is the history, and there is no
        windowed shape to fall back to — so a codex run is billed the way a
        session run is billed, by request count against a conversation the
        endpoint re-reads. Both budgets that a session run derives (the
        grouping budget below, and the compaction budget derived from it)
        therefore have to be derived here too; without this the flagless
        codex run left grouping off and paid per paragraph.
        """
        if self.context_mode == "session":
            return True
        return getattr(self.translate_model, "SESSION_CONTEXT_ALWAYS_ON", False)

    @property
    def _plan_token_budget(self):
        """`--accumulated_num` as plan mode's grouping budget, or None.

        Same meaning as in tag mode — tokens a request may carry — counted
        the same way. A typed value wins outright, `1` included: that is the
        way to turn grouping off, and it has to keep working in every mode.

        With the flag untyped, a session run (see `_session_run`) defaults to
        `session_token_budget`, derived from this run's own prompt overhead.
        There the history is re-read at the endpoint's cache rate, so a run's
        bill is roughly its request count, and leaving grouping off is the
        expensive choice. Everywhere else the untyped default stays None —
        the short-run-only grouping plan mode has always done on its own.
        Plan mode only: tag mode reads `accumulated_num` directly and never
        sees this.
        """
        if self.accumulated_num > 1:
            return self.accumulated_num
        if self.accumulated_num_given:
            # An explicit `--accumulated_num 1`: grouping off, as asked —
            # ALL of it. 0 is the budget nothing fits, so even the short-run
            # rule stands down and every unit is its own request; None here
            # would quietly re-enable that rule and make "off" still group.
            return 0
        if self._session_run:
            # The prompts are user-customisable, so the overhead a request
            # pays before it carries any book is a property of the run, not a
            # constant. A translator that cannot measure it answers None and
            # the floor stands.
            fn = getattr(self.translate_model, "prompt_overhead_tokens", None)
            overhead = fn() if callable(fn) else None
            return session_token_budget(overhead)
        return None

    def _derive_session_compact_budget(self):
        """Default `--context-compact-at` from the grouping budget, once.

        The stock 8000 was measured for an *ungrouped* session run, where
        every paragraph is its own request. Grouping changes the arithmetic:
        the history grows by about 1.4 tokens per budget token per request,
        and the compaction turn is billed (see `_compact_session`), so the
        cost-minimising window is much shorter. `derived_compact_budget`
        solves for it.

        Only when the user did not say otherwise: an explicit
        `--context-compact-at` is left alone, `--no-context-compact` is left
        alone, and an ungrouped run (budget 0 or None) keeps the stock
        default the 8000 was measured for.
        """
        if self._compact_budget_derived:
            return
        self._compact_budget_derived = True
        budget = self._plan_token_budget
        if not budget or not self._session_run:
            return
        model = self.translate_model
        if getattr(model, "no_context_compact", False):
            return
        if not hasattr(model, "context_compact_at"):
            return
        if model.context_compact_at is not None:
            return
        model.context_compact_at = derived_compact_budget(budget)
        print(
            f"session: compacting at ~{model.context_compact_at} estimated "
            f"tokens (derived from the {budget}-token request budget; "
            f"--context-compact-at overrides)"
        )

    def _plan_request_cap(self):
        """Units one plan request may carry, given the endpoint's degree.

        The partition caps a general group at `--batch_units` because that
        is a property of the book; how far an *endpoint* can be
        trusted with one is not, and is not knowable when the partition is
        built (only `--plan-classify auto` probes before the loader runs; an
        explicit plan mode probes later, and `--model_list` rotates across
        models of differing capability). So the split happens here, where
        the verdict is in hand: everything below strict decoding carries
        half a request, per the 260905 eval — both of its content
        regressions were large batches, and neither endpoint could be told
        apart by its probe verdict in advance.

        A translator with no verdict to offer (an MT engine, a test double)
        is not being asked to hold a schema together, and takes the tighter
        cap.
        """
        degree = None
        verdict = getattr(self.translate_model, "_structured_enabled", None)
        if verdict is not None:
            try:
                degree = verdict()
            except Exception:
                # a probe that cannot answer is not a reason to stop; the
                # real request behind it reports its own failure
                degree = None
        if degree == "strict":
            return self.batch_units
        # half, floored, but never zero: a request has to carry something
        return max(1, self.batch_units // 2)

    def _plan_mode_conflict(self):
        """The flags whose meaning the plan would contradict, if any."""
        incompatible = {
            "--retranslate": self.retranslate,
            "--batch/--batch-use": self.batch_flag or self.batch_use_flag,
            "--sentence_mode": self.sentence_mode,
        }
        active = [flag for flag, on in incompatible.items() if on]
        return ", ".join(active)

    def _is_tag_mode_checkpoint(self):
        """A loaded resume cache whose slots index tags, not plan units.

        Tag-mode checkpoints carry no plan fingerprint (see `_save_progress`),
        and their slots are positions in a p-tag sequence: replaying them
        against a plan's unit list would pair translations with unrelated
        units.
        """
        return bool(
            self.resume and self.p_to_save and self._resume_plan_fingerprint is None
        )

    def _report_test_slice_requests(self, chapter_plans, unit_count):
        """Say how many requests `--test` will actually make.

        `--test_num` counts units, not requests, and grouping puts many
        units in one — so the default `--test` on a grouped run can be a
        single request, which exercises neither group rollover nor session
        compaction nor batch misalignment. The number is only knowable once
        the jobs are cut, so it is printed here rather than at the CLI.
        Silent when nothing is grouped: there the two counts are the same
        and there is nothing to say.
        """
        if not self.is_test or not unit_count:
            return
        requests = len(
            {
                (job.document_index, job.batch_index)
                for p in chapter_plans
                for job in p.jobs
            }
        )
        if requests >= unit_count:
            return
        print(
            f"[bold yellow]Warning:[/bold yellow] --test_num counts units, "
            f"not requests: this slice is {unit_count} unit(s) in "
            f"{requests} request(s). A slice this small may never reach a "
            f"group rollover or a session compaction, which is where a "
            f"grouped run goes wrong. Raise --test_num to exercise them."
        )

    def _run_fingerprint(self):
        """What a checkpoint's translations were written by.

        A slot's *position* is bound by its job id (the source text) and, in
        plan mode, by the plan fingerprint. Its *content* is bound by
        nothing at all: the same paragraph translated into Spanish and into
        Chinese produces the same job id, so `--resume --language es` used
        to splice two languages into one book and call it finished. The
        prompt and the model are the same kind of fact — the checkpoint
        holds translations, and a translation is of a language, under a
        prompt, by a model.

        Deliberately not the whole flag set: only what changes the words in
        the slots already written.
        """
        model = self.translate_model
        return hashlib.sha256(
            json.dumps(
                {
                    "language": self.language,
                    "prompt": self._prompt_config or {},
                    "model": getattr(model, "model_name", None) or "",
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    def _check_resume_run_fingerprint(self):
        """Refuse a checkpoint written by a different language/prompt/model.

        Once, before anything is translated. A checkpoint from before this
        record existed carries no fingerprint: it is warned about and used,
        because refusing it would strand every run interrupted until today.
        """
        if self._run_fingerprint_checked:
            return
        self._run_fingerprint_checked = True
        if not self.resume or not self.p_to_save:
            return
        if self._resume_run_fingerprint is None:
            print(
                f"[bold yellow]Warning:[/bold yellow] the resume cache "
                f"{self.bin_path} was written before runs recorded their "
                f"language, prompt and model, so none of them can be "
                f"checked. If this command differs from the one that wrote "
                f"it, delete the cache instead of resuming."
            )
            return
        if self._resume_run_fingerprint != self._run_fingerprint():
            print(
                f"[bold red]The resume cache {self.bin_path} was written by a "
                f"run with a different language, prompt or model; continuing "
                f"it would splice two translations into one book. Delete "
                f"{self.bin_path} to start over, or rerun with the original "
                f"--language / --prompt / --model.[/bold red]"
            )
            raise SystemExit(1)

    def _skip_plan_mode(self, reason):
        """Give up the plan and translate the tag selection instead.

        Only reachable when the plan was entered automatically: an asked-for
        plan that cannot be built is a failure, but an offered one is just an
        offer, and the book the user asked for still translates without it.
        """
        print(
            f"[bold yellow]plan mode skipped ({reason}); translating the "
            f"--translate-tags {self.plan_fallback_tags} selection "
            f"instead[/bold yellow]"
        )
        self.plan_mode = False
        self.translate_tags = self.plan_fallback_tags
        self._plan_fingerprint = None
        self._plan_partitions.clear()

    def _enter_plan_mode(self):
        """Run plan mode's gates and build the plan.

        Returns False when an automatic plan gave way to tag mode; anything
        that stops an explicitly requested plan is re-raised untouched.
        """
        conflict = self._plan_mode_conflict()
        if conflict:
            if self.plan_auto:
                self._skip_plan_mode(f"not compatible with {conflict}")
                return False
            print(
                f"[bold red]plan mode (--plan-classify) is not compatible "
                f"with {conflict}[/bold red]"
            )
            raise SystemExit(1)
        if self.allow_navigable_strings:
            print(
                "note: --allow_navigable_strings is redundant in plan mode "
                "(every text node is already accounted for); ignoring it"
            )
        # The plan would refuse this cache anyway (see _prepare_translation_plan),
        # but only after the classifier has been paid for and a plan JSON
        # written. Under auto the answer is already known here: the cache is
        # the run the user is resuming, so finish it the way it started.
        if self.plan_auto and self._is_tag_mode_checkpoint():
            self._skip_plan_mode("resuming a tag-mode run")
            return False
        try:
            self._prepare_translation_plan()
        except (SystemExit, Exception) as err:
            if not self.plan_auto:
                raise
            # Every plan failure already printed what went wrong; this line
            # says what happens next. SystemExit carries a code, not a
            # message, so it names the step instead.
            lines = [] if isinstance(err, SystemExit) else str(err).strip().splitlines()
            detail = escape(lines[0]) if lines else ""
            self._skip_plan_mode(detail or "the plan could not be built, see above")
            return False
        # Only once the plan is committed: a plan that fell back to tag mode
        # runs ungrouped, which is what the stock compact default was
        # measured for — deriving earlier would leave the short window on a
        # translator the fallback then keeps.
        self._derive_session_compact_budget()
        return True

    def _prepare_translation_plan(self):
        """Build the coverage-complete plan; fail loud below the coverage gate."""
        name, _ = os.path.splitext(self.epub_name)
        plan_path = f"{name}_plan.json"
        # agent mode hands the plan over on the run that creates it, and
        # translates on the run that finds one already there. What each mode
        # does with the file is one table in classify/, not a condition
        # repeated at every branch that happens to care.
        policy = mode_policy(self.plan_classify)
        plan_existed = os.path.exists(plan_path)
        if plan_existed and not policy.reads_saved_plan:
            print(
                f"note: --plan-classify {policy.name} ignores the existing plan "
                f"{plan_path}; it translates the whole partition"
            )
            plan_existed = False
        overrides = None
        saved_ledger = None
        if plan_existed:
            only = {f for f in self.only_filelist.split(",") if f}
            exclude = {f for f in self.exclude_filelist.split(",") if f}
            expected_settings = planning_settings(
                self._exclude_tags_tuple(),
                only,
                exclude,
            )
            saved_ledger, overrides = load_plan_overrides(
                plan_path,
                self.epub_name,
                expected_settings=expected_settings,
            )
            if overrides:
                print(
                    f"Applying {len(overrides)} signature override(s) from {plan_path}"
                )

        self._plan_css = BookCss(self.origin_book)
        self._plan_overrides = overrides

        if is_fixed_layout(self.origin_book):
            print(
                "[bold yellow]warning: this is a fixed-layout (pre-paginated) "
                "EPUB — its text boxes are sized for the original words, so "
                "translated text may overflow or misplace.[/bold yellow]"
            )

        plan = self._build_partitioned_plan()
        if plan.total_chars == 0:
            # coverage of an empty plan is vacuously 100%; a plan that
            # selected nothing is a wrong filter, not a covered book. Gate
            # before anything is classified or written to disk.
            print(
                f"[bold red]The plan selected no translatable text "
                f"({len(plan.files)} document(s) matched). Check "
                f"--only_filelist / --exclude_filelist for typos.[/bold red]"
            )
            raise SystemExit(1)

        # Before anything is paid for: a unit whose translation cannot be
        # written back where it belongs must not be translated at all.
        self._guard_unsafe_units(plan)

        # The ledger carries every question this book asks, plus whatever a
        # previous run already answered.
        ledger = plan.build_ledger(decisions=saved_ledger, overrides=overrides)
        plan_written = False
        # A plan answers the questions the book asked when it was written.
        # Change the file filters or the excluded tags and the book asks
        # different ones — and a question with no row in the file cannot be
        # answered, so the demand to decide it can never be satisfied.
        added, dropped = set(), set()
        if saved_ledger is not None:
            added = set(ledger.rows) - set(saved_ledger.rows)
            dropped = set(saved_ledger.rows) - set(ledger.rows)
        reopened = set(ledger.reopened_keys)
        if policy.name == "all":
            # "all" answers every question the same way, on purpose and out
            # loud — see classify/all.py for why that is not a default.
            decide_everything(ledger)
            plan.record_dispositions(ledger)

        # LLM classification is owed to whatever is still a question — on the
        # run that creates the plan JSON, and equally on a run that finds one
        # drafted by --plan-dry-run or left half-answered by an earlier
        # failure. Rows already decided are never re-asked, so the JSON stays
        # the source of truth (user edits win, resume fingerprints stay
        # stable). Delete it to reclassify from scratch.
        if self.plan_classify == "model" and ledger.undecided_keys():
            if self.is_test:
                # --test truncates the units translated, never the plan: the
                # partition is of the whole book, so classification is of the
                # whole book too, and it is paid for in full by a run the
                # operator asked to be small.
                print(
                    f"[bold yellow]Warning:[/bold yellow] plan classification "
                    f"covers the whole book "
                    f"({len(ledger.undecided_keys())} signatures), not just "
                    f"the --test slice; the plan file is cached and reused by "
                    f"the full run."
                )
            decisions = self._classify_plan(ledger, plan, plan_path)
            for key, (verdict, content_type) in decisions.items():
                ledger.decide(key, verdict, "llm", content_type)
            skips = ledger.skip_keys()
            print(
                f"llm classification: {len(decisions)} verdict(s), "
                f"{len(skips)} skip(s)"
            )
            if skips:
                overrides = {
                    **(overrides or {}),
                    **{k: ("skip", by) for k, by in skips.items()},
                }
                self._plan_overrides = overrides
                self._plan_partitions.clear()
                plan = self._build_partitioned_plan()
                # This plan, not the pre-classification one, is what gets
                # written back to the book. A skipped inline signature
                # changes the segmentation around it, so a run that cannot
                # be placed can exist only here — guard the plan that ships.
                self._guard_unsafe_units(plan)
            # Unconditionally: `decide()` sets the verdict, never the
            # disposition, and the only other call happened while every row
            # was still a question. A run whose model skipped nothing used to
            # save a plan claiming a decision on every row and an outcome on
            # none of them.
            plan.record_dispositions(ledger)
            plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            print(
                f"plan written to {plan_path} (override a verdict by editing its "
                f'"action", "decided_by" and "content_type")'
            )
            plan_written = True

        # Anything that changes the unit list (and therefore the positional
        # meaning of resume-cache slots) is part of the fingerprint; a cache
        # written under a different plan must be refused, not misapplied.
        self._plan_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "schema_version": PLAN_SCHEMA_VERSION,
                    # the book itself is part of the slots' meaning: a
                    # replaced epub at the same path has a different unit
                    # list, and deleting the (sha-mismatching) plan JSON must
                    # not resurrect the old book's cache
                    "book_sha256": file_sha256(self.epub_name),
                    "overrides": overrides or {},
                    "exclude_tags": sorted(self._exclude_tags_tuple()),
                    "only_filelist": self.only_filelist,
                    "exclude_filelist": self.exclude_filelist,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if (
            self.resume
            and self._resume_plan_fingerprint is not None
            and self._resume_plan_fingerprint != self._plan_fingerprint
        ):
            print(
                f"[bold red]The resume cache {self.bin_path} was written under "
                f"a different plan (edited plan JSON, changed book file, or "
                f"changed --exclude-translate-tags / file filters). Its slots "
                f"no longer line up with the current unit list; delete it to "
                f"start over, or restore the previous settings.[/bold red]"
            )
            raise SystemExit(1)
        if self._is_tag_mode_checkpoint():
            # a legacy list-format cache (tag-mode run): its slots index a
            # p-tag sequence, not this plan's unit list — positionally
            # meaningless here, and replaying it would pair units with
            # unrelated translations
            print(
                f"[bold red]The resume cache {self.bin_path} was written by a "
                f"tag-mode run and carries no plan fingerprint; its slots do "
                f"not correspond to plan units. Delete it to start "
                f"over.[/bold red]"
            )
            raise SystemExit(1)

        # samples are book text: rich would eat "[Seven] warriors [they were]"
        print(escape(plan.report(ledger)))
        if plan_written or not policy.writes_plan_file:
            # already written above with its own message, or ("all") a mode
            # that asks nothing and therefore writes no plan file
            pass
        elif not plan_existed:
            plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            undecided = ledger.undecided_keys()
            print(
                f"plan written to {plan_path} with {len(undecided)} "
                f"undecided signature(s) (null actions must be resolved)"
            )
        elif added or reopened or ledger.settings_changed:
            # `dropped` alone is deliberately not a trigger: a row can vanish
            # without the settings moving only because *this run's own* skip
            # overrides re-shaped the partition, and rewriting then deletes
            # decisions the user would need again the moment the skip is
            # reverted. A settings change that drops rows still rewrites,
            # through `settings_changed`.
            plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            changes = []
            if added:
                changes.append(f"{len(added)} added")
            if dropped:
                changes.append(f"{len(dropped)} dropped")
            if reopened:
                reason = (
                    "planning settings changed"
                    if ledger.settings_changed
                    else "occurrence evidence changed"
                )
                changes.append(f"{len(reopened)} decision(s) reopened ({reason})")
            elif ledger.settings_changed:
                changes.append("planning settings refreshed")
            print(f"{plan_path} updated: {', '.join(changes)}")
            if dropped:
                print(
                    f"[bold yellow]{len(dropped)} decided signature(s) no "
                    f"longer occur under these settings and were dropped from "
                    f"{plan_path}: {', '.join(sorted(dropped)[:5])}. Restoring "
                    f"the previous settings means deciding them again."
                    f"[/bold yellow]"
                )
        else:
            if dropped:
                # kept in the file, not dropped from it: the rows are still
                # there with their verdicts, and reverting the skip that
                # re-shaped the partition brings their signatures back.
                print(
                    f"[bold yellow]{len(dropped)} decided signature(s) do not "
                    f"occur in this run's partition and were left untouched in "
                    f"{plan_path}: {', '.join(sorted(dropped)[:5])}."
                    f"[/bold yellow]"
                )
            # never overwrite for its own sake: the file may carry
            # user-edited decisions (and load_plan_overrides already verified
            # its hash)
            print(f"using existing plan {plan_path}")

        if plan.coverage < self.plan_min_coverage:
            print(
                f"[bold red]Plan coverage {100 * plan.coverage:.1f}% is below the "
                f"required {100 * self.plan_min_coverage:.1f}% — refusing to "
                f"translate a fraction of the book silently. Inspect "
                f"{plan_path}, or lower --plan-min-coverage.[/bold red]"
            )
            raise SystemExit(1)

        undecided = ledger.undecided_keys()
        if undecided:
            # Stop: translating now would spend the whole book on questions
            # nobody answered, and the greedy all-translate shortcut must not
            # be reachable by simply rerunning the command. A rerun finds the
            # (edited) plan and goes straight through.
            # The exit code separates this stop from that rerun's success —
            # agent mode reaches both from one command line, so a caller that
            # only saw 0 could not tell a handed-off book from a translated
            # one. See PLAN_HANDOFF_EXIT_CODE.
            # builtins.print, not rich: this block is meant to be copied, and
            # rich would hard-wrap the paths and the rerun command mid-token.
            builtins.print(
                build_agent_prompt(
                    plan_path,
                    self.epub_name,
                    self._rerun_command(),
                    unresolved=undecided,
                )
            )
            raise SystemExit(policy.handoff_exit_code)
        # The last gate before money is spent: nothing undecided, and no
        # decision without a decider and a named content type. It repeats
        # what `decide` and `Ledger.load` already enforce on purpose —
        # reaching it in an illegal state means something wrote the ledger
        # without going through either.
        ledger.require_decided(plan_path)
        return plan

    def _guard_unsafe_units(self, plan):
        """Refuse units whose translation cannot be written back in place.

        Anchored runs make this an invariant check rather than a policy:
        a run that reports a hazard is a segmentation bug, not a book we
        should refuse. Failing loudly here beats writing a document whose
        text has moved, and beats a silent `assert` nobody reads.
        """
        offenders = []
        for fp in plan.files:
            for unit, hazards in file_segment_hazards(fp):
                offenders.append((fp.file_name, unit, hazards))
        if not offenders:
            return
        listed = "\n".join(
            f"  {file_name} / {unit.signature} / {','.join(hazards)} / "
            f"{escape(unit.text[:60])}"
            for file_name, unit, hazards in offenders[:20]
        )
        raise UnsafeSingleTranslateError(
            f"{len(offenders)} run(s) span content that renders between "
            f"their parts, so no single position in the document holds "
            f"them:\n{listed}\n"
            f"This is a partitioning defect, not a problem with the book — "
            f"please report it with the file and signature above. Tag mode "
            f"(--translate-tags) does not use the partition and will run."
        )

    @staticmethod
    def _rerun_command():
        """The command that will translate once the plan is edited.

        Reconstructed from argv so the printed instructions name the user's
        actual invocation (their book, their model, their language) — a
        generic example would have to be translated back by hand. The one
        thing not reproduced verbatim is a secret: a key becomes its env
        variable (see KEY_FLAG_ENV) and every `--extra_headers` value is
        masked, since any of them may be a credential.
        """
        parts = []
        # Set once a bare key flag is seen, so the value that follows it in
        # the next argv entry is replaced instead of quoted.
        pending_env = None
        # The same, for a bare `--extra_headers`: the JSON in the next entry
        # is masked, since any of its values may be a credential.
        pending_header_mask = False
        for arg in sys.argv:
            if pending_env is not None:
                parts.append(f'"${pending_env}"')
                pending_env = None
                continue
            if pending_header_mask:
                parts.append(shlex.quote(_mask_header_values(arg)))
                pending_header_mask = False
                continue
            flag, joined, value = arg.partition("=")
            if _is_extra_headers_flag(flag):
                if joined:
                    # `--extra_headers={…}`: value never becomes its own entry
                    parts.append(f"{flag}={shlex.quote(_mask_header_values(value))}")
                else:
                    parts.append(shlex.quote(arg))
                    pending_header_mask = True
                continue
            env_name = _key_flag_env(flag)
            if env_name is None:
                parts.append(shlex.quote(arg))
            elif joined:
                # `--openai_key=sk-…`: the key never becomes its own entry
                parts.append(f'{flag}="${env_name}"')
            else:
                parts.append(shlex.quote(arg))
                pending_env = env_name
        # A final pass over the whole line masks any registered secret the
        # flag-by-flag substitution above did not place (a key given in an
        # unexpected position, say); redact is a no-op when there is none.
        return redact(" ".join(["python3", *parts]))

    def check_file_filters(self):
        """Every name in --only_filelist / --exclude_filelist must exist.

        A misspelled only-list name reached the coverage gate as "the plan
        selected no translatable text"; a misspelled exclude-list name
        reached nothing at all — the document the user meant to skip was
        translated and paid for, with no warning. Both are typos, and both
        are cheap to catch.

        Called by the CLI as soon as the filters are set, which is before
        any model setup: the answer needs the book and nothing else, so
        there is no reason for a sidecar or a window lookup to happen first.
        Tag mode needs it as much as plan mode — it was the only mode that
        never ran it.
        """
        check_file_filters_against(
            self.origin_book, self.only_filelist, self.exclude_filelist
        )

    def _build_partitioned_plan(self):
        """The plan is built from the same cached partitions the processing
        pass will consume, over the same files it will process — the coverage
        gate judges what will actually be translated, and no file is
        partitioned twice. Filter semantics mirror process_item: an only-list
        wins outright; exclude applies only without one.
        """
        only = {f for f in self.only_filelist.split(",") if f}
        exclude = {f for f in self.exclude_filelist.split(",") if f}
        files = []
        for item in self.origin_book.get_items_of_type(ITEM_DOCUMENT):
            if only:
                if item.file_name not in only:
                    continue
            elif item.file_name in exclude:
                continue
            files.append(self._plan_partition(item)[1])
        return TranslationPlan(
            files,
            self._exclude_tags_tuple(),
            self.poetry_group_size,
            only_files=only,
            exclude_files=exclude,
            token_budget=self._plan_token_budget,
            batch_units=self.batch_units,
        )

    def _classify_plan(self, ledger, plan, plan_path):
        """LLM verdicts for every undecided row.

        There is no degrade-to-defaults path. A classification that cannot
        finish leaves questions unanswered, and answering them with the
        greedy default is the exact silent decision this mode exists to
        remove — so whatever *was* decided is written down and the run
        stops with the same instructions agent mode prints.
        """
        try:
            decisions, _candidates = classify_plan(
                ledger,
                self.translate_model,
                model=self.plan_classify_model,
            )
        except PlanUnresolvedError as e:
            for key, (verdict, content_type) in e.resolved.items():
                ledger.decide(key, verdict, "llm", content_type)
            # An "unsure" row stays a question, but the model still named
            # what it was looking at. Keeping the name means the agent
            # answering these rows starts from that evidence instead of
            # re-deriving it, and nothing paid for is thrown away.
            named = sum(
                ledger.note_content_type(key, content_type) is not None
                for key, content_type in e.considered.items()
            )
            plan.record_dispositions(ledger)
            plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            print(
                f"[bold red]{e}[/bold red]\n"
                f"[yellow]{len(e.resolved)} decided verdict(s) were saved to "
                f"{plan_path}"
                + (f", plus {named} named-but-undecided row(s)" if named else "")
                + f"; the undecided rows are listed below.[/yellow]"
            )
            builtins.print(
                build_agent_prompt(
                    plan_path,
                    self.epub_name,
                    self._rerun_command(),
                    unresolved=e.unresolved,
                )
            )
            raise SystemExit(1)
        except PlanClassifyError as e:
            # No verdicts at all: auth, quota, a model that cannot answer.
            # Nothing that justifies translating — but earlier pages may have
            # *named* rows before the failure, and those names are paid-for
            # evidence. Save them (as questions, not decisions) so a rerun or
            # an agent does not buy the same look twice.
            named = sum(
                ledger.note_content_type(key, content_type) is not None
                for key, content_type in getattr(e, "considered", {}).items()
            )
            if named:
                plan.record_dispositions(ledger)
                plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            print(
                f"[bold red]plan classification failed: {e}[/bold red]\n"
                + (
                    f"[yellow]{named} row(s) were named before the failure and "
                    f"saved to {plan_path} as open questions.[/yellow]\n"
                    if named
                    else ""
                )
                + f"[yellow]Nothing was decided, so nothing will be "
                f"translated. Use --plan-classify agent to decide the rows "
                f"yourself, or --plan-classify all to translate the whole "
                f"partition deliberately.[/yellow]"
            )
            raise SystemExit(1)
        return decisions

    def _partition_item(self, soup, file_name):
        fp, _ = partition_file(
            soup,
            self._plan_css.resolver_for(file_name, soup),
            file_name,
            exclude_tags=self._exclude_tags_tuple(),
            overrides=self._plan_overrides,
            poetry_group_size=self.poetry_group_size,
            token_budget=self._plan_token_budget,
            max_units=self.batch_units,
        )
        return fp

    def _plan_partition(self, item, consume=False):
        """Parse + partition an item's original content exactly once.

        The plan build (_prepare_translation_plan) and the job enumeration
        (_build_translation_plan) share the cached result. Enumeration passes
        consume=True: execution mutates that soup, so the entry must leave
        the cache rather than be handed out again.
        """
        key = item.file_name
        cached = self._plan_partitions.get(key)
        if cached is None:
            soup = bs(item.content, "html.parser")
            cached = (soup, self._partition_item(soup, key))
        if consume:
            self._plan_partitions.pop(key, None)
        else:
            self._plan_partitions[key] = cached
        return cached

    @staticmethod
    def _iter_plan_chunks(jobs):
        """Yield lists of jobs: one poetry window per chunk, others alone.

        Batch identity comes from the job plan (_plan_batch_indexes), so a
        chunk is the same set of units whether it runs sequentially or on a
        parallel worker.
        """
        chunk = []
        chunk_batch = None
        for job in jobs:
            if chunk and job.batch_index == chunk_batch:
                chunk.append(job)
            else:
                if chunk:
                    yield chunk
                chunk = [job]
                chunk_batch = job.batch_index
        if chunk:
            yield chunk

    def _process_plan_chunks(self, jobs, index, p_to_save_len, pbar=None):
        for chunk in self._iter_plan_chunks(jobs):
            if self.translate_model._fatal_error_detected:
                print(
                    "[bold red]Fatal translation error detected. Stopping chapter processing.[/bold red]"
                )
                break
            index, n = self._process_combined_paragraph(
                [job.node for job in chunk],
                index,
                p_to_save_len,
                thread_safe=False,
                plan_units=[job.unit for job in chunk],
            )
            if pbar is not None:
                pbar.update(n)
                self._show_usage(pbar)
        return index

    def _insert_plan_translation(
        self, unit, t_text, translation_style="", single_translate=False
    ):
        """Insert a plan unit's translation without touching text it doesn't own.

        In single-translate mode only the unit's own text nodes are replaced:
        nested block units, line-number spans, anchors and other
        skip-classified nodes stay in the document. Replacing the whole
        element (the tag-mode behavior) would delete them.
        """
        if t_text is None:
            t_text = ""
        if (
            self.translate_model.TRANSLATION_ERROR_MARKER is not None
            and t_text == self.translate_model.TRANSLATION_ERROR_MARKER
        ):
            return
        if unit.markers or MARKER_OPEN in t_text:
            # Lenient by decision: a lost marker is reconciled and reported,
            # never a failed unit and never a retry. Marker placement is not
            # worth re-paying a request for.
            #
            # `unit.markers` is passed as the issued set on purpose: a token
            # the *source* prints verbatim looks the same and belongs to the
            # book, so it must survive reconciliation untouched.
            #
            # A unit that owns *no* markers is reconciled too, whenever its
            # translation carries a marker-shaped token anyway. That is the
            # neighbour a model sprayed a copy into: the token stands for a
            # node this unit does not have, so it is invented here and would
            # otherwise be written into reader-visible prose (the batch is
            # not shifted, so `_marker_slot_mismatch` deliberately lets it
            # through — see there). The empty issued list keeps the book's
            # own verbatim tokens literal, as always.
            issued = list(unit.markers)
            note = marker_report(
                f"{unit.file_name}#{unit.ordinal}", unit.text, t_text, issued
            )
            if note:
                print(f"[yellow]{note}[/yellow]")
            t_text = reconcile_markers(unit.text, t_text, issued)
        if single_translate and unit.nodes:
            # Ruby annotations of text that is about to disappear would
            # survive as orphaned furigana next to non-Japanese text. The
            # wrapper goes with them, *before* the translation is written:
            # EPUB's schema has no annotationless <ruby> — the element
            # requires an rt/rtc after its base (RSC-005 "element ruby
            # incomplete"), so both a <ruby></ruby> husk and a
            # <ruby>translation</ruby> are files epubcheck rejects
            # (1,897 findings on kusamakura between them). Attributes ride
            # on into a <span>: an id here is a link target the book still
            # needs, and unwrap() would drop it.
            rubies = {}
            for node in unit.nodes:
                for ancestor in node.parents:
                    if ancestor is unit.element:
                        break
                    if ancestor.name == "ruby":
                        rubies[id(ancestor)] = ancestor
            for ruby in rubies.values():
                for annotation in ruby.find_all(["rt", "rp", "rtc"]):
                    annotation.extract()
                if ruby.attrs:
                    ruby.name = "span"
                else:
                    ruby.unwrap()
            inserted = self._write_single_translation(
                unit, t_text, translation_style, language=self.language_tag
            )
        elif has_restricted_content_model(unit.element):
            inserted = self._append_inline_translation(
                unit, t_text, translation_style, language=self.language_tag
            )
        elif unit.resolver is not None and (
            # A clone carries *all* of the owner's text, so it is only a
            # translation of this unit when this unit is the whole owner.
            # With several runs — <br>-separated verse, a retained skip
            # between two halves — every run would clone the same source
            # and insert it directly after it, which reverses the
            # translations and detaches each from the run it belongs to.
            unit.owner_runs > 1
            or not is_simple_owner(unit.element, unit.resolver)
        ):
            inserted = self._insert_anchored_translation(
                unit, t_text, translation_style, language=self.language_tag
            )
        else:
            inserted = self._insert_trans_preserving_tags(
                unit.element, t_text, translation_style, False
            )
        if unit.markers:
            self._restore_markers(unit, single_translate, inserted)

    @staticmethod
    def _restore_markers(unit, single_translate=False, inserted=None):
        """Put each marker's source node back where its token landed.

        `inserted` is what the insertion path just wrote — the bilingual
        clone, or the rewritten owner in single-translate mode. Only that is
        searched. Scanning the owner and its next sibling instead was wrong
        in both directions: in single-translate mode the next sibling is
        another *source* element, so a book whose next paragraph prints
        ``⟦code1⟧`` as literal text had the node moved out of the
        translation and into that paragraph; and nothing guarantees the
        clone is the immediately following tag.

        Bilingual mode *clones* the node — the original is still standing in
        the source paragraph beside the translation, so the copy is a second
        rendering and must not be a second anchor (its ids go). Single
        translate *moves* it: the source text it sat in has been replaced, so
        there is nothing left to duplicate, and the node keeps its
        attributes, its id among them — that id is a link target the rest of
        the book points at.

        A token the reply lost is not a problem here: reconciliation already
        appended it, so it is somewhere in the text and gets its node.
        """
        tokens = list(unit.markers)
        roots = inserted if isinstance(inserted, list) else [inserted]
        for root in roots:
            if isinstance(root, NavigableString):
                # `_write_single_translation` writes a bare string when no
                # --translation_style was asked for
                text_nodes = [root]
            elif isinstance(root, Tag):
                text_nodes = [
                    n for n in list(root.descendants) if isinstance(n, NavigableString)
                ]
            else:
                continue
            for text_node in text_nodes:
                raw = str(text_node)
                if not any(token in raw for token in tokens):
                    continue
                pieces = []
                for kind, value in split_on_markers(raw, tokens):
                    if kind == "text":
                        pieces.append(NavigableString(value))
                        continue
                    source = unit.markers[value]
                    if single_translate:
                        source.extract()
                        node = source
                    else:
                        node = copy(source)
                        strip_duplicate_ids(node)
                    pieces.append(node)
                if not pieces:
                    continue
                text_node.replace_with(pieces[0])
                anchor = pieces[0]
                for piece in pieces[1:]:
                    anchor.insert_after(piece)
                    anchor = piece

    @staticmethod
    def _append_inline_translation(unit, t_text, translation_style="", language=None):
        """Put the translation *inside* the element it belongs to.

        Same rule as `helper.append_inline_translation` — the containers that
        accept exactly one of a thing, where a translated sibling is a book
        epubcheck rejects — but anchored to the *run* rather than the owner.
        A plan unit knows which text nodes it owns, so the translation goes
        directly after the last of them: inside the <a> of a navigation
        entry, where that entry's text already lives. The helper appends to
        the element because tag mode has no runs to anchor to, and needs
        `translation_host()` to find the same place from the outside.
        """
        span = make_tag("span")
        if translation_style:
            span["style"] = translation_style
        span.string = f" {t_text}"
        stamp_translation(span, unit.element, language)
        unit.nodes[-1].insert_after(span)
        return span

    @staticmethod
    def _markup_covers_run(markup, owned):
        """Does `markup` hold this run's text and nothing else of the book's?

        The question both insertion paths turn on: a tag that covers the
        whole run can carry the translation (as a clone, or by replacement),
        while one covering only part of it must not — handing a whole
        sentence a fragment's styling is what makes an <a> swallow the
        sentence into a link, or a drop-cap <span> into its first letter.
        """
        return isinstance(markup, Tag) and owned <= {
            id(n) for n in markup.descendants if isinstance(n, NavigableString)
        }

    @staticmethod
    def _insert_anchored_translation(unit, t_text, translation_style="", language=None):
        """Append a translation next to the run it translates.

        For owners that cannot be cloned — a wrapper holding nested blocks,
        or <body> itself — the translated copy goes immediately after this
        run's own markup, so it lands between the same neighbours the source
        text sits between instead of after the whole wrapper.

        When that markup covers the whole run — <span class="lin"> holding
        one verse line — the translation is a clone of it, not a bare
        <span>: the book's CSS (`span.lin { margin-left: 5em }`) must style
        both renderings, the same rule block clones already follow. A tag
        covering only part of the run keeps the bare <span>, because
        cloning it would hand the whole sentence a fragment's styling —
        the hazard `_write_single_translation` documents.
        """
        tail = inline_subtree_root(unit.nodes[-1], unit.resolver)
        if tail.name != "ruby" and EPUBBookLoader._markup_covers_run(
            tail, {id(n) for n in unit.nodes}
        ):
            span = copy(tail)
            restamp_language(span, language)
            span.clear()
            # a translated copy is a second rendering, not a second anchor
            strip_duplicate_ids(span)
        else:
            span = make_tag("span")
            stamp_translation(span, unit.element, language)
        if translation_style:
            span["style"] = translation_style
        span.string = t_text
        # inserted in the order they are read — run, break, translation.
        # `make_tag` is what keeps the break a single <br/>; see helper.py.
        line_break = make_tag("br")
        tail.insert_after(line_break)
        line_break.insert_after(span)
        return span

    @staticmethod
    def _write_single_translation(unit, t_text, translation_style="", language=None):
        """Put a segment's translation where the segment was.

        The translation replaces the first owned node *only when the markup
        around that node covers the whole segment*. Otherwise it goes at the
        position of that markup instead, because writing it inside would
        hand the whole sentence to a fragment's styling: an <a> wrapping
        three words would make the entire translated sentence a link, and a
        drop-cap <span> would swallow the paragraph into its first letter.

        Emptied inline wrappers are then removed. Keeping them would leave
        `<a href="…"></a>` husks — an unclickable link is not preservation.
        """
        translation = EPUBBookLoader._styled_translation(t_text, translation_style)
        # a bare string has nowhere to carry a tag; a styled <span> does
        stamp_translation(translation, unit.element, language)
        container = unit.nodes[0]
        if unit.resolver is not None:
            container = inline_subtree_root(unit.nodes[0], unit.resolver)
        if EPUBBookLoader._markup_covers_run(container, {id(n) for n in unit.nodes}):
            # Wrappers of the *later* nodes empty here just as they do on the
            # anchored path below: a run of several <ruby> bases keeps only
            # its first position, and the other bases' now-annotationless
            # rubies would survive as <ruby></ruby> — a file epubcheck
            # rejects (RSC-005 "element ruby incomplete", 53 on kusamakura).
            emptied = EPUBBookLoader._collect_wrappers(unit.nodes[1:], unit.element)
            unit.nodes[0].replace_with(translation)
            for node in unit.nodes[1:]:
                node.extract()
            EPUBBookLoader._remove_emptied_wrappers(emptied)
            return translation

        anchor = container if isinstance(container, Tag) else unit.nodes[0]
        # only wrappers *we* empty are ours to remove: an already-empty
        # <a id="…"> is a link target the book still needs
        emptied = EPUBBookLoader._collect_wrappers(unit.nodes, unit.element)
        anchor.insert_before(translation)
        for node in unit.nodes:
            node.extract()
        EPUBBookLoader._remove_emptied_wrappers(emptied)
        return translation

    @staticmethod
    def _styled_translation(t_text, translation_style=""):
        """The translated text as it goes into the document.

        A style declaration has to hang on an element, so asking for one
        turns the string into a <span>. Without one it stays a string: a
        wrapper no book asked for is markup the original did not have.
        """
        if not translation_style:
            return NavigableString(t_text)
        span = make_tag("span")
        span["style"] = translation_style
        span.string = t_text
        return span

    @staticmethod
    def _collect_wrappers(nodes, stop):
        """The inline ancestors these nodes are about to leave empty,
        outermost first, each once."""
        emptied = {}
        for node in nodes:
            for ancestor in node.parents:
                if ancestor is stop:
                    break
                # by identity: the same wrapper holds several owned nodes,
                # and visiting it twice would decompose an already-gone tag
                emptied[id(ancestor)] = ancestor
        return list(emptied.values())

    @staticmethod
    def _remove_emptied_wrappers(emptied):
        # depth-first so an inner wrapper is gone before its parent is judged
        for element in reversed(emptied):
            if element.parent is None or element.attrs is None:
                continue  # already detached with an enclosing wrapper
            if (
                element.name in _KEEP_WHEN_EMPTY
                or element.get("id")
                or (element.name == "a" and element.get("name"))
            ):
                continue
            if element.find(string=True) is not None:
                continue
            # An id *inside* the husk is a link target just the same:
            # <span><a id="ch1">Chapter One</a></span> is how a heading marks
            # itself, and deleting the wrapper takes with it the anchor every
            # cross-reference in the book points at.
            if (
                element.find(list(_KEEP_WHEN_EMPTY))
                or element.find(attrs={"id": True})
                or element.find("a", attrs={"name": True})
            ):
                continue
            # extract, not decompose: decompose clears the state of every
            # descendant, and a later run in the same document may still
            # hold references into this subtree
            element.extract()

    def _translate_texts_aligned(self, texts, translator=None, units=None):
        """translate_list with an alignment ladder: group -> halves -> singles.

        The one fallback in the system. Every LLM route's `translate_list`
        returns exactly `len(texts)` aligned items or raises `BatchMismatch`;
        none of them repairs a bad reply itself any more, and none retries
        the same group (a model that miscounted once usually miscounts
        again, and each retry re-pays the whole group). Halving costs about
        twice the batch — 8 + 4 + 2 + 1 + 1 — where a per-line fallback paid
        8 singles on top of the batch.

        `translator` defaults to the shared model; parallel chapters pass
        their own clone so --use_context stays chapter-local.

        `units` (plan mode) is the Unit behind each text, in the same order.
        It is what lets a reply whose *count* is right but whose slots are
        shifted be caught — see `_marker_slot_mismatch`.
        """
        if not texts:
            return []
        translator = translator or self.translate_model
        try:
            # A chunk of one is not a batch: it goes through `translate`,
            # which is the bottom of the ladder and the only rung with
            # nothing left to divide.
            result = (
                [translator.translate(texts[0])]
                if len(texts) == 1
                else translator.translate_list(texts)
            )
        except BatchMismatch as e:
            # Not an error: the contract working. Say what happened once,
            # then divide.
            print(
                f"[yellow]batch of {len(texts)} came back misaligned "
                f"({e}); splitting[/yellow]"
            )
            self._note_misalign_recovery()
            return self._divide_and_translate(texts, translator, units)
        except Exception as e:
            if translator._fatal_error_detected:
                # a clone's fatal flag must reach the shared model, or the
                # other workers keep firing at an endpoint already known dead
                self.translate_model._fatal_error_detected = True
                print(
                    f"[bold red]Fatal translation error detected. "
                    f"Aborting translation.[/bold red]"
                )
                print(f"[bold red]Error: {str(e)}[/bold red]")
                return [translator.TRANSLATION_ERROR_MARKER] * len(texts)
            print(f"[bold red]Translation error: {str(e)}[/bold red]")
            raise
        if translator._fatal_error_detected:
            # some translators (gemini) mark fatal and return error markers
            # instead of raising — the flag must still reach the shared
            # model, or a clone's death stays invisible to other workers
            self.translate_model._fatal_error_detected = True
        if len(result) == len(texts):
            evidence = self._marker_slot_mismatch(texts, result, units)
            if evidence is None:
                return result
            # The count agreed and every slot holds fluent target text, so
            # nothing else in the run would have noticed.
            print(
                f"[bold red]batch of {len(texts)} came back shifted "
                f"({evidence}) — splitting for realignment[/bold red]"
            )
            self._note_misalign_recovery()
            return self._divide_and_translate(texts, translator, units)
        # A belt for routes that still answer with the wrong count instead of
        # raising — the MT engines translate one by one and cannot, but a
        # gateway wrapper might.
        print(
            f"[bold red]alignment mismatch: sent {len(texts)} paragraphs, "
            f"received {len(result)} — splitting for realignment[/bold red]"
        )
        self._note_misalign_recovery()
        return self._divide_and_translate(texts, translator, units)

    @staticmethod
    def _marker_slot_mismatch(texts, result, units):
        """One slot's marker token found in another slot's reply, or None.

        The only cheap evidence there is that a reply of the *right length*
        is nonetheless shifted against the units it answers. A shift is
        otherwise silent: the counts agree, every slot holds fluent target
        text, and no id or link is lost. What it costs is measured (260905
        emergence sweep, cell `s-child-u48`): marker reconciliation and
        restoration are keyed on `unit.markers`, so the reply that *carries*
        `⟦span3⟧` is handed to a neighbour that owns no markers, the whole
        marker block is skipped for it, and the literal token is written into
        reader-visible prose — three of them in that one cell, under a log
        line that said "— reconciled".

        Only **wrong-slot** evidence counts, and that is the point of keying
        on ownership rather than on any marker anomaly. A model that simply
        dropped a marker — the token missing from its own slot and appearing
        in no other — is still reconciled, reported and never retried; that
        leniency is a pinned decision, and `reconcile_markers` does real
        repair work behind it.

        A marker-shaped token the book prints itself is not ours: collision
        avoidance is per unit, so a token issued to one unit can be literal
        text in another. Anything already present in the source that slot was
        sent is therefore left alone, whoever else it was issued to.

        And a token in the wrong slot is only evidence of a shift when its
        *owner's* slot has lost it. A reply that keeps `⟦code2⟧` where it
        belongs and also sprays a copy into the neighbour is not shifted —
        every unit still faces its own translation — and `reconcile_markers`
        drops that copy as invented before anything is written. Charging the
        whole batch through the halving ladder for it repays a request that
        was already correct.
        """
        if not units or len(units) != len(texts) or len(result) != len(texts):
            return None
        owned = [set(getattr(unit, "markers", None) or ()) for unit in units]
        issued = set().union(*owned)
        if not issued:
            return None
        # Every slot a token was issued to: collision avoidance is per unit,
        # so one token can have more than one owner, and any owner that kept
        # it is enough to say the reply is not shifted.
        owners = {}
        for index, own in enumerate(owned):
            for token in own:
                owners.setdefault(token, []).append(index)
        for index, reply in enumerate(result):
            if not reply or MARKER_OPEN not in reply:
                continue
            for token in find_markers(reply):
                if (
                    token in issued
                    and token not in owned[index]
                    and token not in texts[index]
                    and not any(
                        token in (result[owner] or "") for owner in owners[token]
                    )
                ):
                    return (
                        f"{token} came back in slot {index + 1} of "
                        f"{len(result)}, which does not own it, and is gone "
                        f"from the slot that does"
                    )
        return None

    def _note_misalign_recovery(self):
        """Every split retries; a run that splits often is telling the
        operator its batches are too big for this model."""
        count = getattr(self, "_misalign_recoveries", 0) + 1
        self._misalign_recoveries = count
        if count >= 3:
            print(
                f"[yellow]{count} misaligned batches this run — if this "
                f"keeps happening, a lower --batch_units or "
                f"--accumulated_num may fit this model better[/yellow]"
            )

    def _divide_and_translate(self, texts, translator, units=None):
        """Halve a chunk that came back misaligned; a chunk of 1 translates alone.

        `units` rides along so each half is checked for a shift the same way
        the whole chunk was — the ladder's own retry misaligned in the
        measured case, which is how the fault reached the book.
        """
        if len(texts) == 1:
            t = translator.translate(texts[0])
            if t is None:
                raise RuntimeError(
                    "`t_text` is None: your translation model is not working as expected."
                )
            return [t]
        mid = len(texts) // 2
        left = units[:mid] if units else None
        right = units[mid:] if units else None
        return self._translate_texts_aligned(
            texts[:mid], translator, left
        ) + self._translate_texts_aligned(texts[mid:], translator, right)

    def _insert_trans_preserving_tags(
        self, p, translated_text, translation_style="", single_translate=False
    ):
        """Insert translation while preserving special tags (code, pre, etc.) in bilingual mode.

        For bilingual mode: keeps original paragraph (with special tags) + adds translation
        For single translate mode: replaces text content but preserves special tags
        """
        if translated_text is None:
            translated_text = ""

        # Skip insertion if translation failed
        if (
            self.translate_model.TRANSLATION_ERROR_MARKER is not None
            and translated_text == self.translate_model.TRANSLATION_ERROR_MARKER
        ):
            return

        # Check if paragraph has excluded content tags
        exclude_tags_list = [t for t in self.exclude_translate_tags.split(",") if t]
        has_code_tags = any(p.find(tag) for tag in exclude_tags_list)

        if not has_code_tags:
            # Simple case: no code tags, use standard insert_trans
            return self.helper.insert_trans(
                p, translated_text, translation_style, single_translate
            )

        # For paragraphs with code tags
        if single_translate:
            # Single translate mode: preserve code tags structure, replace only text
            # Create a copy to work with
            temp_p = copy(p)
            # Extract code tags temporarily
            code_placeholders = []
            for tag_name in exclude_tags_list:
                for tag in temp_p.find_all(tag_name):
                    code_placeholders.append(copy(tag))
                    tag.extract()

            # Now set the translated text and re-insert code tags
            # This is tricky - we need to map positions
            # Simpler approach: just set the translation and re-append code at the end
            temp_p.clear()
            temp_p.string = translated_text
            for code_tag in code_placeholders:
                temp_p.append(copy(code_tag))

            # Replace original content: the element now holds the translation
            p.clear()
            for content in temp_p.contents:
                p.append(copy(content))
            restamp_language(p, self.language_tag)
            # the element itself now holds nothing but the translation
            return p
        else:
            # Bilingual mode: keep original paragraph with code, add translation after
            if has_restricted_content_model(p):
                return append_inline_translation(
                    p, translated_text, translation_style, self.language_tag
                )
            new_p = copy(p)
            # Remove code tags from translation
            for tag_name in exclude_tags_list:
                for tag in new_p.find_all(tag_name):
                    tag.extract()
            new_p.string = translated_text
            # a translated copy is a second rendering, not a second anchor
            strip_duplicate_ids(new_p)
            restamp_language(new_p, self.language_tag)
            if translation_style != "":
                new_p["style"] = translation_style
            p.insert_after(new_p)
            return new_p

    def _show_usage(self, pbar):
        """Pin in/out/cached tokens on the bar, once a request reported them.

        `cached` is the number session mode is watched by: a history read
        back at full price every request shows up here and nowhere else.
        """
        try:
            postfix = self._usage_postfix()
            if postfix:
                pbar.set_postfix(postfix, refresh=False)
        except Exception:
            pass  # a readout; never a reason to stop the run it decorates

    def _usage_postfix(self):
        return getattr(self.translate_model, "usage_postfix", lambda: None)()

    def _usage_suffix(self):
        try:
            postfix = self._usage_postfix()
            if not postfix:
                return ""
            return " " + " ".join(f"{k}={v}" for k, v in postfix.items())
        except Exception:
            return ""

    def _print_usage(self):
        """One closing line, printed even under --quiet: the bill is not noise."""
        try:
            summary = getattr(self.translate_model, "usage_summary", lambda: None)()
            if summary:
                print(summary)
        except Exception:
            pass

    def _process_paragraph(self, p, new_p, index, p_to_save_len, thread_safe=False):
        if self.resume and index < p_to_save_len:
            # When resuming, keep original text in p, only restore translation
            # p.string should remain as original text from source EPUB
            new_p.string = self.p_to_save[index]
        else:
            t_text = ""
            if self.batch_flag:
                self.translate_model.add_to_batch_translate_queue(index, new_p.text)
            elif self.batch_use_flag:
                t_text = self.translate_model.batch_translate(index)
            else:
                t_text = self.translate_model.translate(new_p.text)
            if t_text is None:
                raise RuntimeError(
                    "`t_text` is None: your translation model is not working as expected. Please check your translation model configuration."
                )
            if type(p) is NavigableString:
                new_p = t_text
                self.p_to_save.append(new_p)
            else:
                new_p.string = t_text
                self.p_to_save.append(new_p.text)

        if type(p) is NavigableString:
            self.helper.insert_trans(
                p, new_p, self.translation_style, self.single_translate
            )
        else:
            self._insert_trans_preserving_tags(
                p, new_p.string, self.translation_style, self.single_translate
            )
        index += 1

        if thread_safe:
            with self._progress_lock:
                if index % 20 == 0:
                    self._save_progress()
        else:
            if index % 20 == 0:
                self._save_progress()
        return index

    def _process_combined_paragraph(
        self, p_block, index, p_to_save_len, thread_safe=False, plan_units=None
    ):
        """Returns (new_index, processed_count).

        `plan_units` (plan mode) supplies the Unit objects for each
        paragraph — pre-cleaned text with line numbers and other
        skip-classified nodes removed, already vetted for translatability.
        """
        # Each entry: (k, paragraph, text_to_translate_or_None_if_resumed, cached_translation_or_None)
        entries = []
        processed_count = 0

        for k, p in enumerate(p_block):
            if self.is_test and index >= self.test_num:
                break

            # Skip paragraphs that only contain excluded tags (code, pre, etc.)
            if plan_units is None and self._is_content_only_excluded_tags(p):
                processed_count += 1
                continue

            if (
                self.resume
                and index < p_to_save_len
                and self.p_to_save[index] is not None
            ):
                cached = self.p_to_save[index]
                entries.append((k, p, None, cached))
            else:
                raw = (
                    plan_units[k].text.rstrip()
                    if plan_units
                    else self._translation_source_text(p).rstrip()
                )
                entries.append((k, p, raw, None))

            index += 1
            processed_count += 1

        # Translate only the non-resumed paragraphs
        new_texts = [text for _, _, text, _ in entries if text is not None]
        # the same filter, so slot i of the reply is unit i of this list
        new_units = (
            [plan_units[k] for k, _, text, _ in entries if text is not None]
            if plan_units is not None
            else None
        )
        translated_text_list = self._translate_texts_aligned(new_texts, units=new_units)

        translate_iter = iter(translated_text_list)
        for k, p, text, cached in entries:
            # Check for fatal error and stop immediately
            if self.translate_model._fatal_error_detected:
                print(
                    "[bold red]Fatal translation error detected. Stopping paragraph processing.[/bold red]"
                )
                break

            if text is not None:
                # Fresh translation
                t = next(translate_iter)
                if plan_units is not None:
                    self._insert_plan_translation(
                        plan_units[k], t, self.translation_style, self.single_translate
                    )
                else:
                    self._insert_trans_preserving_tags(
                        p, t, self.translation_style, self.single_translate
                    )
                self.p_to_save.append(t)
                if not self.quiet:
                    print(text)
                # Check if translation failed
                if (
                    self.translate_model.TRANSLATION_ERROR_MARKER is not None
                    and t == self.translate_model.TRANSLATION_ERROR_MARKER
                ):
                    # an error is a signal, not an echo: it prints even in
                    # quiet mode
                    print(
                        f"[bold red][Translation failed for this paragraph][/bold red]"
                    )
                elif not self.quiet:
                    print(f"[bold green]{t}[/bold green]")
                if not self.quiet:
                    print()
            else:
                # Resumed from cache
                if plan_units is not None:
                    self._insert_plan_translation(
                        plan_units[k],
                        cached,
                        self.translation_style,
                        self.single_translate,
                    )
                else:
                    self._insert_trans_preserving_tags(
                        p, cached, self.translation_style, self.single_translate
                    )

        if thread_safe:
            with self._progress_lock:
                self._save_progress()
        else:
            self._save_progress()
        return index, processed_count

    def translate_paragraphs_acc(self, p_list, send_num):
        count = 0
        wait_p_list = []
        for i in range(len(p_list)):
            p = p_list[i]
            if not self.quiet:
                print(f"translating {i}/{len(p_list)}")
            temp_p = copy(p)

            for p_exclude in self.exclude_translate_tags.split(","):
                # for issue #280
                if type(p) is NavigableString:
                    continue
                for pt in temp_p.find_all(p_exclude):
                    pt.extract()

            # Also exclude content tags (code, pre, etc.)
            exclude_tags_list = [t for t in self.exclude_translate_tags.split(",") if t]
            for tag_name in exclude_tags_list:
                if type(p) is NavigableString:
                    continue
                for pt in temp_p.find_all(tag_name):
                    pt.extract()

            if any(
                [not p.text, self._is_special_text(temp_p.text), not_trans(temp_p.text)]
            ):
                if i == len(p_list) - 1:
                    self._deal_old_acc(wait_p_list, self.single_translate)
                continue
            length = num_tokens_from_text(temp_p.text)
            if length > send_num:
                self._deal_new_acc(p, wait_p_list, self.single_translate)
                continue
            if i == len(p_list) - 1:
                if count + length < send_num:
                    wait_p_list.append(p)
                    self._deal_old_acc(wait_p_list, self.single_translate)
                else:
                    self._deal_new_acc(p, wait_p_list, self.single_translate)
                break
            if count + length < send_num:
                count += length
                wait_p_list.append(p)
            else:
                self._deal_old_acc(wait_p_list, self.single_translate)
                wait_p_list.append(p)
                count = length

    def _deal_old_acc(self, wait_p_list, single_translate):
        """Helper for translate_paragraphs_acc - process accumulated paragraphs."""
        if not wait_p_list:
            return

        result_txt_list = translate_list_or_singles(
            self.translate_model, [p.text for p in wait_p_list]
        )

        for i in range(len(wait_p_list)):
            if i < len(result_txt_list):
                p = wait_p_list[i]
                self._insert_trans_preserving_tags(
                    p,
                    shorter_result_link(result_txt_list[i]),
                    self.translation_style,
                    single_translate,
                )

        wait_p_list.clear()

    def _deal_new_acc(self, p, wait_p_list, single_translate):
        """Helper for translate_paragraphs_acc - process single paragraph."""
        self._deal_old_acc(wait_p_list, single_translate)
        translation = self.translate_model.translate(p.text)
        self._insert_trans_preserving_tags(
            p,
            translation,
            self.translation_style,
            single_translate,
        )

    def _split_into_sentences(self, text):
        """Split text into sentences on punctuation followed by whitespace + uppercase."""
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"\'\(])", text.strip())
        return [s.strip() for s in parts if s.strip()]

    def _process_paragraph_sentence_mode(self, p, soup):
        """Translate a paragraph sentence by sentence, interleaving originals and translations.

        Returns True if sentence-level processing was applied, False if the paragraph
        should fall through to normal paragraph-level translation.
        """
        text = p.get_text().strip()
        sentences = self._split_into_sentences(text)

        # Only one sentence — let normal processing handle it
        if len(sentences) <= 1:
            return False

        try:
            translated_sentences = self.translate_model.translate_list(sentences)
        except Exception as e:
            print(f"[bold red]Sentence translation error: {e}[/bold red]")
            return False

        if len(translated_sentences) != len(sentences):
            return False

        style = self.translation_style or "color: #1e90ff;"
        if self.single_translate:
            p.clear()
            p.string = " ".join(translated_sentences)
        else:
            p.clear()
            for orig, trans in zip(sentences, translated_sentences):
                p.append(NavigableString(orig + " "))
                if trans and trans.strip() != orig.strip():
                    trans_span = soup.new_tag("span")
                    trans_span.string = trans + " "
                    trans_span["style"] = style
                    p.append(trans_span)

        return True

    def get_item(self, book, name):
        for item in book.get_items():
            if item.file_name == name:
                return item

    def find_items_containing_string(self, book, search_string):
        matching_items = []

        for item in book.get_items_of_type(ITEM_DOCUMENT):
            content = item.get_content()
            soup = bs(content, "html.parser")
            if search_string in soup.get_text():
                matching_items.append(item)

        return matching_items

    def retranslate_book(self, index, p_to_save_len, pbar, trans_taglist, retranslate):
        complete_book_name = retranslate[0]
        fixname = retranslate[1]
        fixstart = retranslate[2]
        fixend = retranslate[3]

        if fixend == "":
            fixend = fixstart

        name_fix = complete_book_name

        complete_book = epub.read_epub(complete_book_name)
        # The book being corrected is a previous *output*: its fonts are
        # obfuscated under its own identifier. Unscramble with its own path
        # as the key source, or the rewrite below scrambles them twice.
        deobfuscate_fonts(complete_book, complete_book_name)

        if fixname == "":
            fixname = self.find_items_containing_string(complete_book, fixstart)[
                0
            ].file_name
            print(f"auto find fixname: {fixname}")

        new_book = self._make_new_book(complete_book)

        complete_item = self.get_item(complete_book, fixname)
        if complete_item is None:
            return

        ori_item = self.get_item(self.origin_book, fixname)
        if ori_item is None:
            return

        content_complete = complete_item.content
        content_ori = ori_item.content
        soup_complete = bs(content_complete, "html.parser")
        soup_ori = bs(content_ori, "html.parser")

        p_list_complete = soup_complete.findAll(trans_taglist)
        p_list_ori = soup_ori.findAll(trans_taglist)

        target = None
        tagl = []

        # extract from range
        find_end = False
        find_start = False
        for tag in p_list_complete:
            if find_end:
                tagl.append(tag)
                break

            if fixend in tag.text:
                find_end = True
            if fixstart in tag.text:
                find_start = True

            if find_start:
                if not target:
                    target = tag.previous_sibling
                tagl.append(tag)

        for t in tagl:
            t.extract()

        flag = False
        extract_p_list_ori = []
        for p in p_list_ori:
            if fixstart in p.text:
                flag = True
            if flag:
                extract_p_list_ori.append(p)
            if fixend in p.text:
                break

        for t in extract_p_list_ori:
            if target:
                target.insert_after(t)
                target = t

        for item in complete_book.get_items():
            # A previous output's translation note is replaced by this run's,
            # not carried alongside it — two would be two ids and two members
            # of the same zip.
            if item.file_name != fixname and not is_our_colophon(item):
                new_book.add_item(item)
        if soup_complete:
            complete_item.content = soup_complete.encode()

        index = self.process_item(
            complete_item,
            index,
            p_to_save_len,
            pbar,
            new_book,
            trans_taglist,
            fixstart,
            fixend,
        )
        self._stamp_disclosure(new_book)
        epub.write_epub(f"{name_fix}", new_book, {})
        self._reobfuscate_written(f"{name_fix}")

    def has_nest_child(self, element, trans_taglist):
        if isinstance(element, Tag):
            for child in element.children:
                if child.name in trans_taglist:
                    return True
                if self.has_nest_child(child, trans_taglist):
                    return True
        return False

    def filter_nest_list(self, p_list, trans_taglist):
        filtered_list = [p for p in p_list if not self.has_nest_child(p, trans_taglist)]
        return filtered_list

    def _translation_source_text(self, node):
        if isinstance(node, NavigableString):
            return str(node)
        extracted = self._extract_paragraph(copy(node))
        return extracted.get_text()

    @staticmethod
    def _translation_job_id(document_index, file_name, node_index, source_text):
        source_hash = sha256(source_text.encode("utf-8")).hexdigest()[:16]
        return f"epub:{document_index}:{file_name}:{node_index}:{source_hash}"

    def _assign_batch_indexes(self, source_texts):
        if self.sentence_mode or (self.block_size < 1 and self.accumulated_num <= 1):
            return list(range(len(source_texts)))

        if self.accumulated_num > 1:
            indexes = []
            batch_index = 0
            batch_tokens = 0
            for source_text in source_texts:
                token_count = num_tokens_from_text(source_text)
                if batch_tokens and batch_tokens + token_count >= self.accumulated_num:
                    batch_index += 1
                    batch_tokens = 0
                indexes.append(batch_index)
                batch_tokens += token_count
                if token_count > self.accumulated_num:
                    batch_index += 1
                    batch_tokens = 0
            return indexes

        return [index // self.block_size for index in range(len(source_texts))]

    @staticmethod
    def _plan_batch_indexes(units, max_units=None):
        """Batch identity for plan units: one request per group.

        Grouped units are contiguous by construction, so numbering them in
        first-seen order keeps batch indexes monotonic — the same property
        _assign_batch_indexes gives tag mode.

        `max_units` cuts a group that is larger than this endpoint should be
        handed at once (see `_plan_request_cap`). The split is here rather
        than in the partition on purpose: the plan describes the book and
        must stay the same file whichever endpoint runs it, while how much
        one request may carry is a fact about the endpoint. Nothing else
        moves — resume slots are positions in the unit list, which the split
        does not touch.
        """
        indexes = []
        by_group = {}
        next_free = 0
        for unit in units:
            if unit.group_id is None:
                indexes.append(next_free)
                next_free += 1
                continue
            batch, carried = by_group.get(unit.group_id, (None, 0))
            if batch is None or (max_units is not None and carried >= max_units):
                batch, carried = next_free, 0
                next_free += 1
            by_group[unit.group_id] = (batch, carried + 1)
            indexes.append(batch)
        return indexes

    def _build_translation_plan(self, document_items, trans_taglist):
        """Enumerate all EPUB work before requests begin.

        Both execution modes consume these plans. File/tag filtering, test
        limits, document order, node order and batch identity therefore no
        longer depend on which worker happens to run first.
        """
        plans = []
        global_index = 0
        # asked once per run, not once per document: the answer is a
        # property of the endpoint, and asking is what triggers its probe
        request_cap = self._plan_request_cap() if self._plan_mode else None

        for document_index, item in enumerate(document_items):
            should_translate = True

            # Both filters choose what gets *translated*. Neither removes a
            # document from the book: the new book copies the source's spine
            # wholesale, so a dropped document leaves the spine, the nav and
            # the NCX pointing at a file that is not in the package
            # (epubcheck RSC-007) — a broken book, from a flag that only ever
            # claimed to narrow the work.
            if self.only_filelist and item.file_name not in self.only_filelist.split(
                ","
            ):
                should_translate = False
            elif (
                not self.only_filelist
                and item.file_name in self.exclude_filelist.split(",")
            ):
                should_translate = False

            nodes = []
            source_texts = []
            units = []
            if self._plan_mode:
                # The partition already decided what is content, so plan mode
                # enumerates its units rather than re-running the tag-mode
                # filters over the same soup.
                soup, file_plan = self._plan_partition(item, consume=True)
                if should_translate:
                    for unit in file_plan.units:
                        if self.is_test and global_index >= self.test_num:
                            break
                        units.append(unit)
                        nodes.append(unit.element)
                        source_texts.append(unit.text)
                        global_index += 1
                batch_indexes = self._plan_batch_indexes(units, request_cap)
            else:
                soup = bs(item.content, "html.parser")
                if should_translate:
                    candidate_nodes = soup.find_all(trans_taglist)
                    candidate_nodes = self.filter_nest_list(
                        candidate_nodes, trans_taglist
                    )
                    if self.allow_navigable_strings:
                        candidate_nodes.extend(soup.find_all(string=True))

                    for node in candidate_nodes:
                        if self.is_test and global_index >= self.test_num:
                            break
                        source_text = self._translation_source_text(node)
                        if not source_text or self._is_special_text(source_text):
                            continue
                        if self.accumulated_num > 1 and not_trans(source_text):
                            continue
                        if self._is_content_only_excluded_tags(node):
                            continue
                        nodes.append(node)
                        source_texts.append(source_text)
                        global_index += 1
                batch_indexes = self._assign_batch_indexes(source_texts)
                units = [None] * len(nodes)

            first_global_index = global_index - len(nodes)
            jobs = []
            for node_index, (node, source_text, batch_index, unit) in enumerate(
                zip(nodes, source_texts, batch_indexes, units)
            ):
                job_global_index = first_global_index + node_index
                jobs.append(
                    TranslationJob(
                        job_id=self._translation_job_id(
                            document_index,
                            item.file_name,
                            node_index,
                            source_text,
                        ),
                        document_index=document_index,
                        node_index=node_index,
                        global_index=job_global_index,
                        source_text=source_text,
                        context_group=item.file_name,
                        batch_index=batch_index,
                        node=node,
                        unit=unit,
                    )
                )

            plans.append(ChapterTranslationPlan(item=item, soup=soup, jobs=jobs))

        return plans

    def process_item(
        self,
        item,
        index,
        p_to_save_len,
        pbar,
        new_book,
        trans_taglist,
        fixstart=None,
        fixend=None,
        chapter_plan=None,
    ):
        if chapter_plan is None:
            if (
                self.only_filelist != ""
                and item.file_name not in self.only_filelist.split(",")
            ):
                return index
            elif (
                self.only_filelist == ""
                and item.file_name in self.exclude_filelist.split(",")
            ):
                new_book.add_item(item)
                return index

        if not os.path.exists("log"):
            os.makedirs("log")

        if self._plan_mode:
            if chapter_plan is None:
                # every plan-mode caller goes through _build_translation_plan;
                # falling back to tag-mode partitioning here would silently
                # translate a different set of nodes than the plan promised
                raise ValueError("plan mode requires a prebuilt chapter plan")
            index = self._process_plan_chunks(
                chapter_plan.jobs, index, p_to_save_len, pbar
            )
            item.content = chapter_plan.soup.encode(encoding="utf-8")
            new_book.add_item(item)
            return index

        if chapter_plan is None:
            content = item.content
            soup = bs(content, "html.parser")
            p_list = soup.findAll(trans_taglist)
            p_list = self.filter_nest_list(p_list, trans_taglist)
        else:
            soup = chapter_plan.soup
            p_list = [job.node for job in chapter_plan.jobs]

        if self.retranslate:
            new_p_list = []

            if fixstart is None or fixend is None:
                return

            start_append = False
            for p in p_list:
                text = p.get_text()
                if fixstart in text or fixend in text or start_append:
                    start_append = True
                    new_p_list.append(p)
                if fixend in text:
                    p_list = new_p_list
                    break

        if chapter_plan is None and self.allow_navigable_strings:
            p_list.extend(soup.findAll(text=True))

        send_num = self.accumulated_num
        if send_num > 1:
            with open("log/buglog.txt", "a") as f:
                print(f"------------- {item.file_name} -------------", file=f)

            print("------------------------------------------------------")
            print(f"dealing {item.file_name} ...")
            self.translate_paragraphs_acc(p_list, send_num)
        else:
            is_test_done = self.is_test and index >= self.test_num
            p_block = []
            block_len = 0
            for p in p_list:
                if is_test_done:
                    break

                # Check for fatal error during processing
                if self.translate_model._fatal_error_detected:
                    print(
                        "[bold red]Fatal translation error detected. Stopping chapter processing.[/bold red]"
                    )
                    break

                if not p.text or self._is_special_text(p.text):
                    # Skip empty/special paragraphs without updating progress bar
                    continue

                # If paragraph only contains excluded tags (code, pre, etc.), keep it without translation
                if self._is_content_only_excluded_tags(p):
                    # Don't translate, just keep the original paragraph
                    continue

                new_p = self._extract_paragraph(copy(p))
                if self.sentence_mode:
                    if self._process_paragraph_sentence_mode(p, soup):
                        index += 1
                        pbar.update(1)
                        self._show_usage(pbar)
                        if not self.quiet:
                            print()
                        if self.is_test and index >= self.test_num:
                            is_test_done = True
                        continue
                    # Fall through to normal paragraph processing if <1 sentence split
                if self.block_size >= 1:
                    # Collect paragraphs for batch translation
                    p_block.append(p)

                    # Process when we have enough paragraphs
                    if len(p_block) >= self.block_size:
                        index, n = self._process_combined_paragraph(
                            p_block, index, p_to_save_len, thread_safe=False
                        )
                        pbar.update(n)
                        self._show_usage(pbar)
                        p_block = []
                        if not self.quiet:
                            print()
                else:
                    index = self._process_paragraph(
                        p, new_p, index, p_to_save_len, thread_safe=False
                    )
                    if not self.quiet:
                        print()
                    pbar.update(1)
                    self._show_usage(pbar)

                if self.is_test and index >= self.test_num:
                    is_test_done = True
                    break

            # Process remaining paragraphs in the batch
            if self.block_size >= 1 and len(p_block) > 0:
                index, n = self._process_combined_paragraph(
                    p_block, index, p_to_save_len, thread_safe=False
                )
                pbar.update(n)
                self._show_usage(pbar)

        if soup:
            item.content = soup.encode(encoding="utf-8")
        new_book.add_item(item)

        return index

    def set_parallel_workers(self, workers):
        """Set number of parallel workers for chapter processing.

        Args:
            workers (int): Number of parallel workers. Will be automatically
                         optimized based on actual chapter count during processing.
        """
        self.parallel_workers = max(1, workers)
        self.enable_parallel = workers > 1

        if workers > 8:
            print(
                f"⚠️  Warning: {workers} workers is quite high. Consider using 2-8 workers for optimal performance."
            )

    def _record_translation_result(self, index, translated_text):
        """Commit completed work as a document-ordered contiguous prefix."""
        with self._progress_lock:
            if index < len(self.p_to_save):
                return
            self._pending_translation_results[index] = translated_text
            while len(self.p_to_save) in self._pending_translation_results:
                self.p_to_save.append(
                    self._pending_translation_results.pop(len(self.p_to_save))
                )
            if len(self.p_to_save) // 20 > self._last_saved_progress // 20:
                self._save_progress()

    def _process_chapter_parallel(self, chapter_data):
        """Process a single chapter in parallel mode with proper accumulated_num handling."""
        chapter_plan, p_to_save_len = chapter_data
        item = chapter_plan.item
        chapter_result = {
            "item": item,
            "processed_content": None,
            "success": False,
            "error": None,
        }

        try:
            # Create a chapter-specific translator instance to avoid context conflicts
            # This ensures each chapter has its own independent context
            thread_translator = self._create_chapter_translator()

            soup = chapter_plan.soup

            if self._plan_mode:
                # own context buffers: chapters run out of order here
                plan_translator = self._clone_translator_for_context()
                for chunk in self._iter_plan_chunks(chapter_plan.jobs):
                    if self.translate_model._fatal_error_detected:
                        break
                    fresh = []
                    for job in chunk:
                        idx = job.global_index
                        if (
                            self.resume
                            and idx < p_to_save_len
                            and self.p_to_save[idx] is not None
                        ):
                            self._insert_plan_translation(
                                job.unit,
                                self.p_to_save[idx],
                                self.translation_style,
                                self.single_translate,
                            )
                        else:
                            fresh.append(job)
                    if fresh:
                        translated = self._translate_texts_aligned(
                            [job.source_text for job in fresh],
                            plan_translator,
                            [job.unit for job in fresh],
                        )
                        for job, t_text in zip(fresh, translated):
                            self._record_translation_result(job.global_index, t_text)
                            self._insert_plan_translation(
                                job.unit,
                                t_text,
                                self.translation_style,
                                self.single_translate,
                            )
                chapter_result["processed_content"] = soup.encode(encoding="utf-8")
                chapter_result["success"] = True
                return chapter_result

            # Initialize chapter-specific context lists
            chapter_context_list = []
            chapter_translated_list = []

            send_num = self.accumulated_num
            if send_num > 1:
                self._translate_paragraphs_acc_parallel(
                    [job.node for job in chapter_plan.jobs],
                    send_num,
                    thread_translator,
                    chapter_context_list,
                    chapter_translated_list,
                )
            else:
                # Process paragraphs individually for this chapter
                for job in chapter_plan.jobs:
                    p = job.node
                    index = job.global_index

                    if (
                        self.resume
                        and index < p_to_save_len
                        and self.p_to_save[index] is not None
                    ):
                        t_text = self.p_to_save[index]
                    else:
                        # Use chapter-specific context for translation
                        t_text = self._translate_with_chapter_context(
                            thread_translator,
                            job.source_text,
                            chapter_context_list,
                            chapter_translated_list,
                        )
                        t_text = "" if t_text is None else t_text
                        self._record_translation_result(index, t_text)

                    if isinstance(p, NavigableString):
                        translated_node = NavigableString(t_text)
                        p.insert_after(translated_node)
                        if self.single_translate:
                            p.extract()
                    else:
                        self._insert_trans_preserving_tags(
                            p, t_text, self.translation_style, self.single_translate
                        )

            if soup:
                chapter_result["processed_content"] = soup.encode(encoding="utf-8")
            chapter_result["success"] = True

        except Exception as e:
            chapter_result["error"] = str(e)
            print(f"Error processing chapter {item.file_name}: {e}")

        return chapter_result

    def _create_chapter_translator(self):
        """Create a translator instance for a specific chapter with independent context."""
        # Return the main translator - we'll handle context at the chapter level
        return self.translate_model

    def _clone_translator_for_context(self):
        """A translator with its own context buffers for one parallel chapter.

        Plan mode drives translate_model directly, so with --use_context and
        --parallel-workers every chapter appended into one global
        context_list: paragraphs arrive out of reading order *and* two
        threads mutate the same list. Cloning is shallow on purpose — keys,
        model config and the API/probe locks stay shared (rate limiting and
        the structured-output probe must remain global), only the mutable
        context state is fresh.

        Sequential runs keep the shared instance: there the accumulation is
        in reading order and worth having. Session mode never arrives here:
        one history is the context and workers cannot share it, so the
        pairing is refused on the command line.
        """
        if self.parallel_workers <= 1 or not getattr(
            self.translate_model, "context_flag", False
        ):
            return self.translate_model
        clone = copy(self.translate_model)
        clone.context_list = []
        clone.context_translated_list = []
        if hasattr(clone, "create_convo"):
            # gemini keeps context in its chat object, not the lists above —
            # a shallow copy would share one convo across chapters (a thread
            # race and cross-chapter context bleed). Fresh chat, shared
            # client.
            clone.create_convo()
        return clone

    def _translate_with_chapter_context(
        self, translator, text, chapter_context_list, chapter_translated_list
    ):
        """Translate text with chapter-specific context management."""
        if not translator.context_flag:
            return translator.translate(text)

        # Temporarily replace global context with chapter context
        original_context = getattr(translator, "context_list", [])
        original_translated = getattr(translator, "context_translated_list", [])

        try:
            # Use chapter-specific context
            translator.context_list = chapter_context_list.copy()
            translator.context_translated_list = chapter_translated_list.copy()

            # Perform translation
            result = translator.translate(text)

            # Update chapter context
            chapter_context_list[:] = translator.context_list
            chapter_translated_list[:] = translator.context_translated_list

            return result

        finally:
            # Restore original context
            translator.context_list = original_context
            translator.context_translated_list = original_translated

    def _translate_paragraphs_acc_parallel(
        self,
        p_list,
        send_num,
        translator,
        chapter_context_list,
        chapter_translated_list,
    ):
        """Apply accumulated_num logic for a single chapter in parallel mode with independent context."""
        count = 0
        wait_p_list = []

        # Create chapter-specific helper instance with context-aware translation
        class ChapterHelper:
            def __init__(
                self, parent_loader, translator, context_list, translated_list
            ):
                self.parent_loader = parent_loader
                self.translator = translator
                self.context_list = context_list
                self.translated_list = translated_list

            def translate_with_context(self, text):
                return self.parent_loader._translate_with_chapter_context(
                    self.translator, text, self.context_list, self.translated_list
                )

            def deal_old(self, wait_p_list, single_translate):
                if not wait_p_list:
                    return

                # Use the same translate_list logic as sequential processing
                # Create a temporary translator with chapter context
                original_context = getattr(self.translator, "context_list", [])
                original_translated = getattr(
                    self.translator, "context_translated_list", []
                )

                try:
                    # Set chapter context to the translator
                    self.translator.context_list = self.context_list.copy()
                    self.translator.context_translated_list = (
                        self.translated_list.copy()
                    )

                    # Call translate_list for consistent batch translation logic
                    result_txt_list = translate_list_or_singles(
                        self.translator, [p.text for p in wait_p_list]
                    )

                    # Update chapter context from translator
                    self.context_list[:] = self.translator.context_list
                    self.translated_list[:] = self.translator.context_translated_list

                    # Apply translations using the same logic as helper.deal_old
                    for i in range(len(wait_p_list)):
                        if i < len(result_txt_list):
                            p = wait_p_list[i]

                            self.parent_loader._insert_trans_preserving_tags(
                                p,
                                shorter_result_link(result_txt_list[i]),
                                self.parent_loader.translation_style,
                                single_translate,
                            )

                finally:
                    # Restore original context
                    self.translator.context_list = original_context
                    self.translator.context_translated_list = original_translated

                wait_p_list.clear()

            def deal_new(self, p, wait_p_list, single_translate):
                self.deal_old(wait_p_list, single_translate)
                translation = self.translate_with_context(p.text)
                self.parent_loader._insert_trans_preserving_tags(
                    p,
                    translation,
                    self.parent_loader.translation_style,
                    single_translate,
                )

        chapter_helper = ChapterHelper(
            self, translator, chapter_context_list, chapter_translated_list
        )

        for i in range(len(p_list)):
            p = p_list[i]

            # Skip paragraphs that only contain excluded tags (code, pre, etc.)
            if self._is_content_only_excluded_tags(p):
                if i == len(p_list) - 1:
                    chapter_helper.deal_old(wait_p_list, self.single_translate)
                continue

            temp_p = copy(p)

            for p_exclude in self.exclude_translate_tags.split(","):
                if isinstance(p, NavigableString):
                    continue
                for pt in temp_p.find_all(p_exclude):
                    pt.extract()

            # Exclude content within specified tags from translation (e.g., code, pre)
            exclude_tags_list = [t for t in self.exclude_translate_tags.split(",") if t]
            for tag_name in exclude_tags_list:
                if isinstance(p, NavigableString):
                    continue
                for pt in temp_p.find_all(tag_name):
                    pt.extract()

            if any(
                [not p.text, self._is_special_text(temp_p.text), not_trans(temp_p.text)]
            ):
                if i == len(p_list) - 1:
                    chapter_helper.deal_old(wait_p_list, self.single_translate)
                continue

            length = num_tokens_from_text(temp_p.text)
            if length > send_num:
                chapter_helper.deal_new(p, wait_p_list, self.single_translate)
                continue

            if i == len(p_list) - 1:
                if count + length < send_num:
                    wait_p_list.append(p)
                    chapter_helper.deal_old(wait_p_list, self.single_translate)
                else:
                    chapter_helper.deal_new(p, wait_p_list, self.single_translate)
                break

            if count + length < send_num:
                count += length
                wait_p_list.append(p)
            else:
                chapter_helper.deal_old(wait_p_list, self.single_translate)
                wait_p_list.append(p)
                count = length

    def batch_init_then_wait(self):
        name, _ = os.path.splitext(self.epub_name)
        if self.batch_flag or self.batch_use_flag:
            self.translate_model.batch_init(name)
            if self.batch_use_flag:
                start_time = time.time()
                while not self.translate_model.is_completed_batch():
                    print("Batch translation is not completed yet")
                    time.sleep(2)
                    if time.time() - start_time > 300:  # 5 minutes
                        raise Exception("Batch translation timed out after 5 minutes")

    def make_bilingual_book(self):
        # Before the plan is built and before anything is translated: a
        # checkpoint written into another language (or under another prompt,
        # or by another model) is not this run's to continue.
        self._check_resume_run_fingerprint()
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
            language=self.language,
        )

        # Check for fatal errors before starting
        if self.translate_model._fatal_error_detected:
            print(
                "[bold red]Fatal translation error detected. Aborting book creation.[/bold red]"
            )
            return

        if self._plan_mode:
            self._enter_plan_mode()

        self.batch_init_then_wait()
        new_book = self._make_new_book(self.origin_book)
        trans_taglist = self.translate_tags.split(",")
        # A book being run through the tool a second time already carries a
        # colophon. It is replaced, not translated and kept beside the new
        # one — two of them would be two manifest entries under one id.
        document_items = [
            item
            for item in self.origin_book.get_items_of_type(ITEM_DOCUMENT)
            if not is_our_colophon(item)
        ]
        chapter_plans = self._build_translation_plan(document_items, trans_taglist)
        self._planned_job_ids = [
            job.job_id for plan in chapter_plans for job in plan.jobs
        ]
        if (
            self.resume
            and self._checkpoint_job_ids
            != self._planned_job_ids[: len(self._checkpoint_job_ids)]
        ):
            raise ValueError(
                "The EPUB or translation filters changed after the checkpoint; "
                "delete the resume file and restart the translation"
            )

        # The plan is the single source of truth for progress and execution.
        all_p_length = sum(len(plan.jobs) for plan in chapter_plans)
        self._report_test_slice_requests(chapter_plans, all_p_length)

        # Use leave=False in test mode to prevent duplicate progress bar display
        pbar = tqdm(
            total=self.test_num if self.is_test else all_p_length,
            leave=not self.is_test,
            disable=self.quiet,
        )
        if not self.quiet:
            print()
        index = 0
        p_to_save_len = len(self.p_to_save)
        self._pending_translation_results = {}
        try:
            if self.retranslate:
                self.retranslate_book(
                    index, p_to_save_len, pbar, trans_taglist, self.retranslate
                )
                exit(0)
            # Add the things that don't need to be translated first, so that you can see the img after the interruption
            for item in self.origin_book.get_items():
                if item.get_type() != ITEM_DOCUMENT:
                    new_book.add_item(item)

            # A document with no jobs — filtered out by --only_filelist or
            # --exclude_filelist, or simply holding nothing to translate —
            # goes into the book exactly as it came, without a parse and
            # serialize round-trip it has no reason to pay for.
            output_plans = []
            for plan in chapter_plans:
                if plan.jobs:
                    output_plans.append(plan)
                else:
                    new_book.add_item(plan.item)

            if self.enable_parallel and len(output_plans) > 1:
                # Optimize worker count: no point having more workers than chapters
                effective_workers = min(self.parallel_workers, len(output_plans))

                # Parallel processing with proper accumulated_num handling
                print(f"🚀 Parallel processing: {len(output_plans)} chapters")
                if effective_workers < self.parallel_workers:
                    print(
                        f"📊 Optimized workers: {effective_workers} (reduced from {self.parallel_workers})"
                    )
                else:
                    print(f"📊 Using {effective_workers} workers")

                if self.accumulated_num > 1:
                    print(
                        f"📝 Each chapter applies accumulated_num={self.accumulated_num} independently"
                    )

                if self.context_flag:
                    print(
                        f"🔗 Context enabled: each chapter maintains independent context (limit={self.translate_model.context_paragraph_limit})"
                    )
                else:
                    print(f"🚫 Context disabled for this translation")

                # Create a simpler progress bar for parallel processing
                pbar.close()  # Close the original progress bar
                chapter_pbar = tqdm(
                    total=len(output_plans),
                    desc="Chapters",
                    unit="ch",
                    disable=self.quiet,
                )

                chapter_data_list = [(plan, p_to_save_len) for plan in output_plans]

                failed_chapters = []
                with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                    future_to_item = {
                        executor.submit(
                            self._process_chapter_parallel, chapter_data
                        ): chapter_data[0].item
                        for chapter_data in chapter_data_list
                    }

                    for future in as_completed(future_to_item):
                        # Check for fatal error
                        if self.translate_model._fatal_error_detected:
                            print(
                                "[bold red]Fatal translation error detected. Stopping book creation.[/bold red]"
                            )
                            chapter_pbar.close()
                            return

                        item = future_to_item[future]
                        try:
                            result = future.result()
                            if result["success"] and result["processed_content"]:
                                item.content = result["processed_content"]
                            elif not result["success"]:
                                failed_chapters.append(
                                    (item.file_name, result["error"])
                                )
                            new_book.add_item(item)
                            chapter_pbar.update(1)
                            chapter_pbar.set_postfix_str(
                                f"Latest: {item.file_name[:20]}..."
                                + self._usage_suffix()
                            )

                        except Exception as e:
                            print(f"❌ Error processing {item.file_name}: {e}")
                            failed_chapters.append((item.file_name, str(e)))
                            new_book.add_item(item)
                            chapter_pbar.update(1)

                chapter_pbar.close()
                self._print_usage()
                if failed_chapters:
                    # fail loud: a partial book must not masquerade as done
                    for file_name, error in failed_chapters:
                        print(
                            f"[bold red]chapter failed: {file_name}: {error}[/bold red]"
                        )
                    print(
                        f"[bold red]{len(failed_chapters)}/{len(output_plans)} "
                        f"chapters failed — saving progress, not writing the "
                        f"bilingual book. Re-run with --resume.[/bold red]"
                    )
                    self._save_progress()
                    raise SystemExit(1)
                print(f"✅ Completed all {len(output_plans)} chapters")
            else:
                # Sequential processing (original behavior or single chapter)
                if len(output_plans) == 1 and self.enable_parallel:
                    print(f"📄 Single chapter detected - using sequential processing")

                for chapter_plan in output_plans:
                    item = chapter_plan.item
                    # Check for fatal error before processing each item
                    if self.translate_model._fatal_error_detected:
                        print(
                            "[bold red]Fatal translation error detected. Stopping book creation.[/bold red]"
                        )
                        return

                    index = self.process_item(
                        item,
                        index,
                        p_to_save_len,
                        pbar,
                        new_book,
                        trans_taglist,
                        chapter_plan=chapter_plan,
                    )

                    # Check for fatal error after processing
                    if self.translate_model._fatal_error_detected:
                        print(
                            "[bold red]Fatal translation error detected. Aborting book creation.[/bold red]"
                        )
                        pbar.close()
                        return

                # Close progress bar
                pbar.close()
                self._print_usage()

                if self.accumulated_num > 1:
                    name, _ = os.path.splitext(self.epub_name)
                    self._stamp_disclosure(new_book)
                    epub.write_epub(f"{name}_bilingual.epub", new_book, {})
                    self._reobfuscate_written(f"{name}_bilingual.epub")
            name, _ = os.path.splitext(self.epub_name)
            if self.batch_flag:
                self.translate_model.batch()
            else:
                self._stamp_disclosure(new_book)
                epub.write_epub(f"{name}_bilingual.epub", new_book, {})
                self._reobfuscate_written(f"{name}_bilingual.epub")
        except KeyboardInterrupt as e:
            print(e)
            # The accumulated_num guard is tag-mode-shaped: its positional
            # slots are unreliable mid-batch. Plan-mode checkpoints are
            # unit-keyed and only record finished units, so a batched plan
            # run is exactly as resumable as an unbatched one.
            if self.accumulated_num == 1 or self.plan_mode:
                print("you can resume it next time")
                self._save_progress()
                self._save_temp_book()
            # A halted run has no finished book. 0 told every caller — a
            # script, a shell, an agent — that it did, and the shell's own
            # code for a process killed by SIGINT is 130.
            sys.exit(130)
        except Exception as e:
            # Handle connection errors gracefully
            error_msg = str(e)
            if getattr(e, "user_facing", False):
                # The message is the whole explanation — a traceback on top
                # of it only buries what the reader needs.
                print(f"[bold red]{escape(error_msg)}[/bold red]")
            elif "Connection" in error_msg or "connection" in error_msg:
                print(
                    f"[bold red]Translation failed: Connection error - {error_msg}[/bold red]"
                )
                print("Please check your network connection or API server status.")
            else:
                traceback.print_exc()
            if self.accumulated_num == 1 or self.plan_mode:
                print("Saving progress...")
                try:
                    self._save_progress()
                    self._save_temp_book()
                except Exception:
                    # `_save_temp_book` re-raises so a failed recovery book is
                    # never silent, but here it is already reported and the
                    # run's own failure is the one that owes the caller an
                    # exit code — letting it escape would lose the sys.exit
                    # below and report the wrong cause.
                    traceback.print_exc()
            # the run failed and there is no output book; exiting 0 would
            # tell every caller the opposite
            sys.exit(1)

    def load_state(self):
        try:
            with open(self.bin_path, "rb") as f:
                state = pickle.load(f)
            if (
                not isinstance(state, dict)
                or state.get("version") != self.CHECKPOINT_VERSION
            ):
                raise ValueError(
                    "Legacy EPUB resume checkpoints cannot be safely reused; "
                    f"delete {self.bin_path} and restart the translation"
                )
            if state.get("order") != self.CHECKPOINT_ORDER or not isinstance(
                state.get("translations"), list
            ):
                raise ValueError("Invalid EPUB resume checkpoint")
            if not isinstance(state.get("job_ids"), list) or len(
                state["job_ids"]
            ) != len(state["translations"]):
                raise ValueError("Invalid EPUB resume checkpoint job identities")
            self.p_to_save = state["translations"]
            self._checkpoint_job_ids = state["job_ids"]
            self._last_saved_progress = len(self.p_to_save)
            # plan mode (see _save_progress): which plan — overrides and
            # schema version — these slots were written under. Absent from
            # tag-mode checkpoints, and the fingerprint check that consumes
            # it only runs in plan mode.
            self._resume_plan_fingerprint = state.get("plan_fingerprint")
            # language/prompt/model (see _run_fingerprint). Absent from
            # checkpoints written before this was recorded: those warn and
            # continue rather than being refused.
            self._resume_run_fingerprint = state.get("run_fingerprint")
        except ValueError:
            raise
        except Exception:
            raise Exception("can not load resume file")

    def _save_temp_book(self):
        name, _ = os.path.splitext(self.epub_name)
        temp_path = f"{name}_bilingual_temp.epub"
        origin_book_temp = epub.read_epub(self.epub_name)
        # A second read of the source is a second set of item objects, still
        # carrying the source's obfuscated font bytes. Without this the
        # recovery book ships them scrambled under the source's key and then
        # scrambled again by the write step — a font nothing can parse.
        deobfuscate_fonts(origin_book_temp, self.epub_name)
        new_temp_book = self._make_new_book(origin_book_temp)
        trans_taglist = self.translate_tags.split(",")
        document_items = [
            item
            for item in origin_book_temp.get_items_of_type(ITEM_DOCUMENT)
            if not is_our_colophon(item)
        ]
        chapter_plans = iter(
            self._build_translation_plan(document_items, trans_taglist)
        )
        try:
            for item in origin_book_temp.get_items():
                # A previous run's note is dropped, not carried: it has no
                # plan (asking for one ended the recovery save with
                # StopIteration), and copying it into the book anyway left
                # the stamp below thinking the book was already stamped —
                # so the recovery book kept the *last* run's claim, and
                # --no_disclosure kept it too.
                if is_our_colophon(item):
                    continue
                if item.get_type() == ITEM_DOCUMENT:
                    # one plan per document, consumed in document order: the
                    # replay must walk exactly the selection the processing
                    # pass walked or global_index lands on unrelated text
                    chapter_plan = next(chapter_plans)
                    for job in chapter_plan.jobs:
                        if job.global_index >= len(self.p_to_save):
                            break
                        translated_text = self.p_to_save[job.global_index]
                        if job.unit is not None:
                            # plan mode owns specific text nodes; replacing the
                            # whole element would delete the skip-classified
                            # nodes it deliberately left alone
                            self._insert_plan_translation(
                                job.unit,
                                translated_text,
                                self.translation_style,
                                self.single_translate,
                            )
                        elif isinstance(job.node, NavigableString):
                            translated_node = NavigableString(translated_text)
                            job.node.insert_after(translated_node)
                            if self.single_translate:
                                job.node.extract()
                        else:
                            self._insert_trans_preserving_tags(
                                job.node,
                                translated_text,
                                self.translation_style,
                                self.single_translate,
                            )
                    item.content = chapter_plan.soup.encode()
                new_temp_book.add_item(item)
            self._stamp_disclosure(new_temp_book)
            epub.write_epub(temp_path, new_temp_book, {})
            self._reobfuscate_written(temp_path)
        except Exception as e:
            # The recovery book is the only artifact a crashed run leaves
            # behind. Swallowing this told the user nothing and they found
            # out at the next --resume, with no temp book to show.
            print(
                f"[bold red]could not write the recovery book "
                f"{temp_path}: {e}[/bold red]"
            )
            raise

    def _save_progress(self):
        completed_job_ids = self._planned_job_ids[: len(self.p_to_save)]
        if len(completed_job_ids) != len(self.p_to_save):
            raise ValueError(
                "Cannot save EPUB progress before planning translation jobs"
            )
        try:
            payload = {
                "version": self.CHECKPOINT_VERSION,
                "order": self.CHECKPOINT_ORDER,
                "job_ids": completed_job_ids,
                "translations": self.p_to_save,
                # job ids bind a slot to its source text; this binds its
                # contents to the language, prompt and model that wrote them
                "run_fingerprint": self._run_fingerprint(),
            }
            if self._plan_mode and self._plan_fingerprint:
                # job ids bind the slots to the book's text; the plan
                # fingerprint binds them to the decisions that produced the
                # unit list, which the same text can change between runs
                payload["plan_fingerprint"] = self._plan_fingerprint
            with open(self.bin_path, "wb") as f:
                pickle.dump(payload, f)
            self._last_saved_progress = len(self.p_to_save)
        except Exception:
            raise Exception("can not save resume file")
