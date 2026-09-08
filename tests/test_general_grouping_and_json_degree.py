"""Token-budget general grouping + the json_object capability degree.

The seven contracts from
``docs/260904-plan-GENERAL_GROUPING_AND_JSON_OBJECT_DEGREE.md`` (Stage 2),
plus the four amendments its "Stage 1 results" section pinned after the
260905 off-OpenAI eval:

1. the batch json-degree parse takes ``extract_json_object`` *plus* a
   required-top-key check, and the classifier's rungs stop failing open;
2. sub-strict degrees carry at most half the strict unit cap per request;
3. the plan gate admits ``strict``/``shape``/``json`` and nothing else;
4. ``ENTRY_RUNG`` has no ``unsupported`` key on purpose — the prompt rung is
   the default, and that is a decision, not an accident.
"""

import json
import shutil
import threading
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup as bs

from book_maker.cli import resolve_plan_mode
from book_maker.loader.plan import (
    GENERAL_GROUP_MAX_UNITS,
    SUBSTRICT_GROUP_MAX_UNITS,
    DisplayResolver,
    assign_batches,
    partition_soup,
)
from book_maker.structured import extract_json_object, schema_required_keys
from book_maker.translator.base_translator import BatchMismatch
from book_maker.translator.capabilities import ENTRY_RUNG, CapabilityLedger
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
)

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANIMAL_FARM = REPO / "test_books" / "animal_farm.epub"

LANGUAGE = "Chinese"
BATCH_FIELD = batch_field_name(LANGUAGE)
SINGLE_FIELD = single_field_name(LANGUAGE)


# --------------------------------------------------------------- fixtures


def _units(body_html):
    soup = bs(f"<html><body>{body_html}</body></html>", "html.parser")
    return partition_soup(soup, DisplayResolver([]), "chap.xhtml").units


def _paragraph(words, tag="p"):
    return f"<{tag}>{' '.join(['word'] * words)}.</{tag}>"


def _completion(content, finish_reason="stop"):
    message = SimpleNamespace(content=content, refusal=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _translator(create=None, parse=None):
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "test-model"
    translator.model_list = None
    translator.keys = cycle(["k"])
    translator.temperature = 1.0
    translator.extra_body = {}
    translator.context_flag = False
    translator.context_list = []
    translator.context_translated_list = []
    translator.context_paragraph_limit = 0
    translator.system_content = ""
    translator.prompt_sys_msg = ""
    translator.prompt_template = ChatGPTAPI.DEFAULT_PROMPT
    translator.language = LANGUAGE
    translator.source_language = None
    translator._api_lock = threading.Lock()
    translator.capabilities = CapabilityLedger()
    translator._rung_refusals = {}
    translator.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create or Mock(return_value=_completion("plain")),
                parse=parse or Mock(),
            ),
        )
    )
    return translator


def _reply(texts, ids=None, field=BATCH_FIELD, item_field=SINGLE_FIELD):
    ids = range(len(texts)) if ids is None else ids
    return json.dumps(
        {field: [{"id": i, item_field: t} for i, t in zip(ids, texts)]},
        ensure_ascii=False,
    )


# ------------------------------------ 1. assign_batches with a token budget


class TestTokenBudgetGrouping:
    def test_mixed_lengths_pack_to_the_budget(self):
        # every unit is far too long for the short-run rule (>= 70 chars),
        # which is the whole point: a budget groups *any* consecutive units
        units = _units("".join(_paragraph(30) for _ in range(12)))
        assert all(u.chars >= 70 for u in units)

        assign_batches(units, token_budget=200)

        groups = {}
        for unit in units:
            assert unit.group_id is not None, "long units must group under a budget"
            groups.setdefault(unit.group_id, []).append(unit)
        assert len(groups) > 1
        for members in groups.values():
            assert len(members) <= GENERAL_GROUP_MAX_UNITS
            assert sum(u.token_count for u in members) <= 200

    def test_the_unit_cap_bounds_a_generous_budget(self):
        units = _units("".join(_paragraph(20) for _ in range(40)))

        assign_batches(units, token_budget=10**6)

        sizes = {}
        for unit in units:
            sizes[unit.group_id] = sizes.get(unit.group_id, 0) + 1
        assert max(sizes.values()) == GENERAL_GROUP_MAX_UNITS

    def test_a_unit_alone_over_budget_stays_solo(self):
        units = _units(
            _paragraph(4) * 2 + _paragraph(400) + _paragraph(4) * 2,
        )
        assert len(units) == 5

        assign_batches(units, token_budget=60)

        assert units[2].group_id is None
        # and it ends the run around it rather than joining either side
        assert units[0].group_id == units[1].group_id is not None
        assert units[3].group_id == units[4].group_id is not None
        assert units[0].group_id != units[3].group_id

    def test_no_budget_is_byte_identical_to_the_short_run_grouping(self):
        html = (
            "<p>Short one</p><p>Short two</p>"
            + _paragraph(60)
            + "<h3>Short three</h3><p>Short four</p><p>Short five</p>"
        )
        with_budget_none = _units(html)
        assign_batches(with_budget_none, token_budget=None)
        default = _units(html)
        assign_batches(default)

        assert [u.group_id for u in with_budget_none] == [u.group_id for u in default]
        # the long paragraph is still solo without a budget
        assert default[2].group_id is None

    def test_deterministic(self):
        html = "".join(_paragraph(n) for n in (5, 40, 7, 90, 12))
        a, b = _units(html), _units(html)

        assign_batches(a, token_budget=300)
        assign_batches(b, token_budget=300)

        assert [u.group_id for u in a] == [u.group_id for u in b]

    def test_the_token_count_is_cached_on_the_unit(self):
        units = _units("".join(_paragraph(10) for _ in range(4)))
        assert all(u.token_count is None for u in units)

        assign_batches(units, token_budget=500)

        assert all(isinstance(u.token_count, int) for u in units)

    def test_a_lower_unit_cap_bounds_the_groups(self):
        # --max-batch-units: the ceiling came from one model's eval, so it is the
        # default and not a law. A generous budget makes the cap the only
        # thing bounding a group, which is where it has to be visible.
        units = _units("".join(_paragraph(20) for _ in range(40)))

        assign_batches(units, token_budget=10**6, max_units=4)

        sizes = {}
        for unit in units:
            sizes[unit.group_id] = sizes.get(unit.group_id, 0) + 1
        assert max(sizes.values()) == 4

    def test_the_unit_cap_does_not_reach_the_no_budget_path(self):
        # the short-run grouping is bounded by group_size and GROUP_MAX_CHARS;
        # --max-batch-units is a budget-path bound and must not silently re-cut it
        html = "<p>a</p><p>b</p><p>c</p><p>d</p><p>e</p><p>f</p>"
        capped, plain = _units(html), _units(html)

        assign_batches(capped, max_units=2)
        assign_batches(plain)

        assert [u.group_id for u in capped] == [u.group_id for u in plain]

    def test_the_count_does_not_depend_on_a_model_name(self, monkeypatch):
        # grouping is a property of the book: pinned to cl100k_base, so the
        # same book partitions into the same requests on every endpoint
        import book_maker.loader.plan as plan_mod

        seen = []
        real = plan_mod.num_tokens_from_text

        def spy(text, *args, **kwargs):
            seen.append((args, kwargs))
            return real(text)

        monkeypatch.setattr(plan_mod, "num_tokens_from_text", spy)
        assign_batches(
            _units("".join(_paragraph(9) for _ in range(3))), token_budget=99
        )

        assert seen and all(a == () and k == {} for a, k in seen)


