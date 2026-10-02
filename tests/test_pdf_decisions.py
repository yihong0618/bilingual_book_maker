"""Region roles decided by a vision model (`book_maker/pipeline/decisions.py`).

PIN (lead 260923, packet E2; Codex consult
docs/260923-eval-CODEX_CONSULT_STRUCTURE_DECISION_CLIENT.md, sections 2,
2b, 3): a role change is a typed replacement at the same `texts` slot,
never a bare `.label` write -- docling-core's Markdown serializer
dispatches on the class, so a label-only change exports differently fresh
and after a JSON round trip. Answers are validated one by one, a
duplicate key rejects the reply, an unknown id is a protocol violation, a
page's accepted changes go in atomically, and a page on which most asked
items change keeps its changes and is called out (lead ruling 260923,
docs/260923-feat-PDF_ROLE_DECISIONS.md: the quarantine that stood here
withheld the one right repair it ever met, and helped nowhere).

Every document here is a real `DoclingDocument`; the model is a fake
`ask`. No network, no models.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

docling_core = pytest.importorskip("docling_core.types.doc")

from docling_core.types.doc import (  # noqa: E402
    BoundingBox,
    ContentLayer,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from docling_core.types.doc.document import (  # noqa: E402
    CodeItem,
    SectionHeaderItem,
    TextItem,
    TitleItem,
)

from book_maker.pipeline import decisions, pdf_formula, pdf_headings  # noqa: E402
from book_maker.pipeline.messages import STRUCTURE_PARTIAL  # noqa: E402
from pipeline_helpers import write_pdf  # noqa: E402

WIDTH, HEIGHT = 612.0, 792.0


def prov(page=1, top=700.0, left=72.0, right=400.0, height=14.0):
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(
            l=left,
            t=top,
            r=right,
            b=top - height,
            coord_origin=CoordOrigin.BOTTOMLEFT,
        ),
        charspan=(0, 1),
    )


def new_document(pages=1):
    document = DoclingDocument(name="roles")
    for number in range(1, pages + 1):
        document.add_page(page_no=number, size=Size(width=WIDTH, height=HEIGHT))
    return document


def by_text(document, text):
    return next(item for item in document.texts if item.text == text)


def listed(prompt):
    """The region list the prompt carries, as the model reads it."""
    assert prompt.startswith(decisions.PROMPT)
    return json.loads(prompt[len(decisions.PROMPT) :])


def fake_ask(answers, log=None):
    """An `ask` answering by the region's text; `answers[text]` is the label."""

    def ask(prompt, schema, image_png, deadline=None):
        assert image_png.startswith(b"\x89PNG")
        regions = listed(prompt)
        if log is not None:
            log.append({"regions": regions, "schema": schema})
        return {
            str(region["id"]): answers[region["text_head"]]
            for region in regions
            if region["text_head"] in answers
        }

    return ask


