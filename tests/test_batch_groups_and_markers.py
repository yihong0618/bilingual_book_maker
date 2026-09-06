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
- ``book_maker.utils.parse_language_pair`` for ``--language src:tgt``.
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

        long_code = "x" * (INLINE_MARKER_MAX_CHARS + 10)
        fp = _partition(f"<p>Before the listing <code>{long_code}</code> after it.</p>")
        texts = [u.text for u in fp.units]
        assert len(fp.units) == 2
        assert not MARKER_RE.findall(" ".join(texts))

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


# ----------------------------------------------- --language pair parsing


class TestLanguagePair:
    def test_pair_splits_on_colon(self):
        from book_maker.utils import parse_language_pair

        assert parse_language_pair("en:zh-hant") == ("en", "zh-hant")

    def test_bare_language_is_target_only(self):
        from book_maker.utils import parse_language_pair

        assert parse_language_pair("zh-hans") == (None, "zh-hans")

    def test_target_slug_reaches_the_schema_field(self):
        from book_maker.translator.chatgptapi_translator import (
            single_field_name,
        )

        assert single_field_name("zh-hant") == "zh_hant_translation"
