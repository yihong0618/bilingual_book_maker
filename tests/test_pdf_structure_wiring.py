"""`--img-model`: how the region-role pass is reached, and when not.

PIN (lead 260923, packet E2; packet F, owner 260923 22:30): the pass is
opt-in. Without an image model (the flag, else the provider entry's
img_model; never the run's own model) nothing of it is imported, built or
probed; with one, the model runs on the translation's endpoint and key, or
on `--img-base-url` with `--img-key`; an endpoint of another format is
refused before a page is read, an endpoint that cannot see a page image
leaves the detector's labels and says so, and the model, its base and the
prompt/policy revision are part of the extraction identity.

The model is a fake translator exposing the two names the pass uses
(`structured_json_with_image`, `vision_verdict` / `capabilities.
ensure_vision`); the conversion is docling's `_converter` stubbed with a
real `DoclingDocument`, so the decisions, the apply and the export are
real.
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import pandoc_or_skip, write_pdf  # noqa: E402

from book_maker import cli  # noqa: E402
from book_maker.pipeline import docling_parser, stages, to_epub  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.endpoints import (  # noqa: E402
    IMG_ENDPOINT_UNSUPPORTED,
    IMG_ENDPOINT_UNVERIFIED,
    EndpointChoice,
)
from book_maker.pipeline.messages import DEVICE_SELECTED  # noqa: E402
from book_maker.pipeline.pdf_settings import ExtractionSettings  # noqa: E402

REV = "260923a/260923b"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
def test_the_structure_model_and_revision_are_identity():
    plain = ExtractionSettings()
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert plain.identity()["structure"] is None
    assert luna.identity()["structure"] == "gpt-5.6-luna"
    assert luna.identity()["structure_rev"] == REV
    assert luna.identity() != plain.identity()
    other_model = ExtractionSettings(structure="gpt-5.6-sol", structure_rev=REV)
    other_rev = ExtractionSettings(structure="gpt-5.6-luna", structure_rev="x/y")
    assert len({str(s.identity()) for s in (luna, other_model, other_rev)}) == 3
    # a revision without a model changes nothing: no pass ran
    assert ExtractionSettings(structure_rev=REV).identity() == plain.identity()


def test_the_image_base_is_identity():
    """packet F: the same model id at another address may be another model."""
    run_s = ExtractionSettings(structure="m", structure_rev=REV)
    local = ExtractionSettings(
        structure="m", structure_rev=REV, structure_base="http://127.0.0.1:1/v1"
    )
    assert run_s.identity()["structure_base"] is None
    assert local.identity()["structure_base"] == "http://127.0.0.1:1/v1"
    assert run_s.identity() != local.identity()
    manifest = {"structure": "m", "structure_rev": REV, **local.identity()}
    assert ExtractionSettings.from_manifest(manifest).identity() == local.identity()
    # a base without a model changes nothing
    assert (
        ExtractionSettings(structure_base="http://x/v1").identity()
        == ExtractionSettings().identity()
    )


def test_from_manifest_reads_the_structure_back():
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    manifest = {"structure": "gpt-5.6-luna", "structure_rev": REV}
    assert ExtractionSettings.from_manifest(manifest).identity() == luna.identity()
    # a manifest from before the flag is a run without it
    assert ExtractionSettings.from_manifest({}).identity() == (
        ExtractionSettings().identity()
    )


# --------------------------------------------------------------------------
# A fake translator and a real document
# --------------------------------------------------------------------------
class FakeLedger:
    def __init__(self, verdict):
        self.verdict = verdict
        self.asked = []

    def ensure_vision(self, model, probe=None):
        self.asked.append(model)
        return self.verdict


class FakeUsage:
    prompt = completion = 0


class FakeVisionTranslator:
    """The two names the pass needs, and nothing else."""

    def __init__(self, answers, verdict="verified"):
        self.answers = answers
        self.capabilities = FakeLedger(verdict)
        self.usage = FakeUsage()
        self.calls = []

    def structured_json_with_image(
        self, prompt, schema, image_png, model=None, deadline=None
    ):
        from book_maker.pipeline import decisions

        self.calls.append(model)
        self.usage.prompt += 1000
        self.usage.completion += 20
        regions = json.loads(prompt[len(decisions.PROMPT) :])
        return {
            str(r["id"]): self.answers[r["text_head"]]
            for r in regions
            if r["text_head"] in self.answers
        }


def real_document(pages=1):
    docling = pytest.importorskip("docling_core.types.doc")
    d = docling
    document = d.DoclingDocument(name="wired")
    for number in range(1, pages + 1):
        document.add_page(page_no=number, size=d.Size(width=612.0, height=792.0))

    def prov(top, page=1):
        return d.ProvenanceItem(
            page_no=page,
            bbox=d.BoundingBox(
                l=72, t=top, r=400, b=top - 14, coord_origin=d.CoordOrigin.BOTTOMLEFT
            ),
            charspan=(0, 1),
        )

    document.add_text(label=d.DocItemLabel.TEXT, text="1 Introduction", prov=prov(740))
    document.add_text(label=d.DocItemLabel.TEXT, text="Prose one.", prov=prov(720))
    document.add_text(label=d.DocItemLabel.FOOTNOTE, text="import os", prov=prov(700))
    document.add_text(label=d.DocItemLabel.TEXT, text="Prose two.", prov=prov(680))
    document.add_text(label=d.DocItemLabel.TEXT, text="Prose three.", prov=prov(660))
    if pages >= 2:
        document.add_text(
            label=d.DocItemLabel.TEXT, text="Page two prose.", prov=prov(740, page=2)
        )
    return document


ANSWERS = {
    "1 Introduction": "section_header",
    "import os": "code",
    "Prose one.": "text",
    "Prose two.": "text",
    "Prose three.": "text",
}


@pytest.fixture
def real_pdf(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    return write_pdf(tmp_path / "paper.pdf", ["A line of prose."])


@pytest.fixture
def stub_docling(monkeypatch):
    """docling's converter replaced by one returning a real document."""
    state = {}

    def converter(device, settings, pdfium_page_images=False):
        state["document"] = real_document(state.get("pages", 1))

        class Converter:
            def convert(self, source, page_range=None):
                return types.SimpleNamespace(document=state["document"])

        return Converter()

    monkeypatch.setattr(docling_parser, "_converter", converter)
    # the heading glyph reader needs nothing from this fixture's page
    monkeypatch.setattr(
        docling_parser.pdf_headings,
        "styles",
        lambda pdf_path, boxes: {index: None for index in boxes},
    )
    return state


