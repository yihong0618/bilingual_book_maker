"""The docs -> GitHub Wiki converter (tools/docs_to_wiki.py)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "docs_to_wiki", ROOT / "tools" / "docs_to_wiki.py"
)
w = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(w)


def test_tabs_become_details_blocks():
    text = (
        "Intro\n\n"
        '=== "Linux"\n\n'
        "    ```bash\n"
        "    run --here\n"
        "    ```\n\n"
        '=== "Windows"\n\n'
        "    run `there`\n\n"
        "After.\n"
    )
    assert w.convert_tabs(text) == (
        "Intro\n\n"
        "<details>\n<summary>Linux</summary>\n\n"
        "```bash\nrun --here\n```\n\n"
        "</details>\n\n"
        "<details>\n<summary>Windows</summary>\n\n"
        "run `there`\n\n"
        "</details>\n\n"
        "After.\n"
    )


def test_anchors_follow_github_not_mkdocs():
    # MkDocs collapses the punctuation run, GitHub keeps every hyphen.
    heading = " Named endpoints: `--provider`"
    assert w.mkdocs_slug(heading) == "named-endpoints-provider"
    assert w.github_slug(heading) == "named-endpoints---provider"


def test_links_point_at_pages_and_images_at_img(tmp_path):
    names = {"index.md": "Home", "features/plan-mode.md": "Plan mode"}
    unresolved = []
    out = w.convert_links(
        "[a](../index.md) [b](plan-mode.md) [c](https://x.y/z.md) "
        "`[u](y)` ![i](../img/output_style.jpg)",
        "features/session-mode.md",
        names,
        unresolved,
    )
    assert out == (
        "[a](Home) [b](Plan-mode) [c](https://x.y/z.md) "
        "`[u](y)` ![i](img/output_style.jpg)"
    )
    assert unresolved == []


def test_every_nav_page_converts_without_a_dangling_link(tmp_path):
    # PIN (260926): the wiki namespace is flat, so two nav pages may not
    # share a name, and every relative link on the site must land on a page.
    assert w.main(["docs_to_wiki.py", str(tmp_path)]) == 0
    pages = {p.stem for p in tmp_path.glob("*.md")}
    assert {"Home", "_Sidebar", "_Footer", "Docker", "Plan-mode"} <= pages
    assert (tmp_path / "img" / "output_style.jpg").is_file()
    assert '=== "' not in (tmp_path / "Docker.md").read_text(encoding="utf-8")
