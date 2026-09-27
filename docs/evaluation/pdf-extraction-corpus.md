# How the PDF extraction was checked on real papers and books

## Abstract

The PDF route was checked twice on real files. The first check (September 20) ran 75 arXiv papers through the route as it was then, on the OpenDataLoader Java engine. It found that 3 of 75 papers produced no reading edition, and it listed the extraction faults a translator would inherit. That route has since been replaced by docling, so its numbers describe the old engine; the lessons that carried over are the two-page rule and the dense-page warning. The second corpus (September 23) is a fixture set of papers, scans and web or Word exports built for the docling studies on the other evaluation pages. This page describes both, so you can judge what the other pages rest on.

## Setup

**September 20, old route.** 11 chosen papers (Word 2019, Word 365 and macOS Quartz producers; vector figures; two-column physics; page-1 tables; 1997 and 2002 dvips papers; equation-heavy maths) plus a 64-paper probe batch across 8 more categories. 75 distinct PDFs, 1,785 pages profiled, 18 read-back extraction cells (15 Java-only, 4 with OCR), first two pages only. The route then included a sanitizer that rasterized figures hiding large amounts of clipped text.

**September 23, docling.** The fixtures are cut to two pages: pages 1 and 2, and for long books a two-page slice from the middle. Three families:

- **paper:** born-digital technical documents: 20 arXiv papers, LaTeX lecture notes and a LaTeX book, a calibre-built textbook (Griffiths), a datasheet, a journal paper with a photo page and a code listing.
- **scan:** image-only book, magazine and typewriter scans; scans that carry an embedded OCR layer (ABBYY, Internet Archive); two degraded English scans; two traditional Chinese scans, one of them vertical.
- **web:** browser-saved Wikipedia pages, Word and WPS exports, a generated account statement, a landscape packing-list form, a Chinese report saved from Chrome.

Every downloaded fixture is the publisher's or uploader's original PDF, with source and license in the record.

Runtime calibration on the test machine, copied from the record:

| cell | result |
|---|---|
| interpreter | pyenv 3.12.12, docling 2.129.0, torch 2.9.0, MPS available, no CUDA, 16 GB |
| OCR engines importable (system) | rapidocr (onnxruntime) only; easyocr, ocrmac, tesseract absent |
| docling `auto` OCR order on darwin | ocrmac → rapidocr+onnxruntime → easyocr → rapidocr+torch → **rapidocr/onnxruntime here** |
| eval venv `venv-ocr` | + ocrmac 1.0.1, easyocr 1.7.2; tesseract 5.5.3 via brew (eng only) |
| typed paper pp.1-2, no OCR (arxiv 1706.03762) | convert 5.0 s, peak RSS 854 MB |
| CIA typewriter scan pp.1-2, `--ocr auto` | convert 10.6 s, peak RSS 2259 MB, engine rapidocr |
| Luna vision probe (`gpt-5.6-luna`, 612×792 render, detail=high) | 626 prompt tokens, 2.6 s; 200 completion tokens all reasoning → empty text; `reasoning_effort=low` or ≥2000 tokens needed |
| Luna transcription sample (CIA p.2, first 3 lines) | exact: "Approved For Release 2003/09/10 : CIA-RDP96-00788R001700210016-5 / right brain intuitive insight to achieve a complete comfortable grasp of the / concepts involved. Nevertheless once that is done, I am confident that their" |

## Results

**September 20, old route, result table copied from the record** (Java-only unless marked OCR):

