"""The docs -> GitHub Wiki converter (tools/docs_to_wiki.py)."""

import importlib.util
from pathlib import Path

import pytest

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
    names = {"en/index.md": "Home", "en/features/plan-mode.md": "Plan mode"}
    unresolved = []
    out = w.convert_links(
        "[a](../index.md) [b](plan-mode.md) [c](https://x.y/z.md) "
        "`[u](y)` ![i](../../img/output_style.jpg)",
        "en/features/session-mode.md",
        names,
        unresolved,
    )
    assert out == (
        "[a](Home) [b](Plan-mode) [c](https://x.y/z.md) "
        "`[u](y)` ![i](img/output_style.jpg)"
    )
    assert unresolved == []


def test_readme_links_point_into_the_wiki_or_at_github():
    names = {"en/index.md": "Home", "en/features/plan-mode.md": "Plan mode"}
    unresolved = []
    out = w.convert_readme_links(
        "[zh](./README-CN.md) [p](./docs/en/features/plan-mode.md) [a](#plan-mode) "
        "[w](https://github.com/x/wiki/Docker) ![i](./docs/img/output_style.jpg) "
        "[![L](https://img.shields.io/l.svg)](./LICENSE)",
        names,
        unresolved,
    )
    assert out == (
        f"[zh]({w.REPO_URL}/blob/main/README-CN.md) [p](Plan-mode) [a](#plan-mode) "
        "[w](https://github.com/x/wiki/Docker) ![i](img/output_style.jpg) "
        f"[![L](https://img.shields.io/l.svg)]({w.REPO_URL}/blob/main/LICENSE)"
    )
    assert unresolved == []


def test_home_is_the_readme(tmp_path):
    # PIN (owner 260928, docs/260928-docs-WIKI_HOME_README_EVAL_GROUP.md): the
    # wiki's Home is README.md, the owner's own text, with only its relative
    # links rewritten; docs/index.md is not published.
    assert w.main(["docs_to_wiki.py", str(tmp_path)]) == 0
    home = (tmp_path / "Home.md").read_text(encoding="utf-8")
    readme = (w.ROOT / "README.md").read_text(encoding="utf-8")
    assert home.splitlines()[0] == readme.splitlines()[0]
    assert len(home.splitlines()) == len(readme.splitlines())
    assert "](./" not in home
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    shouye = (tmp_path / "首页.md").read_text(encoding="utf-8")
    readme_cn = (w.ROOT / "README-CN.md").read_text(encoding="utf-8")
    assert len(shouye.splitlines()) == len(readme_cn.splitlines())
    assert "](./" not in shouye


def test_every_nav_page_converts_without_a_dangling_link(tmp_path):
    # PIN (260926): the wiki namespace is flat, so two nav pages may not
    # share a name, and every relative link on the site must land on a page.
    assert w.main(["docs_to_wiki.py", str(tmp_path)]) == 0
    pages = {p.stem for p in tmp_path.glob("*.md")}
    assert {"Home", "_Sidebar", "_Footer", "Docker", "Plan-mode"} <= pages
    assert (tmp_path / "img" / "output_style.jpg").is_file()
    assert '=== "' not in (tmp_path / "Docker.md").read_text(encoding="utf-8")
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    # The English names are the URLs from before the split and stay put.
    assert {
        "Recommended-settings-for-EPUB",
        "Recommended-settings-for-PDF",
        "首页",
        "快速开始",
        "EPUB-推荐设置",
    } <= pages


def _mini_repo(tmp_path, monkeypatch, nav):
    """A repository with two languages, for the flattener to read."""
    repo = tmp_path / "repo"
    (repo / "docs" / "en").mkdir(parents=True)
    (repo / "docs" / "zh").mkdir(parents=True)
    (repo / "docs" / "img").mkdir(parents=True)
    (repo / "docs" / "img" / "a.jpg").write_bytes(b"jpg")
    (repo / "mkdocs.yml").write_text(f"site_name: x\n\nnav:\n{nav}\n", "utf-8")
    (repo / "README.md").write_text(
        "# Maker\n\n[q](./docs/en/quickstart.md) [zh](./README-CN.md)\n", "utf-8"
    )
    (repo / "README-CN.md").write_text(
        "# 制作\n\n[快](./docs/quickstart.md) [英](./README.md) "
        "![图](./docs/img/a.jpg)\n",
        "utf-8",
    )
    for lang, title in (("en", "Quick start"), ("zh", "快速开始")):
        (repo / "docs" / lang / "index.md").write_text("unused\n", "utf-8")
        (repo / "docs" / lang / "quickstart.md").write_text(
            f"# {title}\n\n![a](../img/a.jpg)\n", "utf-8"
        )
    monkeypatch.setattr(w, "ROOT", repo)
    monkeypatch.setattr(w, "DOCS", repo / "docs")
    monkeypatch.setattr(w, "MKDOCS", repo / "mkdocs.yml")
    monkeypatch.setattr(w, "README", repo / "README.md")
    monkeypatch.setattr(w, "README_CN", repo / "README-CN.md")
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    return wiki