@pytest.fixture
def pdf(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    return write_pdf(tmp_path / "page.pdf", ["A line of prose.", "Second page."])


def snapshot(document):
    """What must not move: every item's ref, class, text, prov, in order."""
    return [
        (
            item.self_ref,
            item.parent.cref if item.parent else None,
            [child.cref for child in item.children],
            item.text,
            [p.model_dump() for p in item.prov],
        )
        for item in document.texts
    ]


def reading_order(document):
    return [item.self_ref for item, _level in document.iterate_items()]


# --------------------------------------------------------------------------
# Which regions are asked about
# --------------------------------------------------------------------------
def eligibility_document():
    document = new_document(pages=2)
    document.add_text(label=DocItemLabel.TEXT, text="Body prose.", prov=prov(top=760))
    document.add_heading(text="1 Introduction", prov=prov(top=740))
    group = document.add_list_group()
    document.add_list_item(text="A list item.", parent=group, prov=prov(top=720))
    document.add_text(
        label=DocItemLabel.TEXT, text="bbm-formula-0000", prov=prov(top=700)
    )
    document.add_formula(text="", prov=prov(top=690))
    caption = document.add_text(
        label=DocItemLabel.CAPTION, text="Figure 1: owned.", prov=prov(top=600)
    )
    document.add_picture(caption=caption, prov=prov(top=660, height=50))
    document.add_text(
        label=DocItemLabel.CAPTION, text="A free caption.", prov=prov(top=580)
    )
    table = document.add_table(
        data=TableData(num_rows=1, num_cols=1, table_cells=[]), prov=prov(top=560)
    )
    document.add_text(
        label=DocItemLabel.TEXT, text="In a cell.", parent=table, prov=prov(top=550)
    )
    document.add_text(
        label=DocItemLabel.PAGE_HEADER,
        text="Running head",
        content_layer=ContentLayer.FURNITURE,
        prov=prov(top=780),
    )
    spanning = document.add_text(
        label=DocItemLabel.TEXT, text="Runs over the page.", prov=prov(top=100)
    )
    spanning.prov.append(prov(page=2, top=760))
    document.add_text(
        label=DocItemLabel.FOOTNOTE, text="A page-two note.", prov=prov(page=2, top=60)
    )
    return document


def test_only_plain_text_like_body_items_are_asked_about():
    document = eligibility_document()
    one = decisions.eligible(document, 1)
    assert [c.text_head for c in one] == [
        "Body prose.",
        "1 Introduction",
        "A free caption.",
        "Runs over the page.",
    ]
    assert [c.label for c in one] == ["text", "section_header", "caption", "text"]
    # The id is the region's number on the overlay, and every region of
    # the page is drawn -- the ones not asked about tagged `x`.
    drawn = decisions.regions(document, 1)
    tags = {r.tag for r in drawn}
    assert {str(c.id) for c in one} <= tags
    excluded = {
        "list_item",
        "formula",
        "picture",
        "table",
        "page_header",
    }
    assert excluded <= {r.label for r in drawn if r.tag.startswith("x")}
    marker = next(
        r
        for r in drawn
        if r.label == "text" and r.tag.startswith("x") and r.bbox[3] == 700
    )
    assert marker  # the formula marker is drawn, not asked
    # the owned caption and the table's cell are context only
    owned = by_text(document, "Figure 1: owned.")
    cell = by_text(document, "In a cell.")
    assert owned.self_ref not in {c.ref for c in one}
    assert cell.self_ref not in {c.ref for c in one}


def test_an_item_over_two_pages_is_asked_on_its_first_page_only():
    document = eligibility_document()
    two = decisions.eligible(document, 2)
    assert [c.text_head for c in two] == ["A page-two note."]
    # but it is drawn on page two for context
    spanning = by_text(document, "Runs over the page.")
    assert any(
        r.tag.startswith("x") and r.bbox == (72.0, 746.0, 400.0, 760.0)
        for r in decisions.regions(document, 2)
    )
    assert spanning.self_ref in {c.ref for c in decisions.eligible(document, 1)}


# --------------------------------------------------------------------------
# The question
# --------------------------------------------------------------------------
def test_the_schema_has_one_required_property_per_candidate():
    document = eligibility_document()
    candidates = decisions.eligible(document, 1)
    wrapped = decisions.schema(candidates)
    assert wrapped["strict"] is True
    body = wrapped["schema"]
    assert body["additionalProperties"] is False
    assert body["required"] == [str(c.id) for c in candidates]
    assert set(body["properties"]) == {str(c.id) for c in candidates}
    for c in candidates:
        enum = body["properties"][str(c.id)]["enum"]
        assert set(enum) == decisions.ALLOWED[c.label] | {c.label, "abstain"}
        assert len(enum) == len(set(enum))
    caption = next(c for c in candidates if c.label == "caption")
    assert set(body["properties"][str(caption.id)]["enum"]) == {
        "text",
        "footnote",
        "caption",
        "abstain",
    }


def test_the_prompt_is_the_lead_s_text_then_the_regions_as_json():
    document = eligibility_document()
    candidates = decisions.eligible(document, 1)
    text = decisions.prompt(candidates)
    assert text.endswith("]")
    assert listed(text) == [
        {"id": c.id, "docling_label": c.label, "text_head": c.text_head}
        for c in candidates
    ]


# --------------------------------------------------------------------------
# The answer
# --------------------------------------------------------------------------
def three_candidates():
    return [
        decisions.Candidate(0, "#/texts/0", "text", "a", (0, 0, 1, 1), 1),
        decisions.Candidate(1, "#/texts/1", "footnote", "b", (0, 0, 1, 1), 1),
        decisions.Candidate(2, "#/texts/2", "caption", "c", (0, 0, 1, 1), 1),
    ]


def statuses(parsed):
    return {str(d["id"]): d["status"] for d in parsed}


def test_a_duplicate_key_rejects_the_whole_reply():
    with pytest.raises(decisions.ReplyRejected):
        decisions.parse_reply(
            '{"0": "code", "1": "text", "0": "text", "2": "text"}',
            three_candidates(),
        )


def test_a_parsed_reply_that_carries_its_text_is_checked_for_duplicates():
    class Parsed(dict):
        raw = '{"0": "code", "0": "text"}'

    with pytest.raises(decisions.ReplyRejected):
        decisions.parse_reply(Parsed({"0": "text"}), three_candidates())


def test_each_answer_is_judged_on_its_own():
    parsed = decisions.parse_reply(
        {"0": "section_header", "1": "banana", "2": "code", "7": "title"},
        three_candidates(),
    )
    assert statuses(parsed) == {
        "0": "accepted",
        "1": "invalid",  # not a label at all
        "2": "disallowed",  # a label, but not one a caption may become
        "7": "protocol_violation",
    }
    missing = decisions.parse_reply(
        {"0": "abstain", "1": "footnote"}, three_candidates()
    )
    assert statuses(missing) == {"0": "kept", "1": "kept", "2": "unanswered"}


def test_text_that_is_not_json_is_rejected():
    with pytest.raises(decisions.ReplyRejected):
        decisions.parse_reply("I think 0 is a title.", three_candidates())
    with pytest.raises(decisions.ReplyRejected):
        decisions.parse_reply(["0", "title"], three_candidates())


# --------------------------------------------------------------------------
# Applying it: the export is what counts
# --------------------------------------------------------------------------
def export_document():
    document = new_document()
    document.add_text(
        label=DocItemLabel.TEXT, text="1 Introduction", prov=prov(top=740)
    )
    document.add_text(
        label=DocItemLabel.TEXT, text="Some prose here.", prov=prov(top=720)
    )
    document.add_text(label=DocItemLabel.FOOTNOTE, text="import os", prov=prov(top=700))
    document.add_heading(text="Amy Pavel", prov=prov(top=680))
    document.add_formula(text="", prov=prov(top=640, height=30))
    document.add_picture(prov=prov(top=600, height=100))
    document.add_text(
        label=DocItemLabel.TEXT, text="Closing prose.", prov=prov(top=300)
    )
    return document


ROLES = {
    "1 Introduction": "section_header",
    "import os": "code",
    "Amy Pavel": "text",
    "Some prose here.": "text",
    "Closing prose.": "text",
}


def no_styles(monkeypatch):
    monkeypatch.setattr(
        pdf_headings, "styles", lambda pdf_path, boxes: {index: None for index in boxes}
    )


def test_accepted_roles_reach_the_exported_markdown(pdf, monkeypatch):
    """The constraining test: the real serializer must write the new roles.

    A label-only change would pass every other assertion here and fail
    this one (a text relabelled section_header still exports as prose).
    """
    document = export_document()
    before, order = snapshot(document), reading_order(document)
    overlay = decisions.decide_roles(fake_ask(ROLES), document, pdf, [1])
    assert overlay.totals["accepted"] == 3
    applied = decisions.apply(document, overlay)
    assert applied.count == 3
    assert applied.transitions == {
        "text->section_header": 1,
        "footnote->code": 1,
        "section_header->text": 1,
    }
    # texts, provenance, refs and reading order untouched by the change
    assert snapshot(document) == before
    assert reading_order(document) == order
    regions = pdf_formula.mark(document)
    no_styles(monkeypatch)
    pdf_headings.assign(document, pdf)
    markdown = pdf_headings.promote(document.export_to_markdown())

    assert "## 1 Introduction" in markdown.splitlines()
    assert "```\nimport os\n```" in markdown
    assert "Amy Pavel" in markdown.splitlines()
    assert "# Amy Pavel" not in markdown
    # the formula marker and the picture are where they were
    assert len(regions) == 1
    assert f"$${pdf_formula.MARKER.format(index=0)}$$" in markdown
    assert "<!-- image -->" in markdown
    assert type(by_text(document, "1 Introduction")) is SectionHeaderItem
    assert type(by_text(document, "import os")) is CodeItem
    assert type(by_text(document, "Amy Pavel")) is TextItem


def test_the_applied_document_exports_the_same_after_a_round_trip(pdf):
    document = export_document()
    overlay = decisions.decide_roles(fake_ask(ROLES), document, pdf, [1])
    decisions.apply(document, overlay)
    fresh = document.export_to_markdown()
    reloaded = DoclingDocument.model_validate(document.model_dump())
    assert reloaded.export_to_markdown() == fresh
    assert [type(t) for t in reloaded.texts] == [type(t) for t in document.texts]


def test_a_title_is_a_title_item_and_a_demoted_title_is_prose(pdf):
    document = new_document()
    document.add_heading(text="The Paper", prov=prov(top=760))
    document.add_title(text="Not the title", prov=prov(top=700))
    overlay = decisions.decide_roles(
        fake_ask({"The Paper": "title", "Not the title": "section_header"}),
        document,
        pdf,
        [1],
    )
    assert decisions.apply(document, overlay).count == 2
    assert type(by_text(document, "The Paper")) is TitleItem
    assert type(by_text(document, "Not the title")) is SectionHeaderItem
    assert document.export_to_markdown().startswith("# The Paper")


def test_a_mixed_reply_changes_exactly_the_one_valid_item(pdf):
    document = new_document()
    for text, top in (("alpha", 760), ("beta", 740), ("gamma", 720), ("delta", 700)):
        document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(top=top))
    ids = {c.text_head: str(c.id) for c in decisions.eligible(document, 1)}

    def ask(prompt, schema, image_png, deadline=None):
        return {
            ids["alpha"]: "footnote",  # valid change
            ids["beta"]: "banana",  # invalid
            "99": "title",  # unknown id
            ids["gamma"]: "abstain",
            ids["delta"]: "text",
        }

    before = [type(t) for t in document.texts], [t.label for t in document.texts]
    overlay = decisions.decide_roles(ask, document, pdf, [1])
    applied = decisions.apply(document, overlay)
    assert applied.count == 1
    assert statuses(overlay.pages[1]["decisions"]) == {
        ids["alpha"]: "accepted",
        ids["beta"]: "invalid",
        "99": "protocol_violation",
        ids["gamma"]: "kept",
        ids["delta"]: "kept",
    }
    labels = [t.label for t in document.texts]
    assert labels == [DocItemLabel.FOOTNOTE] + before[1][1:]
    assert overlay.totals["rejected"] == 2


