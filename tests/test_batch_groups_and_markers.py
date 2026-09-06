"""Acceptance contracts for the grouping/batching + inline-marker change set.

Written by the lead agent BEFORE implementation (260904); these are the
contracts the change set is accepted against, red until the work is done.
Design: docs/260901-refactor-SHORT_RUN_BATCHING_REPLACES_POETRY_WINDOWS.md
(layers A-E, §9) in the main checkout, plus the pinned user decisions in
plan.md ("Grouping / batching + inline placeholders"). Implementation may
add tests but must not weaken these without the lead's sign-off.

Interfaces pinned here (the implementation writes to these names):

- ``plan.assign_batches(units, group_size=8, next_group_id=0) -> int``
  replaces ``assign_context_windows``; ``SHORT_UNIT_CHARS`` (70) and
  ``GROUP_MAX_CHARS`` (500) live beside it.
- ``base_translator.BatchMismatch`` raised by every LLM route's
  ``translate_list`` instead of self-repairing per line; the loader's
  ``_translate_texts_aligned`` ladder is the only fallback.
- ``chatgptapi_translator.batch_translation_model(language, n)``: items
  are ``{id, <lang>_translation}`` objects, nothing else; replies align
  by echoed id, not by position.
- ``book_maker.loader.markers``: ``INLINE_MARKER_MAX_CHARS`` (40) and
  ``reconcile_markers(sent, reply) -> str`` (lenient: never raises).
  Units carry ``unit.markers`` — an ordered ``{token: source node}``.
- ``book_maker.utils.parse_language_spec`` for ``--language TAG:NAME``.
"""

import re
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup as bs


def _soup(body_html):
    return bs(f"<html><body>{body_html}</body></html>", "html.parser")


def _resolver():
    from book_maker.loader.plan import DisplayResolver

    return DisplayResolver([])


def _partition(body_html, **kwargs):
    from book_maker.loader.plan import partition_soup

    return partition_soup(_soup(body_html), _resolver(), "chap.xhtml", **kwargs)


# ------------------------------------------------- A. grouping (plan.py)


class TestAssignBatches:
    def _units(self, body_html):
        fp = _partition(body_html)
        return fp.units

    def test_mixed_shape_short_run_groups(self):
        # No sibling/stanza/signature test any more: consecutive short
        # units group regardless of tag or parent.
        from book_maker.loader.plan import assign_batches

        units = self._units(
            "<h3>One short line</h3>"
            "<p>Another short line</p>"
            "<div>A third short line</div>"
        )
        assert len(units) == 3
        assign_batches(units)
        ids = [u.group_id for u in units]
        assert ids[0] is not None
        assert len(set(ids)) == 1

    def test_a_run_of_two_groups(self):
        # The old WINDOW_MIN_RUN was 3; the design says a run of >= 2 groups.
        from book_maker.loader.plan import assign_batches

        units = self._units("<p>First short line</p><p>Second short line</p>")
        assign_batches(units)
        assert units[0].group_id is not None
        assert units[0].group_id == units[1].group_id

    def test_long_unit_is_its_own_batch_and_breaks_the_run(self):
        from book_maker.loader.plan import SHORT_UNIT_CHARS, assign_batches

        long_text = "word " * 40  # ~200 chars, well over the threshold
        units = self._units(
            f"<p>Short one</p><p>Short two</p><p>{long_text}</p>"
            f"<p>Short three</p><p>Short four</p>"
        )
        assert len(units) == 5
        assert units[2].chars >= SHORT_UNIT_CHARS
        assign_batches(units)
        assert units[2].group_id is None
        # the run does not span the long unit
        assert units[0].group_id == units[1].group_id
        assert units[3].group_id == units[4].group_id
        assert units[0].group_id != units[3].group_id

    def test_group_size_cap(self):
        from book_maker.loader.plan import assign_batches

        units = self._units("".join(f"<p>line {i}</p>" for i in range(11)))
        assign_batches(units, group_size=8)
        first = [u for u in units if u.group_id == units[0].group_id]
        assert len(first) <= 8
        assert all(u.group_id is not None for u in units)

    def test_group_char_cap(self):
        from book_maker.loader.plan import GROUP_MAX_CHARS, assign_batches

        # each ~65 chars: short individually, but eight together over 500
        line = "m" * 65
        units = self._units("".join(f"<p>{line}</p>" for _ in range(8)))
        assign_batches(units, group_size=8)
        totals = {}
        for u in units:
            totals[u.group_id] = totals.get(u.group_id, 0) + u.chars
        assert all(total <= GROUP_MAX_CHARS for total in totals.values())
        assert len(totals) > 1

    def test_deterministic(self):
        from book_maker.loader.plan import assign_batches

        html = "<p>alpha line</p><h4>beta line</h4><p>gamma line</p>"
        a, b = self._units(html), self._units(html)
        assign_batches(a)
        assign_batches(b)
        assert [u.group_id for u in a] == [u.group_id for u in b]

    def test_windows_api_is_gone(self):
        # the stanza/sibling tier is deleted, not kept alongside
        import book_maker.loader.plan as plan

        assert not hasattr(plan, "assign_context_windows")
        assert not hasattr(plan, "_run_compatible")


