#!/usr/bin/env python3
"""Turn the MkDocs pages under docs/ into a GitHub Wiki tree.

    python tools/docs_to_wiki.py <wiki checkout>

The site has one tree per language, `docs/en/` and `docs/zh/`, under the
top-level nav groups `English` and `中文`; images live once in `docs/img/`.
The wiki is a flat space of pages, so every page takes its title from the
mkdocs.yml nav (the two English "Recommended settings" entries get their
section appended; the Chinese titles are unique already), every name must be
unique across both languages, the nav becomes `_Sidebar.md` with the two
language groups at the top, and the site's few MkDocs-only constructs are
rewritten for GitHub's renderer:

- relative `.md` links (with `../`, subdirectories and `#anchor`) become
  links to the wiki page names;
- `=== "Tab"` blocks become `<details><summary>Tab</summary>` blocks, so a
  reader still opens only the system or document type they have;
- the images under `docs/img/` are copied into `img/` of the wiki checkout.

`Home` is README.md itself, not `docs/en/index.md` (owner 260928: the README
is the owner's own text), and the Chinese home `首页` is README-CN.md in the
same way. Their links are relative to the repository root: pages and images
under `docs/` point into the wiki (a `docs/<page>.md` without a language
directory means the home's own language), any other file at GitHub.

Pages that are not in the nav are left out. Nothing is committed or pushed;
the caller reviews the checkout and does that.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MKDOCS = ROOT / "mkdocs.yml"
README = ROOT / "README.md"
README_CN = ROOT / "README-CN.md"
REPO_URL = "https://github.com/yihong0618/bilingual_book_maker"
LANGUAGES = ("en", "zh")

# The wiki namespace is flat, so the two English "Recommended settings" pages
# need their section in the name. These names are the pages' URLs from before
# the language split and must not change.
TITLE_OVERRIDES = {
    "en/features/recommended-epub.md": "Recommended settings for EPUB",
    "en/features/recommended-pdf.md": "Recommended settings for PDF",
    "en/index.md": "Home",
}


def home_sources() -> dict[str, tuple[Path, str]]:
    """Each language's home page is a README at the repository root, not its
    docs/<lang>/index.md: `nav path -> (README, language)`."""
    return {"en/index.md": (README, "en"), "zh/index.md": (README_CN, "zh")}


_NAV_LINE = re.compile(r"^( *)- (.+?):(?: (.+))?$")
# The label may hold one image, as a badge does: [![alt](src)](target).
_LINK = re.compile(r"(!?)\[((?:[^\[\]]|!\[[^\]]*\]\([^)]*\))*)\]\(([^)\s]+)\)")
_TAB = re.compile(r'^=== "(.+)"\s*$')


def read_nav(text: str) -> list:
    """Parse the `nav:` block of mkdocs.yml into a nested list.

    Each item is `(title, path)` for a page or `(title, [children])` for a
    section. The block is plain indented YAML lists, which is all the nav
    ever uses, so a small reader replaces a yaml dependency.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("nav:"))
    except StopIteration:
        raise SystemExit("mkdocs.yml has no nav block")
    block = []
    for line in lines[start + 1 :]:
        if line.strip() == "":
            continue
        if not line.startswith(" "):
            break
        block.append(line)

    root: list = []
    stack: list[tuple[int, list]] = [(-1, root)]
    for line in block:
        match = _NAV_LINE.match(line)
        if not match:
            raise SystemExit(f"unreadable nav line: {line!r}")
        indent = len(match.group(1))
        title, path = match.group(2).strip(), match.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if path is None:
            children: list = []
            parent.append((title, children))
            stack.append((indent, children))
        else:
            parent.append((title, path.strip()))
    return root


def pages_of(nav: list) -> list[tuple[str, str]]:
    """Flatten the nav to `(title, path)` pairs in nav order."""
    out = []
    for title, item in nav:
        if isinstance(item, list):
            out.extend(pages_of(item))
        else:
            out.append((title, item))
    return out