# -------------------------------------------- 2. the loader honors the flag


def _plan_loader(tmp_path, model_cls, **attrs):
    from book_maker.loader.epub_loader import EPUBBookLoader

    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / ANIMAL_FARM.name
    shutil.copy(ANIMAL_FARM, src)
    loader = EPUBBookLoader(
        str(src), model_cls, "dummy-key", resume=False, language="zh-hans"
    )
    loader.plan_mode = True
    loader.translate_tags = "auto"
    loader.plan_classify = "all"
    loader.only_filelist = "index_split_008.html"
    for name, value in attrs.items():
        setattr(loader, name, value)
    return loader, src


class _RecordingModel:
    """Minimal translator that records the size of every batch it is sent."""

    TRANSLATION_ERROR_MARKER = None
    _fatal_error_detected = False
    degree = False

    def __init__(self, key, language, **kwargs):
        self.list_calls = []
        self.single_calls = []

    def _structured_enabled(self):
        return self.degree

    def translate(self, text, needprint=True):
        self.single_calls.append(text)
        return f"T[{text}]"

    def translate_list(self, text_list):
        self.list_calls.append(list(text_list))
        return [f"T[{t}]" for t in text_list]


class _StrictModel(_RecordingModel):
    degree = "strict"


def _OverheadModel(tokens):
    """A strict translator that reports a known per-request prompt overhead."""
    return type(
        "_OverheadModel",
        (_StrictModel,),
        {"prompt_overhead_tokens": lambda self: tokens},
    )


def _SessionModel(compact_at=None, no_compact=False):
    """A strict translator carrying the two knobs the derivation reads."""
    return type(
        "_SessionModel",
        (_StrictModel,),
        {"context_compact_at": compact_at, "no_context_compact": no_compact},
    )