_TWO_LANGUAGE_NAV = (
    "  - English:\n"
    "      - Home: en/index.md\n"
    "      - Quick start: en/quickstart.md\n"
    "  - 中文:\n"
    "      - 首页: zh/index.md\n"
    "      - 快速开始: zh/quickstart.md"
)


def test_each_language_home_is_its_readme(tmp_path, monkeypatch):
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    wiki = _mini_repo(tmp_path, monkeypatch, _TWO_LANGUAGE_NAV)
    assert w.main(["docs_to_wiki.py", str(wiki)]) == 0
    assert (wiki / "Home.md").read_text("utf-8") == (
        f"# Maker\n\n[q](Quick-start) [zh]({w.REPO_URL}/blob/main/README-CN.md)\n"
    )
    # README-CN's `./docs/quickstart.md` has no language directory: the
    # Chinese home means the Chinese page.
    assert (wiki / "首页.md").read_text("utf-8") == (
        f"# 制作\n\n[快](快速开始) [英]({w.REPO_URL}/blob/main/README.md) "
        "![图](img/a.jpg)\n"
    )


def test_sidebar_opens_with_the_two_languages(tmp_path, monkeypatch):
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    wiki = _mini_repo(tmp_path, monkeypatch, _TWO_LANGUAGE_NAV)
    assert w.main(["docs_to_wiki.py", str(wiki)]) == 0
    assert (wiki / "_Sidebar.md").read_text("utf-8") == (
        "- **English**\n"
        "  - [Home](Home)\n"
        "  - [Quick start](Quick-start)\n"
        "- **中文**\n"
        "  - [首页](首页)\n"
        "  - [快速开始](快速开始)\n"
    )
    assert "docs/" in (wiki / "_Footer.md").read_text("utf-8")


def test_unicode_page_name_is_written_verbatim(tmp_path, monkeypatch):
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    wiki = _mini_repo(
        tmp_path,
        monkeypatch,
        _TWO_LANGUAGE_NAV.replace("快速开始: zh/", "快速 开始: zh/"),
    )
    assert w.main(["docs_to_wiki.py", str(wiki)]) == 0
    page = wiki / "快速-开始.md"
    assert page.is_file()
    assert page.read_text("utf-8") == "# 快速开始\n\n![a](img/a.jpg)\n"


@pytest.mark.parametrize(
    "nav, message",
    [
        # the same title in both languages
        (
            _TWO_LANGUAGE_NAV.replace("快速开始: zh/", "Quick start: zh/"),
            "two nav pages on wiki page 'Quick-start'",
        ),
        # equal once the space becomes a hyphen and case is ignored
        (
            _TWO_LANGUAGE_NAV.replace("快速开始: zh/", "quick-Start: zh/"),
            "two nav pages on wiki page 'quick-Start'",
        ),
        (
            _TWO_LANGUAGE_NAV.replace("快速开始: zh/", "快速/开始: zh/"),
            "contains '/'",
        ),
    ],
)
def test_a_name_the_flat_wiki_cannot_hold_is_refused(
    tmp_path, monkeypatch, nav, message
):
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    wiki = _mini_repo(tmp_path, monkeypatch, nav)
    (wiki / "keep.md").write_text("earlier generation\n", "utf-8")
    with pytest.raises(SystemExit) as refused:
        w.main(["docs_to_wiki.py", str(wiki)])
    assert message in str(refused.value)
    assert "zh/quickstart.md" in str(refused.value)
    # Refused before anything in the checkout is touched.
    assert (wiki / "keep.md").is_file()


def test_an_empty_name_is_refused():
    # PIN owner 260929: one wiki, language as the top level of docs/ (docs/260929-docs-WIKI_ZH_LANGUAGE_TOP_LEVEL.md)
    with pytest.raises(SystemExit, match="empty title"):
        w.check_names({"zh/x.md": " "})
