"""Corpus gate — every book in ./epub-sample must partition and classify.

Tier 1 of the acceptance gate: deterministic, offline, no API. It runs the
whole partition over all 45 IDPF `epub3-samples` books and asserts the
properties a design tuned on hand-picked books cannot be trusted to have —
that every character is accounted for, that every run can actually be
written back where it came from, that the ledger round-trips, and that a
translated book is still a book.

Run it explicitly:  pytest -m corpus tests/test_corpus_epub_samples.py

Tiers 2 (live classification of all 45 books, with the model and request
config recorded) and 3 (agent review of the verdicts) are separate: they
cost money and judgment, and neither belongs in a unit-test run.
"""

import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from bs4 import BeautifulSoup as bs
from ebooklib import ITEM_DOCUMENT, epub

from book_maker.loader.ledger import Ledger
from book_maker.loader.plan import build_plan, file_segment_hazards, file_sha256

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "epub-sample"
AUDIT_PATH = REPO / "output" / "corpus-partition-audit.json"

pytestmark = pytest.mark.corpus

BOOKS = sorted(CORPUS.glob("*.epub")) if CORPUS.exists() else []

# Round-trip rendering runs on the worst case of each kind we know about,
# not on all 45: writing every book is minutes of IO for a property three
# books already prove.
ROUND_TRIP_RUN_CAP = 400

ROUND_TRIP_BOOKS = [
    "accessible_epub_3.epub",  # the nav-preservation case
    "mahabharata.epub",  # verse directly under <body>
    "jlreq-in-english.epub",  # table-heavy, vertical writing
    "childrens-literature.epub",  # rich OPF metadata, clean source
    "wasteland.epub",  # non-default package prefix (cc:) used by a <meta
    # property> — the OPF-028 class 20 of the 45 books could show. The one
    # other prefix-bearing book here (mahabharata) only uses cc: via a
    # <link rel> a separate rule drops, so without this book the gate
    # cannot see the deliberate prefix drop at all.
    "kusamakura-japanese-vertical-writing.epub",  # NCX in a subdirectory
    # (xhtml/toc.ncx) — the regenerated NCX must rebase its srcs to that
    # location or every EPUB 2 TOC link is dead (RSC-007); also a second
    # prefix name (foaf:) for the OPF-028 class.
]

# Findings the package rewrite is *known* to introduce, pinned exactly, per
# book. These are the deferred metadata/manifest rewrite (PR 1b): ebooklib
# emits its own <dc:identifier id="id"> next to the copied one, drops
# "scripted" from properties, and writes <content src=""/> into a
# regenerated NCX. Pinned with equality, not tolerance: one finding more
# than the pin is a regression and fails; one finding fewer means PR 1b
# landed and this pin must shrink with it — the gate goes red until it does.
KNOWN_PACKAGE_FINDINGS = {
    "childrens-literature.epub": Counter(
        {
            "OPF-014: The property "
            '"scripted" should be declared in the OPF file.': 1,
        }
    ),
    # The deliberate package-prefix drop (see
    # docs/260813-fix-PR554_PACKAGE_DOCUMENT_AND_INLINE_BR.md) orphans the
    # cc: property this book's metadata uses. Restoring prefix preservation
    # makes this pin stale and the gate red — by design.
    "wasteland.epub": Counter(
        {
            'OPF-028: Undeclared prefix: "cc".': 1,
        }
    ),
    # same prefix drop, different prefix: six foaf: meta properties
    "kusamakura-japanese-vertical-writing.epub": Counter(
        {
            'OPF-028: Undeclared prefix: "foaf".': 6,
        }
    ),
}


def _require_corpus():
    if not BOOKS:
        pytest.fail(
            f"the acceptance corpus is missing: expected 45 EPUBs in {CORPUS}. "
            f"Download the IDPF epub3-samples 20230704 release into it."
        )


@pytest.fixture(scope="module")
def plans():
    _require_corpus()
    built = {}
    for path in BOOKS:
        book = epub.read_epub(str(path))
        built[path.name] = build_plan(book)
    return built