class TestLoaderHonorsAccumulatedNum:
    def test_a_budget_groups_long_paragraphs_and_prints_no_ignore_note(
        self, tmp_path, capsys
    ):
        loader, _ = _plan_loader(tmp_path, _StrictModel, accumulated_num=800)
        loader.make_bilingual_book()

        out = capsys.readouterr().out
        assert "--accumulated_num is ignored" not in out
        model = loader.translate_model
        assert any(len(call) > 1 for call in model.list_calls)
        # ordinary prose, not the short runs plan mode groups on its own
        assert any(
            len(call) > 1 and all(len(t) >= 70 for t in call)
            for call in model.list_calls
        ), model.list_calls

    def test_without_the_flag_the_derived_budget_still_groups(self, tmp_path):
        # was `only short runs group`, until 260906: the untyped default used
        # to be no budget at all outside session mode, which meant a request
        # per paragraph. Now every plan run derives one, so long paragraphs
        # share a request here as well.
        loader, _ = _plan_loader(tmp_path, _StrictModel)
        loader.make_bilingual_book()

        assert any(
            len(call) > 1 and sum(len(text) >= 70 for text in call) >= 2
            for call in loader.translate_model.list_calls
        ), loader.translate_model.list_calls

    def test_a_substrict_endpoint_gets_the_tighter_cap(self, tmp_path):
        # amendment 2: both content regressions the eval found were large
        # batches on endpoints below strict decoding
        loader, _ = _plan_loader(tmp_path, _RecordingModel, accumulated_num=100_000)
        loader.make_bilingual_book()

        sizes = [len(call) for call in loader.translate_model.list_calls]
        assert sizes and max(sizes) <= SUBSTRICT_GROUP_MAX_UNITS

    def test_a_strict_endpoint_keeps_the_full_cap(self, tmp_path):
        loader, _ = _plan_loader(tmp_path, _StrictModel, accumulated_num=100_000)
        loader.make_bilingual_book()

        sizes = [len(call) for call in loader.translate_model.list_calls]
        assert max(sizes) > SUBSTRICT_GROUP_MAX_UNITS
        assert max(sizes) <= GENERAL_GROUP_MAX_UNITS

    def test_the_default_cap_is_the_measured_cap(self, tmp_path):
        # no --max-batch-units: 32 strict, 16 below it — the halving is derived,
        # so the two can never drift apart
        strict, _ = _plan_loader(tmp_path / "s", _StrictModel)
        sub, _ = _plan_loader(tmp_path / "j", _RecordingModel)

        assert strict._plan_request_cap() == GENERAL_GROUP_MAX_UNITS
        assert sub._plan_request_cap() == SUBSTRICT_GROUP_MAX_UNITS

    def test_a_chosen_cap_is_halved_below_strict_decoding(self, tmp_path):
        # --max-batch-units 4: the strict cap as typed, half of it otherwise —
        # the same 32/16 ratio the default ships with
        strict, _ = _plan_loader(tmp_path / "s", _StrictModel, batch_units=4)
        sub, _ = _plan_loader(tmp_path / "j", _RecordingModel, batch_units=4)

        assert strict._plan_request_cap() == 4
        assert sub._plan_request_cap() == 2

    def test_a_cap_of_one_never_halves_to_nothing(self, tmp_path):
        sub, _ = _plan_loader(tmp_path, _RecordingModel, batch_units=1)

        assert sub._plan_request_cap() == 1

    def test_a_chosen_cap_bounds_the_batches_that_are_sent(self, tmp_path):
        loader, _ = _plan_loader(
            tmp_path, _StrictModel, accumulated_num=100_000, batch_units=3
        )
        loader.make_bilingual_book()

        sizes = [len(call) for call in loader.translate_model.list_calls]
        assert sizes and max(sizes) == 3