# ------------------------------ C. carrier contract: exactly N or raise


class TestBatchMismatchContract:
    def test_check_batch_wrong_count(self):
        from book_maker.translator.base_translator import BatchMismatch, Base

        with pytest.raises(BatchMismatch):
            Base._check_batch(["a", "b", "c"], ["x", "y"])

    def test_check_batch_empty_slot(self):
        from book_maker.translator.base_translator import BatchMismatch, Base

        with pytest.raises(BatchMismatch):
            Base._check_batch(["a", "b"], ["x", "  "])

    def test_check_batch_accepts_aligned(self):
        from book_maker.translator.base_translator import Base

        assert Base._check_batch(["a", "b"], ["x", "y"]) is None

    def test_delimiterless_reply_raises_not_per_line_fallback(self):
        # _extract_paragraphs step 4 (split on every non-blank line) is
        # gone: a multi-line reply with no delimiter is a mismatch the
        # loader's ladder handles — the translator must not quietly
        # manufacture N items or fall back per line itself.
        from book_maker.translator.base_translator import (
            BATCH_DELIMITER,
            BatchMismatch,
        )
        from book_maker.translator.claude_translator import Claude

        claude = Claude("k", "Chinese")
        calls = []

        def create(**request):
            calls.append(request)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="一行\n二行\n三行\n四行")]
            )

        claude.client = SimpleNamespace(messages=SimpleNamespace(create=create))
        stanza = ["one line", "two line", "three line", "four line"]
        with pytest.raises(BatchMismatch):
            claude.translate_list(stanza)
        assert len(calls) == 1  # no self-repair requests
        assert BATCH_DELIMITER  # still the wire format for this route


# --------------------------------- C. structured id echo (openai route)


class TestStructuredIdEcho:
    def _translator(self, parse):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        t = ChatGPTAPI("k", "zh-hant")
        # fixture only: the structured path is gated on the capability probe,
        # and the probe goes through `create` — which this fixture nulls on
        # purpose. Seed the verdict so the request under test is the batch
        # one. No assertion below depends on this line.
        t.capabilities.verdicts[t.model] = "strict"
        t.openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=None,
                    parse=parse,
                )
            )
        )
        return t

    @staticmethod
    def _parsed(items):
        message = SimpleNamespace(
            parsed=SimpleNamespace(paragraphs=None), refusal=None, content=None
        )
        # the container field is language-slugged; set dynamically
        setattr(message.parsed, "zh_hant_paragraphs", items)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")]
        )

    def test_schema_items_are_id_plus_lang_field_only(self):
        from book_maker.translator.chatgptapi_translator import (
            batch_translation_model,
        )

        schema = batch_translation_model("zh-hant", 3).model_json_schema()
        text = str(schema)
        assert "zh_hant_translation" in text
        assert "'id'" in text or '"id"' in text
        # trimmed on purpose: no notes/free-text companion fields
        assert "notes" not in text

    def test_replies_align_by_echoed_id_not_position(self):
        replies = [
            SimpleNamespace(id=1, zh_hant_translation="乙"),
            SimpleNamespace(id=0, zh_hant_translation="甲"),
        ]
        calls = []

        def parse(**request):
            calls.append(request)
            return self._parsed(replies)

        t = self._translator(parse)
        assert t.translate_list(["first", "second"]) == ["甲", "乙"]

    def test_duplicate_id_is_a_mismatch(self):
        from book_maker.translator.base_translator import BatchMismatch

        replies = [
            SimpleNamespace(id=0, zh_hant_translation="甲"),
            SimpleNamespace(id=0, zh_hant_translation="乙"),
        ]
        t = self._translator(lambda **request: self._parsed(replies))
        with pytest.raises(BatchMismatch):
            t.translate_list(["first", "second"])

    def test_missing_id_is_a_mismatch(self):
        from book_maker.translator.base_translator import BatchMismatch

        replies = [
            SimpleNamespace(id=0, zh_hant_translation="甲"),
            SimpleNamespace(id=5, zh_hant_translation="乙"),
        ]
        t = self._translator(lambda **request: self._parsed(replies))
        with pytest.raises(BatchMismatch):
            t.translate_list(["first", "second"])


# ------------------------------------- D. the loader ladder is the fallback


class _HalvingFake:
    """Raises BatchMismatch for any batch containing the poison text."""

    TRANSLATION_ERROR_MARKER = "[Translation failed for this paragraph]"
    _fatal_error_detected = False

    def __init__(self):
        self.batch_calls = []
        self.single_calls = []

    def translate_list(self, texts):
        from book_maker.translator.base_translator import BatchMismatch

        self.batch_calls.append(list(texts))
        if any("POISON" in t for t in texts):
            raise BatchMismatch(f"scripted mismatch for {len(texts)}")
        return [f"T[{t}]" for t in texts]

    def translate(self, text):
        self.single_calls.append(text)
        return f"T[{text}]"