class TestCorpusPartition:
    def test_the_corpus_is_complete(self):
        _require_corpus()
        assert len(BOOKS) == 45, f"expected 45 sample books, found {len(BOOKS)}"

    def test_every_character_is_accounted_for(self, plans):
        for name, plan in plans.items():
            for fp in plan.files:
                assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
                    fp.skipped.values()
                ), f"{name}/{fp.file_name} loses characters"

    def test_no_unit_is_unplaceable(self, plans):
        """Every run must be writable back where its text came from."""
        offenders = []
        for name, plan in plans.items():
            for fp in plan.files:
                for unit, hazards in file_segment_hazards(fp):
                    offenders.append(
                        f"{name}/{fp.file_name} {unit.signature} {hazards} "
                        f"{unit.text[:60]!r}"
                    )
        assert not offenders, "unplaceable units:\n" + "\n".join(offenders[:20])

    def test_units_ascend_in_document_order(self, plans):
        """Positional consumers — checkpoints, windows, --test — all mean
        document order, so the unit list has to be in it."""
        for name, plan in plans.items():
            for fp in plan.files:
                if len(fp.units) < 2:
                    continue
                positions = []
                seen = {}
                for index, node in enumerate(n for n in _all_strings(fp) if True):
                    seen[id(node)] = index
                for unit in fp.units:
                    positions.append(seen.get(id(unit.nodes[0]), -1))
                assert positions == sorted(
                    positions
                ), f"{name}/{fp.file_name} emits runs out of document order"
                assert [u.ordinal for u in fp.units] == list(range(len(fp.units)))

    def test_body_owned_text_is_line_sized_and_never_cloned(self, plans):
        """Books that hang verse straight off <body> (mahabharata) once
        produced chapter-sized units of ~20k characters with nowhere to put
        a translation, and a bilingual run cloned <body> after <body>.

        Body-owned *runs* are fine — one line each, anchored after their own
        markup. What must never happen is a clone.
        """
        from book_maker.loader.plan import is_simple_owner

        for name, plan in plans.items():
            for fp in plan.files:
                for unit in fp.units:
                    if unit.element.name not in ("body", "html"):
                        continue
                    assert not is_simple_owner(
                        unit.element, fp.resolver
                    ), f"{name}/{fp.file_name} would clone <{unit.element.name}>"
                    assert unit.chars < 5000, (
                        f"{name}/{fp.file_name} has a {unit.chars}-char "
                        f"body-owned run — that is a chapter, not a line"
                    )

    def test_every_signature_appears_exactly_once_in_the_ledger(self, plans):
        for name, plan in plans.items():
            ledger = plan.build_ledger()
            keys = list(ledger.rows)
            assert len(keys) == len(set(keys)), f"{name} has duplicate ledger keys"
            unit_keys = {u.key for fp in plan.files for u in fp.all_units}
            assert unit_keys <= set(keys), f"{name} has units with no ledger row"

    def test_the_ledger_round_trips(self, plans, tmp_path):
        for name, plan in plans.items():
            ledger = plan.build_ledger()
            for key in ledger.undecided_keys():
                ledger.decide(key, "translate", "agent", "prose")
            meta = {"book_sha256": file_sha256(CORPUS / name)}
            path = tmp_path / f"{name}.json"
            ledger.save(path, meta)
            first = path.read_text()
            again = Ledger.load(path, expected_sha256=meta["book_sha256"])
            again.save(path, meta)
            assert path.read_text() == first, f"{name} does not round-trip"

    # Books whose text is deliberately *not* translatable prose. Pinned so a
    # change that starts translating their internals — SVG shape text,
    # Braille cell codes — fails here instead of shipping nonsense into a
    # book. The default --plan-min-coverage of 0.5 refuses all of them at
    # runtime, which is the loud behaviour we want.
    EXPECTED_LOW_COVERAGE = {
        # every glyph lives inside <svg>; "translating" it breaks the markup
        "sous-le-vent.epub": (0.0, 0.05),
        # bitmap pages in the spine: there is no text to translate
        "page-blanche.epub": (0.75, 0.90),
        "page-blanche-bitmaps-in-spine.epub": (0.75, 0.90),
        # Unicode Braille patterns are symbols, not letters
        "WCAG.epub": (0.0, 0.01),
    }

    def test_non_prose_books_stay_uncovered(self, plans):
        for name, (low, high) in self.EXPECTED_LOW_COVERAGE.items():
            if name not in plans:
                continue
            coverage = plans[name].coverage
            assert low <= coverage <= high, (
                f"{name} coverage moved to {coverage:.3f}, expected "
                f"{low}-{high}. Either the partition regressed, or this book "
                f"genuinely became translatable — decide which, then update "
                f"the pin."
            )

    def test_prose_books_are_substantially_covered(self, plans):
        """The complement: a narrative book that suddenly stops being
        covered is the silent-loss failure this whole gate exists for."""
        for name, plan in plans.items():
            if name in self.EXPECTED_LOW_COVERAGE:
                continue
            assert plan.coverage >= 0.5, (
                f"{name} covers only {100 * plan.coverage:.1f}% of its text; "
                f"it is not in the known non-prose list, so this is loss"
            )

    def test_partition_audit_is_written(self, plans):
        """The review surface: how much each book charges to symbol/hidden.

        Not an assertion — a number that changes silently is what a corpus
        gate exists to make visible.
        """
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        audit = {}
        for name, plan in plans.items():
            skipped = plan.skipped_totals
            ledger = plan.build_ledger()
            audit[name] = {
                "total_chars": plan.total_chars,
                "units": sum(len(f.units) for f in plan.files),
                "coverage": round(plan.coverage, 4),
                "rows": len(ledger.rows),
                "inline_rows": sum(
                    1 for r in ledger.rows.values() if r["scope"] == "inline"
                ),
                "skipped": dict(skipped),
            }
        AUDIT_PATH.write_text(json.dumps(audit, indent=1, sort_keys=True))
        assert AUDIT_PATH.exists()


