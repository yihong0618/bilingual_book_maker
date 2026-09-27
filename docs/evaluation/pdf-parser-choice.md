# Why docling, and what the opendataloader survey found

## Abstract

The first version of the PDF route read files with OpenDataLoader's Java engine and called docling only for OCR. On September 21 the owner ruled that docling becomes the only parser: a Java runtime is a system install that pip cannot provide, and the time Java saved was a few seconds against a translation measured in minutes. A later survey (September 22) went through OpenDataLoader module by module on 20 arXiv papers and asked one question: is any part of it strictly better than docling, and worth porting? One small rule was, for running headers and footers. On heading levels, OpenDataLoader's style ranking was ported into the route's own heading rule. On everything else docling was already better, the two tied, or there was no evidence.

## Setup

- **The ruling (September 21), owner's words:** "normal users has to download the jre, which means is about as troublesome. The time saved? Trivial. We'd offer them cpu and gpu route for those, and two separate pdf dependencies txt and a install guide for mps/cuda or cpu at md. Don't want anything java here. Make it so."
- **The time measured then:** 0.34 s per page for Java against 0.87 s per page for docling, on a 5-page cut. The difference is under 3 seconds.
- **The survey (September 22):** an Opus worker, the owner's instruction "if func's strictly better then port". 20 arXiv papers, 100 pages, fresh docling and OpenDataLoader output with labels; 14 experiments, each porting or prototyping the module and running it on docling's items where that made sense.
- **The first comparison (September 21)**, `260921-eval-DOCLING_VS_OPENDATALOADER.md`, is superseded. The owner does not trust it (it was run by a weaker agent), so its figures are not used on this page. The survey reproduced two of its figures and marks which.

## Results

The survey's ranking, shortened; the evidence column is copied from the record:

| # | module | evidence | verdict |
|---|---|---|---|
| 1 | Running header/footer repetition | docling leaks 2 page numbers in 100 pages. Ported as OpenDataLoader runs it: 4 moves, 2 wrong. Narrowed version (move an item only when docling marked an overlapping item as header/footer 1–2 pages away): **2/2 right, 0 wrong**. OpenDataLoader alone leaks 34 of docling's headers/footers | port the narrowed version (low impact) |
| 2 | ≠ overlay stroke (own fix, not OpenDataLoader) | 14 cases in prose on 4 papers. OpenDataLoader is as wrong as docling on three of them. A pypdfium2 prototype fixed **13/14** and safely skipped 1 | build our own |
| 3 | Split diacritics (own fix) | OpenDataLoader fails too, sometimes worse. A text-only prototype fixed 15 words on 6 papers | build our own |
| 4 | Ligatures | raw ligatures: docling 0, OpenDataLoader 791 | docling already better |
| 5 | Debris | 4.8 vs 51.4 blocks/paper, reproduced exactly | docling already better |
| 6 | Captions | bound to their figure/table: docling 56/70, OpenDataLoader 15/71 | docling already better |
| 7 | Lists | OpenDataLoader 317 items, at least 93 obviously false; docling 87, genuine | docling already better |
| 8 | Footnotes | OpenDataLoader has no footnote detection on untagged PDFs; docling labels 56 | docling already better |
| 9 | Paragraph assembly | docling joins 40 paragraphs across pages; OpenDataLoader splits 30 | docling already better |
| 10 | Tables | cell recall 0.995 vs 0.519 (from stats.md, not re-run) | docling already better |
| 11 | Hidden text inside figures | one paper, pages 1-2: docling 45 lines vs OpenDataLoader 8,657 | docling already better |
| 12 | Hyphen joining | 0 disagreements in 100 pages | not worth it |
| 13 | Reading order (XY-Cut++) | ported and run on docling's boxes: average ordering score 0.9892 → 0.9899, better on 4 papers, **worse on 3** | not worth it |
| 14–17 | Tiny text, hidden-text contrast, duplicate text, underline/strikethrough | no real case in the corpus | not worth it |
| 18 | U+FFFD warning | no sample PDF available | unverified |
| 19 | Table-of-contents detection | OpenDataLoader itself disables it for false positives | not worth it |
| 20–21 | Tagged structure tree; headings and levels | reading order worse than docling on 3 tagged PDFs | headings: separate study |

**Headings, from the separate study** (196 headings on the same 20 papers):

| arm | recall | junk | exact level | consecutive relation |
|---|---|---|---|---|
| ODL-java (its 260921 output) | 94/196 | 50 | 18/94 (92/94 with its one-level offset forgiven) | 73/75 |
| docling as shipped, flat `##` | 195/196 | 6 | 83/195 (42.6%) | 102/175 |
| docling + ODL's style rank | same | same | 163/195 (83.6%) | 153/175 |
| docling + numbering first, style rank as fallback | same | same | **187/195 (95.9%)**, 19/20 papers fully right | 170/175 |

OpenDataLoader's levels are right when it finds a heading, but it finds fewer than half. docling finds nearly all of them and gets the levels wrong. The route keeps docling's detection and takes the levels from the page; OpenDataLoader's style ranking is part of that rule. See [Why heading levels come from the page](pdf-heading-levels.md).

**Hidden text inside figures.** The old route needed a sanitizer for a figure placed about 140 times under small clip windows. docling's picture region swallows the clipped copies (45 lines against 8,657), so the sanitizer was removed with the Java engine.

## Decision

docling is the only PDF parser. There is no Java, no OpenDataLoader and no zero-model tier. PDF support is an optional install with a CPU route and a GPU route (see [PDF extra](../installation-pdf.md)); Pandoc stays, because it builds the EPUB, not the text.

From the survey: the style ranking went into the heading rule (built); the narrowed header/footer rule, the ≠ fix and the diacritics fix are recommended, and none of them is built yet. The `--with-ocr` and `--no-gpu` flags from the Java route still parse as hidden aliases of `--pdf-ocr` and `--device cpu`.

## Limits

- The survey covers arXiv papers only: no books, novels, scans or non-Latin scripts.
- The header/footer rule was narrowed after the worker saw the misfire on the same corpus, so its 2/2 is not a held-out result.
- The table figure is copied from the earlier statistics, not re-run.
- The 0.34 s against 0.87 s per page timing comes from the superseded September 21 comparison, as quoted in the owner-approved design; it is the only timing available.

Source: docs/260922-eval-ODL_MODULE_SURVEY.md; docs/260921-plan-PDF_DOCLING_ONLY_AND_INSTALL_ROUTES.md for the ruling; docs/260922-feat-PDF_HEADING_LEVELS.md for the heading table; docs/260921-eval-DOCLING_VS_OPENDATALOADER.md is superseded and not trusted (repository, dated records)
