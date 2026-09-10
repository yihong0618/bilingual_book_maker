"""`--prompt <file>.md`: the block form, read here rather than by a package.

Three things this pins:

1. The shapes an operator writes — system + user, user only, a style, and the
   `## Developer Message` heading an older file carries — all parse, and the
   ones that cannot be read say what to write instead. The table form is the
   one that matters: the repo shipped a sample in it, so the README's own
   example command failed, and the error a reader gets has to name block form.
2. `.md` reaches the *same* validation and the *same* echo as a JSON prompt.
   It used to return early with a line of its own, which is how it came to
   accept what JSON refused and to carry no `style` section at all.
3. A `user` template naming a placeholder nothing fills is refused here, at
   parse time, rather than by `str.format` on the first paid request.
"""

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from book_maker.cli import check_user_placeholders, parse_prompt_arg
from book_maker.prompt_file import PromptFileError, parse_prompt_markdown

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = "Translate {text} into {language}."
SYSTEM = "Be faithful."
STYLE = "Clipped, unsentimental."


def _md(tmp_path, body, name="p.md"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


BLOCK = f"""# Translation Prompt

## System Message

{SYSTEM}

## Conversation

**User:**

{TEMPLATE}
"""


class TestTheBlockForm:
    def test_system_and_user(self):
        assert parse_prompt_markdown(BLOCK) == {"user": TEMPLATE, "system": SYSTEM}

    def test_a_user_only_file(self):
        text = f"# P\n\n## Conversation\n\n**User:**\n\n{TEMPLATE}\n"
        assert parse_prompt_markdown(text) == {"user": TEMPLATE}

    def test_a_style_section_is_carried(self):
        text = BLOCK.replace(
            "## Conversation", f"## Style\n\n{STYLE}\n\n## Conversation"
        )
        assert parse_prompt_markdown(text)["style"] == STYLE

    def test_an_empty_style_section_is_no_style(self):
        # what the shipped examples rely on: the heading shows the section
        # exists without imposing a voice on anyone who copies the file
        text = BLOCK.replace("## Conversation", "## Style\n\n## Conversation")
        assert "style" not in parse_prompt_markdown(text)

    def test_the_developer_heading_is_read_as_system(self):
        # `developer` is not a channel this tool has; `system` is the only
        # operator channel on every wire. An old file still works, silently —
        # there is nothing for its author to do about it.
        text = BLOCK.replace("## System Message", "## Developer Message")
        assert parse_prompt_markdown(text)["system"] == SYSTEM

    def test_the_turn_may_trail_the_role_line(self):
        text = f"## Conversation\n\n**User:** {TEMPLATE}\n"
        assert parse_prompt_markdown(text)["user"] == TEMPLATE

    def test_a_multi_line_turn_keeps_its_newlines(self):
        text = "## Conversation\n\n**User:**\n\nLine one.\n\n{text}\n"
        assert parse_prompt_markdown(text)["user"] == "Line one.\n\n{text}"

    def test_bold_text_inside_a_turn_is_not_a_role(self):
        # `**Important**` is emphasis in the operator's own template, not a
        # turn delimiter. Read as one it opened a role nothing asks for and
        # silently truncated the template at that line.
        text = (
            "## Conversation\n\n**User:**\n\nTranslate {text}.\n"
            "**Important**\nPreserve every footnote.\n"
        )
        assert parse_prompt_markdown(text)["user"] == (
            "Translate {text}.\n**Important**\nPreserve every footnote."
        )

    def test_a_bold_role_name_without_a_colon_is_content_too(self):
        text = "## Conversation\n\n**User:**\n\n{text}\n**Assistant**\nnot a turn\n"
        assert parse_prompt_markdown(text)["user"] == (
            "{text}\n**Assistant**\nnot a turn"
        )

    def test_the_colon_may_sit_outside_the_bold(self):
        text = "## Conversation\n\n**User**:\n\n{text}\n"
        assert parse_prompt_markdown(text)["user"] == "{text}"

    def test_an_assistant_turn_is_not_the_template(self):
        text = f"## Conversation\n\n**User:**\n\n{TEMPLATE}\n\n**Assistant:**\n\nOk.\n"
        assert parse_prompt_markdown(text) == {"user": TEMPLATE}


class TestHeadingsAWriterActuallyProduces:
    """An editor's byte order mark and markdown's own heading indentation are
    not malformed input. Both used to leave every heading unmatched, so the
    whole file parsed as no sections at all and the error blamed the
    conversation."""

    def test_a_byte_order_mark_does_not_hide_the_first_heading(self):
        # an editor's BOM lands on the first line, which is the heading in a
        # file with no title
        text = f"\ufeff## System Message\n\n{SYSTEM}\n\n## Conversation\n\n**User:**\n\n{TEMPLATE}\n"
        assert parse_prompt_markdown(text)["system"] == SYSTEM

    def test_a_heading_indented_up_to_three_spaces_is_still_a_heading(self):
        text = BLOCK.replace("## System Message", "  ## System Message")
        assert parse_prompt_markdown(text)["system"] == SYSTEM

    def test_a_section_heading_indented_past_markdown_is_refused_by_name(self):
        # markdown reads four spaces as a code block, so this is not a
        # heading — but it is unmistakably a misindented one, and losing the
        # instruction under it in silence is the whole bug class here
        text = BLOCK.replace("## System Message", "    ## System Message")
        with pytest.raises(PromptFileError) as refused:
            parse_prompt_markdown(text, "p.md")
        assert "System Message" in str(refused.value)

    def test_deeper_indentation_that_names_no_section_is_left_as_content(self):
        # a `##` line inside a code block in the operator's own template
        text = (
            "## Conversation\n\n**User:**\n\nTranslate:\n\n"
            "    ## a heading in the source\n\n{text}\n"
        )
        assert "## a heading in the source" in parse_prompt_markdown(text)["user"]


class TestWhatCannotBeRead:
    def test_the_table_form_names_block_form(self):
        text = "## Conversation\n\n| Role | Content |\n|---|---|\n| User | {text} |\n"
        with pytest.raises(PromptFileError) as refused:
            parse_prompt_markdown(text, "p.md")
        assert "Block form only" in str(refused.value)
        assert "**User:**" in str(refused.value)

    def test_no_conversation_section(self):
        with pytest.raises(PromptFileError) as refused:
            parse_prompt_markdown(f"## System Message\n\n{SYSTEM}\n", "p.md")
        assert "**User:**" in str(refused.value)

    def test_a_conversation_with_no_user_turn(self):
        with pytest.raises(PromptFileError) as refused:
            parse_prompt_markdown("## Conversation\n\n**Assistant:**\n\nOk.\n", "p.md")
        assert "**User:**" in str(refused.value)

    def test_an_unknown_heading_is_named_rather_than_dropped(self):
        # a misspelled heading means the instruction under it was never sent
        with pytest.raises(PromptFileError) as refused:
            parse_prompt_markdown(
                BLOCK.replace("## System Message", "## Sytem"), "p.md"
            )
        assert "## Sytem" in str(refused.value)


class TestTheMarkdownPathIsValidatedLikeEveryOther:
    def test_a_template_without_text_is_refused(self, tmp_path):
        path = _md(tmp_path, "## Conversation\n\n**User:**\n\nTranslate it.\n")
        with pytest.raises(ValueError) as refused:
            parse_prompt_arg(path, announce=False)
        assert "`{text}`" in str(refused.value)

    def test_the_echo_is_the_same_line_json_prints(self, tmp_path, capsys):
        parse_prompt_arg(json.dumps({"user": TEMPLATE, "system": SYSTEM}))
        from_json = capsys.readouterr().out
        parse_prompt_arg(_md(tmp_path, BLOCK))
        from_md = capsys.readouterr().out
        assert from_json.startswith("prompt config:")
        assert from_md == from_json

    def test_the_shipped_sample_parses(self):
        parsed = parse_prompt_arg("prompt_md.prompt.md", announce=False)
        assert "{text}" in parsed["user"]
        assert parsed["system"]

    def test_an_uppercase_extension_is_still_a_file(self, tmp_path):
        # `--prompt Config.JSON` named a real file and was translated with as
        # a literal template, unfilled placeholders and all
        path = tmp_path / "P.JSON"
        path.write_text(json.dumps({"user": TEMPLATE}), encoding="utf-8")
        assert parse_prompt_arg(str(path), announce=False) == {"user": TEMPLATE}

    def test_a_missing_markdown_file_is_not_read_as_a_template(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_prompt_arg(str(tmp_path / "absent.md"), announce=False)


class TestThePlaceholderPreflight:
    """Q6: the highest-value fix. `{name}` in a user template used to raise
    KeyError inside the translator, on the first request of a paid run."""

    def test_an_unknown_placeholder_is_named(self):
        with pytest.raises(ValueError) as refused:
            parse_prompt_arg('{"user": "Render {text} for {audience}"}', announce=False)
        assert "{audience}" in str(refused.value)

    @pytest.mark.parametrize(
        "template",
        [
            "Translate {text}",
            "To {language}:{crlf}{text}",
            'Answer as {{"tone": "dry"}}: {text}',
        ],
    )
    def test_what_a_run_can_fill_passes(self, template):
        assert parse_prompt_arg(json.dumps({"user": template}), announce=False)

    def test_an_attribute_reach_is_named_not_a_traceback(self):
        # `str.format` answers `{language.name}` with AttributeError, which
        # the pre-flight did not catch: the template passed here and killed
        # the run on its first paid request instead.
        with pytest.raises(ValueError) as refused:
            parse_prompt_arg('{"user": "To {language.name}: {text}"}', announce=False)
        assert "{language.name}" in str(refused.value)

    def test_an_item_reach_is_named_too(self):
        with pytest.raises(ValueError) as refused:
            check_user_placeholders("{text[0]}")
        assert "{text[0]}" in str(refused.value)

    def test_a_positional_field_is_refused_too(self):
        with pytest.raises(ValueError):
            check_user_placeholders("Translate {} — {text}")

    def test_the_optional_sections_stay_lenient(self):
        # `system` and `style` carry no required placeholder, so a brace in
        # them is the operator's own text and must not kill a run
        parsed = parse_prompt_arg(
            json.dumps(
                {
                    "user": TEMPLATE,
                    "system": 'Answer as {"tone": "dry"}.',
                    "style": "Use {em dashes} sparingly.",
                }
            ),
            announce=False,
        )
        assert parsed["system"] == 'Answer as {"tone": "dry"}.'


class TestThePackageIsGone:
    """`promptdown` was a 17-star single-maintainer dependency reached by one
    lazy import, and it shipped a console script naming a module that exists
    nowhere — so every `pip install bbook-maker` put a broken `promptdown`
    executable on PATH under a name it does not own."""

    def test_the_reader_imports_nothing_third_party(self):
        tree = ast.parse(
            (ROOT / "book_maker/prompt_file.py").read_text(encoding="utf-8")
        )
        imported = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert imported <= set(sys.stdlib_module_names)

    def test_it_is_not_declared_anywhere(self):
        for name in ("pyproject.toml", "requirements.txt"):
            assert "promptdown" not in (ROOT / name).read_text(encoding="utf-8"), name

    def test_no_console_script_shadows_it(self):
        # read the [project.scripts] table textually: tomllib is stdlib only
        # from 3.11, and this project still supports 3.10
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r"^\[project\.scripts\]\n(.*?)(?:^\[|\Z)", text, re.M | re.S)
        assert match, "pyproject.toml has no [project.scripts] table"
        names = re.findall(r"^([\w-]+)\s*=", match.group(1), re.M)
        assert names == ["bbook_maker"]