class TestDivideLadder:
    def test_batch_mismatch_halves_to_singles(self):
        from book_maker.loader.epub_loader import EPUBBookLoader

        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        fake = _HalvingFake()
        loader.translate_model = fake

        texts = ["a1", "a2", "POISON", "a4"]
        assert loader._translate_texts_aligned(texts) == [
            "T[a1]",
            "T[a2]",
            "T[POISON]",
            "T[a4]",
        ]
        # halving, not 4 immediate singles: 4 -> [a1,a2] ok, [POISON,a4]
        # mismatch -> singles for that half only
        assert fake.batch_calls[0] == texts
        assert ["POISON", "a4"] in fake.batch_calls
        assert fake.single_calls == ["POISON", "a4"]
        assert "a1" not in fake.single_calls

    def test_frequent_recoveries_earn_the_lower_your_caps_hint(self, capsys):
        """One split is the contract working; three is the operator's cue.

        The hint names the two knobs that shrink a batch and appears only
        once the run has recovered three times — a single misalignment
        must not nag."""
        from book_maker.loader.epub_loader import EPUBBookLoader

        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        loader.translate_model = _HalvingFake()

        hint = "may fit this model better"
        loader._translate_texts_aligned(["POISON", "b"])
        assert hint not in capsys.readouterr().out  # events 1 (then singles)

        loader._translate_texts_aligned(["POISON", "d"])
        assert hint not in capsys.readouterr().out  # event 2

        loader._translate_texts_aligned(["POISON", "f"])
        out = capsys.readouterr().out  # event 3: the cue
        assert "3 misaligned batches this run" in out
        assert "--max-batch-units" in out and "--accumulated_num" in out


# ------------------------- D2. a right-length reply that is shifted anyway

MARKER_ANY_RE = re.compile(r"⟦[^⟦⟧\s]{1,32}⟧")


class _MarkerBatchFake:
    """Answers batches straight, except one size it answers shifted by a slot.

    `shift_at=3` reproduces the measured failure: the reply is the right
    length so nothing raises, but slot 3 carries the text — and therefore the
    marker token — that belongs to unit 2. Halves are answered honestly, so
    the ladder converges.
    """

    TRANSLATION_ERROR_MARKER = None
    _fatal_error_detected = False

    def __init__(self, shift_at=None, drop_markers=False, duplicate_into=None):
        self.shift_at = shift_at
        self.drop_markers = drop_markers
        # (owner slot, other slot): the owner keeps its token and a copy of
        # it is sprayed into the other slot as well.
        self.duplicate_into = duplicate_into
        self.batch_calls = []
        self.single_calls = []

    def _render(self, text):
        if self.drop_markers:
            text = MARKER_ANY_RE.sub("", text)
        return f"T[{text}]"

    def translate_list(self, texts):
        self.batch_calls.append(list(texts))
        straight = [self._render(t) for t in texts]
        if self.shift_at is not None and len(texts) == self.shift_at:
            # one slot late: every reply lands on the following unit
            return ["T[the slot before]"] + straight[:-1]
        if self.duplicate_into is not None and len(texts) > max(self.duplicate_into):
            owner, other = self.duplicate_into
            tokens = MARKER_ANY_RE.findall(straight[owner])
            if tokens:
                straight[other] = f"{straight[other]} {tokens[0]}"
        return straight

    def translate(self, text):
        self.single_calls.append(text)
        return self._render(text)


def _marker_loader():
    """A bare loader that can both run the ladder and write a unit back."""
    from book_maker.loader.epub_loader import EPUBBookLoader
    from book_maker.loader.helper import EPUBBookLoaderHelper

    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.language_tag = "zh-Hans"
    loader.translation_style = ""
    loader.single_translate = False
    loader.exclude_translate_tags = "sup,code"
    loader.helper = EPUBBookLoaderHelper(
        SimpleNamespace(TRANSLATION_ERROR_MARKER=None), "", False, "zh-Hans"
    )
    return loader


THREE_UNITS_ONE_MARKER = (
    "<p>Alpha, the paragraph that comes first.</p>"
    "<p>Press <code>Ctrl+C</code> to stop it now.</p>"
    "<p>Gamma, the paragraph that comes last.</p>"
)


def _three_units():
    from book_maker.loader.plan import DisplayResolver, partition_soup

    soup = _soup(THREE_UNITS_ONE_MARKER)
    fp = partition_soup(soup, DisplayResolver([]), "chap.xhtml")
    assert len(fp.units) == 3
    assert not fp.units[0].markers and not fp.units[2].markers
    assert len(fp.units[1].markers) == 1
    return soup, fp.units