def test_a_disallowed_transition_is_recorded_and_not_applied(pdf):
    document = new_document()
    document.add_text(label=DocItemLabel.CAPTION, text="Listing 1:", prov=prov(top=760))
    for text, top in (("one", 740), ("two", 720)):
        document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(top=top))
    overlay = decisions.decide_roles(
        fake_ask({"Listing 1:": "code", "one": "text", "two": "text"}),
        document,
        pdf,
        [1],
    )
    [decision] = [d for d in overlay.pages[1]["decisions"] if d["from"] == "caption"]
    assert decision["status"] == "disallowed"
    applied = decisions.apply(document, overlay)
    assert applied.count == 0
    assert type(document.texts[0]) is TextItem
    assert document.texts[0].label == DocItemLabel.CAPTION


def test_apply_checks_the_policy_again_even_on_an_edited_overlay(pdf):
    document = new_document()
    document.add_text(label=DocItemLabel.CAPTION, text="Listing 1:", prov=prov(top=760))
    overlay = decisions.Overlay(
        pages={
            1: {
                "status": "asked",
                "calls": [],
                "decisions": [
                    {
                        "id": 0,
                        "ref": "#/texts/0",
                        "from": "caption",
                        "to": "code",
                        "status": "accepted",
                    }
                ],
            }
        }
    )
    applied = decisions.apply(document, overlay)
    assert applied.count == 0 and applied.disallowed == 1
    assert overlay.pages[1]["decisions"][0]["status"] == "disallowed"
    assert document.texts[0].label == DocItemLabel.CAPTION