def _all_strings(fp):
    """Document-order text nodes of the file the plan was built from."""
    seen = []
    if not fp.units:
        return seen
    root = fp.units[0].element
    while root.parent is not None:
        root = root.parent
    from book_maker.loader.plan import TEXT_NODE_TYPES

    return [n for n in root.descendants if type(n) in TEXT_NODE_TYPES]


# ------------------------------------------------------------- round trip


class MarkerModel:
    """A translator that returns a unique marker per source string.

    Unique per segment, deliberately: with one shared marker a duplicated
    insertion and a missing one cancel out in a count, and the gate would
    pass on a book it mangled.
    """

    is_test = False
    TRANSLATION_ERROR_MARKER = None

    def __init__(self, key="", language="zh-hans", **kwargs):
        self._fatal_error_detected = False
        self.seen = []

    def rotate_key(self):
        pass

    def set_deployment_id(self, *a, **k):
        pass

    def translate(self, text):
        self.seen.append(text)
        return f"[[T{len(self.seen) - 1}]]{text}"

    def translate_list(self, texts):
        return [self.translate(t) for t in texts]


def _load_plan_answers(loader, action="translate"):
    from book_maker.loader.ledger import Ledger as _L

    plan_path = Path(loader.epub_name).with_name(
        Path(loader.epub_name).stem + "_plan.json"
    )
    data = json.loads(plan_path.read_text())
    for row in data["signatures"]:
        row["action"] = action
        row["decided_by"] = "agent"
        row["content_type"] = "prose"
    plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return plan_path


