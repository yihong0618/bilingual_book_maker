"""Keep the user-facing CLI references in sync with argparse.

An option carrying `help=argparse.SUPPRESS` is deliberately unadvertised and
is skipped here — it is absent from `--help` too, so requiring a README row
for it would be requiring the opposite of what it asks for. An option whose
help text opens with "deprecated" is skipped for the same reason (owner
ruling 260908): the flag itself warns and points at its replacement at
runtime, and a README is for what people should use, not what still parses.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _is_suppressed(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "help":
            continue
        value = keyword.value
        if isinstance(value, ast.Attribute) and value.attr == "SUPPRESS":
            return True
        # A help text that opens with "deprecated" warns and redirects at
        # runtime; the references document the replacement instead.
        return (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value.lower().startswith("deprecated")
        )
    return False


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
        if _is_suppressed(node):
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


def test_help_renders():
    """`--help` must survive argparse's own %-interpolation.

    argparse runs every help string through `%` formatting, so a literal
    percent sign in one of them ("90% of it") is read as a conversion and
    raises TypeError — taking down `--help` for the whole CLI, not just the
    flag that owns the string. Nothing else exercises this: the help text is
    never formatted until someone asks for it.
    """
    proc = subprocess.run(
        [sys.executable, "make_book.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--context-compact-at" in proc.stdout
    # the interpolated help strings render too — argparse's own `%` path,
    # which is what a literal percent sign elsewhere would break
    assert "available:" in proc.stdout


# Flags that appear in the references without being ours: `--help`, docker's
# own flags in the docker section, and the shell placeholders the skill uses
# in its recipes.
_FOREIGN_FLAGS = frozenset(
    {"--help", "--rm", "--name", "--mount", "--tag", "--flag", "--git-common-dir"}
)

# Every file an operator or an agent reads to decide what to type. The skill
# and its references are read by an LLM, which cannot tell a flag that was
# removed from one that still exists.
_REFERENCES = (
    "README.md",
    "README-CN.md",
    "docs/cmd.md",
    "docs/migration.md",
    "docs/model_lang.md",
    "docs/env_settings.md",
    "docs/quickstart.md",
    "docs/index.md",
    "docs/prompt.md",
    "docs/book_source.md",
    "docs/installation.md",
    ".agents/skills/bbm-plan/SKILL.md",
    ".agents/skills/bbm-plan/references/providers.md",
    ".agents/skills/bbm-plan/references/prompt-files.md",
    ".agents/skills/bbm-plan/assets/env.example",
)


def _legacy_flags() -> set[str]:
    """The removed flags `legacy_cli` still accepts and rewrites.

    A reference may name these — migration.md is nothing but these — so they
    are documentable even though argparse has never heard of them.
    """
    from book_maker import legacy_cli

    flags = set(legacy_cli._KEY_FLAGS)
    flags.update({"--model", "--ollama_model", "--custom_api", "--deployment_id"})
    return flags


def test_references_name_no_flag_that_does_not_exist():
    """The other direction of the test above: nothing invented, nothing stale.

    A flag deleted from the parser leaves its rows behind in prose, and a
    typo'd name (`--accumulation_num`) reads exactly like a real one. Both
    send a reader — or an agent following the skill — to an argparse error.
    """
    known = _long_cli_options() | _legacy_flags() | _FOREIGN_FLAGS
    # Options hidden with argparse.SUPPRESS are absent from
    # _long_cli_options by design; they are still legal to type, so a
    # reference naming one is not an error.
    tree = ast.parse((ROOT / "book_maker/cli.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") == "add_argument"
        ):
            known.update(
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.startswith("--")
            )
    for name in _REFERENCES:
        path = ROOT / name
        if not path.exists():  # docs/ is trimmed on some branches
            continue
        named = set(re.findall(r"--[A-Za-z][A-Za-z0-9_-]*", path.read_text("utf-8")))
        assert not (
            named - known
        ), f"{name} names flags the CLI does not have: {sorted(named - known)}"