def test_a_page_where_most_regions_change_keeps_them_and_is_called_out(pdf):
    """PIN (lead ruling 260923): no quarantine; the changes stand and the
    page is marked `high_change` above `CHANGE_RATE_WARN` (0.6, a guess)."""
    document = new_document()
    for text, top in (("a", 760), ("b", 740), ("c", 720), ("d", 700)):
        document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(top=top))
    overlay = decisions.decide_roles(
        fake_ask({"a": "footnote", "b": "footnote", "c": "caption", "d": "text"}),
        document,
        pdf,
        [1],
    )
    applied = decisions.apply(document, overlay)
    assert applied.count == 3
    assert [t.label for t in document.texts] == [
        DocItemLabel.FOOTNOTE,
        DocItemLabel.FOOTNOTE,
        DocItemLabel.CAPTION,
        DocItemLabel.TEXT,
    ]
    page = overlay.pages[1]
    assert sorted(d["status"] for d in page["decisions"]) == [
        "accepted",
        "accepted",
        "accepted",
        "kept",
    ]
    assert (page["asked"], page["changed"], page["high_change"]) == (4, 3, True)
    assert page["histogram"] == {"text->footnote": 2, "text->caption": 1}
    assert overlay.totals["high_change_pages"] == 1
    assert "quarantined" not in overlay.totals