def page_name(path: str, title: str) -> str:
    return TITLE_OVERRIDES.get(path, title)


def wiki_file(name: str) -> str:
    """GitHub stores a page called "A b" as `A-b.md`; every other character,
    Chinese included, is kept as it is (a UTF-8 file name)."""
    return name.replace(" ", "-")


def check_names(names: dict[str, str]) -> None:
    """Refuse a nav whose pages cannot share one flat wiki.

    Every page needs a name, a name with `/` would be a path, and two pages
    on one wiki file (the same name, or names equal once spaces become
    hyphens or case is ignored, as GitHub's page URLs are) would overwrite
    each other. Exits non-zero naming the pages.
    """
    seen: dict[str, str] = {}
    for path, name in names.items():
        if not name.strip():
            raise SystemExit(f"nav page {path} has an empty title")
        if "/" in name:
            raise SystemExit(f"nav page {path}: title {name!r} contains '/'")
        key = wiki_file(name).casefold()
        if key in seen:
            raise SystemExit(
                f"two nav pages on wiki page {wiki_file(name)!r}: "
                f"{seen[key]} and {path}"
            )
        seen[key] = path


def convert_tabs(text: str) -> str:
    """Rewrite pymdownx.tabbed blocks as <details> blocks.

    A tab is `=== "Title"` followed by a body indented by four spaces; the
    block ends at the first non-blank line that is not indented.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        match = _TAB.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        title = match.group(1)
        body: list[str] = []
        i += 1
        while i < len(lines):
            line = lines[i]
            if line.strip() == "":
                body.append("")
            elif line.startswith("    "):
                body.append(line[4:])
            else:
                break
            i += 1
        while body and body[-1] == "":
            body.pop()
        while body and body[0] == "":
            body.pop(0)
        out.extend(
            ["<details>", f"<summary>{title}</summary>", ""] + body + ["", "</details>"]
        )
        # A blank line after the block, so GitHub closes it before the next
        # tab or paragraph (the body loop consumed the source's blank line).
        if i < len(lines) and lines[i].strip() != "":
            out.append("")
    return "\n".join(out) + "\n"


def mkdocs_slug(heading: str) -> str:
    """The toc extension's slug: punctuation dropped, runs of space and
    hyphen collapsed to one hyphen."""
    value = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"[-\s]+", "-", value)


def github_slug(heading: str) -> str:
    """GitHub's slug keeps every hyphen, so `a: --b` becomes `a---b`."""
    value = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return value.replace(" ", "-")


def anchors(path: Path) -> dict[str, str]:
    """Map each heading's MkDocs anchor to its GitHub anchor."""
    out = {}
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            heading = line.lstrip("#")
            out[mkdocs_slug(heading)] = github_slug(heading)
    return out


def normalise(path: str) -> str:
    """Collapse `.` and `..` in a relative path."""
    parts: list[str] = []
    for part in Path(path).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def convert_links(
    text: str, source: str, names: dict[str, str], unresolved: list[str]
) -> str:
    """Point relative .md links at wiki pages and images at `img/`."""
    base = Path(source).parent

    def resolve(target: str) -> str:
        return (base / target).as_posix().replace("\\", "/")

    def repl(match: re.Match) -> str:
        bang, label, target = match.groups()
        if "://" in target or target.startswith(("#", "mailto:")):
            return match.group(0)
        path, _, anchor = target.partition("#")
        if not bang and not path.endswith(".md"):
            return match.group(0)
        key = normalise(resolve(path))
        if bang:
            if not (DOCS / key).is_file():
                unresolved.append(f"{source}: image {target}")
                return match.group(0)
            return f"![{label}]({key})"
        if key in names:
            page = wiki_file(names[key])
            if anchor:
                known = anchors(DOCS / key)
                if anchor not in known:
                    unresolved.append(f"{source}: {target} (no such heading)")
                    return match.group(0)
                anchor = "#" + known[anchor]
            return f"[{label}]({page}{anchor})"
        unresolved.append(f"{source}: {target}")
        return match.group(0)

    return _LINK.sub(repl, text)