def request(translator, model="gpt-5.6-luna", base=""):
    choice = EndpointChoice(model, base, "k", "openai", "cli", own_base=bool(base))
    return docling_parser._structure_ask(choice, None, translator=translator)


def test_convert_writes_the_overlay_and_exports_the_new_roles(
    tmp_path, real_pdf, stub_docling
):
    translator = FakeVisionTranslator(ANSWERS)
    out_dir = tmp_path / "staging"
    out_dir.mkdir()
    report = {}
    markdown, _count, _warnings = docling_parser._convert(
        real_pdf,
        out_dir=out_dir,
        span=None,
        device="cpu",
        settings=ExtractionSettings(),
        report=report,
        structure=request(translator),
    )
    overlay = json.loads((out_dir / "decisions.json").read_text(encoding="utf-8"))
    assert overlay["prompt_rev"] == "260923a" and overlay["policy_rev"] == "260923b"
    assert overlay["model"] == "gpt-5.6-luna"
    [call] = overlay["pages"]["1"]["calls"]
    assert call["usage"] == {"prompt_tokens": 1000, "completion_tokens": 20}
    assert translator.calls == ["gpt-5.6-luna"]
    summary = report["structure"]
    assert summary["applied"] == 2 and summary["asked"] == 5
    # the export saw the corrected items; the snapshot did not
    # a lone bare number is level 2 in pdf_headings, lifted by one: `##`
    assert "## 1 Introduction" in markdown.splitlines()
    assert "```\nimport os\n```" in markdown
    raw = json.loads((out_dir / docling_parser.SNAPSHOT).read_text(encoding="utf-8"))
    assert [t["label"] for t in raw["texts"]][:3] == ["text", "text", "footnote"]


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(
        docling_parser,
        "resolve_device",
        lambda requested: ("cpu", DEVICE_SELECTED.format(device="cpu")),
    )


