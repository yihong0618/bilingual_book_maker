#!/usr/bin/env python3
"""Run the real EPUB loader with a deterministic, offline stand-in translator."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as E
from zipfile import ZipFile

from build import build

HERE = Path(__file__).resolve().parent


class OfflineTranslator:
    TRANSLATION_ERROR_MARKER = None
    model_name = "offline-table-fixture"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False
        self.context_flag = False
        self.calls = 0

    def translate(self, text):
        self.calls += 1
        return "示例译文：" + str(text)

    def translate_list(self, texts):
        return [self.translate(text) for text in texts]


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def table_shapes(path):
    with ZipFile(path) as archive:
        member = next(
            name for name in archive.namelist() if name.endswith("/content.xhtml")
        )
        root = E.fromstring(archive.read(member))
    tables = {}
    for table in (e for e in root.iter() if local(e) == "table"):
        rows, active_spans = [], {}
        for row_index, row in enumerate(e for e in table.iter() if local(e) == "tr"):
            occupied = {
                col for col, through in active_spans.items() if through >= row_index
            }
            cells = [e for e in row if local(e) in {"th", "td"}]
            column = 0
            for cell in cells:
                while column in occupied:
                    column += 1
                width, height = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
                for col in range(column, column + width):
                    occupied.add(col)
                    active_spans[col] = row_index + height - 1
                column += width
            rows.append(
                {
                    "cells": len(cells),
                    "logical_columns": max(occupied, default=-1) + 1,
                    "spans": [
                        [int(c.get("colspan", 1)), int(c.get("rowspan", 1))]
                        for c in cells
                    ],
                }
            )
        tables[table.get("id")] = rows
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=HERE.parents[1],
        help="Checkout whose loader to exercise.",
    )
    parser.add_argument(
        "--label", default="current", help="Output subdirectory under runs/."
    )
    args = parser.parse_args()
    if Path(args.label).name != args.label or args.label in {".", "..", ""}:
        parser.error("--label must be a simple directory name")
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo))
    from book_maker.loader.epub_loader import EPUBBookLoader

    output_dir = HERE / "runs" / args.label
    output_dir.mkdir(parents=True, exist_ok=True)
    source = build(output_dir / "table-columns.epub")
    log = io.StringIO()
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        loader = EPUBBookLoader(
            str(source),
            OfflineTranslator,
            key="",
            resume=False,
            language="zh-hans",
            disclose=False,
        )
        loader.plan_mode = True
        loader.plan_classify = "all"
        loader.translate_tags = "auto"
        loader.only_filelist = "content.xhtml"
        loader.quiet = True
        loader.make_bilingual_book()
    (output_dir / "loader.log").write_text(log.getvalue(), encoding="utf-8")
    translated = output_dir / "table-columns_bilingual.epub"
    before, after = table_shapes(source), table_shapes(translated)
    report = {
        "loader_source": str(
            Path(sys.modules[EPUBBookLoader.__module__].__file__).resolve()
        ),
        "source": str(source),
        "output": str(translated),
        "stand_in_calls": loader.translate_model.calls,
        "source_tables": before,
        "output_tables": after,
        "table_geometry_preserved": before == after,
        "note": "The stand-in adds a Chinese prefix; it does not test translation quality. Both buggy and fixed outputs are retained without asserting success.",
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
