"""Pinned vocabulary: file format, adaptive matching, prompt block, merge.

The matcher is the load-bearing part. Injecting a term that does not occur in
the unit wastes fresh tokens on every request in session mode, and missing one
that does occur silently drops the pin the user asked for.
"""

import pytest

from book_maker.glossary import Glossary, GlossaryEntry, GlossaryConflict


class TestParsing:
    def test_arrow_and_ascii_arrow(self):
        g = Glossary.parse("Winston → 温斯顿\nJulia -> 茱莉亚\n")
        assert [(e.term, e.translation) for e in g.entries] == [
            ("Winston", "温斯顿"),
            ("Julia", "茱莉亚"),
        ]

    def test_note_after_hash(self):
        g = Glossary.parse("Big Brother → 老大哥  # keep the irony\n")
        assert g.entries[0].note == "keep the irony"

    def test_comments_and_blank_lines_skipped(self):
        g = Glossary.parse("# a comment\n\nWinston → 温斯顿\n\n")
        assert len(g.entries) == 1

    def test_blank_input_is_empty_glossary(self):
        assert Glossary.parse("").entries == ()

    def test_malformed_line_fails_loud(self):
        with pytest.raises(ValueError) as e:
            Glossary.parse("Winston 温斯顿\n")
        assert "line 1" in str(e.value)

    def test_empty_side_fails_loud(self):
        with pytest.raises(ValueError):
            Glossary.parse("Winston → \n")

    def test_later_duplicate_wins_within_a_file(self):
        g = Glossary.parse("Winston → 温斯顿\nWinston → 溫斯頓\n")
        assert len(g.entries) == 1
        assert g.entries[0].translation == "溫斯頓"

    def test_from_file_roundtrip(self, tmp_path):
        p = tmp_path / "glossary.txt"
        p.write_text("Winston → 温斯顿\n", encoding="utf-8")
        assert Glossary.from_file(p).entries[0].term == "Winston"

    def test_missing_file_fails_loud(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Glossary.from_file(tmp_path / "nope.txt")


class TestMatching:
    def test_latin_respects_word_boundaries(self):
        g = Glossary.parse("Ann → 安\n")
        assert g.matches("Ann went home")
        assert not g.matches("Announcement pending")

    def test_latin_is_case_insensitive_by_default(self):
        g = Glossary.parse("Winston → 温斯顿\n")
        assert g.matches("winston smiled")

    def test_case_sensitive_entry_respects_case(self):
        g = Glossary((GlossaryEntry("IT", "信息技术", case_sensitive=True),))
        assert g.matches("the IT department")
        assert not g.matches("it was cold")

    def test_cjk_matches_as_substring(self):
        g = Glossary.parse("温斯顿 → Winston\n")
        assert g.matches("那天温斯顿回家了")

    def test_multiword_term(self):
        g = Glossary.parse("Big Brother → 老大哥\n")
        assert g.matches("Big Brother is watching")
        assert not g.matches("Brother Big is watching")

    def test_no_hits_returns_empty(self):
        g = Glossary.parse("Winston → 温斯顿\n")
        assert g.matches("nothing relevant here") == []

    def test_only_hits_are_returned(self):
        g = Glossary.parse("Winston → 温斯顿\nJulia → 茱莉亚\n")
        hits = g.matches("Winston alone")
        assert [h.term for h in hits] == ["Winston"]

    def test_regex_metacharacters_in_term_are_literal(self):
        g = Glossary.parse("C++ → C加加\n")
        assert g.matches("we wrote it in C++")
        assert not g.matches("we wrote it in Cxx")


class TestPromptBlock:
    def test_empty_when_no_hits(self):
        g = Glossary.parse("Winston → 温斯顿\n")
        assert g.prompt_block("nothing here") == ""

    def test_contains_only_hit_terms(self):
        g = Glossary.parse("Winston → 温斯顿\nJulia → 茱莉亚\n")
        block = g.prompt_block("Winston alone")
        assert "温斯顿" in block
        assert "茱莉亚" not in block

    def test_has_verbatim_instruction_and_tags(self):
        g = Glossary.parse("Winston → 温斯顿\n")
        block = g.prompt_block("Winston")
        assert "<glossary>" in block and "</glossary>" in block
        assert "verbatim" in block.lower()

    def test_note_is_rendered(self):
        g = Glossary.parse("Big Brother → 老大哥 # keep the irony\n")
        assert "keep the irony" in g.prompt_block("Big Brother")


class TestMerge:
    def test_pinned_wins_over_learned(self):
        pinned = Glossary.parse("Winston → 温斯顿\n")
        learned = Glossary.parse("Winston → 溫斯頓\n")
        merged, conflicts = pinned.merge(learned)
        assert merged.lookup("Winston").translation == "温斯顿"
        assert [c.term for c in conflicts] == ["Winston"]

    def test_conflict_records_both_sides(self):
        pinned = Glossary.parse("Winston → 温斯顿\n")
        learned = Glossary.parse("Winston → 溫斯頓\n")
        _, conflicts = pinned.merge(learned)
        assert conflicts[0].kept == "温斯顿"
        assert conflicts[0].dropped == "溫斯頓"

    def test_non_conflicting_entries_are_unioned(self):
        pinned = Glossary.parse("Winston → 温斯顿\n")
        learned = Glossary.parse("Julia → 茱莉亚\n")
        merged, conflicts = pinned.merge(learned)
        assert conflicts == []
        assert merged.lookup("Julia") is not None

    def test_identical_translation_is_not_a_conflict(self):
        a = Glossary.parse("Winston → 温斯顿\n")
        b = Glossary.parse("Winston → 温斯顿\n")
        _, conflicts = a.merge(b)
        assert conflicts == []


class TestJsonHandoff:
    def test_from_json_entries(self):
        g = Glossary.from_json(
            [{"term": "Winston", "translation": "温斯顿", "note": "protagonist"}]
        )
        assert g.entries[0].note == "protagonist"

    def test_from_json_skips_incomplete_rows(self):
        g = Glossary.from_json(
            [{"term": "Winston", "translation": "温斯顿"}, {"term": "x"}, {}]
        )
        assert len(g.entries) == 1

    def test_from_json_rejects_non_list(self):
        with pytest.raises(ValueError):
            Glossary.from_json({"term": "Winston"})

    def test_to_lines_roundtrips_through_parse(self):
        g = Glossary.parse("Winston → 温斯顿 # note\nJulia → 茱莉亚\n")
        assert Glossary.parse(g.to_lines()).entries == g.entries


class TestMixedScriptAndFolding:
    """Findings from the adversarial review."""

    def test_mixed_script_term_keeps_its_latin_boundary(self):
        g = Glossary.parse("AI模型 → AI model\n")
        assert g.matches("这个AI模型很好")
        # The term starts with a latin letter, so XAI模型 is a different word.
        assert not g.matches("这个XAI模型很好")

    def test_mixed_script_term_with_cjk_edge_still_matches_inside_text(self):
        g = Glossary.parse("模型AI → model AI\n")
        assert g.matches("那个模型AI跑得快")

    def test_supplementary_plane_cjk_matches_as_a_substring(self):
        g = Glossary.parse("\U00020000 → glyph\n")
        assert g.matches("甲\U00020000乙")

    def test_dedup_and_matching_agree_on_case(self):
        """Deduplicating more aggressively than matching would drop a pin."""
        g = Glossary.parse("Stra\u00dfe → 街道\nSTRASSE → 大街\n")
        for entry in g.entries:
            assert g.matches(entry.term), f"stored {entry.term!r} does not match itself"


class TestNonLatinBoundaries:
    """The boundary guard must work for every script, not just latin."""

    def test_cyrillic_term_is_not_matched_inside_a_longer_word(self):
        g = Glossary.parse("Анна → Anna\n")
        assert g.matches("Анна пришла")
        assert not g.matches("Жанна пришла")

    def test_greek_term_respects_boundaries(self):
        g = Glossary.parse("λόγος → word\n")
        assert g.matches("ο λόγος ήταν")
        assert not g.matches("ο λόγοσύνη ήταν")

    def test_latin_boundary_still_holds(self):
        g = Glossary.parse("Ann → 安\n")
        assert g.matches("Ann went home")
        assert not g.matches("Announcement pending")

    def test_mixed_script_still_matches_against_cjk_neighbours(self):
        g = Glossary.parse("AI模型 → AI model\n")
        assert g.matches("这个AI模型很好")
        assert not g.matches("这个XAI模型很好")
