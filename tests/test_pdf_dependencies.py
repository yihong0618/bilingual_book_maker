"""The PDF route's dependency split, pinned.

PIN (owner, 2026-09-21, docs/260921-plan-PDF_DOCLING_ONLY_AND_INSTALL_ROUTES.md):
PDF support leaves the base install entirely. docling and PyTorch are 200 MB
to 1.8 GB of wheels plus ~500 MB of models on first run, and most of this
tool's users translate EPUBs and never open a PDF, so the base install can no
longer read a PDF at all -- it refuses with the install line instead. The
`pdf` extra declares WHAT is needed and never WHICH PyTorch build: a PyPI
package cannot express "the CPU build" (PEP 735 groups are not emitted into
distributions, direct references are banned from public indexes, and uv's
--torch-backend cannot be inherited by a dependent), which is why that choice
lives in requirements-pdf-cpu.txt / requirements-pdf-gpu.txt and
docs/installation-pdf.md instead.

Supersedes the 260920 split, where the route's own packages were base
dependencies and only the OCR runtime was an extra. That arrangement belonged
to the retired Java parser, which needed no models to read a typed page.
"""

import re
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = Path(__file__).resolve().parents[1]

# Nothing the PDF route needs may sit in the base install.
PDF_ONLY = (
    "docling",
    "pypdfium2",
    "torch",
    "transformers",
    "easyocr",
    "onnxruntime",
    "ocrmac",
)

CPU_FILE = ROOT / "requirements-pdf-cpu.txt"
GPU_FILE = ROOT / "requirements-pdf-gpu.txt"


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def _name(requirement):
    return re.split(r"[\[><=!~;\s]", requirement, maxsplit=1)[0].lower()


def _pinned(text, name):
    # PDM exports an extra as `docling[easyocr]==…`; the name is what matters.
    return re.search(rf"^{re.escape(name)}(\[[^\]]*\])?==", text, re.M)


def test_the_base_install_carries_nothing_the_pdf_route_needs():
    deps = _project()["dependencies"]
    names = {_name(d) for d in deps}
    assert not names & set(PDF_ONLY), "the PDF route must not be a base dependency"
    assert not [d for d in deps if "[" in d], "no extras in the base list"


def test_the_pdf_extra_names_what_is_needed_and_not_which_torch_build():
    extras = _project()["optional-dependencies"]
    pdf = extras["pdf"]
    # PIN (owner, 250925, docs/250925-feat-PDF_EXTRA_OCR_RUNTIMES.md):
    # onnxruntime and ocrmac (darwin) in the extra, so rapidocr downloads
    # nothing on first use and `auto` reads with ocrmac on every Mac.
    assert {_name(d) for d in pdf} == {"docling", "pypdfium2", "onnxruntime", "ocrmac"}
    (ocrmac,) = [d for d in pdf if _name(d) == "ocrmac"]
    assert re.search(r"sys_platform\s*==\s*['\"]darwin['\"]", ocrmac)
    # A direct reference or an index URL here would be rejected by PyPI and
    # would still not reach anyone installing us as a dependency.
    assert not [d for d in pdf if "@" in d or "://" in d]
    # The retired `ocr` extra keeps working for a release: `--with-ocr` used
    # to start a second engine, and OCR is now one option of the one parser.
    assert extras["ocr"] == ["bbook_maker[pdf]"]


def test_both_pdf_files_carry_the_ocr_runtimes():
    # PIN (owner, 250925, docs/250925-feat-PDF_EXTRA_OCR_RUNTIMES.md).
    for path in (CPU_FILE, GPU_FILE):
        text = path.read_text()
        assert _pinned(text, "onnxruntime"), path.name
        assert re.search(
            r'^ocrmac==\S+; sys_platform == "darwin"', text, re.M
        ), path.name


def test_the_exported_base_file_keeps_the_split():
    base = (ROOT / "requirements.txt").read_text()
    for name in PDF_ONLY:
        assert not _pinned(base, name), f"{name} in the base file"


# Unconditional since 260921: `.gitignore`'s `*.txt` rule is an allowlist and
# named neither file, so the export script wrote them and git silently never
# took them -- while CI and the Dockerfile referenced them. Both are tracked
# now, so a clone always has them and a skip here would only hide the same
# bug coming back.
def test_the_two_pdf_files_differ_only_in_which_torch_index_they_name():
    """Which build you get is an index choice, not a version one.

    The GPU file is the lock as exported -- PyPI's torch, which on Linux is
    the CUDA build. The CPU file names PyTorch's CPU index in the file
    itself, because pip reads `--extra-index-url` from a requirements file
    and the operator then has no flag to remember; and it drops the
    `nvidia-*`/triton pins, which the lock carries only because PyPI's torch
    requires them and the +cpu build requires none of them.
    """
    cpu = CPU_FILE.read_text()
    gpu = GPU_FILE.read_text()
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in cpu
    assert "download.pytorch.org" not in gpu
    assert not re.search(r"^(nvidia-[a-z0-9-]+|triton)", cpu, re.M)
    for name in ("docling", "torch"):
        assert _pinned(gpu, name), f"{name} missing from the gpu file"
        assert re.search(rf"^{name}(\[[^\]]*\])?==", cpu, re.M), f"{name} missing"