| id | producer | p1 / p2 chars | hdgs | img | tbl | sanitized | pixel diff p1 / p2 | verdict |
|---|---|---|---|---|---|---|---|---|
| 1906.00495v1 | pdfTeX | 5502 / 5610 | 5 | 1 | 0 | p2 figure (251 paths, 188 vis) | 0 / 1.10% in box | blemish: heading soup |
| 2011.00876v1 | pdfTeX | 5552 / 4718 | 3 | 1 | 0 | p2 figure (88, 213) | 0 / 1.75% | blemish: stamp/eq headings |
| 2010.03667v1 | pdfTeX | 5001 / 5491 | 3 | 2 | 0 | p2 figure (36, 248, hid 11) | 0 / 1.22% | clean |
| 2010.03667v1 OCR | | 4965 / 5499 | 7 all H1 | 2 | 0 | same | | blemish: flat headings |
| 1906.00790v2 | pdfTeX | 3767 / 4414 | 6 | 0 | 0 | no | | problem: table as prose |
| 1906.00790v2 OCR | | 3792 / 4410 | 5 all H1 | 0 | 1 | no | | blemish: table data altered |
| 2310.19788v3 | pdfTeX | 3590 / 4243 | 5 | 0 | 0 | no | | blemish: stamp is H1 |
| 1901.04946v1 | Word 2019 | 5548 / 6468 | 1 | 0 | 0 | no | | blemish: no headings |
| 2009.12164v1 | Word 365 | 4341 / 6360 | 1 | 0 | 0 | no | | problem: order broken |
| 2009.12164v1 OCR | | 4995 / 5452 | 3 | 0 | 3 | no | | problem: 1,181 chars lost, bogus table |
| 2010.15624v1 | Quartz (Word/macOS) | 1620 / 2196 | 1 | 0 | 0 | no | | clean |
| 2008.02692v1 | dvips+GS | 4491 / 6751 | 1 | 12 | 0 | no | | problem: equations shredded |
| math/0211159 | dvips+GS 2002 | 1899 / 2741 | 2 | 2 | 0 | no | | clean |
| hep-th/9711200 | dvips+GS 1997 | 1350 / 2836 | 1 | 1 | 0 | no | | blemish: prose as bullets |
| 1905.03268v2 | pdfTeX | — | | | | | | problem: engine crash, both routes |
| 2006.05569v1 (probe) | pdfTeX | | | 1 | 0 | p2 figure (164, 649) | 0 / 2.94% | clean |
| 2006.04323v2 (probe) | pdfTeX | | | 1 | 0 | p2 figure (154, 318) | 0 / 2.27% | clean |
| 1808.07354v1 (probe) | pdfTeX | 4886 / 6364 | 3 | 0 | 0 | p1 clipped-text ×3 (57 ch) | 0.009% / 0 | clean: duplicate line removed |
| 2007.03671v2 (probe) | pdfTeX | — / 3919 | | 0 | 0 | no (blind spot) | | problem: 30 label lines leaked |

- **3 of 75 papers (4%) gave no reading edition:** one engine crash (`The glyph can not be mapped to Unicode`), and two refusals by the route's own Markdown check on ordinary maths prose (`T ⊂ V \s` and `Fσ[u](y) =`).
- **The sanitizer fired 6 times, every time on a genuine figure.** Outside the replaced box the page was pixel-identical, and no visible text was removed. It has a blind spot: a chart drawn directly on the page (no form object) is never examined, and its 30 label lines leaked into the text.
- **`PAGE_TOO_DENSE` never fired.** The densest real page of 1,785 had 10,790 characters, against the 12,000 limit.
- **Old-engine faults:** OCR dropped 1,181 characters of body prose on a Word 365 paper and invented an 85-row table on the same paper; tables arrived as prose; display equations were shredded on dvips papers; headings were unreliable (a drop cap or the arXiv stamp as the title).

**September 23, docling:** the results on this corpus are on their own pages:

- [Why heading levels come from the page](pdf-heading-levels.md)
- [Why formulas are pictures](pdf-formulas-as-images.md)
- [Why a vision model reads scans better](pdf-ocr-llm-vs-local.md)
- [Why docling-parse stays](pdf-page-render-backend.md)
- [Where an LLM fixes layout](pdf-structure-llm-roles.md)

The record also pins a survey of how others score extraction (OmniDocBench, olmOCR-bench, docling-eval) and of docling's own extension points. Its main takeaway for this project: olmOCR-bench's pass/fail checker can score our own fixtures once tests are written for them. That has not been done.

## Decision

- Every PDF run, test or smoke starts with the first two pages (`--pages 1-2`), and you read the extracted `source.md`, headings at least, before any translation is paid for. A page whose Markdown runs to thousands of lines is broken, not long.
- `PAGE_TOO_DENSE` stays as a warning at 12,000 characters per page. Its headroom over a dense bibliography page is about 11%, so a false alarm there is possible; it costs a warning line.
- The route moved from OpenDataLoader to docling (see [Why docling](pdf-parser-choice.md)). The figure sanitizer belonged to the old route and is not part of the docling route.

## Limits

- The September 20 numbers describe the retired Java engine. They are kept because they explain the two-page rule and the density warning, not as a measure of the current route.
- Both corpora are cut to two pages per file. No whole book was run as part of either check.
- The September 23 fixtures are mostly English, with one simplified and two traditional Chinese scans. Other scripts are not covered apart from Greek lines on two scans and one Romanian scan.
- No shared benchmark score (OmniDocBench, olmOCR-bench) was computed for this route.

Source: docs/260920-eval-ARXIV_CORPUS_PDF_EXTRACTION_CHECK.md; docs/260923-eval-PDF_EVAL_FIXTURES_AND_PRACTICE.md (repository, dated records)