def test_a_verified_endpoint_runs_the_pass_and_the_manifest_says_so(
    tmp_path, real_pdf, stub_docling, device, capsys
):
    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    structure = request(FakeVisionTranslator(ANSWERS))
    stages.prepare(bundle, real_pdf, pandoc=pandoc, structure=structure, progress=False)
    out = " ".join(capsys.readouterr().out.split())
    assert (
        "Region roles: 2 of 5 asked items changed by gpt-5.6-luna (3 kept, "
        "0 rejected, 0 pages with many changes); overlay at "
        ".work/extraction/decisions.json." in out
    )
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["structure"] == "gpt-5.6-luna"
    assert extraction["structure_rev"] == REV
    assert extraction["structure_applied"] is True
    assert extraction["structure_status"] == "complete"
    assert extraction["structure_totals"]["applied"] == 2
    assert extraction["img_source"] == "cli"
    assert extraction["img_verdict"] == "verified"
    assert extraction["structure_base"] is None
    assert (bundle.work_file("extraction") / "decisions.json").is_file()
    source = bundle.source.read_text(encoding="utf-8")
    assert "```\nimport os\n```" in source
    # the same request is answered from the bundle; another model is not
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert stages.already_prepared(bundle, real_pdf, "docling", None, luna)
    sol = ExtractionSettings(structure="gpt-5.6-sol", structure_rev=REV)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None, sol)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None)


def test_an_endpoint_that_cannot_see_keeps_the_detector_s_labels_and_says_so(
    tmp_path, real_pdf, stub_docling, device, capsys
):
    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    translator = FakeVisionTranslator(ANSWERS, verdict="unsupported")
    stages.prepare(
        bundle, real_pdf, pandoc=pandoc, structure=request(translator), progress=False
    )
    line = IMG_ENDPOINT_UNVERIFIED.format(
        model="gpt-5.6-luna", base="the run's endpoint", verdict="unsupported"
    )
    assert line in capsys.readouterr().out.replace("\n", "")
    assert translator.calls == []  # not one page was sent
    manifest = bundle.read_manifest()
    assert line in manifest["limitations"]
    extraction = manifest["extraction"]
    assert extraction["structure"] == "gpt-5.6-luna"
    assert extraction["structure_applied"] is False
    assert extraction["structure_status"] == "not_run"
    assert extraction["img_source"] == "cli"
    assert extraction["img_verdict"] == "unsupported"
    assert not (bundle.work_file("extraction") / "decisions.json").exists()
    assert "import os" in bundle.source.read_text(encoding="utf-8")
    assert "```" not in bundle.source.read_text(encoding="utf-8")


def test_vision_verdict_is_preferred_when_the_translator_has_one():
    translator = FakeVisionTranslator(ANSWERS, verdict="unsupported")
    translator.vision_verdict = lambda model: "verified"
    assert request(translator).vision() == "verified"
    assert translator.capabilities.asked == []


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------
TRANSLATION = ["--api_format", "google", "--language", "zh-hans"]


@pytest.fixture
def fake_pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


def refusing_stages():
    def prepare_stage(*args, **kwargs):
        pytest.fail("the extraction was reached")

    return {
        "prepare_stage": prepare_stage,
        "translate_stage": prepare_stage,
        "export_stage": prepare_stage,
    }


def test_another_api_format_is_refused_before_anything_is_extracted(
    fake_pdf, monkeypatch
):
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(
            fake_pdf,
            TRANSLATION,
            img_model="gpt-5.6-luna",
            **refusing_stages(),
        )
    assert refused.value.detail.startswith(
        "--img-model needs an OpenAI-compatible endpoint;"
    )
    assert refused.value.detail.endswith("resolves to the google format.")
    assert not (fake_pdf.parent / "book_book").exists()


def test_an_image_base_of_another_format_is_refused(fake_pdf, monkeypatch):
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(
            fake_pdf,
            ["--model", "gpt-5.6-nano", "--key", "sk-test", "--language", "zh-hans"],
            img_model="claude-x",
            img_base_url="https://api.anthropic.com",
            **refusing_stages(),
        )
    assert refused.value.detail == IMG_ENDPOINT_UNSUPPORTED.format(
        base="https://api.anthropic.com", api_format="anthropic"
    )