def convert_readme_links(
    text: str,
    names: dict[str, str],
    unresolved: list[str],
    lang: str = "en",
    readme: str = "README.md",
) -> str:
    """Rewrite a README's root-relative links for its language's home page.

    `./docs/<lang>/x.md` and `./docs/img/...` are taken as they are; a page
    link without a language directory (`./docs/x.md`) means `lang`'s page.
    """

    def repl(match: re.Match) -> str:
        bang, label, target = match.groups()
        if "://" in target or target.startswith(("#", "mailto:")):
            return match.group(0)
        path, sep, anchor = target.partition("#")
        key = normalise(path)
        if key.startswith("docs/"):
            rest = key[len("docs/") :]
            if not bang and rest.split("/")[0] not in LANGUAGES + ("img",):
                rest = f"{lang}/{rest}"
            link = f"{bang}[{label}]({rest}{sep}{anchor})"
            return convert_links(link, "index.md", names, unresolved)
        if not (ROOT / key).exists():
            unresolved.append(f"{readme}: {target}")
            return match.group(0)
        kind = "raw" if bang else "blob"
        return f"{bang}[{label}]({REPO_URL}/{kind}/main/{key}{sep}{anchor})"

    return _LINK.sub(repl, text)


def sidebar(nav: list, names: dict[str, str], depth: int = 0) -> list[str]:
    lines = []
    for title, item in nav:
        pad = "  " * depth
        if isinstance(item, list):
            lines.append(f"{pad}- **{title}**")
            lines.extend(sidebar(item, names, depth + 1))
        else:
            lines.append(f"{pad}- [{title}]({wiki_file(names[item])})")
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    wiki = Path(argv[1]).resolve()
    if not wiki.is_dir():
        raise SystemExit(f"not a directory: {wiki}")

    nav = read_nav(MKDOCS.read_text(encoding="utf-8"))
    pages = pages_of(nav)
    names = {path: page_name(path, title) for title, path in pages}
    if len(names) != len(pages):
        raise SystemExit("a docs page is listed twice in the nav")
    check_names(names)

    # Drop pages of an earlier generation so a renamed page leaves no orphan.
    for old in wiki.glob("*.md"):
        old.unlink()
    if (wiki / "img").is_dir():
        shutil.rmtree(wiki / "img")

    unresolved: list[str] = []
    homes = home_sources()
    for path, name in names.items():
        if path in homes:
            readme, lang = homes[path]
            text = convert_readme_links(
                readme.read_text(encoding="utf-8"),
                names,
                unresolved,
                lang=lang,
                readme=readme.name,
            )
        else:
            text = convert_tabs((DOCS / path).read_text(encoding="utf-8"))
            text = convert_links(text, path, names, unresolved)
        (wiki / f"{wiki_file(name)}.md").write_text(text, encoding="utf-8")

    (wiki / "img").mkdir()
    for image in sorted((DOCS / "img").iterdir()):
        if image.is_file():
            shutil.copy2(image, wiki / "img" / image.name)

    (wiki / "_Sidebar.md").write_text(
        "\n".join(sidebar(nav, names)) + "\n", encoding="utf-8"
    )
    (wiki / "_Footer.md").write_text(
        "These pages are generated from the repository's `docs/` directory "
        "(Home from README.md, 首页 from README-CN.md) by "
        "`tools/docs_to_wiki.py`; edit them there.\n",
        encoding="utf-8",
    )

    print(f"{len(names)} pages, {sum(1 for _ in (wiki / 'img').iterdir())} images")
    for line in unresolved:
        print(f"unresolved: {line}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
