# Table-column reproduction fixture

This small EPUB 3 fixture contains original, invented reading-room examples.
It contains no text, images, or tables from a published book.

`content.xhtml` exercises:

- Plain `th` headers and direct text in `td` cells.
- Header text inside an inline `span` with an ID, and `headers` references.
- A paragraph nested inside a cell.
- `colspan` and `rowspan`, including a spanning footer.

Both source tables have three logical columns. A bilingual translation should
keep the same rows, cells, and span attributes. Appending translated `th`/`td`
siblings increases the column count; translations belong inside those cells.

## Rebuild

From the repository root:

```sh
python test_books/table_columns/build.py
```

The standard-library builder writes `table-columns.epub` beside the sources.
ZIP timestamps and package metadata are fixed, so rebuilding is reproducible.
The EPUB includes its required uncompressed first `mimetype` entry, container,
package metadata, manifest, spine, and EPUB 3 navigation document.

## Offline reproduction

Use a Python environment with this repository's dependencies installed:

```sh
python test_books/table_columns/reproduce.py --label current
```

This instantiates the real `EPUBBookLoader` in plan mode with a stand-in that
returns `示例译文：` followed by the input. It needs no API keys, credentials,
network, or model. It rebuilds a private input copy under `runs/current/` and
writes `table-columns_bilingual.epub`, `report.json`, and `loader.log` there.
The report includes physical cell counts, span attributes, and logical column
counts accounting for active rowspans. It reports geometry changes without
asserting that a fix is already present, so it can reproduce an older version.

To compare checkouts, run this same script twice with the same Python:

```sh
python test_books/table_columns/reproduce.py --repo /path/to/old-checkout --label before
python test_books/table_columns/reproduce.py --repo /path/to/fixed-checkout --label after
```

The selected checkout is prepended to the Python import path. Each report records
the actual loader source path. Open the two generated EPUBs in a reading system
or compare their reports: `table_geometry_preserved` should change from `false`
to `true`. This fixture checks layout preservation, not translation quality.

For independent EPUB specification validation, run an installed EPUBCheck:

```sh
java -jar /path/to/epubcheck.jar test_books/table_columns/table-columns.epub
```

EPUBCheck alone may accept a table with unintended extra columns; compare the
geometry as well. Generated reproduction outputs are ignored by Git.

## Validation results

Compared with upstream commit `b5a251a0a088ec4792a03f439d64792c1600919a`:

| Table | Original logical columns per row | Before fix | After fix |
| --- | --- | --- | --- |
| `simple` | 3, 3, 3 | 6, 5, 6 | 3, 3, 3 |
| `spanning` | 3, 3, 3, 3, 3 | 6, 6, 6, 6, 6 | 3, 3, 3, 3, 3 |

EPUBCheck 5.4.0 reported **0 fatals, 0 errors, and 0 warnings** for the
committed source fixture and both generated outputs. The geometry report
therefore catches a regression that EPUB conformance validation alone does not.

The default test suite checks table geometry through both tag and plan modes.
The EPUBCheck regression test additionally validates the source and plan-mode
output when `EPUBCHECK_JAR` or an `epubcheck` executable is available.
