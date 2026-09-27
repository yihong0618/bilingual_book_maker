# Evaluation

Several defaults in this tool were set by a measurement, and some by an owner ruling on top of one. These pages show what was asked, what was measured, and what came out of it, so you can judge a default before you change it. Each page ends with Limits: the sample size and what was not measured. Where a default is a chosen margin rather than a measured optimum, the page says so.

Every number on these pages is copied from a dated record in the repository's `docs/` folder, named at the foot of each page. Nothing is recomputed or rounded differently. Where two records disagree, the page says which is later and which one the owner trusts.

## Translation

### [Why 16 units per request, and why the budget halves on weaker endpoints](grouping-batch-size.md)

A request that carries too many paragraphs starts to shift translations into the wrong slot, and the measured fault onset was 64 units. Verdict: 16 units and 1200 to 1600 tokens per request, halved to 8 units and 800 tokens without a strict schema, as a chosen safety margin, not a measured optimum.

### [Why the session window compacts at 8192 tokens](session-compact-budget.md)

Session mode carries a running conversation and summarizes it when it grows past a threshold. Verdict: 8192 is kept for continuity on local models, and it is not the cheap setting; at 300 units, 4096 used fewer tokens.

### [Why the tool probes the endpoint and steps down a ladder instead of trusting the schema flag](structured-output-ladder.md)

Endpoints that claim schema support do not always apply it. Verdict: probe each endpoint once, remember the result, and step down from schema to JSON to delimiters, splitting a reply that cannot be aligned.

### [Can a dedicated classifier decide the plan? Jev against gpt-5.6-luna](plan-classifier-jev.md)

TypeSafe's Jev and gpt-5.6-luna classified the same 31 signatures of one book. Verdict: 27 of 31 agree, the 4 disagreements are Jev's low-confidence skips. Leaner requests later halved Jev's prompt tokens without moving agreement beyond run-to-run variation, and a doubtful skip now falls back to translate at a gate measured on 45 EPUBs. Jev is available as a classify model; the default stays the translating model.

## PDF to EPUB

### [Why heading levels are read from the page and docling's heading model stays off](pdf-heading-levels.md)

docling writes every heading at the same level, and the EPUB contents follow the headings. Verdict: numbering first and glyph size second gave 187 of 195 exact levels on 20 papers; docling's own model gave 48.

### [Why display formulas are cropped as pictures instead of decoded](pdf-formulas-as-images.md)

docling finds display equations but does not read them. Verdict: each one is cropped from the page as a picture; decoding to LaTeX stays off after two failed tries.

### [Why a vision model reads a scanned page better than a local OCR engine](pdf-ocr-llm-vs-local.md)

A vision model had the lower character error rate on all 25 scanned pages measured, and invented text at low resolution. Verdict: the evidence favors a vision model at full resolution. OCR stays local for now (`--pdf-ocr`); no default needs a vision model, and an image model runs only when you name one.

### [Which local OCR engine reads a scan best: rapidocr, ocrmac, easyocr and tesseract](pdf-ocr-engines.md)

The four engines `--ocr-engine` can name, on the same 20 scanned pages, all on the CPU. Verdict: tesseract and ocrmac for English, rapidocr for simplified Chinese, ocrmac or rapidocr for traditional; easyocr last and slowest. The default stays `auto`.

### [Why the PDF route keeps docling-parse and only swaps the page image on JBIG2 scans](pdf-page-render-backend.md)

docling's default backend draws some scans wrong, so OCR reads nothing. Verdict: keep docling-parse and take only the page image from pypdfium2 when the file carries a JBIG2 mask (8 of 8 pages found, no false positives). Built.

### [Where an LLM fixes a layout detector's mistakes, and where it cannot](pdf-structure-llm-roles.md)

123 structure faults on 85 pages were catalogued, and a vision model was asked to relabel regions. Verdict: it fixed 40 of 66 wrong labels, but running headers and shattered OCR pages need geometry rules first. The role pass is built as `--img-model`; the geometry rules are not.

### [How the PDF extraction was checked on real papers and books](pdf-extraction-corpus.md)

A 75-paper check of the old Java route and the fixture set behind the docling studies. Verdict: run the first two pages and read `source.md` before you pay for a translation.

### [Why docling, and what the opendataloader survey found](pdf-parser-choice.md)

The route dropped the Java engine. Verdict: a module-by-module survey found only a small header/footer rule and the heading style ranking worth taking; docling was already better on the rest.