def test_a_translator_without_an_image_channel_cannot_see():
    # the resolver refuses a format without the channel; a translator that
    # still lacks one answers "unsupported" and the pass is skipped
    assert request(object()).vision() == "unsupported"


def test_the_structure_model_runs_on_the_translation_s_endpoint_and_key(
    fake_pdf, monkeypatch
):
    built = {}

    class Built(FakeVisionTranslator):
        def __init__(self, key, language, api_base=None, **kwargs):
            super().__init__(ANSWERS)
            built.update(key=key, api_base=api_base)

        def set_model_list(self, models):
            built["models"] = list(models)

    monkeypatch.setitem(cli.FORMAT_DICT, "openai", Built)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    seen = {}

    def prepare_stage(bundle, source, **kwargs):
        seen.update(kwargs)
        raise PipelineError("stop here", stage="extract")

    stages_ = {
        "prepare_stage": prepare_stage,
        "translate_stage": None,
        "export_stage": None,
    }
    translation = [
        "--model",
        "gpt-5.6-nano",
        "--api_base",
        "https://gateway.example/v1",
        "--key",
        "sk-test",
        "--language",
        "zh-hans",
    ]
    with pytest.raises(PipelineError):
        to_epub.pdf_to_epub(fake_pdf, translation, img_model="gpt-5.6-luna", **stages_)
    assert built == {
        "key": "sk-test",
        "api_base": "https://gateway.example/v1",
        "models": ["gpt-5.6-luna"],
    }
    structure = seen["structure"]
    assert structure.model == "gpt-5.6-luna"
    assert structure.rev == REV
    assert structure.source == "cli"
    assert structure.base == "https://gateway.example/v1"

    # at an address of its own, with its own key; the run's key stays home
    built.clear()
    with pytest.raises(PipelineError):
        to_epub.pdf_to_epub(
            fake_pdf,
            translation,
            img_model="local-vl",
            img_base_url="http://127.0.0.1:8080/v1",
            img_key="vk-test",
            **stages_,
        )
    assert built == {
        "key": "vk-test",
        "api_base": "http://127.0.0.1:8080/v1",
        "models": ["local-vl"],
    }
    assert seen["structure"].base == "http://127.0.0.1:8080/v1"


def test_img_model_none_builds_nothing(fake_pdf, monkeypatch):
    """PIN (owner 260923 22:30, packet F): 'none' is off, and nothing of the
    pass is built or probed."""

    def never(*args, **kwargs):
        pytest.fail("the pass was built for --img-model none")

    monkeypatch.setattr(docling_parser, "_structure_ask", never)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    seen = {}

    def prepare_stage(bundle, source, **kwargs):
        seen.update(kwargs)
        raise PipelineError("stop here", stage="extract")

    with pytest.raises(PipelineError):
        to_epub.pdf_to_epub(
            fake_pdf,
            TRANSLATION,
            img_model="none",
            prepare_stage=prepare_stage,
            translate_stage=None,
            export_stage=None,
        )
    assert seen["structure"] is None


def test_a_stop_row_refuses_before_the_extraction(fake_pdf, monkeypatch):
    """packet F, after the port's Codex review: the route diverts before
    `check_compatibility`, so its stop rows (A12 codex x --no-thinking here)
    are asked before `prepare_stage`, which is never called."""
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(
            fake_pdf,
            ["--api_format", "codex", "--no-thinking", "--language", "zh-hans"],
            **refusing_stages(),
        )
    assert "--no-thinking" in refused.value.detail
    assert not (fake_pdf.parent / "book_book").exists()


def test_the_image_usage_is_its_own_line(fake_pdf, monkeypatch, capsys):
    from book_maker.pipeline.messages import IMAGE_MODEL_USAGE
    from book_maker.translator.base_translator import UsageMeter

    meter = UsageMeter()

    class Built(FakeVisionTranslator):
        def __init__(self, key, language, api_base=None, **kwargs):
            super().__init__(ANSWERS)
            self.usage = meter

        def set_model_list(self, models):
            pass

    monkeypatch.setitem(cli.FORMAT_DICT, "openai", Built)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")

    def prepare_stage(bundle, source, **kwargs):
        meter.note(prompt=3000, completion=40)

    def translate_stage(bundle, options, *, pandoc):
        print("the inner run's own usage line")

    def export_stage(bundle, *, pandoc):
        bundle.epub.write_bytes(b"PK")
        return bundle.epub

    to_epub.pdf_to_epub(
        fake_pdf,
        ["--model", "gpt-5.6-nano", "--key", "sk-test", "--language", "zh-hans"],
        img_model="gpt-5.6-luna",
        prepare_stage=prepare_stage,
        translate_stage=translate_stage,
        export_stage=export_stage,
    )
    out = capsys.readouterr().out
    line = IMAGE_MODEL_USAGE.format(
        model="gpt-5.6-luna", base="the run's endpoint", summary=meter.summary()
    )
    assert line in out.replace("\n", "")
    assert out.index("Image model (") < out.index("the inner run's own usage line")


