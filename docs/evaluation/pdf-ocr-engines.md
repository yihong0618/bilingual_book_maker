# Which local OCR engine reads a scan best: rapidocr, ocrmac, easyocr and tesseract

Since this measurement, the `pdf` extra installs onnxruntime, and ocrmac on macOS, so rapidocr and ocrmac download nothing on a fresh install; the install notes below describe the install as it was measured.

## Abstract

`--ocr-engine` picks the engine docling runs on pages with no text layer. The four engines docling can run locally were measured on the same 20 scanned pages (14 English, 6 Chinese), with the same scorer and the same language asked of each, all on the CPU. On English, tesseract had the lowest error (mean CER 0.104) and ocrmac came next (0.136); rapidocr (0.169) and easyocr (0.267) trailed. On simplified Chinese, rapidocr was best (mean Han CER 0.054 over five pages, against ocrmac 0.097 and tesseract 0.116); on the one traditional page ocrmac and rapidocr were close (0.043 and 0.060). easyocr was last almost everywhere, took three times as long and up to 6.6 GB of memory, and dropped text on Chinese pages. ocrmac, Apple's own engine, was the fastest (median 6 s per two pages) and needs nothing downloaded.

The default stays `auto`. This page is the evidence behind [Which OCR engine](../features/pdf-ocr-engines.md).

## Setup

- **Route:** the PDF route's own extraction, run through the staged harness (`tools/pdf_to_book.py extract`), docling 2.129.0, `--pdf-ocr --device cpu --pages 1-2`, default OCR mode (a page's embedded layer is kept where docling keeps it). One conversion at a time, each under a memory cap, `HF_HUB_OFFLINE=1`.
- **Engines:** rapidocr 3.9.2 on onnxruntime 1.23.2 (PP-OCRv6 small models, shipped inside the rapidocr package); ocrmac 1.0.1 (Apple Vision, macOS 26); easyocr 1.7.2 on PyTorch 2.9.0 (CPU); tesseract 5.5.3 (Homebrew) with the `tessdata_fast` language files (`eng`, `chi_sim`, `chi_tra`, `osd`).
- **Languages:** the same `iso:` tag for every engine, which docling maps onto each engine's own code: `iso:en` for the English pages, `iso:zh-Hans` for simplified Chinese, `iso:zh-Hant` for traditional. The run's own line confirmed the engine and the languages in every cell.
- **Device:** `--device cpu` on every cell, so the layout and table models ran on the CPU for all four arms. rapidocr (onnxruntime), easyocr (PyTorch) and tesseract ran on the CPU; ocrmac runs inside Apple's Vision framework, which chooses its own hardware.
- **Machine:** Apple silicon, 16 GB, Python 3.12.12.
- **Pages:** two-page cuts; the scored pages are the ones with ground truth:

| fixture | scored page(s) | what it is | language flag |
|---|---|---|---|
| scan_cia_typewriter | 1, 2 | degraded typewriter scan (CCITT) | `iso:en` |
| scan_cia_typewriter_mid | 2 | typewriter scan, mid-document | `iso:en` |
| scan_forward1963 | 1 | 1963 magazine, two columns, footnotes | `iso:en` |
| scan_en_degraded_1_mid | 1 | colour pamphlet with a table | `iso:en` |
| scan_en_degraded_2_mid | 1 | bitonal old novel page | `iso:en` |
| scan_zh_hans_qm_mid | 1 | Internet Archive book scan, simplified Chinese, invisible OCR layer, JBIG2 masks | `iso:zh-Hans` |
| zh_hant_scan_2_mid | 2 | traditional Chinese, horizontal, with a table | `iso:zh-Hant` |
| syn_arxiv_1706_03762_{clean,deg} | 1, 2 | born-digital paper rendered as a scan | `iso:en` |
| syn_wps_eula_{clean,deg} | 1, 2 | WPS licence document rendered as a scan | `iso:en` |
| syn_web_zh_report_chrome_{clean,deg} | 1, 2 | Chinese web report rendered as a scan | `iso:zh-Hans` |

