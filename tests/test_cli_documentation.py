"""Keep the user-facing CLI references in sync with argparse."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _long_cli_options() -> set[str]:
    tree = ast.parse((ROOT / "book_maker/cli.py").read_text(encoding="utf-8"))
    options: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        options.update(
            arg.value
            for arg in node.args
            if isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value.startswith("--")
        )
    return options


def _documented_options(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    # Avoid treating --batch_size as documentation for --batch.
    return {
        option
        for option in _long_cli_options()
        if re.search(re.escape(option) + r"(?![\w-])", text)
    }


def test_cli_references_mention_every_long_option():
    expected = _long_cli_options()
    for name in ("README.md", "README-CN.md", "docs/cmd.md"):
        documented = _documented_options(ROOT / name)
        assert (
            documented == expected
        ), f"{name} is missing CLI options: {sorted(expected - documented)}"