class TestSessionModeDefaultsTheBudget:
    """`--use_context session` groups by default; every other run does not."""

    def test_session_default_budget_is_the_margin_floor(self, tmp_path):
        # 1200 since the 260907 owner ruling: a chosen margin under the
        # 260906 weak-model B sweep's measured-clean 1600-4800 range, not a
        # fraction of a measured fault onset (none was ever found on the B
        # axis). A model that cannot measure its prompt overhead (and one
        # whose overhead is small) both land on the floor.
        from book_maker.loader.plan import SESSION_BUDGET_FLOOR

        bare, _ = _plan_loader(tmp_path / "bare", _StrictModel, context_mode="session")
        assert bare._plan_token_budget == SESSION_BUDGET_FLOOR == 1200

        lean, _ = _plan_loader(
            tmp_path / "lean",
            _OverheadModel(104),
            context_mode="session",
        )
        # 3 * 104 = 312, well under the floor
        assert lean._plan_token_budget == 1200

        # and tag mode is untouched: the default lives in the plan property,
        # not in the attribute every tag-mode path reads
        assert bare.accumulated_num == 1

    def test_a_fat_prompt_raises_the_session_budget(self, tmp_path):
        # the prompts are user-customisable and are paid for once per
        # request, so the budget grows to keep the overhead under ~1/3 of one
        from book_maker.loader.plan import SESSION_BUDGET_CEILING

        fat, _ = _plan_loader(
            tmp_path / "fat", _OverheadModel(500), context_mode="session"
        )
        # 3 * 500 = 1500: above the floor, still under the ceiling
        assert fat._plan_token_budget == 1500

        huge, _ = _plan_loader(
            tmp_path / "huge", _OverheadModel(1200), context_mode="session"
        )
        # 3 * 1200 = 3600, far above the clamp
        assert huge._plan_token_budget == SESSION_BUDGET_CEILING == 1600

    def test_an_explicit_one_turns_grouping_off_in_session_mode_too(self, tmp_path):
        # `--accumulated_num 1` is the documented way to say "no grouping";
        # a default that overrode it would leave no way to say it. The
        # answer is 0, not None: None re-enables the short-run rule, and
        # "off" that still groups short lines is not off (codex P1, 260905).
        loader, _ = _plan_loader(
            tmp_path,
            _StrictModel,
            context_mode="session",
            accumulated_num_given=True,
        )

        assert loader._plan_token_budget == 0

    def test_a_zero_budget_groups_nothing_at_all(self):
        # even a run of short lines — the off switch beats the short-run rule
        units = _units("<p>One.</p><p>Two.</p><p>Three.</p>")
        assert all(u.chars < 70 for u in units)

        assign_batches(units, token_budget=0)

        assert all(u.group_id is None for u in units)

    def test_an_explicit_value_still_wins_in_session_mode(self, tmp_path):
        loader, _ = _plan_loader(
            tmp_path,
            _StrictModel,
            context_mode="session",
            accumulated_num_given=True,
            accumulated_num=1500,
        )

        assert loader._plan_token_budget == 1500

    @pytest.mark.parametrize("mode", [None, "window"])
    def test_a_schema_route_derives_the_full_budget_without_a_session(
        self, tmp_path, mode
    ):
        # 260906: the untyped default is a derived budget on every plan run,
        # not only a session one. It used to be None here, and None meant a
        # request per paragraph — 45,010 of them across the corpus, against
        # 8,689 at the earlier 1600-token floor.
        from book_maker.loader.plan import session_token_budget

        loader, _ = _plan_loader(tmp_path, _StrictModel, context_mode=mode)

        assert loader._plan_token_budget == session_token_budget(None) == 1200
        assert loader._partition_route() == "schema"
        # a strict endpoint sends what the partition grouped, unsplit
        assert loader._plan_request_budget() is None

    @pytest.mark.parametrize("mode", [None, "window"])
    def test_a_substrict_route_carries_half_of_it_per_request(self, tmp_path, mode):
        # the same margin `SUBSTRICT_GROUP_MAX_UNITS` takes off the unit cap,
        # off the same verdict and in the same place: both content
        # regressions the 260905 json_object eval found were large batches
        # below strict decoding. The *partition* stays endpoint-independent —
        # asking the endpoint is what triggers its probe, and the plan is
        # built before anything has asked it a question.
        from book_maker.loader.plan import (
            SUBSTRICT_BUDGET_FLOOR,
            session_token_budget,
            substrict_token_budget,
        )

        loader, _ = _plan_loader(tmp_path, _RecordingModel, context_mode=mode)

        assert loader._plan_token_budget == session_token_budget(None) == 1200
        assert loader._plan_request_budget() == substrict_token_budget(None) == 800
        assert loader._plan_request_budget() == SUBSTRICT_BUDGET_FLOOR

        strict, _ = _plan_loader(tmp_path / "strict", _StrictModel, context_mode=mode)
        assert loader._plan_request_budget() != strict._plan_request_budget()

    def test_the_substrict_budget_actually_divides_the_requests(self):
        # the visible half: a group packed to 1600 tokens is sent as two
        # requests when the endpoint is below strict decoding
        from book_maker.loader.epub_loader import EPUBBookLoader

        units = _units("".join(_paragraph(120) for _ in range(8)))
        assign_batches(units, token_budget=1600)
        assert len({u.group_id for u in units}) == 1

        whole = EPUBBookLoader._plan_batch_indexes(units)
        halved = EPUBBookLoader._plan_batch_indexes(units, max_tokens=800)

        assert len(set(whole)) == 1
        assert len(set(halved)) > 1
        # and no request carries more than the budget, unless one unit does
        totals = {}
        for index, unit in zip(halved, units):
            totals[index] = totals.get(index, 0) + unit.token_count
        assert all(t <= 800 for t in totals.values()), totals

    def test_a_fat_prompt_raises_both_route_classes_together(self, tmp_path):
        # the halving rides on top of the overhead derivation, it does not
        # replace it: 3 * 500 = 1500, and half of that is 750 — which the
        # sub-strict floor then lifts to 800
        Fat = _OverheadModel(500)
        FatSub = type(
            "FatSub", (_RecordingModel,), {"prompt_overhead_tokens": lambda s: 500}
        )

        strict, _ = _plan_loader(tmp_path / "s", Fat)
        sub, _ = _plan_loader(tmp_path / "j", FatSub)

        assert strict._plan_token_budget == 1500
        assert sub._plan_token_budget == 1500
        assert sub._plan_request_budget() == 800

    def test_the_substrict_budget_is_pinned_at_its_floor_by_the_new_ceiling(
        self, tmp_path
    ):
        # A consequence of the 260907 numbers worth stating out loud: the
        # ceiling is 1600 and the sub-strict floor is a typed 800, so half of
        # the *largest* budget any prompt can derive is exactly the floor.
        # The sub-strict per-request budget is therefore constant — a fat
        # `--prompt` raises the partition's budget but never the sub-strict
        # request's. That is the owner's margin doing its job, not a bug; if
        # it should track the prompt again, the floor is the thing to move.
        from book_maker.loader.plan import SUBSTRICT_BUDGET_FLOOR

        for overhead in (0, 104, 500, 900, 5000):
            Sub = type(
                "Sub",
                (_RecordingModel,),
                {"prompt_overhead_tokens": lambda s, o=overhead: o},
            )
            loader, _ = _plan_loader(tmp_path / f"o{overhead}", Sub)
            assert loader._plan_request_budget() == SUBSTRICT_BUDGET_FLOOR == 800

    def test_an_explicit_one_turns_grouping_off_outside_session_mode_too(
        self, tmp_path
    ):
        # the off switch has to keep working now that every plan run has a
        # default to override — and the sub-strict split must not re-group
        # what it turned off
        for name, model in (("s", _StrictModel), ("j", _RecordingModel)):
            loader, _ = _plan_loader(tmp_path / name, model, accumulated_num_given=True)
            assert loader._plan_token_budget == 0
            assert loader._plan_request_budget() is None

    def test_a_typed_budget_is_never_halved(self, tmp_path):
        # typed wins outright: the operator asked for this many tokens per
        # request, on this endpoint
        loader, _ = _plan_loader(
            tmp_path, _RecordingModel, accumulated_num_given=True, accumulated_num=1500
        )
        assert loader._plan_token_budget == 1500
        assert loader._plan_request_budget() is None

    def test_a_session_run_is_unchanged_by_the_route_split(self, tmp_path):
        # pinned: the session budget was measured as a whole on the 260905
        # eval, and a session run keeps it whatever the endpoint decodes —
        # including the codex route, which offers no verdict at all
        from book_maker.loader.plan import session_token_budget

        for name, model in (("s", _StrictModel), ("j", _RecordingModel)):
            loader, _ = _plan_loader(tmp_path / name, model, context_mode="session")
            assert loader._plan_token_budget == session_token_budget(None)
            assert loader._partition_route() == "session"
            assert loader._plan_request_budget() is None

    def test_the_dry_run_line_names_the_number_every_route_will_land_on(self):
        # the parity the finish checklist calls out: a preview that promises
        # a budget the run does not use is worse than no preview. A dry run
        # has no endpoint to probe, so it names both numbers — and each of
        # them is what the matching route class actually derives.
        from book_maker.loader.plan import derived_token_budget, plan_budget_notice

        for overhead in (None, 650):
            preview = plan_budget_notice(overhead, None)
            for route in ("session", "schema", "substrict"):
                budget = derived_token_budget(overhead, route)
                assert str(budget) in preview, (route, overhead, preview)
                assert str(budget) in plan_budget_notice(overhead, route)

    def test_the_derived_budget_is_narrated_once_with_its_route(self, tmp_path, capsys):
        loader, _ = _plan_loader(tmp_path, _RecordingModel)

        budget = loader._plan_request_budget()
        loader._narrate_plan_budget(budget)
        loader._narrate_plan_budget(budget)

        out = " ".join(capsys.readouterr().out.split())
        assert out == (
            "plan grouping: budget 800 tokens per request (endpoint below "
            "strict decoding; derived, --accumulated_num overrides)"
        )

    def test_a_strict_route_narrates_the_full_budget(self, tmp_path, capsys):
        loader, _ = _plan_loader(tmp_path, _StrictModel)

        loader._narrate_plan_budget(loader._plan_request_budget())

        out = " ".join(capsys.readouterr().out.split())
        assert out == (
            "plan grouping: budget 1200 tokens per request (schema-verified "
            "endpoint; derived, --accumulated_num overrides)"
        )

    def test_a_typed_budget_narrates_nothing(self, tmp_path, capsys):
        loader, _ = _plan_loader(
            tmp_path, _StrictModel, accumulated_num_given=True, accumulated_num=900
        )

        loader._narrate_plan_budget(loader._plan_request_budget())

        assert capsys.readouterr().out == ""

    def test_the_session_default_groups_ordinary_prose(self, tmp_path):
        # the visible half of the contract: without the flag, a session run
        # batches consecutive paragraphs instead of sending one request each
        loader, _ = _plan_loader(tmp_path, _StrictModel, context_mode="session")
        loader.make_bilingual_book()

        # the 1200-token budget is big enough that the chapter heading rides
        # along with the prose, so "grouped" means a multi-text call carrying
        # at least two real paragraphs — not a call of nothing but prose
        assert any(
            len(call) > 1 and sum(len(t) >= 70 for t in call) >= 2
            for call in loader.translate_model.list_calls
        ), loader.translate_model.list_calls

    def test_the_report_names_the_budget_it_defaulted_to(self, tmp_path, capsys):
        loader, _ = _plan_loader(tmp_path, _StrictModel, context_mode="session")
        loader.make_bilingual_book()

        out = capsys.readouterr().out
        assert "1200 tokens" in out


