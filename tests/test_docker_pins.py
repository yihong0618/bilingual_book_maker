"""The Docker images' pins, read from the files; no Docker needed.

Three images come from one Dockerfile: `core`, `pdf` (core plus the PDF
route with PyTorch's CPU build) and `pdf-cuda` (the official PyTorch CUDA
runtime image plus the route). pdf-cuda installs the pdf extra without
torch, torchvision, triton and the nvidia wheels, trusting the base image
to carry the lock's versions, so the base tag must name the lock's torch.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
TOOL = ROOT / "tools" / "torch_base_requirements.py"


def _torch_pin(name):
    text = (ROOT / name).read_text()
    match = re.search(r"^torch==([^\s;\\]+)", text, re.M)
    assert match, f"no torch== pin in {name}"
    return match.group(1)


def _base_tag():
    tags = re.findall(r"^FROM\s+pytorch/pytorch:(\S+)", DOCKERFILE, re.M)
    assert len(tags) == 1, f"expected one pytorch/pytorch base, found {tags}"
    return tags[0]


def test_the_cuda_base_names_the_locked_torch():
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    tag = _base_tag()
    torch = tag.split("-", 1)[0]
    assert re.match(r"^\d+\.\d+\.\d+-cuda\d+\.\d+-", tag), tag
    assert torch == _torch_pin("requirements-pdf-gpu.txt")
    assert torch == _torch_pin("requirements-pdf-cpu.txt")


def test_the_cuda_base_is_the_runtime_variant():
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    tag = _base_tag()
    assert tag.endswith("-runtime"), tag
    assert "devel" not in DOCKERFILE.split("FROM pytorch/pytorch:", 1)[1].split()[0]
    assert "devel" not in tag


def test_pandoc_is_new_enough_for_the_epub_contents():
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    versions = re.findall(r"^ARG PANDOC_VERSION=(\S+)", DOCKERFILE, re.M)
    assert len(versions) == 1, versions
    parts = tuple(int(p) for p in versions[0].split("."))
    assert parts >= (3, 1, 12), versions[0]


def _stages():
    """{stage: (base, instructions without comments)} in file order."""
    code = "\n".join(
        line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#")
    )
    parts = re.split(r"^FROM\s+(\S+)\s+AS\s+(\S+)\s*$", code, flags=re.M)
    return {parts[i + 1]: (parts[i], parts[i + 2]) for i in range(1, len(parts) - 1, 3)}


def test_the_stages_install_the_files_they_are_meant_to():
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    stages = _stages()
    assert list(stages) == ["core-deps", "core", "pdf", "pdf-cuda", "default"]
    assert stages["core-deps"][0].startswith("python:3.12-slim")
    # core and pdf share core's dependency layers; the application is
    # copied last in each, so a code change never rebuilds pdf's PyTorch.
    assert stages["core"][0] == "core-deps"
    assert stages["pdf"][0] == "core-deps"
    assert stages["pdf-cuda"][0].startswith("pytorch/pytorch:")
    assert stages["default"][0] == "core"
    assert "book_maker/" not in stages["core-deps"][1]
    for stage in ("core", "pdf", "pdf-cuda"):
        body = stages[stage][1]
        assert "COPY book_maker/" in body, stage
        assert "pip install" not in body.split("COPY book_maker/", 1)[1], stage
    assert "requirements-pdf-cpu.txt" in stages["pdf"][1]
    assert "requirements-pdf-gpu.txt" not in stages["pdf"][1]
    assert "torch_base_requirements.py" in stages["pdf-cuda"][1]
    assert "requirements-pdf-cpu.txt" not in stages["pdf-cuda"][1]
    # pdf-cuda runs exactly like core
    for line in (
        'ENTRYPOINT ["python", "make_book.py"]',
        'CMD ["--help"]',
        "WORKDIR /app",
        "/app/log /app/batch_files",
    ):
        assert line in stages["core-deps"][1] and line in stages["pdf-cuda"][1]


def test_the_torch_base_file_drops_only_what_the_base_provides():
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    gpu = (ROOT / "requirements-pdf-gpu.txt").read_text()
    out = subprocess.run(
        [sys.executable, str(TOOL), str(ROOT / "requirements-pdf-gpu.txt")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    entry = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==", re.M)
    before = set(entry.findall(gpu))
    after = set(entry.findall(out))
    dropped = before - after
    assert {"torch", "torchvision", "triton"} <= dropped
    assert all(
        re.match(r"^(torch|torchvision|triton|nvidia-[a-z0-9-]+)$", n) for n in dropped
    ), dropped
    assert not re.search(r"^(torch|torchvision|triton|nvidia-)", out, re.M)
    # every kept entry keeps its hashes: pip stays in hash-checking mode
    assert "docling==" in out and out.count("--hash=sha256:") > 100


def _check(monkeypatch, installed, removed):
    import importlib.metadata

    sys.path.insert(0, str(TOOL.parent))
    try:
        import torch_base_requirements as tool
    finally:
        sys.path.pop(0)

    def version(name):
        if name not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(importlib.metadata, "version", version)
    return tool.check_installed(removed)


def test_the_base_check_accepts_the_locked_versions_and_local_builds(monkeypatch):
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    installed = {"torch": "2.7.1+cu126", "triton": "3.3.1"}
    removed = [("torch", "2.7.1", None), ("triton", "3.3.1", None)]
    assert _check(monkeypatch, installed, removed) == []


def test_the_base_check_refuses_a_missing_or_different_package(monkeypatch):
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    installed = {"torch": "2.8.0+cu126"}
    removed = [("torch", "2.7.1", None), ("nvidia-cublas-cu12", "12.6.4.1", None)]
    problems = _check(monkeypatch, installed, removed)
    assert len(problems) == 2
    assert "the base image has 2.8.0+cu126" in problems[0]
    assert "not installed" in problems[1]


def test_the_base_check_skips_a_pin_whose_marker_does_not_apply(monkeypatch):
    # PIN owner 260929: pdf-cuda is built on the official torch image matching the lock; pdf is CPU torch (docs/260929-infra-DOCKER_THREE_IMAGES.md)
    removed = [("triton", "3.3.1", 'sys_platform == "no-such-platform"')]
    assert _check(monkeypatch, {}, removed) == []