def test_a_page_at_the_warning_share_is_not_called_out(pdf):
    document = new_document()
    for index in range(5):
        document.add_text(
            label=DocItemLabel.TEXT, text=f"t{index}", prov=prov(top=760 - 20 * index)
        )
    overlay = decisions.decide_roles(
        fake_ask({"t0": "footnote", "t1": "footnote", "t2": "code"}),
        document,
        pdf,
        [1],
    )
    decisions.apply(document, overlay)
    page = overlay.pages[1]
    # 3 of 5 is exactly 0.6: not more than it
    assert (page["asked"], page["changed"], page["high_change"]) == (5, 3, False)


def test_a_replacement_that_fails_leaves_the_page_as_it_was(pdf, monkeypatch):
    document = new_document()
    for text, top in (("a", 760), ("b", 740), ("c", 720), ("d", 700)):
        document.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(top=top))
    overlay = decisions.decide_roles(
        fake_ask({"a": "footnote", "b": "code", "c": "text", "d": "text"}),
        document,
        pdf,
        [1],
    )
    assert overlay.status() == decisions.STATUS_COMPLETE
    before = document.model_dump()
    items = list(document.texts)
    real = decisions._replacement
    calls = []

    def second_fails(item, target):
        calls.append(item.self_ref)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(item, target)

    monkeypatch.setattr(decisions, "_replacement", second_fails)
    applied = decisions.apply(document, overlay)
    assert len(calls) == 2  # the first one did go in before the second failed
    assert applied.count == 0 and applied.failed_pages == [1]
    assert overlay.pages[1]["status"] == "failed_apply"
    assert document.model_dump() == before
    assert all(a is b for a, b in zip(document.texts, items))
    assert overlay.totals["failed_apply_pages"] == 1
    assert overlay.status() == decisions.STATUS_PARTIAL


# --------------------------------------------------------------------------
# The pass: batches, budget, record
# --------------------------------------------------------------------------
def test_a_crowded_page_is_asked_in_batches_over_one_image(pdf):
    document = new_document()
    for index in range(5):
        document.add_text(
            label=DocItemLabel.TEXT,
            text=f"line {index}",
            prov=prov(top=760 - 20 * index),
        )
    log = []
    overlay = decisions.decide_roles(
        fake_ask({f"line {i}": "text" for i in range(5)}, log),
        document,
        pdf,
        [1],
        batch_cap=2,
    )
    assert [len(entry["regions"]) for entry in log] == [2, 2, 1]
    for entry in log:
        ids = [str(region["id"]) for region in entry["regions"]]
        assert entry["schema"]["schema"]["required"] == ids
    assert len(overlay.pages[1]["calls"]) == 3
    assert overlay.totals["kept"] == 5


def test_a_spent_budget_leaves_the_remaining_pages_unasked(pdf):
    document = new_document(pages=2)
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov(page=1))
    document.add_text(label=DocItemLabel.TEXT, text="second", prov=prov(page=2))
    said = []
    overlay = decisions.decide_roles(
        fake_ask({"first": "text", "second": "footnote"}),
        document,
        pdf,
        [1, 2],
        budget=decisions.Budget(max_calls=1),
        log=said.append,
    )
    assert overlay.pages[1]["status"] == "asked"
    assert overlay.pages[2]["status"] == "unasked"
    assert overlay.totals["unasked_pages"] == 1
    assert overlay.totals["budget_exhausted"] == "max_calls"
    assert said and said[0].startswith(STRUCTURE_PARTIAL.split("{detail}")[0])
    assert "max_calls" in said[0] and "2" in said[0]
    assert decisions.apply(document, overlay).count == 0
    assert overlay.status() == decisions.STATUS_PARTIAL