class TestTheCompactBudgetIsPinned:
    """One number for every session run, grouped or not. Owner ruling: a
    per-run optimum is a moving target for a difference under 30%, and a
    budget an operator can predict beats one they have to work out."""

    def test_a_grouped_run_narrates_the_pinned_default_and_derives_nothing(
        self, tmp_path, capsys
    ):
        from book_maker.session_context import DEFAULT_COMPACT_BUDGET

        loader, _ = _plan_loader(tmp_path, _SessionModel(), context_mode="session")
        loader.make_bilingual_book()

        # left None: the translator falls back to the default itself
        assert loader.translate_model.context_compact_at is None
        out = " ".join(capsys.readouterr().out.split())
        assert (
            f"session: compacting at {DEFAULT_COMPACT_BUDGET} estimated tokens "
            f"(the default; --context-compact-at overrides)" in out
        )

    def test_the_line_is_printed_once(self, tmp_path, capsys):
        loader, _ = _plan_loader(tmp_path, _SessionModel(), context_mode="session")
        loader.make_bilingual_book()

        assert capsys.readouterr().out.count("session: compacting at") == 1

    def test_an_explicit_compact_budget_survives_grouping(self, tmp_path):
        loader, _ = _plan_loader(
            tmp_path, _SessionModel(compact_at=6000), context_mode="session"
        )
        loader.make_bilingual_book()

        assert loader.translate_model.context_compact_at == 6000

    def test_compaction_turned_off_says_nothing(self, tmp_path, capsys):
        loader, _ = _plan_loader(
            tmp_path, _SessionModel(no_compact=True), context_mode="session"
        )
        loader.make_bilingual_book()

        assert loader.translate_model.context_compact_at is None
        assert "session: compacting at" not in capsys.readouterr().out

    def test_an_ungrouped_session_gets_the_same_number(self, tmp_path, capsys):
        # `--accumulated_num 1`: budget 0, one request per unit. It used to
        # be the only shape the 8000 applied to; now every session run has it.
        from book_maker.session_context import DEFAULT_COMPACT_BUDGET

        loader, _ = _plan_loader(
            tmp_path,
            _SessionModel(),
            context_mode="session",
            accumulated_num_given=True,
        )
        loader.make_bilingual_book()

        assert loader.translate_model.context_compact_at is None
        out = " ".join(capsys.readouterr().out.split())
        assert f"compacting at {DEFAULT_COMPACT_BUDGET} estimated tokens" in out

    def test_a_plan_fallback_gets_the_same_number(self, tmp_path, monkeypatch, capsys):
        # The 260905 codex finding — a budget derived before the plan was
        # committed leaked onto the fallback's ungrouped translator — cannot
        # recur, because nothing is derived and nothing is written to the
        # translator at all. The narration is owed either way.
        from book_maker.session_context import DEFAULT_COMPACT_BUDGET

        loader, _ = _plan_loader(tmp_path, _SessionModel(), context_mode="session")
        loader.plan_auto = True

        def boom():
            raise RuntimeError("a gate below the coverage bar")

        monkeypatch.setattr(loader, "_prepare_translation_plan", boom)

        loader._narrate_session_compact_budget()
        assert loader._enter_plan_mode() is False
        assert loader.translate_model.context_compact_at is None
        out = " ".join(capsys.readouterr().out.split())
        assert f"compacting at {DEFAULT_COMPACT_BUDGET} estimated tokens" in out

    def test_prompt_overhead_is_about_a_hundred_tokens(self):
        # the sizing hint session_token_budget reads: the default prompts'
        # own per-request cost, book text excluded. No network.
        t = ChatGPTAPI(key="sk-not-used", language="Chinese")
        overhead = t.prompt_overhead_tokens()

        assert isinstance(overhead, int)
        assert 60 <= overhead <= 400