class TestShiftedReplyIsCaughtByItsMarkers:
    """A one-slot shift is silent unless the markers give it away.

    Measured 260905 (`s-child-u48`): the counts agree, every slot holds
    fluent target text, no id or href is lost — but marker restoration is
    keyed on `unit.markers`, so the reply carrying `⟦span3⟧` reaches a unit
    that owns no markers, its marker block is skipped entirely, and the
    literal token is written into reader-visible prose.
    """

    def test_a_token_in_the_wrong_slot_sends_the_batch_to_the_ladder(self):
        soup, units = _three_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        fake = _MarkerBatchFake(shift_at=3)
        loader.translate_model = fake

        result = loader._translate_texts_aligned(texts, fake, units)

        # the shifted batch was sent once, then divided rather than written
        assert fake.batch_calls[0] == texts
        assert fake.single_calls == [texts[0]]
        assert texts[1:] in fake.batch_calls
        # every slot now answers its own unit
        assert result == [f"T[{t}]" for t in texts]

        for unit, t_text in zip(units, result):
            loader._insert_plan_translation(unit, t_text)

        assert "⟦" not in soup.get_text()
        # the marker's source node is restored beside the original
        assert [c.get_text() for c in soup.find_all("code")] == ["Ctrl+C", "Ctrl+C"]
        # and it is unit 2's translation that sits beside unit 2, not a
        # neighbour's — the shift itself is gone, not merely its residue
        paragraphs = [p.get_text() for p in soup.find_all("p")]
        assert paragraphs == [
            "Alpha, the paragraph that comes first.",
            "T[Alpha, the paragraph that comes first.]",
            "Press Ctrl+C to stop it now.",
            "T[Press Ctrl+C to stop it now.]",
            "Gamma, the paragraph that comes last.",
            "T[Gamma, the paragraph that comes last.]",
        ]

    def test_the_shift_is_named_in_the_realignment_notice(self, capsys):
        soup, units = _three_units()
        loader = _marker_loader()
        fake = _MarkerBatchFake(shift_at=3)
        loader.translate_model = fake

        loader._translate_texts_aligned([u.text for u in units], fake, units)

        out = capsys.readouterr().out
        assert "shifted" in out and "splitting for realignment" in out
        assert next(iter(units[1].markers)) in out

    def test_a_clean_reply_is_returned_byte_identically(self):
        soup, units = _three_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        fake = _MarkerBatchFake()
        loader.translate_model = fake

        result = loader._translate_texts_aligned(texts, fake, units)

        assert result == [f"T[{t}]" for t in texts]
        assert fake.batch_calls == [texts]  # one request, no ladder
        assert fake.single_calls == []

    def test_a_dropped_marker_is_still_reconciled_not_retried(self):
        """The fix keys on wrong-slot evidence, not on any marker anomaly.

        A model that simply left the token out of its own slot is the
        pinned-lenient case: reconciliation appends it and the run never
        re-pays the request.
        """
        soup, units = _three_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        fake = _MarkerBatchFake(drop_markers=True)
        loader.translate_model = fake

        result = loader._translate_texts_aligned(texts, fake, units)

        assert fake.batch_calls == [texts]  # no split
        assert fake.single_calls == []
        assert "⟦" not in result[1]

        loader._insert_plan_translation(units[1], result[1])
        assert [c.get_text() for c in soup.find_all("code")] == ["Ctrl+C", "Ctrl+C"]
        assert "⟦" not in soup.get_text()

    def test_a_duplicated_marker_is_not_a_shift(self):
        """The owner kept its token; a copy landed next door as well.

        Nothing is misaligned — every slot still answers its own unit — and
        `reconcile_markers` drops the copy as invented. Sending the batch to
        the halving ladder for it would repay a request that was correct.
        """
        soup, units = _three_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        # unit 2 owns the only marker; slot 1 gets a copy of it
        fake = _MarkerBatchFake(duplicate_into=(1, 0))
        loader.translate_model = fake

        result = loader._translate_texts_aligned(texts, fake, units)

        assert fake.batch_calls == [texts]  # one request, no ladder
        assert fake.single_calls == []
        token = next(iter(units[1].markers))
        assert token in result[0] and token in result[1]

        for unit, t_text in zip(units, result):
            loader._insert_plan_translation(unit, t_text)

        # the stray copy is reconciled away, the owner's is restored
        assert "⟦" not in soup.get_text()
        assert [c.get_text() for c in soup.find_all("code")] == ["Ctrl+C", "Ctrl+C"]

    def test_a_marker_less_unit_still_has_an_invented_token_scrubbed(self):
        # the write path used to skip reconciliation entirely for a unit
        # owning no markers, so a stray token reached reader-visible prose
        soup, units = _three_units()
        loader = _marker_loader()
        loader.translate_model = SimpleNamespace(TRANSLATION_ERROR_MARKER=None)
        token = next(iter(units[1].markers))

        loader._insert_plan_translation(units[0], f"T[alpha] {token}")

        assert "⟦" not in soup.get_text()

    def test_a_duplicated_marker_does_not_mask_a_real_shift(self):
        # the owner's slot losing its token is still the whole test: the
        # shifted reply keeps splitting
        _soup_, units = _three_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        fake = _MarkerBatchFake(shift_at=3)
        loader.translate_model = fake

        assert (
            loader._marker_slot_mismatch(texts, fake.translate_list(texts), units)
            is not None
        )

    def test_without_units_the_check_cannot_and_does_not_fire(self):
        # tag mode passes no units; the reply is accepted exactly as before
        _soup_, units = _three_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        fake = _MarkerBatchFake(shift_at=3)
        loader.translate_model = fake

        result = loader._translate_texts_aligned(texts, fake)

        assert result[0] == "T[the slot before]"
        assert fake.batch_calls == [texts]