@pytest.mark.parametrize("flagged", [True, False])
def test_no_thinking_reaches_the_structure_translator(fake_pdf, monkeypatch, flagged):
    """Port 260923 (Codex finding on port/260920-batch): the CLI set
    `no_thinking` on the translation-stage instance only, so `--to-epub
    --structure-model M --no-thinking` (now `--img-model`) still sent the pass's image requests
    with reasoning on. The pass's translator is built here from the same
    parsed options and gets the flag on the routes that carry it."""
    built = {}

    class Built(FakeVisionTranslator):
        SUPPORTS_REQUEST_EXTRAS = True
        no_thinking = False

        def __init__(self, key, language, api_base=None, **kwargs):
            super().__init__(ANSWERS)
            built["instance"] = self

        def set_model_list(self, models):
            pass

    monkeypatch.setitem(cli.FORMAT_DICT, "openai", Built)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")

    def prepare_stage(bundle, source, **kwargs):
        raise PipelineError("stop here", stage="extract")

    translation = [
        "--model",
        "gpt-5.6-nano",
        "--key",
        "sk-test",
        "--language",
        "zh-hans",
    ]
    if flagged:
        translation.append("--no-thinking")
    with pytest.raises(PipelineError):
        to_epub.pdf_to_epub(
            fake_pdf,
            translation,
            img_model="gpt-5.6-luna",
            prepare_stage=prepare_stage,
            translate_stage=None,
            export_stage=None,
        )
    assert built["instance"].no_thinking is flagged


def test_without_the_flag_nothing_of_the_pass_is_loaded_or_probed(
    fake_pdf, monkeypatch
):
    """PIN (packet E2): defaults off -- no client, no probe, no import."""
    from book_maker.translator import capabilities

    def never(*args, **kwargs):
        pytest.fail("the image probe ran without --img-model")

    monkeypatch.setattr(capabilities.CapabilityLedger, "ensure_vision", never)
    monkeypatch.setattr(docling_parser, "_structure_ask", never)
    monkeypatch.delitem(sys.modules, "book_maker.pipeline.decisions", raising=False)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    seen = {}

    def prepare_stage(bundle, source, **kwargs):
        seen.update(kwargs)

    def translate_stage(bundle, options, *, pandoc):
        pass

    def export_stage(bundle, *, pandoc):
        bundle.epub.write_bytes(b"PK")
        return bundle.epub

    to_epub.pdf_to_epub(
        fake_pdf,
        TRANSLATION,
        prepare_stage=prepare_stage,
        translate_stage=translate_stage,
        export_stage=export_stage,
    )
    assert seen["structure"] is None
    assert "book_maker.pipeline.decisions" not in sys.modules


def test_the_flag_reaches_the_route_and_not_the_translation(fake_pdf, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        to_epub,
        "pdf_to_epub",
        lambda path, argv, **kwargs: seen.update(argv=list(argv), **kwargs),
    )
    cli.main(
        [
            "--book_name",
            str(fake_pdf),
            "--to-epub",
            "--img-model",
            "gpt-5.6-luna",
            "--img-base-url",
            "http://127.0.0.1:1/v1",
            "--img-key",
            "vk",
            *TRANSLATION,
        ]
    )
    assert seen["img_model"] == "gpt-5.6-luna"
    assert seen["img_base_url"] == "http://127.0.0.1:1/v1"
    assert seen["img_key"] == "vk"
    assert to_epub.translation_argv(seen["argv"]) == TRANSLATION