Synthetic scans: 200 dpi; clean is a lossless PNG, degraded is grayscale, rotated 1.5°, blurred (r=0.8) and JPEG q40. Their ground truth is exact (the source page's text layer). The real scans' ground truth was typed from the page image. The Chinese book scan carries JBIG2 masks, so its page images came from pypdfium2 on every arm (the run printed the JBIG2 line).

- **Scorer:** the one used for [the vision-model study](pdf-ocr-llm-vs-local.md), unchanged. Normalisation: Markdown syntax stripped (image links, comments, `#`, table pipes and rules, `**`, escapes, list bullets), HTML unescaped, NFKC, every quote variant mapped to `"`, every hyphen and dash removed, whitespace collapsed (removed entirely for Chinese). **CER** is order-sensitive: a paragraph read in another order counts as errors. **Han CER** is CER over the Han characters only. **line_err** ignores order: the length-weighted mean of (1 − partial match) over ground-truth lines, so it measures recognition and detection alone. The heading the route adds above an extraction that does not open with one (`# <file name>`) was removed before scoring.

Commands, one per cell (`$E` is the evaluation directory, `$ENGINE` one of the four, `$LANG` from the table above):

```bash
HF_HUB_OFFLINE=1 TESSDATA_PREFIX=$E/tessdata \
  python tools/pdf_to_book.py extract $E/fixtures/p12/scan_cia_typewriter.pdf \
  --output $E/runs/K/$ENGINE_cia/bundle \
  --pdf-ocr --ocr-engine $ENGINE --ocr-lang iso:en --device cpu --pages 1-2
```

and the same with `--ocr-engine auto` on one fixture per script, to see what `auto` picks.

## Results

CER per page, lower is better; Chinese pages also give the Han-only CER:

| page | rapidocr | ocrmac | easyocr | tesseract | auto |
|---|---|---|---|---|---|
| cia p1 | 0.158 | 0.119 | 0.073 | 0.120 | 0.119 |
| cia p2 | 0.272 | 0.109 | 0.148 | 0.110 | 0.109 |
| ciamid p2 | 0.188 | 0.029 | 0.080 | 0.017 | - |
| fwd p1 | 0.251 | 0.231 | 0.260 | 0.212 | - |
| deg1 p1 | 0.355 | 0.360 | 0.965 | 0.046 | - |
| deg2 p1 | 0.543 | 0.491 | 0.602 | 0.408 | - |
| zh p1 | 0.112 (Han 0.009) | 0.210 (Han 0.056) | 0.390 (Han 0.235) | 0.201 (Han 0.036) | 0.210 (Han 0.056) |
| hant2 p2 | 0.069 (Han 0.060) | 0.041 (Han 0.043) | 0.670 (Han 0.724) | 0.322 (Han 0.350) | 0.041 (Han 0.043) |
| synarxc p1 | 0.051 | 0.050 | 0.124 | 0.051 | - |
| synarxc p2 | 0.040 | 0.003 | 0.056 | 0.004 | - |
| synarxd p1 | 0.142 | 0.055 | 0.431 | 0.169 | - |
| synarxd p2 | 0.002 | 0.269 | 0.534 | 0.272 | - |
| syneulac p1 | 0.015 | 0.014 | 0.071 | 0.015 | - |
| syneulac p2 | 0.171 | 0.008 | 0.028 | 0.008 | - |
| syneulad p1 | 0.110 | 0.163 | 0.215 | 0.014 | - |
| syneulad p2 | 0.066 | 0.008 | 0.153 | 0.008 | - |
| synzhc p1 | 0.042 (Han 0.000) | 0.040 (Han 0.003) | 0.564 (Han 0.514) | 0.077 (Han 0.019) | - |
| synzhc p2 | 0.076 (Han 0.053) | 0.045 (Han 0.000) | 0.656 (Han 0.611) | 0.085 (Han 0.021) | - |
| synzhd p1 | 0.188 (Han 0.191) | 0.167 (Han 0.163) | 0.799 (Han 0.862) | 0.193 (Han 0.171) | - |
| synzhd p2 | 0.136 (Han 0.018) | 0.312 (Han 0.262) | 0.800 (Han 0.848) | 0.390 (Han 0.333) | - |

line_err per page (order ignored):

| page | rapidocr | ocrmac | easyocr | tesseract | auto |
|---|---|---|---|---|---|
| cia p1 | 0.085 | 0.001 | 0.045 | 0.003 | 0.001 |
| cia p2 | 0.181 | 0.076 | 0.110 | 0.078 | 0.076 |
| ciamid p2 | 0.093 | 0.007 | 0.036 | 0.000 | - |
| fwd p1 | 0.009 | 0.020 | 0.054 | 0.007 | - |
| deg1 p1 | 0.010 | 0.043 | 0.559 | 0.033 | - |
| deg2 p1 | 0.054 | 0.118 | 0.291 | 0.019 | - |
| zh p1 | 0.036 | 0.144 | 0.142 | 0.119 | 0.144 |
| hant2 p2 | 0.050 | 0.037 | 0.536 | 0.272 | 0.037 |
| synarxc p1 | 0.034 | 0.033 | 0.057 | 0.033 | - |
| synarxc p2 | 0.025 | 0.002 | 0.034 | 0.004 | - |
| synarxd p1 | 0.035 | 0.034 | 0.265 | 0.082 | - |
| synarxd p2 | 0.001 | 0.003 | 0.316 | 0.007 | - |
| syneulac p1 | 0.001 | 0.000 | 0.047 | 0.001 | - |
| syneulac p2 | 0.109 | 0.002 | 0.018 | 0.002 | - |
| syneulad p1 | 0.001 | 0.041 | 0.117 | 0.000 | - |
| syneulad p2 | 0.047 | 0.002 | 0.085 | 0.002 | - |
| synzhc p1 | 0.031 | 0.030 | 0.482 | 0.062 | - |
| synzhc p2 | 0.059 | 0.035 | 0.563 | 0.067 | - |
| synzhd p1 | 0.089 | 0.043 | 0.693 | 0.074 | - |
| synzhd p2 | 0.047 | 0.044 | 0.719 | 0.117 | - |

Means per class, CER (Han CER for Chinese) / line_err:

| class | n | rapidocr | ocrmac | easyocr | tesseract |
|---|---|---|---|---|---|
| typewriter scan, English (cia p1, p2, ciamid p2) | 3 | 0.206 / 0.120 | 0.086 / 0.028 | 0.100 / 0.064 | 0.082 / 0.027 |
| magazine and old print, English (fwd, deg1, deg2) | 3 | 0.383 / 0.024 | 0.361 / 0.060 | 0.609 / 0.301 | 0.222 / 0.020 |
| synthetic scan, English, clean | 4 | 0.069 / 0.042 | 0.019 / 0.009 | 0.070 / 0.039 | 0.019 / 0.010 |
| synthetic scan, English, degraded | 4 | 0.080 / 0.021 | 0.124 / 0.020 | 0.333 / 0.196 | 0.116 / 0.023 |
| book scan, simplified Chinese (zh, JBIG2) | 1 | 0.009 / 0.036 | 0.056 / 0.144 | 0.235 / 0.142 | 0.036 / 0.119 |
| scan, traditional Chinese, horizontal (hant2) | 1 | 0.060 / 0.050 | 0.043 / 0.037 | 0.724 / 0.536 | 0.350 / 0.272 |
| synthetic scan, simplified Chinese, clean | 2 | 0.027 / 0.045 | 0.001 / 0.033 | 0.562 / 0.523 | 0.020 / 0.065 |
| synthetic scan, simplified Chinese, degraded | 2 | 0.105 / 0.068 | 0.213 / 0.044 | 0.855 / 0.706 | 0.252 / 0.096 |

Per engine:

| engine | pages | mean CER, English (n) | mean Han CER, Chinese (n) | mean line_err, English | mean line_err, Chinese | median extraction s per two-page cell | peak memory MB (max) |
|---|---|---|---|---|---|---|---|
| rapidocr | 20 | 0.169 (14) | 0.055 (6) | 0.049 | 0.052 | 12 | 2248 |
| ocrmac | 20 | 0.136 (14) | 0.088 (6) | 0.027 | 0.056 | 6 | 1661 |
| easyocr | 20 | 0.267 (14) | 0.632 (6) | 0.145 | 0.523 | 37 | 6596 |
| tesseract | 20 | 0.104 (14) | 0.155 (6) | 0.019 | 0.119 | 10 | 1902 |
| auto | 4 | 0.114 (2) | 0.050 (2) | 0.039 | 0.090 | 12 | 1977 |

Extraction time is the run's own `PDF extracted: 2 pages, …, N s.` line: model loading plus layout, tables and OCR for two pages. Per cell: rapidocr 9–19 s, ocrmac 4–11 s, tesseract 7–21 s, easyocr 32–90 s (the 90 s cell includes its first-use model download).

**What `auto` picked:** ocrmac in 3 of 3 cells (`OCR engine: ocrmac (docling's choice on this install), …`), because ocrmac was installed; its text was identical to the ocrmac cells.

**Reading order, not recognition, explains several CER gaps.** synarxd p2: ocrmac and tesseract CER 0.269 and 0.272 against rapidocr's 0.002, but line_err 0.003 and 0.007: the text is right, a block was placed elsewhere. deg1: rapidocr and ocrmac CER 0.355 and 0.360 with line_err 0.010 and 0.043. For a book, a paragraph in the wrong place is a real defect, so CER is the primary score; line_err says whether the words themselves were read.

**easyocr dropped text.** On the Chinese synthetic pages it returned 108 to 370 of 753 Han characters; on deg1 it returned 133 characters of 3,518. It is also the only engine over 4 GB of memory: the simplified Chinese cell passed 6,000 MB and was killed by the cap, and the retry at an 8,000 MB cap peaked at 6,596 MB.

**Install and first-use download**, measured on this machine:

| engine | installed beyond the `pdf` extra | downloaded on first use | runs off-line afterwards |
|---|---|---|---|
| rapidocr | nothing: rapidocr 3.9.2 (31 MB) is in the extra, its onnxruntime models are inside the package; **onnxruntime (62 MB) is not in the extra** | nothing with onnxruntime (no file appeared during the sweep, for `iso:en`, `iso:zh-Hans` or `iso:zh-Hant`); on PyTorch (what `auto` falls back to without onnxruntime): 9.8 + 0.6 + 20.3 MB of models from modelscope.cn, observed once | yes |
| ocrmac | ocrmac 1.0.1 and pyobjc (28 MB), macOS only | nothing: Apple's Vision framework is part of macOS | yes |
| easyocr | easyocr 1.7.2 (15 MB) plus opencv-python-headless (98 MB), scikit-image (28 MB), networkx (7 MB) and smaller ones: about 150 MB | the detector `craft_mlt_25k.pth` (83 MB) always, then one recognizer per script: `english_g2.pth` 15 MB, `latin_g2.pth` 15 MB, `zh_sim_g2.pth` 22 MB, `chinese.pth` (traditional) 226 MB, downloaded during this sweep. From GitHub, by easyocr itself: `HF_HUB_OFFLINE` does not stop it | yes, from `~/.EasyOCR/model` |
| tesseract | the tesseract program (Homebrew: 25 MB plus leptonica 7 MB and about 35 libraries it depends on) | nothing: language files are installed by hand, `tessdata_fast` sizes `eng` 4.1 MB, `chi_sim` 2.5 MB, `chi_tra` 2.4 MB, `osd` 10.6 MB | yes |

## Decision

- The default stays `auto`: docling takes the first installed of ocrmac, rapidocr and easyocr, and names the one it used. `--ocr-engine` names one explicitly; an engine that is not installed, or ocrmac off macOS, is refused before any page is read, with the line that installs it.
- The recommendations on [Which OCR engine](../features/pdf-ocr-engines.md) follow the class table: tesseract or ocrmac for English (tesseract ahead on old print), rapidocr for simplified Chinese, ocrmac or rapidocr for traditional Chinese, easyocr only when nothing else reads the script.
- The `pdf` extra installs ocrmac on macOS, so `auto` picks it with nothing to download, and installs onnxruntime, so rapidocr runs on its bundled models instead of downloading PyTorch ones.

## Limits

- 20 pages, one to four per class; the real Chinese rows are one page each. Differences under about 0.02 CER between two engines on one class are within what a different page would change, so they are called ties. The engines are deterministic: rapidocr reproduced the earlier study's CIA numbers (0.158, 0.272) exactly, and `auto` reproduced the ocrmac cells exactly.
- Japanese, Korean and other scripts were not measured. Vertical Chinese was not measured here.
- One run per cell; no GPU. On a GPU, easyocr's time would drop; its memory would not.
- rapidocr was measured on onnxruntime. On PyTorch (the `auto` fallback of an install without onnxruntime) it was not scored: the one check stopped after the downloads.
- tesseract needs its `configs` folder beside the language files: with `TESSDATA_PREFIX` pointing at a folder of `.traineddata` files alone, every tesseract cell failed (`KeyError: 'text'`, tesseract printing `read_params_file: Can't open tsv`). Linking Homebrew's `configs` and `tessconfigs` folders into it fixed that; the numbers above are from the rerun.
- Default OCR mode: on the simplified Chinese scan, whose invisible OCR layer docling reads alongside, the engine's text is mixed with the layer's. `--ocr-replace-layer` was not measured here.
- Ground truth for the real scans was typed by one person.

Source: docs/260925-eval-PDF_OCR_ENGINES.md (repository, dated records); scorer and ground truth as in docs/260923-eval-PDF_OCR_LUNA_VS_LOCAL_BASELINE.md