# ------------------- D3. a count-compensated shift, caught by the numbers
#
# Measured 260906, sweep cell `ds-waste-u96-b4800` (DeepSeek, strict schema,
# 96 units a batch). The model merged two adjacent source lines into one
# reply slot and kept the *count* right by inventing a filler item — the
# meta-comment below, which was written into the book. Counts, echoed ids
# and per-slot fluency all agreed; 42 slots faced the wrong original. No
# marker was near it, so `_marker_slot_mismatch` could not see it. What did
# see it was a number: verse line `170` belongs to source slot 176 and came
# back inside slot 175's Chinese.

FILLER = "（此处应有一个空行表示节段间隔）"

# The Waste Land, ll. 168-178 as the loader sees them, verse numbers and the
# repeated refrain included: the numbers ride at the end of the line they
# count, and two slots are byte-identical.
WASTE_LINES = [
    "You are a proper fool, I said.",
    "Well, if Albert won't leave you alone, there it is, I said,",
    "What you get married for if you don't want children?160",
    "HURRY UP PLEASE ITS TIME",
    "Well, that Sunday Albert was home, they had a hot gammon,",
    "And they asked me in to dinner, to get the beauty of it hot―",
    "HURRY UP PLEASE ITS TIME",
    "Ta ta. Goonight. Goonight.",
    "Goonight Bill. Goonight Lou. Goonight May. Goonight.170",
    "Good night, ladies, good night, sweet ladies, good night.",
    "III. THE FIRE SERMON",
    "The river's tent is broken: the last fingers of leaf178",
]


class _MergingBatchFake:
    """Answers one batch size with the measured fault, others honestly.

    `merge_at=12`: slots 0 and 1 come back welded into one, every later slot
    holds the *following* source's translation, and a fabricated filler pads
    the tail so the count still matches. Halves are answered straight, so the
    ladder converges.
    """

    TRANSLATION_ERROR_MARKER = None
    _fatal_error_detected = False

    def __init__(self, merge_at=None):
        self.merge_at = merge_at
        self.batch_calls = []
        self.single_calls = []

    @staticmethod
    def _render(text):
        return f"译[{text}]"

    def translate_list(self, texts):
        self.batch_calls.append(list(texts))
        straight = [self._render(t) for t in texts]
        if self.merge_at is not None and len(texts) == self.merge_at:
            merged = f"{straight[0]}{straight[1]}"
            return [merged] + straight[2:] + [FILLER]
        return straight

    def translate(self, text):
        self.single_calls.append(text)
        return self._render(text)


def _shift_check(texts, result):
    from book_maker.loader.epub_loader import EPUBBookLoader

    return EPUBBookLoader._numeric_slot_shift(texts, result)