def test_the_harness_hands_the_structure_request_to_the_stage(
    tmp_path, fake_pdf, monkeypatch
):
    pandoc = pandoc_or_skip()
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pdf_to_book",
        Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py",
    )
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    seen = {}

    def record(bundle, path, **kwargs):
        seen.update(kwargs)
        raise PipelineError("stopped before the models", stage="extract")

    made = []
    monkeypatch.setattr(harness, "prepare", record)
    monkeypatch.setattr(
        docling_parser,
        "image_request",
        lambda image, translation: made.append(image.img_model) or "request",
    )
    base = ["--pandoc", pandoc, "extract", str(fake_pdf), "--output", str(tmp_path)]
    assert harness.main(base) == 1
    assert "structure" not in seen and made == []
    assert harness.main([*base, "--img-model", "gpt-5.6-luna"]) == 1
    assert seen["structure"] == "request" and made == ["gpt-5.6-luna"]


def test_a_page_where_most_items_changed_is_called_out_and_recorded(
    tmp_path, real_pdf, stub_docling, device, capsys
):
    """PIN (lead ruling 260923): the changes stand, the page is named."""
    from book_maker.pipeline.messages import STRUCTURE_HIGH_CHANGE

    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    answers = {**ANSWERS, "Prose one.": "footnote", "Prose two.": "footnote"}
    stages.prepare(
        bundle,
        real_pdf,
        pandoc=pandoc,
        structure=request(FakeVisionTranslator(answers)),
        progress=False,
    )
    line = STRUCTURE_HIGH_CHANGE.format(changed=4, asked=5, page=1)
    assert line in capsys.readouterr().out.replace("\n", "")
    manifest = bundle.read_manifest()
    assert line in manifest["limitations"]
    assert manifest["extraction"]["structure_totals"]["applied"] == 4
    assert "```\nimport os\n```" in bundle.source.read_text(encoding="utf-8")


def test_a_bundle_whose_pass_never_ran_is_not_reused_for_one_that_asks(
    tmp_path, real_pdf, stub_docling, device
):
    """PIN (lead ruling 260923): `structure_applied: false` holds the
    detector's labels, so a run asking for the pass extracts again."""
    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    blind = FakeVisionTranslator(ANSWERS, verdict="unsupported")
    stages.prepare(
        bundle, real_pdf, pandoc=pandoc, structure=request(blind), progress=False
    )
    assert bundle.read_manifest()["extraction"]["structure_applied"] is False
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None, luna)
    # and the next run does extract, and runs the pass
    seeing = FakeVisionTranslator(ANSWERS)
    stages.prepare(
        bundle, real_pdf, pandoc=pandoc, structure=request(seeing), progress=False
    )
    assert seeing.calls == ["gpt-5.6-luna"]
    assert bundle.read_manifest()["extraction"]["structure_applied"] is True
    assert stages.already_prepared(bundle, real_pdf, "docling", None, luna)


class FailingOnPageTwo(FakeVisionTranslator):
    """Answers page 1; the question about page 2 gets no usable reply."""

    def structured_json_with_image(
        self, prompt, schema, image_png, model=None, deadline=None
    ):
        from book_maker.translator.vision import VisionRequestFailed

        if "Page two prose." in prompt:
            self.calls.append(model)
            raise VisionRequestFailed("the endpoint refused the image")
        return super().structured_json_with_image(
            prompt, schema, image_png, model=model, deadline=deadline
        )


BOTH_PAGES = {**ANSWERS, "Page two prose.": "text"}