@pytest.mark.parametrize("book_name", ROUND_TRIP_BOOKS)
@pytest.mark.parametrize("single", [False, True], ids=["bilingual", "single"])
def test_round_trip_produces_a_valid_book(book_name, single, tmp_path):
    from book_maker.loader.epub_loader import EPUBBookLoader

    source = CORPUS / book_name
    if not source.exists():
        pytest.fail(f"{book_name} missing from {CORPUS}")
    work = tmp_path / book_name
    shutil.copy(source, work)

    loader = EPUBBookLoader(
        str(work), MarkerModel, "k", resume=False, language="zh-hans"
    )
    loader.plan_mode = True
    loader.translate_tags = "auto"
    loader.single_translate = single
    loader.plan_classify = "agent"
    with pytest.raises(SystemExit) as stop:
        loader.make_bilingual_book()
    assert stop.value.code == 0
    _load_plan_answers(loader)

    runner = EPUBBookLoader(
        str(work), MarkerModel, "k", resume=False, language="zh-hans"
    )
    runner.plan_mode = True
    runner.translate_tags = "auto"
    runner.single_translate = single
    runner.plan_classify = "agent"
    # A cap, not a shortcut: what this asserts is *structural* — one <body>,
    # markers where their runs were, a book epubcheck accepts. Those hold on
    # the first few hundred runs exactly as on all 158,000 of mahabharata's,
    # and translating every one of them costs minutes per book for no extra
    # evidence. `log()` the cap rather than let it read as full coverage.
    runner.is_test = True
    runner.test_num = ROUND_TRIP_RUN_CAP
    print(f"round trip: translating the first {ROUND_TRIP_RUN_CAP} run(s)")
    runner.make_bilingual_book()

    out = work.with_name(work.stem + "_bilingual.epub")
    assert out.exists(), f"{book_name} produced no output"

    with zipfile.ZipFile(out) as z:
        docs = [n for n in z.namelist() if n.endswith((".xhtml", ".html"))]
        for name in docs:
            soup = bs(z.read(name), "html.parser")
            assert (
                len(soup.find_all("body")) <= 1
            ), f"{book_name}/{name} has more than one <body>"
        # The EPUB 2 fallback TOC must point at files that exist. Checked
        # structurally, not left to the epubcheck diff: the NCX is
        # regenerated with OPF-root-relative srcs, so a book that keeps its
        # NCX in a subdirectory needs them rebased (kusamakura), and a
        # source book with its own broken links must not absorb ours.
        for name in z.namelist():
            if not name.endswith(".ncx"):
                continue
            base = posixpath.dirname(name)
            for src in re.findall(r'src="([^"#]*)', z.read(name).decode("utf-8")):
                if not src or "://" in src or src.startswith("/"):
                    continue
                target = posixpath.normpath(posixpath.join(base, src))
                assert target in z.namelist(), (
                    f"{book_name}/{name} NCX links to {src!r} -> {target}, "
                    f"which is not in the book"
                )

    _assert_epubcheck_clean(source, out, book_name)


# ------------------------------------------------------------- epubcheck

EPUBCHECK_HOME = "https://github.com/w3c/epubcheck/releases"


def _epubcheck_jar():
    """Whatever epubcheck this machine has, or a failure explaining how to
    get one.

    Deliberately *not* version-pinned: any epubcheck new enough to know
    EPUB 3 answers the only question this gate asks — did translating the
    book introduce a finding its source did not already have — and that
    comparison is per book against its own source, so a checker that
    reports more (or fewer) findings than another version cancels out on
    both sides.

    Presence is still mandatory. The main risk this gate covers is
    emitting XHTML no reading system will open, and "epubcheck was
    unavailable so we passed" is the report of a gate that is not one.

    Override with EPUBCHECK_JAR to point at a specific build.
    """
    override = os.environ.get("EPUBCHECK_JAR")
    if override:
        jar = Path(override)
        if not jar.exists():
            pytest.fail(f"EPUBCHECK_JAR={override} does not exist")
        return jar
    found = sorted((REPO / "tools").glob("epubcheck*/epubcheck.jar"))
    if not found:
        pytest.fail(
            f"epubcheck is required by this gate and was not found under "
            f"{REPO / 'tools'}. Download a release from {EPUBCHECK_HOME}, "
            f"unzip it there (any version that validates EPUB 3), or set "
            f"EPUBCHECK_JAR to a jar elsewhere."
        )
    return found[-1]


class EpubcheckFailed(Exception):
    """epubcheck did not deliver a verdict — which is not the same as a
    clean book, and must never be reported as one."""


REPORTED_SEVERITIES = frozenset(["FATAL", "ERROR", "WARNING"])


