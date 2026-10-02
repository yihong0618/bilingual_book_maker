"""What an extraction was asked to do, as one typed value.

The PDF route configures docling from these fields and nothing else, and
the same value is what a rerun is compared against: a bundle is reused
only when the settings that decide its text are the ones asked for now.

Device is deliberately not here. The models produce the same text on any
processor, so the device is provenance -- recorded, reported when it
differs, never a reason to extract again.

No docling import in this module: it is read on every resume check, and a
Markdown run must not pay for the PDF parser.
"""

import importlib.util
import shutil
import sys
from dataclasses import dataclass

from .errors import PipelineError
from .messages import OCR_ENGINE_INSTALL, OCR_ENGINE_MISSING, OCR_ENGINE_NOT_MACOS

OCR_ENGINES = ("auto", "rapidocr", "easyocr", "ocrmac", "tesseract")
OCR_MODES = ("default", "full_page", "layout_regions", "pdf_aware_layout_regions")
TABLE_MODES = ("accurate", "fast", "v2")

# `--ocr-replace-layer`: docling's full-page OCR skips the PDF's own text
# cells and keeps only what the engine read, so an embedded layer is
# replaced rather than kept (owner ruling 260923: an explicit setting, off
# by default). `ocr_mode` is already identity, so toggling it re-extracts.
OCR_MODE_REPLACE_LAYER = "full_page"

# What an engine named by `--ocr-engine` needs importable before docling
# can build it. rapidocr runs on onnxruntime (docling's default backend for
# it). tesseract is a program, looked up on PATH instead.
ENGINE_MODULES = {
    "rapidocr": ("rapidocr", "onnxruntime"),
    "easyocr": ("easyocr",),
    "ocrmac": ("ocrmac",),
}


def check_ocr_engine(engine):
    """Refuse an OCR engine this install cannot run, before a page is read.

    `auto` is never refused: docling takes whichever engine is installed.
    A named engine is checked without importing it (importing easyocr
    starts torch): its modules must be findable, tesseract must be on PATH,
    and ocrmac must be on macOS. What the check cannot see (a missing
    language file) is docling's to refuse when the converter is built.
    """
    engine = engine or "auto"
    if engine == "auto":
        return
    if engine not in OCR_ENGINES:
        raise PipelineError(
            f"ocr_engine {engine!r} is not one of {', '.join(OCR_ENGINES)}",
            stage="extract",
        )
    if engine == "ocrmac" and sys.platform != "darwin":
        raise PipelineError(OCR_ENGINE_NOT_MACOS, stage="extract")
    if engine == "tesseract":
        present = shutil.which("tesseract") is not None
    else:
        present = all(
            importlib.util.find_spec(module) is not None
            for module in ENGINE_MODULES[engine]
        )
    if not present:
        raise PipelineError(
            OCR_ENGINE_MISSING.format(
                engine=engine, install=OCR_ENGINE_INSTALL[engine]
            ),
            stage="extract",
        )


@dataclass(frozen=True)
class ExtractionSettings:
    ocr: bool = False
    ocr_engine: str = "auto"
    ocr_mode: str = "default"
    ocr_lang: tuple = ()
    table_mode: str = "accurate"
    formula_images: bool = True
    # `--img-model`: the vision model that re-names the detector's
    # regions, the address it is asked at (`--img-base-url`, None for the
    # run's own endpoint: the same id elsewhere may be another model), and
    # the prompt/policy revision it was asked under
    # (`decisions.PROMPT_REV/POLICY_REV`). All decide the text, so all are
    # identity: replaying an overlay onto a stored document is a later
    # stage (Codex consult 260923), until then another model extracts again.
    structure: str = None
    structure_rev: str = None
    structure_base: str = None

    def __post_init__(self):
        # Frozen, so the normalising goes through object.__setattr__: a
        # list handed in (as the manifest stores it) becomes the tuple
        # that keeps the value hashable and comparable.
        object.__setattr__(self, "ocr", bool(self.ocr))
        object.__setattr__(self, "formula_images", bool(self.formula_images))
        object.__setattr__(
            self, "ocr_lang", tuple(str(code) for code in (self.ocr_lang or ()))
        )
        for field, value, allowed in (
            ("ocr_engine", self.ocr_engine, OCR_ENGINES),
            ("ocr_mode", self.ocr_mode, OCR_MODES),
            ("table_mode", self.table_mode, TABLE_MODES),
        ):
            if value not in allowed:
                raise PipelineError(
                    f"{field} {value!r} is not one of {', '.join(allowed)}",
                    stage="extract",
                )

    @property
    def ocr_replace_layer(self):
        """Whether OCR replaces the PDF's own text layer (derived, for readers)."""
        return self.ocr and self.ocr_mode == OCR_MODE_REPLACE_LAYER

    def identity(self):
        """The fields that decide the extracted text, as the manifest keeps them.

        Without OCR the OCR fields changed nothing, so they are written as
        their defaults: a bundle made without OCR is not invalidated by an
        engine or a language list nobody used.
        """
        return {
            "ocr": self.ocr,
            "ocr_engine": self.ocr_engine if self.ocr else "auto",
            "ocr_mode": self.ocr_mode if self.ocr else "default",
            "ocr_lang": list(self.ocr_lang) if self.ocr and self.ocr_lang else None,
            "table_mode": self.table_mode,
            "formula_images": self.formula_images,
            "structure": self.structure or None,
            "structure_rev": (self.structure_rev or None) if self.structure else None,
            "structure_base": (
                (self.structure_base or None) if self.structure else None
            ),
        }

    @classmethod
    def from_manifest(cls, extraction):
        """The settings a past extraction ran with.

        A key the manifest lacks takes its default, because that is what
        the runs from before the key existed did. The engine asked for is
        `ocr_engine_requested`; `ocr_engine` is what docling resolved it to.
        """
        extraction = extraction or {}
        return cls(
            ocr=bool(extraction.get("ocr", False)),
            ocr_engine=extraction.get("ocr_engine_requested") or "auto",
            ocr_mode=extraction.get("ocr_mode") or "default",
            ocr_lang=tuple(extraction.get("ocr_lang") or ()),
            table_mode=extraction.get("table_mode") or "accurate",
            formula_images=bool(extraction.get("formula_images", True)),
            structure=extraction.get("structure") or None,
            structure_rev=extraction.get("structure_rev") or None,
            structure_base=extraction.get("structure_base") or None,
        )
