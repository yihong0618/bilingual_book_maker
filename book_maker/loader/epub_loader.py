import builtins
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

from book_maker.utils import num_tokens_from_text, prompt_config_to_kwargs

from .base_loader import BaseBookLoader
from .helper import (
    EPUBBookLoaderHelper,
    append_inline_translation,
    backfill_toc_hrefs,
    derive_translation_identity,
    has_restricted_content_model,
    is_text_link,
    make_tag,
    not_trans,
    rebase_ncx_srcs,
    shorter_result_link,
    strip_duplicate_ids,
)
from .plan import (
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
)
from .classify import (
    PlanClassifyError,
    PlanUnresolvedError,
    build_agent_prompt,
    classify_plan,
    decide_everything,
    mode_policy,
)

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


class EPUBBookLoader(BaseBookLoader):
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
        temperature=1.0,
        source_lang="auto",
        parallel_workers=1,
    ):
        self.epub_name = epub_name
        self.language = language
        self.new_epub = epub.EpubBook()
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            context_flag=context_flag,
            context_paragraph_limit=context_paragraph_limit,
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
        self.translation_style = ""
        self.context_flag = context_flag
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
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
        self.plan_min_coverage = 0.5
        self.poetry_group_size = 8
        # most | model | agent (see .classify). "most" is the deliberate
        # translate-the-whole-partition choice and the only mode that asks
        # nothing; there is no mode where nobody decides and the code
        # translates whatever it could not rule out.
        self.plan_classify = "most"
        self.plan_classify_model = None  # user-chosen classifier; failure blocks
        self._plan_css = None
        self._plan_overrides = None
        self._plan_partitions = {}  # file_name -> (soup, FilePlan), see _plan_partition
        self._plan_fingerprint = None
        self._resume_plan_fingerprint = None
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

        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(epub_name).parent}/.{Path(epub_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

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
        identifier_id = derive_translation_identity(
            new_book,
            self.origin_book,
            self.language,
            "single" if self.single_translate else "bilingual",
        )
        allowed_ns = set(epub.NAMESPACES.keys()) | set(epub.NAMESPACES.values())

        for namespace, metas in book.metadata.items():
            # Only keep namespaces recognized by ebooklib
            if namespace not in allowed_ns:
                continue

            if isinstance(metas, dict):
                entries = (
                    (name, value, others)
                    for name, values in metas.items()
                    for value, others in (
                        (item if isinstance(item, tuple) else (item, None))
                        for item in values
                    )
                )
            else:
                entries = metas

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

                if name == "link":
                    # ebooklib parses OPF <link rel=… href=…> into metadata
                    # but writes every metadata entry back as <meta>, where
                    # rel/href are not legal attributes — the book then
                    # fails validation (RSC-005) over an accessibility
                    # statement it merely copied. Dropping the link loses a
                    # pointer; keeping it loses the book.
                    continue

                if (
                    identifier_id
                    and name == "identifier"
                    and (others or {}).get("id") == identifier_id
                ):
                    # the derived identity above owns this id attribute
                    # (RSC-005 otherwise); the source's value stays, as a
                    # secondary identifier without it
                    others = {k: v for k, v in others.items() if k != "id"}

                # `others` can be {} or None
                if others:
                    new_book.add_metadata(namespace, name, value, others)
                else:
                    new_book.add_metadata(namespace, name, value)

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
        ]
        new_book.toc = backfill_toc_hrefs(self._fix_toc_uids(book.toc))
        return new_book

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
            saved_ledger, overrides = load_plan_overrides(plan_path, self.epub_name)
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
        ledger = plan.build_ledger(decisions=saved_ledger)
        plan_written = False
        # A plan answers the questions the book asked when it was written.
        # Change the file filters or the excluded tags and the book asks
        # different ones — and a question with no row in the file cannot be
        # answered, so the demand to decide it can never be satisfied.
        added, dropped = set(), set()
        if saved_ledger is not None:
            added = set(ledger.rows) - set(saved_ledger.rows)
            dropped = set(saved_ledger.rows) - set(ledger.rows)
        if policy.name == "most":
            # "most" answers every question the same way, on purpose and out
            # loud — see classify/most.py for why that is not a default.
            decide_everything(ledger)
            plan.record_dispositions(ledger)

        # LLM classification is owed to whatever is still a question — on the
        # run that creates the plan JSON, and equally on a run that finds one
        # drafted by --plan-dry-run or left half-answered by an earlier
        # failure. Rows already decided are never re-asked, so the JSON stays
        # the source of truth (user edits win, resume fingerprints stay
        # stable). Delete it to reclassify from scratch.
        if self.plan_classify == "model" and ledger.undecided_keys():
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
        if self.resume and self.p_to_save and self._resume_plan_fingerprint is None:
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
            # already written above with its own message, or ("most") a mode
            # that asks nothing and therefore writes no plan file
            pass
        elif not plan_existed:
            plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            undecided = ledger.undecided_keys()
            print(
                f"plan written to {plan_path} with {len(undecided)} "
                f"undecided signature(s) (null actions must be resolved)"
            )
        elif added:
            # The rewrite carries every existing decision forward (they were
            # merged into this ledger before anything was applied), so the
            # only rows it changes are the ones that had nowhere to live.
            plan.save_json(plan_path, book_path=self.epub_name, ledger=ledger)
            print(
                f"{plan_path} updated: {len(added)} signature(s) this run's "
                f"settings introduced now have rows to decide"
            )
            if dropped:
                print(
                    f"[bold yellow]{len(dropped)} decided signature(s) no "
                    f"longer occur under these settings and were dropped from "
                    f"{plan_path}: {', '.join(sorted(dropped)[:5])}. Restoring "
                    f"the previous settings means deciding them again."
                    f"[/bold yellow]"
                )
        else:
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
        generic example would have to be translated back by hand.
        """
        parts = [shlex.quote(a) for a in sys.argv]
        return " ".join(["python3", *parts])

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
            files, self._exclude_tags_tuple(), self.poetry_group_size
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
                f"yourself, or --plan-classify most to translate the whole "
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
            self._write_single_translation(unit, t_text)
        elif has_restricted_content_model(unit.element):
            self._append_inline_translation(unit, t_text, translation_style)
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
            self._insert_anchored_translation(unit, t_text, translation_style)
        else:
            self._insert_trans_preserving_tags(
                unit.element, t_text, translation_style, False
            )

    @staticmethod
    def _append_inline_translation(unit, t_text, translation_style=""):
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
        unit.nodes[-1].insert_after(span)

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
    def _insert_anchored_translation(unit, t_text, translation_style=""):
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
        if EPUBBookLoader._markup_covers_run(tail, {id(n) for n in unit.nodes}):
            span = copy(tail)
            span.clear()
            # a translated copy is a second rendering, not a second anchor
            strip_duplicate_ids(span)
        else:
            span = make_tag("span")
        if translation_style:
            span["style"] = translation_style
        span.string = t_text
        # inserted in the order they are read — run, break, translation.
        # `make_tag` is what keeps the break a single <br/>; see helper.py.
        line_break = make_tag("br")
        tail.insert_after(line_break)
        line_break.insert_after(span)

    @staticmethod
    def _write_single_translation(unit, t_text):
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
            unit.nodes[0].replace_with(NavigableString(t_text))
            for node in unit.nodes[1:]:
                node.extract()
            EPUBBookLoader._remove_emptied_wrappers(emptied)
            return

        anchor = container if isinstance(container, Tag) else unit.nodes[0]
        # only wrappers *we* empty are ours to remove: an already-empty
        # <a id="…"> is a link target the book still needs
        emptied = EPUBBookLoader._collect_wrappers(unit.nodes, unit.element)
        anchor.insert_before(NavigableString(t_text))
        for node in unit.nodes:
            node.extract()
        EPUBBookLoader._remove_emptied_wrappers(emptied)

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
            if element.name in _KEEP_WHEN_EMPTY or element.get("id"):
                continue
            if element.find(string=True) is not None:
                continue
            # An id *inside* the husk is a link target just the same:
            # <span><a id="ch1">Chapter One</a></span> is how a heading marks
            # itself, and deleting the wrapper takes with it the anchor every
            # cross-reference in the book points at.
            if element.find(list(_KEEP_WHEN_EMPTY)) or element.find(attrs={"id": True}):
                continue
            # extract, not decompose: decompose clears the state of every
            # descendant, and a later run in the same document may still
            # hold references into this subtree
            element.extract()

    def _translate_texts_aligned(self, texts, translator=None):
        """translate_list with an alignment ladder: group -> halves -> singles.

        A response with the wrong item count must never desync originals and
        translations; instead we split and retry until counts match.
        `translator` defaults to the shared model; parallel chapters pass
        their own clone so --use_context stays chapter-local.
        """
        if not texts:
            return []
        translator = translator or self.translate_model
        try:
            result = translator.translate_list(texts)
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
            return result
        print(
            f"[bold red]alignment mismatch: sent {len(texts)} paragraphs, "
            f"received {len(result)} — splitting for realignment[/bold red]"
        )
        if len(texts) == 1:
            t = translator.translate(texts[0])
            if t is None:
                raise RuntimeError(
                    "`t_text` is None: your translation model is not working as expected."
                )
            return [t]
        mid = len(texts) // 2
        return self._translate_texts_aligned(
            texts[:mid], translator
        ) + self._translate_texts_aligned(texts[mid:], translator)

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
            self.helper.insert_trans(
                p, translated_text, translation_style, single_translate
            )
            return

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

            # Replace original content
            p.clear()
            for content in temp_p.contents:
                p.append(copy(content))
        else:
            # Bilingual mode: keep original paragraph with code, add translation after
            if has_restricted_content_model(p):
                append_inline_translation(p, translated_text, translation_style)
                return
            new_p = copy(p)
            # Remove code tags from translation
            for tag_name in exclude_tags_list:
                for tag in new_p.find_all(tag_name):
                    tag.extract()
            new_p.string = translated_text
            # a translated copy is a second rendering, not a second anchor
            strip_duplicate_ids(new_p)
            if translation_style != "":
                new_p["style"] = translation_style
            p.insert_after(new_p)

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
        translated_text_list = self._translate_texts_aligned(new_texts)

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

        result_txt_list = self.translate_model.translate_list(
            [p.text for p in wait_p_list]
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
            if item.file_name != fixname:
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
        epub.write_epub(f"{name_fix}", new_book, {})

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
    def _plan_batch_indexes(units):
        """Batch identity for plan units: one window per poetry group.

        Grouped units are contiguous by construction, so numbering them in
        first-seen order keeps batch indexes monotonic — the same property
        _assign_batch_indexes gives tag mode.
        """
        indexes = []
        by_group = {}
        next_free = 0
        for unit in units:
            if unit.group_id is None:
                indexes.append(next_free)
                next_free += 1
                continue
            if unit.group_id not in by_group:
                by_group[unit.group_id] = next_free
                next_free += 1
            indexes.append(by_group[unit.group_id])
        return indexes

    def _build_translation_plan(self, document_items, trans_taglist):
        """Enumerate all EPUB work before requests begin.

        Both execution modes consume these plans. File/tag filtering, test
        limits, document order, node order and batch identity therefore no
        longer depend on which worker happens to run first.
        """
        plans = []
        global_index = 0

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
                batch_indexes = self._plan_batch_indexes(units)
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

                if self.is_test and index >= self.test_num:
                    is_test_done = True
                    break

            # Process remaining paragraphs in the batch
            if self.block_size >= 1 and len(p_block) > 0:
                index, n = self._process_combined_paragraph(
                    p_block, index, p_to_save_len, thread_safe=False
                )
                pbar.update(n)

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
                            [job.source_text for job in fresh], plan_translator
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
        in reading order and worth having.
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
                    result_txt_list = self.translator.translate_list(
                        [p.text for p in wait_p_list]
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
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
        )

        # Check for fatal errors before starting
        if self.translate_model._fatal_error_detected:
            print(
                "[bold red]Fatal translation error detected. Aborting book creation.[/bold red]"
            )
            return

        if self._plan_mode:
            incompatible = {
                "--retranslate": self.retranslate,
                "--batch/--batch-use": self.batch_flag or self.batch_use_flag,
                "--sentence_mode": self.sentence_mode,
            }
            active = [flag for flag, on in incompatible.items() if on]
            if active:
                print(
                    f"[bold red]plan mode (--plan-classify) is not compatible "
                    f"with {', '.join(active)}[/bold red]"
                )
                raise SystemExit(1)
            if self.allow_navigable_strings:
                print(
                    "note: --allow_navigable_strings is redundant in plan mode "
                    "(every text node is already accounted for); ignoring it"
                )
            if self.accumulated_num > 1:
                print(
                    "note: plan mode batches poetry windows itself; "
                    "--accumulated_num is ignored"
                )
            self._prepare_translation_plan()

        self.batch_init_then_wait()
        new_book = self._make_new_book(self.origin_book)
        trans_taglist = self.translate_tags.split(",")
        document_items = list(self.origin_book.get_items_of_type(ITEM_DOCUMENT))
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
                            )

                        except Exception as e:
                            print(f"❌ Error processing {item.file_name}: {e}")
                            failed_chapters.append((item.file_name, str(e)))
                            new_book.add_item(item)
                            chapter_pbar.update(1)

                chapter_pbar.close()
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

                if self.accumulated_num > 1:
                    name, _ = os.path.splitext(self.epub_name)
                    epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            name, _ = os.path.splitext(self.epub_name)
            if self.batch_flag:
                self.translate_model.batch()
            else:
                epub.write_epub(f"{name}_bilingual.epub", new_book, {})
        except KeyboardInterrupt as e:
            print(e)
            if self.accumulated_num == 1:
                print("you can resume it next time")
                self._save_progress()
                self._save_temp_book()
            sys.exit(0)
        except Exception as e:
            # Handle connection errors gracefully
            error_msg = str(e)
            if "Connection" in error_msg or "connection" in error_msg:
                print(
                    f"[bold red]Translation failed: Connection error - {error_msg}[/bold red]"
                )
                print("Please check your network connection or API server status.")
            else:
                traceback.print_exc()
            if self.accumulated_num == 1:
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
        except ValueError:
            raise
        except Exception:
            raise Exception("can not load resume file")

    def _save_temp_book(self):
        name, _ = os.path.splitext(self.epub_name)
        temp_path = f"{name}_bilingual_temp.epub"
        origin_book_temp = epub.read_epub(self.epub_name)
        new_temp_book = self._make_new_book(origin_book_temp)
        trans_taglist = self.translate_tags.split(",")
        document_items = list(origin_book_temp.get_items_of_type(ITEM_DOCUMENT))
        chapter_plans = iter(
            self._build_translation_plan(document_items, trans_taglist)
        )
        try:
            for item in origin_book_temp.get_items():
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
            epub.write_epub(temp_path, new_temp_book, {})
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