def test_a_failed_question_is_recorded_and_the_pass_goes_on(pdf):
    document = new_document(pages=2)
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov(page=1))
    document.add_text(label=DocItemLabel.TEXT, text="1 Intro", prov=prov(page=2))
    document.add_text(label=DocItemLabel.TEXT, text="prose", prov=prov(page=2, top=600))

    def ask(prompt, schema, image_png, deadline=None):
        regions = listed(prompt)
        if regions[0]["text_head"] == "first":
            raise decisions.AskFailed("the endpoint refused the image")
        return {str(regions[0]["id"]): "section_header", str(regions[1]["id"]): "text"}

    overlay = decisions.decide_roles(ask, document, pdf, [1, 2])
    assert overlay.pages[1]["decisions"][0]["status"] == "unanswered"
    assert "refused the image" in overlay.pages[1]["calls"][0]["error"]
    assert overlay.pages[2]["decisions"][0]["status"] == "accepted"
    assert overlay.status() == decisions.STATUS_PARTIAL


def test_a_reply_rejected_whole_makes_the_pass_partial(pdf):
    document = new_document()
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov())

    def ask(prompt, schema, image_png, deadline=None):
        raise decisions.ReplyRejected("the reply is not a JSON object")

    overlay = decisions.decide_roles(ask, document, pdf, [1])
    assert overlay.pages[1]["decisions"][0]["status"] == "unanswered"
    assert overlay.status() == decisions.STATUS_PARTIAL


def test_every_batch_answered_is_a_complete_pass(pdf):
    document = new_document(pages=2)
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov(page=1))
    document.add_text(label=DocItemLabel.TEXT, text="second", prov=prov(page=2))
    overlay = decisions.decide_roles(
        fake_ask({"first": "text", "second": "footnote"}), document, pdf, [1, 2]
    )
    decisions.apply(document, overlay)
    assert overlay.status() == decisions.STATUS_COMPLETE


class FakeClock:
    """`time.monotonic` that moves only when tenacity sleeps."""

    def __init__(self, now=1000.0):
        self.now = now
        self.naps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.now += seconds


def vision_structure(create):
    """The route's `StructureRequest` over a real `ChatGPTAPI` whose
    client's `create` is `create`."""
    from book_maker.pipeline.docling_parser import _structure_ask
    from book_maker.translator.base_translator import UsageMeter
    from book_maker.translator.capabilities import CapabilityLedger
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "vision-model"
    translator.extra_body = {}
    translator.capabilities = CapabilityLedger()
    translator.capabilities.verdicts["vision-model"] = "strict"
    # what the extraction's `vision()` left in the ledger before the pass
    # ran: every question is asked through the Classifier's image gate
    translator.capabilities.vision["vision-model"] = "verified"
    translator._rung_refusals = {}
    translator.usage = UsageMeter()
    translator.openai_client = type(
        "Client",
        (),
        {
            "chat": type(
                "Chat",
                (),
                {"completions": type("C", (), {"create": staticmethod(create)})},
            )
        },
    )()
    from book_maker.endpoints import EndpointChoice

    choice = EndpointChoice("vision-model", "", "k", "openai", "cli")
    return _structure_ask(choice, None, translator=translator)


def test_each_question_is_given_the_pass_s_deadline(pdf, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(decisions.time, "monotonic", clock)
    document = new_document()
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov())
    seen = []

    def ask(prompt, schema, image_png, deadline=None):
        seen.append(deadline)
        return {str(listed(prompt)[0]["id"]): "text"}

    decisions.decide_roles(
        ask, document, pdf, [1], budget=decisions.Budget(max_seconds=120)
    )
    assert seen == [1120.0]
    decisions.decide_roles(ask, document, pdf, [1], budget=decisions.Budget())
    assert seen[-1] is None