@pytest.fixture
def two_page_pdf(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    return write_pdf(tmp_path / "paper.pdf", ["A line of prose.", "Page two prose."])


def test_a_partial_pass_is_extracted_again_and_a_complete_one_is_reused(
    tmp_path, two_page_pdf, stub_docling, device, capsys
):
    """PIN (lead 260923, Codex review of E2): only a `complete` pass
    satisfies a later run that asks for one; `partial` (a failed question,
    a failed apply, a budget stop, an unasked page) is extracted again,
    and the terminal names the status."""
    from book_maker.pipeline.messages import STRUCTURE_NOT_REUSED

    pandoc = pandoc_or_skip()
    stub_docling["pages"] = 2
    bundle = Bundle(tmp_path / "b").create()
    flaky = FailingOnPageTwo(BOTH_PAGES)
    stages.prepare(
        bundle, two_page_pdf, pandoc=pandoc, structure=request(flaky), progress=False
    )
    assert flaky.calls == ["gpt-5.6-luna", "gpt-5.6-luna"]
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["structure_applied"] is True
    assert extraction["structure_status"] == "partial"
    capsys.readouterr()

    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert not stages.already_prepared(bundle, two_page_pdf, "docling", None, luna)
    line = STRUCTURE_NOT_REUSED.format(status="partial", model="gpt-5.6-luna")
    assert line in capsys.readouterr().out.replace("\n", "")
    # a run without the flag still asks for different labels: not reused
    assert not stages.already_prepared(bundle, two_page_pdf, "docling", None)

    # the rerun extracts again, asks both pages, and completes
    steady = FakeVisionTranslator(BOTH_PAGES)
    stages.prepare(
        bundle, two_page_pdf, pandoc=pandoc, structure=request(steady), progress=False
    )
    assert steady.calls == ["gpt-5.6-luna", "gpt-5.6-luna"]
    assert bundle.read_manifest()["extraction"]["structure_status"] == "complete"
    capsys.readouterr()
    # and a complete pass is reused: nothing is asked again, nothing said
    again = FakeVisionTranslator(BOTH_PAGES)
    stages.prepare(
        bundle, two_page_pdf, pandoc=pandoc, structure=request(again), progress=False
    )
    assert again.calls == []
    assert "Extracting again" not in capsys.readouterr().out


def test_a_pass_that_raised_is_recorded_failed(
    tmp_path, real_pdf, stub_docling, device
):
    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()

    class Broken(FakeVisionTranslator):
        def structured_json_with_image(self, *args, **kwargs):
            raise RuntimeError("the client broke")

    with pytest.raises(PipelineError):
        stages.prepare(
            bundle,
            real_pdf,
            pandoc=pandoc,
            structure=request(Broken(ANSWERS)),
            progress=False,
        )
    manifest = bundle.read_manifest()
    assert manifest["stages"]["extract"]["status"] == "failed"
    assert manifest["extraction"]["structure_status"] == "failed"
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None, luna)


def test_a_reply_that_left_an_id_out_is_extracted_again(
    tmp_path, real_pdf, stub_docling, device, capsys
):
    """PIN (lead 260923, Codex re-verification of E2): an id missing from
    an otherwise valid reply is `unanswered`, so the pass is `partial`
    and a later run that asks for structure extracts and asks again."""
    from book_maker.pipeline.messages import STRUCTURE_NOT_REUSED

    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    shy = {k: v for k, v in ANSWERS.items() if k != "Prose three."}
    stages.prepare(
        bundle,
        real_pdf,
        pandoc=pandoc,
        structure=request(FakeVisionTranslator(shy)),
        progress=False,
    )
    assert bundle.read_manifest()["extraction"]["structure_status"] == "partial"
    capsys.readouterr()
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None, luna)
    line = STRUCTURE_NOT_REUSED.format(status="partial", model="gpt-5.6-luna")
    assert line in capsys.readouterr().out.replace("\n", "")

    full = FakeVisionTranslator(ANSWERS)
    stages.prepare(
        bundle, real_pdf, pandoc=pandoc, structure=request(full), progress=False
    )
    assert full.calls == ["gpt-5.6-luna"]
    assert bundle.read_manifest()["extraction"]["structure_status"] == "complete"
    assert stages.already_prepared(bundle, real_pdf, "docling", None, luna)


@pytest.mark.parametrize("error", ["QuestionTimedOut", "VisionRequestFailed"])
def test_a_timed_out_or_refused_question_is_lost_not_the_run(error):
    """packet F (added 260923): `QuestionTimedOut` stays a soft failure of
    one structure question through the Classifier's image backend, like a
    refused image; the pass records it and goes on."""
    from book_maker.pipeline import decisions
    from book_maker.translator import vision

    class Timing(FakeVisionTranslator):
        def structured_json_with_image(self, *args, **kwargs):
            raise getattr(vision, error)("no answer before the deadline")

    structure = request(Timing(ANSWERS))
    schema = {"schema": {"properties": {"1": {"enum": ["text", "abstain"]}}}}
    with pytest.raises(decisions.AskFailed):
        structure.ask("prompt", schema, b"png")
