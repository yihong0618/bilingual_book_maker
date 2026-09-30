#!/usr/bin/env python3
"""The pdf extra for an image that already has PyTorch: the lock minus torch.

The `pdf-cuda` Docker image is built on the official
`pytorch/pytorch:<torch>-cuda…-runtime` image, which ships torch, torchvision,
triton and the nvidia-*-cu12 CUDA libraries already. Installing
requirements-pdf-gpu.txt there as it is would let pip fetch any of them again
the moment the base's copy differs from the lock in the smallest way, which
is several gigabytes of downloads for nothing. So the Dockerfile installs the
pdf extra from this script's output instead: requirements-pdf-gpu.txt with
those entries removed, every other pin and hash kept verbatim. Generated at
build time from the tracked file, so it cannot drift from the lock by hand.

    python tools/torch_base_requirements.py requirements-pdf-gpu.txt > out.txt

With --check-installed it also compares the removed pins against what the
running interpreter has installed and exits 1 on a missing package or a
different version: a base image that no longer matches the lock fails the
build loudly instead of shipping a torch the rest of the lock was not
resolved against. A local version (`2.7.1+cu126`) matches `2.7.1`, as in
PEP 440. Pins whose environment marker does not apply here are not checked.
"""

import argparse
import re
import sys

# The packages the torch base image provides. triton and the nvidia wheels are
# in the lock only because PyPI's torch requires them on Linux x86_64.
PROVIDED = re.compile(r"^(torch|torchvision|triton|nvidia-[a-z0-9-]+)$")

ENTRY = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;\\]+)\s*(;[^\\]*)?"
)


def split_entries(text):
    """Yield (name, version, marker, lines) per entry; other lines as (None, …)."""
    entry = None
    for line in text.splitlines(keepends=True):
        if entry is not None and line[:1].isspace() and line.strip():
            entry[3].append(line)
            continue
        if entry is not None:
            yield entry
            entry = None
        match = ENTRY.match(line)
        if match:
            marker = (match.group(4) or "").lstrip(";").strip() or None
            entry = (match.group(1).lower(), match.group(3), marker, [line])
        else:
            yield (None, None, None, [line])
    if entry is not None:
        yield entry


def strip(text):
    """Return (the text without the provided entries, the removed entries)."""
    kept, removed = [], []
    for name, version, marker, lines in split_entries(text):
        if name is not None and PROVIDED.match(name):
            removed.append((name, version, marker))
        else:
            kept.extend(lines)
    names = {name for name, _, _ in removed}
    for required in ("torch", "torchvision"):
        if required not in names:
            raise SystemExit(f"torch_base_requirements: no `{required}==` pin found")
    return "".join(kept), removed


def _marker_applies(marker):
    if marker is None:
        return True
    try:
        from packaging.markers import Marker
    except ImportError:  # pip vendors it when the environment does not
        from pip._vendor.packaging.markers import Marker
    return Marker(marker).evaluate()


def check_installed(removed):
    from importlib.metadata import PackageNotFoundError, version

    problems = []
    for name, pinned, marker in removed:
        if not _marker_applies(marker):
            continue
        try:
            have = version(name)
        except PackageNotFoundError:
            problems.append(f"{name}=={pinned}: not installed in the base image")
            continue
        if have.split("+", 1)[0] != pinned:
            problems.append(f"{name}=={pinned}: the base image has {have}")
        else:
            print(f"base provides {name} {have}", file=sys.stderr)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("requirements", help="requirements-pdf-gpu.txt")
    parser.add_argument("--check-installed", action="store_true")
    args = parser.parse_args(argv)
    with open(args.requirements, encoding="utf-8") as handle:
        text, removed = strip(handle.read())
    if args.check_installed:
        problems = check_installed(removed)
        if problems:
            print("the base image does not match the lock:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