def _epubcheck_findings(jar, path):
    """``Counter({"CODE: message": occurrences})`` for one book, or a loud
    failure.

    Counted, not a set. Findings are compared by subtraction, and set
    subtraction answers "does this *kind* of finding already occur?" when the
    question is "does the output carry more of them?" — a book whose source
    has one RSC-005 would absorb a hundred introduced by translation.

    Read from ``--json``, not scraped from stdout. The scraper this replaces
    treated any capitalized line as a finding, which inverted the gate in the
    case that matters most: a checker that cannot start (wrong Java major
    version, corrupt jar) prints a stack trace, contributes an *equal* junk
    set for source and output, and the subtraction comes out empty — a
    required gate reporting a pass because it never ran.

    Three distinct outcomes, kept distinct:

    - a report with findings  -> the counts (this is the only comparable one)
    - a report with none      -> an empty Counter, but only when the checker
                                 actually examined a publication
    - no usable report        -> ``EpubcheckFailed``
    """
    result = subprocess.run(
        [
            "java",
            "-jar",
            str(jar),
            "--quiet",
            "--failonwarnings",
            "--json",
            "-",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        raise EpubcheckFailed(
            f"epubcheck produced no JSON report for {path} (exit "
            f"{result.returncode}). The checker itself failed — a corrupt "
            f"jar, the wrong Java, or a crash:\n"
            f"{(result.stderr or result.stdout)[:500]}"
        )
    checker = report.get("checker") or {}
    if not checker.get("checkerVersion"):
        raise EpubcheckFailed(f"epubcheck report for {path} has no checker block")

    messages = report.get("messages")
    if not isinstance(messages, list):
        raise EpubcheckFailed(f"epubcheck report for {path} has no messages array")

    # A missing or unreadable input yields a *valid* report with no messages
    # and no items — indistinguishable from a clean book by message count
    # alone, which is exactly how a typo'd path could have passed the gate.
    if not messages and not (report.get("items") or []):
        raise EpubcheckFailed(
            f"epubcheck examined nothing in {path} (exit {result.returncode}, "
            f"no messages, no items) — the file is missing or unreadable"
        )
    if result.returncode != 0 and not messages:
        raise EpubcheckFailed(
            f"epubcheck exited {result.returncode} for {path} with nothing to "
            f"report — treat as a checker failure, not a clean book"
        )

    findings = Counter()
    for message in messages:
        if not isinstance(message, dict):
            raise EpubcheckFailed(
                f"epubcheck report for {path} has a malformed message"
            )
        if message.get("severity") not in REPORTED_SEVERITIES:
            continue
        # ID + text only. Locations carry line numbers that translation
        # legitimately moves, and the file path differs between the source
        # and the output copy — but *how many* there are is comparable, and
        # epubcheck reports one message per (ID, text) with every occurrence
        # in `locations` (plus `additionalLocations` for the ones it capped).
        locations = message.get("locations")
        occurrences = len(locations) if isinstance(locations, list) else 1
        occurrences += int(message.get("additionalLocations") or 0)
        findings[f'{message.get("ID")}: {message.get("message")}'] += max(
            1, occurrences
        )
    return findings


def _assert_epubcheck_clean(source, output, book_name):
    """The output may carry no finding its source did not already carry,
    except the exact pinned set in KNOWN_PACKAGE_FINDINGS.

    Several IDPF samples are not finding-free themselves, so the baseline is
    per book rather than absolute. The pin is compared with equality in both
    directions: findings beyond it are a regression, findings missing from
    it mean the defect was fixed and the pin is stale — both fail loudly.
    """
    if shutil.which("java") is None:
        pytest.fail("java is required for the corpus gate (Temurin is installed)")
    jar = _epubcheck_jar()
    try:
        baseline = _epubcheck_findings(jar, source)
        produced = _epubcheck_findings(jar, output)
    except EpubcheckFailed as e:
        pytest.fail(f"{book_name}: {e}")
    # Counter subtraction keeps only what grew: one pre-existing RSC-005 in
    # the source no longer absorbs a hundred in the output.
    new = produced - baseline
    known = KNOWN_PACKAGE_FINDINGS.get(book_name, Counter())
    beyond = new - known
    assert (
        not beyond
    ), f"{book_name}: translation introduced EPUB findings:\n" + "\n".join(
        f"{count}x {finding} (source had {baseline[finding]}, "
        f"pinned {known[finding]})"
        for finding, count in sorted(beyond.items())[:20]
    )
    stale = known - new
    assert not stale, (
        f"{book_name}: pinned package findings no longer occur — the fix "
        f"landed, so shrink or delete its KNOWN_PACKAGE_FINDINGS entry:\n"
        + "\n".join(f"{count}x {finding}" for finding, count in sorted(stale.items()))
    )


class TestEpubcheckWrapper:
    """The gate's instrument, tested like one.

    Every case here is a way epubcheck can fail to deliver a verdict. The
    scraper this replaced turned three of them into an empty finding set,
    which subtracts to "no new findings" for source and output alike — the
    required gate passing precisely because it never ran.
    """

    def _jar(self):
        if shutil.which("java") is None:
            pytest.skip("java not available")
        return _epubcheck_jar()

    def test_a_clean_book_reports_no_findings(self):
        book = CORPUS / "moby-dick.epub"
        if not book.exists():
            pytest.skip("corpus not present")
        assert _epubcheck_findings(self._jar(), book) == Counter()

    def test_an_invalid_book_reports_its_findings(self, tmp_path):
        """A book with real problems is the one case that must come back as
        data rather than an exception."""
        book = CORPUS / "moby-dick.epub"
        if not book.exists():
            pytest.skip("corpus not present")
        broken = tmp_path / "broken.epub"
        with zipfile.ZipFile(book) as src, zipfile.ZipFile(broken, "w") as dst:
            for item in src.namelist():
                data = src.read(item)
                if item.endswith(".opf"):
                    data = data.replace(b"</metadata>", b"<meta/></metadata>")
                dst.writestr(item, data)
        findings = _epubcheck_findings(self._jar(), broken)
        assert findings, "a damaged OPF must produce findings"
        assert all(":" in f for f in findings)

    def test_a_missing_file_is_a_checker_failure_not_a_clean_book(self, tmp_path):
        """epubcheck answers a missing path with a *valid* JSON report that
        has no messages and no items — byte-identical in shape to a clean
        book. This is the case that could pass the gate silently."""
        with pytest.raises(EpubcheckFailed, match="missing or unreadable"):
            _epubcheck_findings(self._jar(), tmp_path / "nope.epub")

    def test_a_corrupt_jar_is_a_checker_failure(self, tmp_path):
        if shutil.which("java") is None:
            pytest.skip("java not available")
        bad = tmp_path / "bad.jar"
        bad.write_text("this is not a jar")
        with pytest.raises(EpubcheckFailed, match="no JSON report"):
            _epubcheck_findings(bad, CORPUS / "moby-dick.epub")

    def test_a_crashing_checker_is_a_checker_failure(self, tmp_path, monkeypatch):
        """The wrong Java major version prints UnsupportedClassVersionError
        and nothing else. Lowercase-first stack-trace lines used to parse as
        zero findings for source *and* output, so the subtraction was empty
        and the gate reported a pass."""

        class Crashed:
            returncode = 1
            stdout = ""
            stderr = (
                "Error: LinkageError occurred while loading main class\n"
                "\tjava.lang.UnsupportedClassVersionError: has been compiled "
                "by a more recent version of the Java Runtime\n"
            )

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Crashed())
        with pytest.raises(EpubcheckFailed, match="no JSON report"):
            _epubcheck_findings(Path("epubcheck.jar"), Path("book.epub"))

    def test_a_report_without_a_checker_block_is_refused(self, monkeypatch):
        class Truncated:
            returncode = 0
            stdout = '{"messages": []}'
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Truncated())
        with pytest.raises(EpubcheckFailed, match="no checker block"):
            _epubcheck_findings(Path("epubcheck.jar"), Path("book.epub"))

    def test_a_nonzero_exit_with_nothing_to_report_is_refused(self, monkeypatch):
        class Empty:
            returncode = 1
            stdout = json.dumps(
                {
                    "checker": {"checkerVersion": "5.3.0"},
                    "messages": [],
                    "items": [{"id": "x"}],
                }
            )
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Empty())
        with pytest.raises(EpubcheckFailed, match="nothing to"):
            _epubcheck_findings(Path("epubcheck.jar"), Path("book.epub"))

    def test_usage_severity_is_not_a_finding(self, monkeypatch):
        class Usage:
            returncode = 0
            stdout = json.dumps(
                {
                    "checker": {"checkerVersion": "5.3.0"},
                    "messages": [
                        {"ID": "ACC-001", "severity": "USAGE", "message": "hint"},
                        {"ID": "RSC-005", "severity": "ERROR", "message": "real"},
                    ],
                    "items": [{"id": "x"}],
                }
            )
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Usage())
        assert _epubcheck_findings(Path("j.jar"), Path("b.epub")) == Counter(
            {"RSC-005: real": 1}
        )

    def test_a_pinned_finding_passes_and_one_beyond_the_pin_fails(
        self, monkeypatch, tmp_path
    ):
        """The pin admits exactly the recorded PR 1b findings — the very
        next duplicate id on top of them must still fail the gate."""
        mod = sys.modules[__name__]
        pinned = Counter({"RSC-005: dup": 2})
        monkeypatch.setitem(KNOWN_PACKAGE_FINDINGS, "pinned.epub", pinned)
        reports = iter([Counter(), Counter({"RSC-005: dup": 2})])
        monkeypatch.setattr(mod, "_epubcheck_findings", lambda jar, path: next(reports))
        monkeypatch.setattr(mod, "_epubcheck_jar", lambda: Path("j.jar"))
        _assert_epubcheck_clean(tmp_path / "s", tmp_path / "o", "pinned.epub")

        reports = iter([Counter(), Counter({"RSC-005: dup": 3})])
        with pytest.raises(AssertionError, match="introduced EPUB findings"):
            _assert_epubcheck_clean(tmp_path / "s", tmp_path / "o", "pinned.epub")

    def test_a_stale_pin_fails_instead_of_lingering(self, monkeypatch, tmp_path):
        """When PR 1b fixes the package writer, the pin must go red until
        it is deleted — a tolerance that outlives its defect is a hole."""
        mod = sys.modules[__name__]
        monkeypatch.setitem(
            KNOWN_PACKAGE_FINDINGS, "pinned.epub", Counter({"RSC-005: dup": 2})
        )
        reports = iter([Counter(), Counter()])
        monkeypatch.setattr(mod, "_epubcheck_findings", lambda jar, path: next(reports))
        monkeypatch.setattr(mod, "_epubcheck_jar", lambda: Path("j.jar"))
        with pytest.raises(AssertionError, match="pin"):
            _assert_epubcheck_clean(tmp_path / "s", tmp_path / "o", "pinned.epub")

    def test_repeated_occurrences_of_one_finding_are_counted(self, monkeypatch):
        """The gate compares by subtraction. With sets, a source carrying one
        RSC-005 absorbed any number of them in the output — the regression
        this gate exists to catch, invisible to it."""

        class Repeated:
            returncode = 1
            stdout = json.dumps(
                {
                    "checker": {"checkerVersion": "5.3.0"},
                    "messages": [
                        {
                            "ID": "RSC-005",
                            "severity": "ERROR",
                            "message": "bad",
                            "locations": [{"path": "a.xhtml"}, {"path": "b.xhtml"}],
                            "additionalLocations": 98,
                        }
                    ],
                    "items": [{"id": "x"}],
                }
            )
            stderr = ""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: Repeated())
        produced = _epubcheck_findings(Path("j.jar"), Path("b.epub"))
        assert produced == Counter({"RSC-005: bad": 100})
        baseline = Counter({"RSC-005: bad": 1})
        assert produced - baseline == Counter({"RSC-005: bad": 99})