def test_an_outage_ends_the_question_at_the_deadline_and_the_pass_returns(
    pdf, monkeypatch
):
    # PIN (lead 260923, Codex review of E2): the translator's patient
    # retries have no attempt cap (owner ruling 260907), so a question is
    # bounded by the pass's time budget instead: at the deadline the last
    # transport error comes back, the batch is recorded unanswered and the
    # pass returns -- it does not hang the extraction.
    import httpx
    from openai import APIConnectionError

    from book_maker.pipeline.messages import STRUCTURE_DETAIL_DEADLINE

    clock = FakeClock()
    monkeypatch.setattr("time.monotonic", clock)
    monkeypatch.setattr("tenacity.nap.time.sleep", clock.sleep)
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    attempts = []

    def create(**kwargs):
        attempts.append(clock.now)
        raise APIConnectionError(request=request)

    structure = vision_structure(create)

    document = new_document(pages=2)
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov(page=1))
    document.add_text(label=DocItemLabel.TEXT, text="second", prov=prov(page=2))
    said = []
    overlay = decisions.decide_roles(
        structure.ask,
        document,
        pdf,
        [1, 2],
        budget=decisions.Budget(max_seconds=120),
        log=said.append,
    )

    # retried, patiently, and never past the deadline
    assert len(attempts) > 3
    assert clock.now == 1120.0 and attempts[-1] == 1120.0
    assert max(clock.naps) <= 120
    # the question ended as a recorded failure, not an exception
    [call] = overlay.pages[1]["calls"]
    prefix = STRUCTURE_DETAIL_DEADLINE.split("{error}")[0]
    assert call["error"].startswith("AskFailed: " + prefix)
    assert "APIConnectionError" in call["error"]
    assert [d["status"] for d in overlay.pages[1]["decisions"]] == ["unanswered"]
    # the budget is spent: the next page is not asked, and the operator hears
    assert overlay.pages[2]["status"] == "unasked"
    assert overlay.totals["budget_exhausted"] == "max_seconds"
    assert said and "max_seconds" in said[0] and "2" in said[0]
    assert overlay.status() == decisions.STATUS_PARTIAL


def test_an_auth_error_still_ends_the_pass_at_once(pdf, monkeypatch):
    import httpx
    from openai import AuthenticationError

    clock = FakeClock()
    monkeypatch.setattr("time.monotonic", clock)
    monkeypatch.setattr("tenacity.nap.time.sleep", clock.sleep)
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    attempts = []

    def create(**kwargs):
        attempts.append(clock.now)
        raise AuthenticationError(
            "bad key", response=httpx.Response(401, request=request), body=None
        )

    structure = vision_structure(create)
    document = new_document()
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov())

    with pytest.raises(AuthenticationError):
        decisions.decide_roles(
            structure.ask,
            document,
            pdf,
            [1],
            budget=decisions.Budget(max_seconds=120),
        )
    assert attempts == [1000.0] and clock.naps == []


def test_an_error_that_is_not_about_the_question_ends_the_pass(pdf):
    document = new_document()
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov())

    class AuthenticationError(Exception):
        pass

    def ask(prompt, schema, image_png, deadline=None):
        raise AuthenticationError("bad key")

    with pytest.raises(AuthenticationError):
        decisions.decide_roles(ask, document, pdf, [1])


def test_the_call_s_model_usage_and_latency_are_recorded(pdf):
    document = new_document()
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov())

    class Reply(dict):
        model = "vision-1"
        usage = {"prompt_tokens": 1200, "completion_tokens": 30}

    overlay = decisions.decide_roles(
        lambda p, s, i, deadline=None: Reply({str(listed(p)[0]["id"]): "text"}),
        document,
        pdf,
        [1],
        model="asked-for",
    )
    [call] = overlay.pages[1]["calls"]
    assert call["model"] == "vision-1"
    assert call["usage"] == {"prompt_tokens": 1200, "completion_tokens": 30}
    assert call["latency_s"] >= 0
    assert overlay.totals["prompt_tokens"] == 1200
    assert overlay.model == "asked-for"


def test_the_overlay_round_trips_through_json(pdf):
    document = export_document()
    overlay = decisions.decide_roles(fake_ask(ROLES), document, pdf, [1], model="m")
    decisions.apply(document, overlay)
    again = decisions.Overlay.from_json(overlay.to_json())
    assert again == overlay
    assert again.prompt_rev == decisions.PROMPT_REV
    assert again.policy_rev == decisions.POLICY_REV
    assert set(again.pages) == {1}