class TestCountCompensatedShiftIsCaughtByItsNumbers:
    def test_the_measured_pattern_sends_the_batch_to_the_ladder(self):
        from book_maker.loader.epub_loader import EPUBBookLoader

        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        fake = _MergingBatchFake(merge_at=len(WASTE_LINES))
        loader.translate_model = fake

        result = loader._translate_texts_aligned(WASTE_LINES)

        # the poisoned batch was sent once, then halved rather than written
        assert fake.batch_calls[0] == WASTE_LINES
        assert WASTE_LINES[:6] in fake.batch_calls
        assert WASTE_LINES[6:] in fake.batch_calls
        # every slot now answers its own line, and the invention is gone
        assert result == [f"译[{line}]" for line in WASTE_LINES]
        assert FILLER not in "".join(result)

    def test_the_moved_number_is_named_in_the_realignment_notice(self, capsys):
        from book_maker.loader.epub_loader import EPUBBookLoader

        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        loader.translate_model = _MergingBatchFake(merge_at=len(WASTE_LINES))

        loader._translate_texts_aligned(WASTE_LINES)

        out = " ".join(capsys.readouterr().out.split())  # rich wraps the line
        # the same narration wrong-slot marker evidence earns
        assert "came back shifted" in out and "splitting for realignment" in out
        # the moved number, where it belongs and where it landed
        assert "160 belongs to slot 3 of 12 and came back in slot 2" in out
        assert "2 more numbers moved the same way" in out

    def test_a_clean_reply_costs_one_request(self):
        from book_maker.loader.epub_loader import EPUBBookLoader

        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        fake = _MergingBatchFake()
        loader.translate_model = fake

        result = loader._translate_texts_aligned(WASTE_LINES)

        assert result == [f"译[{line}]" for line in WASTE_LINES]
        assert fake.batch_calls == [WASTE_LINES]  # no ladder
        assert fake.single_calls == []

    def test_digits_turned_into_cjk_numerals_in_place_are_not_a_shift(self):
        """The commonest way a number leaves its slot, and it is innocent.

        DeepSeek writes `171` as 「一七一」 and drops verse numbers outright —
        six of twenty-four on the clean cell `ds-waste-u64-b4800`. Absence
        alone must never fire, or every weak-model batch pays the ladder.
        """
        texts = [
            "And went on in sunlight, into the Hofgarten,10",
            "Out of this stony rubbish? Son of man,20",
            "I will show you fear in a handful of dust.30",
        ]
        cjk = [
            "又继续前行，在阳光下，进入宫廷花园一〇",
            "从这片碎石废墟中生长？人之子啊，二〇",
            "我要让你看一把尘土里的恐惧。",  # dropped entirely
        ]
        assert _shift_check(texts, cjk) is None

        from book_maker.loader.epub_loader import EPUBBookLoader

        loader = EPUBBookLoader.__new__(EPUBBookLoader)
        loader.translate_model = SimpleNamespace(
            TRANSLATION_ERROR_MARKER=None,
            _fatal_error_detected=False,
            translate_list=lambda _texts: list(cjk),
        )
        assert loader._translate_texts_aligned(texts) == cjk

    def test_one_cross_slot_number_is_not_enough(self):
        """Cell `o4m-waste-u256-b1600` has exactly one and is not shifted.

        A book supplies coincidences — a number quoted in the sentence before
        the one that owns it — so a single witness cannot buy a whole batch's
        halving ladder.
        """
        texts = ["line one, 160", "line two", "line three, 170", "line four"]
        result = ["译[line one]", "译[160 line two]", "译[line three, 170]", "译[four]"]
        assert _shift_check(texts, result) is None

    def test_two_witnesses_disagreeing_on_direction_do_not_fire(self):
        """A shift moves every slot the same way; contradiction is noise."""
        texts = ["a 160", "b", "c 170", "d"]

        agreeing = [
            "译[a]",
            "译[b 160]",  # slot 1's number, one slot later
            "译[c]",
            "译[d 170]",  # slot 3's number, one slot later
        ]
        assert _shift_check(texts, agreeing) is not None  # a real shift

        opposed = [
            "译[a]",
            "译[b 160 170]",  # slot 1's number one later, slot 3's one earlier
            "译[c]",
            "译[d]",
        ]
        assert _shift_check(texts, opposed) is None

    def test_a_number_the_neighbour_source_also_carries_is_not_a_witness(self):
        # the refrain repeats verbatim; the neighbour's own line explains the
        # number without any shift
        texts = ["Ash Wednesday 1930", "Ash Wednesday 1930", "and after, 1940"]
        result = ["译[Ash Wednesday]", "译[1930 again]", "译[and after, 1940]"]
        assert _shift_check(texts, result) is None

    def test_single_digit_runs_are_ignored(self):
        # "1" is on every page of every book
        texts = ["part 1 of it", "part 2 of it", "part 3 of it"]
        result = ["译[part]", "译[1 and 2]", "译[part]"]
        assert _shift_check(texts, result) is None

    def test_a_marker_token_is_not_numeric_evidence(self):
        # ⟦code12⟧ is ours, not the book's; a stray one is the marker check's
        # business, under its own pinned leniency
        texts = ["press ⟦code12⟧ now", "or ⟦code13⟧ instead", "then stop"]
        result = ["译[press now]", "译[or ⟦code12⟧ ⟦code13⟧]", "译[then stop]"]
        assert _shift_check(texts, result) is None


class TestShiftPoisonedBatchDoesNotReachTheBook:
    """End to end over the write path: the retry's text is what is written."""

    def _soup_and_units(self):
        from book_maker.loader.plan import DisplayResolver, partition_soup

        body = "".join(
            f"<h2>{line}</h2>" if line.startswith("III.") else f"<p>{line}</p>"
            for line in WASTE_LINES
        )
        soup = _soup(body)
        fp = partition_soup(soup, DisplayResolver([]), "wasteland.xhtml")
        assert [u.text for u in fp.units] == WASTE_LINES
        return soup, fp.units

    def test_the_clean_retry_is_what_lands_beside_each_original(self):
        soup, units = self._soup_and_units()
        texts = [u.text for u in units]
        loader = _marker_loader()
        fake = _MergingBatchFake(merge_at=len(texts))
        loader.translate_model = fake

        result = loader._translate_texts_aligned(texts, fake, units)
        for unit, t_text in zip(units, result):
            loader._insert_plan_translation(unit, t_text)

        # the fabricated filler never reached the markup
        assert FILLER not in soup.get_text()
        # and the shift is gone, not merely its residue: every translation
        # sits next to the line it translates, the heading included
        rendered = [e.get_text() for e in soup.find_all(["p", "h2"])]
        assert rendered == [
            text for line in WASTE_LINES for text in (line, f"译[{line}]")
        ]
        assert [h.get_text() for h in soup.find_all("h2")] == [
            "III. THE FIRE SERMON",
            "译[III. THE FIRE SERMON]",
        ]