# ----------------------------------------------- 3. the plan mode gate


class _Probe:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.verdict


@pytest.mark.parametrize(
    "verdict,named",
    [("strict", "strict"), ("shape", "shape"), ("json", "JSON object")],
)
def test_every_admitted_degree_plans_and_names_itself(verdict, named):
    mode, reason = resolve_plan_mode("epub", "openai", False, _Probe(verdict))

    assert mode == "model"
    assert named in reason


@pytest.mark.parametrize("verdict", [False, "unsupported", "request rejected: 400"])
def test_no_structuring_at_all_stays_in_tag_mode(verdict):
    # amendment 3: `unsupported` is the router dropping response_format; a
    # prompt-rung plan entry is a separate decision, not this change
    mode, reason = resolve_plan_mode("epub", "openai", False, _Probe(verdict))

    assert mode == "none"
    assert "schema" in reason


# ------------------------------------ 4. batch translate at the json degree


class TestJsonDegreeBatchTranslate:
    def _translator_at_json(self, create):
        translator = _translator(create=create)
        translator.capabilities.verdicts["test-model"] = "json"
        return translator

    def test_a_fenced_reply_with_the_right_ids_aligns(self):
        create = Mock(
            return_value=_completion(
                "Sure, here you go:\n```json\n" + _reply(["一", "二"]) + "\n```"
            )
        )
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
        assert create.call_args.kwargs["response_format"] == {"type": "json_object"}
        assert "json_schema" not in json.dumps(create.call_args.kwargs)

    def test_the_described_schema_travels_in_the_prompt(self):
        create = Mock(return_value=_completion(_reply(["一", "二"])))
        translator = self._translator_at_json(create)

        translator._do_structured_batch_translate(["a", "b"])

        content = create.call_args.kwargs["messages"][-1]["content"]
        assert BATCH_FIELD in content
        assert "single JSON object" in content
        # the target language stays the last thing the model reads
        assert content.rstrip().endswith(f"{LANGUAGE}.")

    def test_unparsable_json_is_a_batch_mismatch_for_the_loader(self):
        create = Mock(return_value=_completion("I am afraid I cannot do that."))
        translator = self._translator_at_json(create)
        translator.translate = Mock(side_effect=lambda t, _=True: f"t:{t}")

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

        # one attempt, no per-item sweep: the loader's ladder halves instead
        assert create.call_count == 1
        assert translator.translate.call_count == 0

    def test_a_missing_top_key_is_a_batch_mismatch_not_a_fall_through(self):
        # amendment 1: `extract_json_object` alone would hand back the first
        # object it can parse, whatever it is
        create = Mock(
            return_value=_completion(json.dumps({"translations": ["一", "二"]}))
        )
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

    def test_a_stray_object_in_the_prose_is_stepped_over(self):
        create = Mock(
            return_value=_completion(
                'Note: {"about": "I translated these"}\n' + _reply(["一", "二"])
            )
        )
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]

    def test_wrong_ids_are_a_batch_mismatch(self):
        create = Mock(return_value=_completion(_reply(["一", "二"], ids=[0, 7])))
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

    def test_an_empty_tail_slot_is_a_batch_mismatch(self):
        # amendment 2's other half: the eval's empty-tail corruption is what
        # the existing empty-slot check is for, at this degree too
        create = Mock(return_value=_completion(_reply(["一+二 merged", ""])))
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch):
            translator._do_structured_batch_translate(["a", "b"])

    def test_an_oversized_batch_is_chunked_not_refused(self):
        # codex P1 + P2: the loader sizes batches for the model current at
        # plan build (and tag mode sizes them by characters), so a batch
        # over the json-degree cap is normal here. The cap is a per-request
        # bound: the translator honours it with more requests, because a
        # refusal would send tag mode's fallback into an N-singles sweep.
        from book_maker.loader.plan import SUBSTRICT_GROUP_MAX_UNITS

        n = SUBSTRICT_GROUP_MAX_UNITS * 2
        create = Mock(
            side_effect=[
                _completion(
                    _reply([f"t{i}" for i in range(SUBSTRICT_GROUP_MAX_UNITS)])
                ),
                _completion(
                    _reply(
                        [f"t{i}" for i in range(SUBSTRICT_GROUP_MAX_UNITS, n)],
                    )
                ),
            ]
        )
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(
            [f"p{i}" for i in range(n)]
        ) == [f"t{i}" for i in range(n)]
        assert create.call_count == 2
        # each request carried at most the cap's worth of paragraphs
        for call in create.call_args_list:
            content = call.kwargs["messages"][-1]["content"]
            assert f"EXACTLY {SUBSTRICT_GROUP_MAX_UNITS} objects" in content

    def test_a_rotation_race_to_a_json_model_is_refused_before_the_request(self):
        # The backstop inside the request path: the caller chunked against
        # the degree it saw, but rotate_model() can flip execution to a
        # json-degree model mid-batch. Refusing pre-request lets the
        # loader's ladder divide; nothing is spent.
        from book_maker.loader.plan import SUBSTRICT_GROUP_MAX_UNITS

        n = SUBSTRICT_GROUP_MAX_UNITS + 1
        create = Mock(return_value=_completion(_reply(["x"] * n)))
        translator = self._translator_at_json(create)

        with pytest.raises(BatchMismatch, match="json-degree cap"):
            translator._execute_structured_batch_translate(
                [f"p{i}" for i in range(n)], n
            )
        assert create.call_count == 0

    def test_the_class_default_cap_is_the_partitions_substrict_cap(self):
        # the translator spells the number itself (importing the loader from
        # here only works lazily); this is what keeps the two in step
        assert ChatGPTAPI.substrict_batch_cap == SUBSTRICT_GROUP_MAX_UNITS

    def test_a_lowered_cap_chunks_at_the_lowered_size(self):
        # --max-batch-units 4 halves to 2 here, and the chunking has to follow
        # that rather than the class default. Six, not five: a tail of one
        # leaves the batch path entirely (`get_translation`, through .parse),
        # which would measure the single-translate path instead of this cap.
        create = Mock(
            side_effect=[
                _completion(_reply(["t0", "t1"])),
                _completion(_reply(["t2", "t3"], ids=[0, 1])),
                _completion(_reply(["t4", "t5"], ids=[0, 1])),
            ]
        )
        translator = self._translator_at_json(create)
        translator.substrict_batch_cap = 2

        assert translator._do_structured_batch_translate(
            [f"p{i}" for i in range(6)]
        ) == [f"t{i}" for i in range(6)]
        assert create.call_count == 3
        for call in create.call_args_list:
            assert "EXACTLY 2 objects" in call.kwargs["messages"][-1]["content"]

    def test_the_rotation_race_backstop_honours_the_lowered_cap(self):
        create = Mock(return_value=_completion(_reply(["x"] * 3)))
        translator = self._translator_at_json(create)
        translator.substrict_batch_cap = 2

        with pytest.raises(BatchMismatch, match="cap of 2 units"):
            translator._execute_structured_batch_translate(
                [f"p{i}" for i in range(3)], 3
            )
        assert create.call_count == 0

    def test_a_batch_at_the_json_degree_cap_goes_through(self):
        from book_maker.loader.plan import SUBSTRICT_GROUP_MAX_UNITS

        n = SUBSTRICT_GROUP_MAX_UNITS
        create = Mock(return_value=_completion(_reply([f"t{i}" for i in range(n)])))
        translator = self._translator_at_json(create)

        assert translator._do_structured_batch_translate(
            [f"p{i}" for i in range(n)]
        ) == [f"t{i}" for i in range(n)]

    def test_a_strict_endpoint_still_gets_a_schema(self):
        parse = Mock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            parsed=SimpleNamespace(
                                **{
                                    BATCH_FIELD: [
                                        SimpleNamespace(
                                            **{"id": 0, SINGLE_FIELD: "一"}
                                        ),
                                        SimpleNamespace(
                                            **{"id": 1, SINGLE_FIELD: "二"}
                                        ),
                                    ]
                                }
                            ),
                            refusal=None,
                            content=None,
                        ),
                        finish_reason="stop",
                    )
                ]
            )
        )
        create = Mock()
        translator = _translator(create=create, parse=parse)
        translator.capabilities.verdicts["test-model"] = "strict"

        assert translator._do_structured_batch_translate(["a", "b"]) == ["一", "二"]
        assert parse.call_count == 1
        assert create.call_count == 0

    def test_a_shape_endpoint_batches_with_the_schema(self):
        # the batch schema pins no *values* (the language rides in the field
        # name and the prose), so shape decoding is enough for it
        translator = _translator()
        translator.capabilities.verdicts["test-model"] = "shape"

        assert translator._structured_enabled() == "shape"

    @pytest.mark.parametrize("verdict", [False, "unsupported"])
    def test_no_structuring_falls_back_to_the_delimiter_method(self, verdict):
        translator = _translator()
        translator.capabilities.verdicts["test-model"] = verdict

        assert translator._structured_enabled() is False


