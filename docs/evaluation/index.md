# Measurements behind the parameters

Each page here measures one parameter: what was compared, on which sample, and the numbers that came out. The default is stated with the measurement it rests on, and each page ends with Limits: the sample size and what was not measured. Every number is copied from the measurement's own record.

## Translation

### [Units and tokens per request](grouping-batch-size.md)

`--max-batch-units`, `--accumulated_num`. Where grouped requests start to shift translations into the wrong slot (measured onset: 64 units), and the cost of the retry overhead at each size. Default 16 units and 1200 to 1600 tokens per request; 8 units and 800 tokens without a strict schema.

### [Session compact budget](session-compact-budget.md)

`--context-compact-at`. Session cost at 4096 and 8192 over 300 units, compactions counted. Default 8192, chosen for continuity on local models; 4096 used fewer tokens.

### [Jev as the plan classifier](plan-classifier-jev.md)

`--classify-model`, `--classify-min-confidence`. TypeSafe's Jev against gpt-5.6-luna on the 31 signatures of one book (27 of 31 agree), Jev's prompt tokens before and after the lean requests, and the confidence gate measured over 662 signatures in 45 EPUBs (0.95). Default: the translating model.

## PDF to EPUB

### [OCR engines on scanned pages](pdf-ocr-engines.md)

`--ocr-engine`. rapidocr, ocrmac, easyocr and tesseract on the same 20 scanned pages, character error rate and time per page, on the CPU. tesseract and ocrmac for English, rapidocr for simplified Chinese, ocrmac or rapidocr for traditional. Default `auto`.

### [Region roles with an image model](pdf-structure-llm-roles.md)

`--img-model`. 123 structure faults on 85 pages, and which of them a vision model relabelling regions fixes (40 of 66 wrong labels) and which it does not (running headers, shattered OCR pages). About 3,000 prompt tokens a page. Off unless a model is named.