# --------------------------------------------------------------------------
# The picture
# --------------------------------------------------------------------------
def test_the_overlay_box_lands_on_the_glyphs_of_a_turned_page(tmp_path):
    """Same frame as `pdf_formula.crop` and `pdf_headings.styles`: docling's
    box on a /Rotate 90 page is in the displayed frame (measured 260922,
    test_pdf_headings), and the overlay must draw it on the text there."""
    pdfium = pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    from io import BytesIO

    from PIL import Image

    source = write_pdf(tmp_path / "flat.pdf", ["A heading line"])
    document = pdfium.PdfDocument(str(source))
    document[0].set_rotation(90)
    pdf = tmp_path / "turned.pdf"
    document.save(str(pdf))
    document.close()
    page = pdfium.PdfDocument(str(pdf))[0]
    width, height = page.get_size()
    assert (width, height) == (792.0, 612.0)
    clean = page.render(scale=2).to_pil().convert("L")

    def ink(box):
        l, b, r, t = box
        window = clean.crop(
            (int(l * 2), int((height - t) * 2), int(r * 2), int((height - b) * 2))
        )
        return sum(1 for v in window.get_flattened_data() if v < 128)

    total = sum(1 for v in clean.get_flattened_data() if v < 128)
    turned = (690.0, 380.0, 720.0, 545.0)
    assert total > 0
    assert ink(turned) == total  # every glyph is inside the docling box

    png = decisions.overlay_png(
        pdf, 1, [decisions.Region("0", "text", turned)], scale=2
    )
    drawn = Image.open(BytesIO(png)).convert("RGB")
    assert drawn.size == clean.size
    # the box's left edge, halfway down, is drawn in the text colour
    x0 = int(turned[0] * 2)
    mid = int((height - (turned[1] + turned[3]) / 2) * 2)
    assert drawn.getpixel((x0, mid)) == decisions.COLOURS["text"]


def test_a_stalled_request_ends_at_the_deadline_too(pdf, monkeypatch):
    # PIN (lead 260923, Codex re-verification of E2): the deadline bounds
    # the call in flight, not only the waits between calls -- each attempt
    # is sent with the seconds left as its timeout, so an endpoint that
    # accepts the request and never answers costs the budget, not the run.
    import httpx
    from openai import APITimeoutError

    from book_maker.pipeline.messages import STRUCTURE_DETAIL_DEADLINE

    clock = FakeClock()
    monkeypatch.setattr("time.monotonic", clock)
    monkeypatch.setattr("tenacity.nap.time.sleep", clock.sleep)
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    timeouts = []

    def create(**kwargs):
        # a request that hangs: it runs for exactly as long as it may
        timeouts.append(kwargs["timeout"])
        clock.now += kwargs["timeout"]
        raise APITimeoutError(request=request)

    structure = vision_structure(create)
    document = new_document(pages=2)
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov(page=1))
    document.add_text(label=DocItemLabel.TEXT, text="second", prov=prov(page=2))
    overlay = decisions.decide_roles(
        structure.ask,
        document,
        pdf,
        [1, 2],
        budget=decisions.Budget(max_seconds=120),
        log=lambda line: None,
    )
    assert timeouts == [120.0]
    assert clock.now == 1120.0 and clock.naps == []
    [call] = overlay.pages[1]["calls"]
    prefix = STRUCTURE_DETAIL_DEADLINE.split("{error}")[0]
    assert call["error"].startswith("AskFailed: " + prefix)
    assert "APITimeoutError" in call["error"]
    assert overlay.pages[2]["status"] == "unasked"
    assert overlay.status() == decisions.STATUS_PARTIAL


def test_a_reply_that_leaves_an_id_out_is_a_partial_pass(pdf):
    document = new_document()
    document.add_text(label=DocItemLabel.TEXT, text="first", prov=prov(top=760))
    document.add_text(label=DocItemLabel.TEXT, text="second", prov=prov(top=700))
    overlay = decisions.decide_roles(fake_ask({"first": "text"}), document, pdf, [1])
    statuses = sorted(d["status"] for d in overlay.pages[1]["decisions"])
    assert statuses == ["kept", "unanswered"]
    assert not any(call.get("error") for call in overlay.pages[1]["calls"])
    assert overlay.status() == decisions.STATUS_PARTIAL