# --------------------------------------------- §9 inline atomic markers

MARKER_RE = re.compile(r"⟦[a-z0-9]+⟧")


class TestInlineMarkers:
    def test_short_excluded_inline_no_longer_splits_the_sentence(self):
        fp = _partition("<p>Press <code>Ctrl+C</code> to stop it now.</p>")
        assert len(fp.units) == 1
        unit = fp.units[0]
        tokens = MARKER_RE.findall(unit.text)
        assert len(tokens) == 1
        assert tokens[0].startswith("⟦code")
        # the sentence around the marker is intact, in one unit
        assert "Press" in unit.text and "to stop it now." in unit.text
        # the unit knows its marker's source node
        assert list(unit.markers) == tokens
        assert unit.markers[tokens[0]].name == "code"

    def test_marker_carries_no_characters(self):
        # "Ctrl+C" stays accounted as skipped; the marker is placement only
        fp = _partition("<p>Press <code>Ctrl+C</code> to stop it now.</p>")
        assert fp.skipped["excluded-tag"] == len("Ctrl+C")
        assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
            fp.skipped.values()
        )

    def test_rendered_void_img_becomes_a_marker(self):
        fp = _partition('<p>see the picture <img src="x.png"/> right here</p>')
        assert len(fp.units) == 1
        tokens = MARKER_RE.findall(fp.units[0].text)
        assert len(tokens) == 1
        assert tokens[0].startswith("⟦img")

    def test_long_excluded_inline_keeps_the_barrier(self):
        from book_maker.loader.markers import INLINE_MARKER_MAX_CHARS

        # one unbroken alphabetic run is a word by the wordless rule, so the
        # prose cap applies here — see the wordless cases below for what does
        # not read as prose
        long_code = "x" * (INLINE_MARKER_MAX_CHARS + 10)
        fp = _partition(f"<p>Before the listing <code>{long_code}</code> after it.</p>")
        texts = [u.text for u in fp.units]
        assert len(fp.units) == 2
        assert not MARKER_RE.findall(" ".join(texts))

    def test_a_long_wordless_code_url_is_a_marker_not_a_barrier(self):
        # epub30-spec.epub: <code> URLs of 41-59 characters shattered one
        # sentence into four units, three of them 5-12 characters long
        url = "http://www.w3.org/TR/SVG11/feature#AnimationEventsAttribute"
        assert len(url) > 40
        fp = _partition(
            f"<p>The string <code>{url}</code> minus the animation "
            f"features is what applies here.</p>"
        )
        assert len(fp.units) == 1
        unit = fp.units[0]
        tokens = MARKER_RE.findall(unit.text)
        assert len(tokens) == 1 and tokens[0].startswith("⟦code")
        assert unit.text.startswith("The string ")
        assert unit.text.endswith("is what applies here.")
        # the URL never reaches the model, and comes back verbatim
        assert url not in unit.text
        assert unit.markers[tokens[0]].get_text() == url

    def test_a_spaced_formula_inline_is_a_marker(self):
        # linear-algebra.epub: MathML-adjacent formulas render as spaced-out
        # single characters, which the 40-char cap read as "too long"
        formula = "0 . 2 1 ( 9 6 0 ) + 2 4 6 3 = 2 6 6 4 . 6 0"
        assert len(formula) > 40
        fp = _partition(
            f"<p>The resulting daily profit is "
            f'<span class="math">{formula}</span> and that is a '
            f"pleasant surprise.</p>",
            exclude_tags=("span",),
        )
        assert len(fp.units) == 1
        unit = fp.units[0]
        tokens = MARKER_RE.findall(unit.text)
        assert len(tokens) == 1 and tokens[0].startswith("⟦span")
        assert unit.text.startswith("The resulting daily profit is ")
        assert unit.text.endswith("and that is a pleasant surprise.")
        assert unit.markers[tokens[0]].get_text() == formula

    def test_a_short_function_name_does_not_make_a_formula_prose(self):
        # "det" and "dim" are borrowed by mathematics; a three-letter rule
        # would have left these two linear-algebra formulas as barriers
        formula = "d ( det A ) ≤ m d ( A , 1 ) + m d ( A , 2 ) + … + m d ( A , n )"
        assert len(formula) > 40
        fp = _partition(
            f'<p>We know that <span class="math">{formula}</span> holds.</p>',
            exclude_tags=("span",),
        )
        assert len(fp.units) == 1
        assert len(MARKER_RE.findall(fp.units[0].text)) == 1

    def test_a_long_prose_inline_still_bars(self):
        prose = "minus the determinant of the leading submatrix"
        assert len(prose) > 40
        fp = _partition(f"<p>The value is <code>{prose}</code> in every case here.</p>")
        assert len(fp.units) == 2
        assert not MARKER_RE.findall(" ".join(u.text for u in fp.units))

    def test_a_wordless_inline_past_the_wordless_cap_still_bars(self):
        from book_maker.loader.markers import INLINE_MARKER_WORDLESS_MAX_CHARS

        blob = "0 " * INLINE_MARKER_WORDLESS_MAX_CHARS
        fp = _partition(f"<p>The value is <code>{blob}</code> in every case.</p>")
        assert len(fp.units) == 2
        assert not MARKER_RE.findall(" ".join(u.text for u in fp.units))

    def test_combining_mark_scripts_are_words(self):
        """Devanagari prose read as wordless before marks were erased.

        Vowel signs and viramas are combining marks, not letters, so the
        raw scan saw runs of one or two letters in every Hindi word — a
        54-character sentence took the wordless cap (codex review 260906).
        """
        from book_maker.loader.markers import is_wordless

        assert not is_wordless("यह साहित्य की कहानियाँ हैं और इनका अनुवाद पढ़ना चाहिए।")
        # A zero-width non-joiner inside a word must not split it either.
        assert not is_wordless("किताब‌खाना पढ़ने की जगह है")
        # Arabic prose carries harakat the same way.
        assert not is_wordless("هذه قصة طويلة عن الكتب والمكتبات القديمة")
        # Erasing marks must not turn a real atom into prose.
        assert is_wordless("http://www.w3.org/TR/xml/#sec-references")

    def test_cjk_is_words_even_without_spaces(self):
        # CJK prose has no whitespace, so one 40-character token of it is a
        # sentence rather than an atom: the prose cap must still apply
        from book_maker.loader.markers import is_wordless

        assert not is_wordless("这是一段没有空格的中文句子，它当然是散文。")
        assert is_wordless("http://www.w3.org/TR/SVG11/feature#Animation")
        assert is_wordless("( − 1 . 0 4 ) ( 8 2 5 ) + 3 6 6 3 = 2 8 0 5")
        assert not is_wordless("minus the determinant")

    def test_br_is_still_a_barrier_not_a_marker(self):
        fp = _partition("<p>first line<br/>second line</p>")
        assert len(fp.units) == 2
        assert not MARKER_RE.findall(" ".join(u.text for u in fp.units))

    def test_marker_token_is_collision_safe(self):
        # the source text itself contains a marker-shaped literal; the
        # generated token must not be it — every marker token appears
        # exactly once in the sent text
        fp = _partition(
            "<p>the manual prints ⟦code1⟧ verbatim next to "
            "<code>rm -rf</code> here.</p>"
        )
        assert len(fp.units) == 1
        unit = fp.units[0]
        for token in unit.markers:
            assert unit.text.count(token) == 1