# ---------------------------------------- 5. the classifier from "json"


class TestClassifierAtTheJsonDegree:
    def test_the_ladder_starts_at_json_object_and_classifies(self):
        create = Mock(
            return_value=_completion(
                '```json\n{"p.header": {"content_type": "running head", '
                '"verdict": "skip"}}\n```'
            )
        )
        translator = _translator(create=create)
        translator.capabilities.verdicts["test-model"] = "json"
        schema = {
            "name": "classify",
            "schema": {
                "type": "object",
                "properties": {"p.header": {"type": "object"}},
                "required": ["p.header"],
            },
        }

        result = translator.structured_json("classify these", schema)

        assert result == {
            "p.header": {"content_type": "running head", "verdict": "skip"}
        }
        assert (
            translator.structured_rungs("classify these", schema)[0][0] == "json_object"
        )
        assert create.call_args_list[0].kwargs["response_format"] == {
            "type": "json_object"
        }

    def test_an_object_that_answers_nothing_descends_instead_of_passing(self):
        # amendment 1: the fail-open. An unescaped quote leaves the outer
        # object unparsable and the scan finds an inner fragment; without a
        # key check that fragment came back as the answer.
        schema = {
            "name": "classify",
            "schema": {
                "type": "object",
                "properties": {"p.header": {"type": "object"}},
                "required": ["p.header"],
            },
        }
        create = Mock(
            side_effect=[
                # the outer object does not parse (the stray quote), and the
                # scan's next candidate is the *inner* one — an object that
                # answers nothing
                _completion(
                    '{"p.header": {"verdict": "skip"}, '
                    '"p.foot": {"verdict": "sk"ip"}}'
                ),
                _completion('{"p.header": {"verdict": "skip"}}'),
            ]
        )
        translator = _translator(create=create)
        translator.capabilities.verdicts["test-model"] = "json"

        assert translator.structured_json("classify", schema) == {
            "p.header": {"verdict": "skip"}
        }
        assert create.call_count == 2

    def test_required_keys_come_from_the_schema(self):
        schema = {
            "name": "classify",
            "schema": {
                "type": "object",
                "properties": {"a": {}, "b": {}},
                "required": ["a", "b"],
            },
        }
        assert schema_required_keys(schema) == ("a", "b")
        # no `required`: the declared properties are what an answer carries
        assert schema_required_keys({"schema": {"properties": {"x": {}}}}) == ("x",)
        assert schema_required_keys({"schema": {}}) == ()

    def test_a_schema_echo_is_still_recovered(self):
        # the required-key check must not cost us the measured echo case
        echoed = json.dumps(
            {"type": "object", "properties": {"p.header": {"verdict": "skip"}}}
        )
        assert extract_json_object(echoed, ("p.header",)) is not None

    def test_partial_answers_still_count(self):
        # "lacks *every* expected key" is the bar, not "lacks one"
        obj = extract_json_object(json.dumps({"a": 1}), ("a", "b"))
        assert obj == {"a": 1}


def test_entry_rung_defaults_to_the_prompt_for_an_unsupported_endpoint():
    # amendment 4: the absence of an "unsupported" key is the decision —
    # an endpoint that produced no JSON at all is asked in prose
    assert "unsupported" not in ENTRY_RUNG
    assert ENTRY_RUNG.get("unsupported", "prompt") == "prompt"

    translator = _translator()
    translator.capabilities.verdicts["test-model"] = "unsupported"
    assert translator.structured_rungs("q", {"schema": {}})[0][0] == "prompt"


# ------------------------------------------------- 6. the plan records it


class TestPlanMetaRecordsTheBudget:
    def _plan(self, tmp_path, token_budget, **kwargs):
        from ebooklib import epub

        from book_maker.loader.plan import build_plan

        book = epub.read_epub(str(ANIMAL_FARM))
        return build_plan(book, token_budget=token_budget, **kwargs)

    def test_the_budget_is_recorded_and_changes_the_plan_identity(self, tmp_path):
        src = tmp_path / ANIMAL_FARM.name
        tmp_path.mkdir(parents=True, exist_ok=True)
        shutil.copy(ANIMAL_FARM, src)

        none = self._plan(tmp_path, None).plan_meta(str(src))
        small = self._plan(tmp_path, 400).plan_meta(str(src))
        large = self._plan(tmp_path, 4000).plan_meta(str(src))

        assert none["token_budget"] is None
        assert small["token_budget"] == 400
        assert small != large
        assert small != none

    def test_the_stored_shape_still_reads_as_this_schema(self, tmp_path):
        from book_maker.loader.plan import PLAN_SCHEMA_VERSION

        tmp_path.mkdir(parents=True, exist_ok=True)
        src = tmp_path / ANIMAL_FARM.name
        shutil.copy(ANIMAL_FARM, src)
        plan = self._plan(tmp_path, 800)
        path = tmp_path / "plan.json"
        plan.save_json(str(path), book_path=str(src))

        data = json.loads(path.read_text())
        # additive meta only: the budget changes which units share a request,
        # never which units exist, so no resume cache or row key moves
        assert data["schema_version"] == PLAN_SCHEMA_VERSION
        assert data["token_budget"] == 800
        assert data["batch_units"] == GENERAL_GROUP_MAX_UNITS

    def test_the_budget_is_not_a_planning_setting(self, tmp_path):
        # same reason `poetry_group_size` is not one: it decides how many
        # units share a request, never what a row's evidence says, so a
        # changed budget must not reopen a fully decided plan
        from book_maker.loader.plan import planning_settings

        assert "token_budget" not in planning_settings(("sup", "code"))

    def test_the_unit_cap_is_recorded_and_changes_the_plan_identity(self, tmp_path):
        # --max-batch-units gets the budget's treatment: it shaped the requests,
        # so it is written down
        tmp_path.mkdir(parents=True, exist_ok=True)
        src = tmp_path / ANIMAL_FARM.name
        shutil.copy(ANIMAL_FARM, src)

        default = self._plan(tmp_path, 400).plan_meta(str(src))
        narrow = self._plan(tmp_path, 400, batch_units=4).plan_meta(str(src))

        assert default["batch_units"] == GENERAL_GROUP_MAX_UNITS
        assert narrow["batch_units"] == 4
        assert narrow != default

    def test_the_unit_cap_is_not_a_planning_setting_either(self, tmp_path):
        from book_maker.loader.plan import planning_settings

        assert "batch_units" not in planning_settings(("sup", "code"))