class TestReconcileMarkers:
    def test_faithful_reply_passes_through(self):
        from book_maker.loader.markers import reconcile_markers

        sent = "Press ⟦code1⟧ to stop it now."
        reply = "按 ⟦code1⟧ 立即停止。"
        assert reconcile_markers(sent, reply) == reply

    def test_dropped_marker_is_appended_never_fatal(self):
        from book_maker.loader.markers import reconcile_markers

        sent = "Press ⟦code1⟧ to stop it now."
        got = reconcile_markers(sent, "按下即可立即停止。")
        assert "按下即可立即停止。" in got
        assert got.count("⟦code1⟧") == 1
        assert got.rstrip().endswith("⟦code1⟧")

    def test_dropped_markers_append_in_source_order(self):
        from book_maker.loader.markers import reconcile_markers

        sent = "one ⟦code1⟧ two ⟦img2⟧ three"
        got = reconcile_markers(sent, "一二三")
        assert got.index("⟦code1⟧") < got.index("⟦img2⟧")

    def test_hallucinated_marker_is_stripped(self):
        from book_maker.loader.markers import reconcile_markers

        sent = "Press ⟦code1⟧ to stop it now."
        got = reconcile_markers(sent, "按 ⟦code1⟧⟦img7⟧ 停止。")
        assert "⟦img7⟧" not in got
        assert got.count("⟦code1⟧") == 1


# ------------------------------------------- --language TAG:NAME parsing


class TestLanguageSpec:
    def test_a_colon_splits_the_tag_from_the_name(self):
        from book_maker.utils import parse_language_spec

        spec = parse_language_spec("zh-hant:Traditional Chinese")
        assert (spec.tag, spec.name, spec.pinned) == (
            "zh-hant",
            "Traditional Chinese",
            True,
        )

    def test_a_bare_tag_resolves_its_name_and_keeps_itself(self):
        """Retires the `spec.tag == "zh"` pin: a typed tag is no longer
        round-tripped through its name, and `zh-hans` owns that name now."""
        from book_maker.utils import parse_language_spec

        spec = parse_language_spec("zh-hans")
        assert (spec.tag, spec.name, spec.pinned) == (
            "zh-hans",
            "simplified chinese",
            False,
        )

    def test_target_slug_reaches_the_schema_field(self):
        from book_maker.translator.chatgptapi_translator import (
            single_field_name,
        )

        assert single_field_name("zh-hant") == "zh_hant_translation"
