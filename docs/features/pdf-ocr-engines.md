# Which OCR engine

When a PDF page is a scan, `--pdf-ocr` has an OCR engine read it. `--ocr-engine` chooses which one. Four run on your own machine; they differ in what they read well, what they install and whether they download anything.

```bash
python make_book.py --book_name scan.pdf --to-epub --pdf-ocr \
  --ocr-engine ocrmac --ocr-lang iso:en --language zh-hans
```

Try two pages first (`--pages 1-2`) and read `source.md` in the bundle before you pay for a translation. The run prints the engine it used (`OCR engine: …`).

## If you need X, use Y

| your situation | use | why |
|---|---|---|
| A Mac, and you do not want to download anything | `ocrmac` (already there with the `pdf` extra) | Apple's own engine, part of macOS: no model download. The fastest in the measurement (6 s per two pages) and second-best on English, tied with the best on typewriter and clean pages. Weaker than rapidocr on simplified Chinese. |
| You have not chosen | `auto` (the default) | Takes the first installed of ocrmac, rapidocr, easyocr, and names it. With the `pdf` extra that is ocrmac on a Mac and rapidocr elsewhere. |
| Simplified Chinese | `rapidocr` | Best on simplified Chinese: mean Han-character error 0.054, against ocrmac 0.097 and tesseract 0.116. |
| Traditional Chinese (horizontal) | `ocrmac` or `rapidocr` | Close on the one page measured (0.043 and 0.060). tesseract (0.350) and easyocr (0.724) were far behind. Vertical text comes out in the wrong column order with any engine: check `source.md`. |
| Japanese or Korean | `ocrmac` or `rapidocr`, with `--ocr-lang iso:ja` or `iso:ko` | Not measured. Both engines have these languages; tesseract needs the `jpn` or `kor` language file installed. |
| English typewriter or other degraded scans | `tesseract` or `ocrmac` | Tied on typewriter pages (error 0.082 and 0.086; rapidocr 0.206). On old print and a magazine, tesseract was clearly ahead (0.222; ocrmac 0.361, rapidocr 0.383). |
| A machine without a GPU | anything but `easyocr` | Every number here was measured on the CPU. easyocr took 37 s per two pages (others 6 to 12 s) and up to 6.6 GB of memory. |
| Linux or Windows | `tesseract` for English, `rapidocr` for Chinese | ocrmac exists only on macOS. |
| A script none of the above reads | `easyocr` | It has models for 80+ languages. In the measurement it was last on English and Chinese and dropped text. |

## Installing each engine

| engine | install | downloads on first use | size |
|---|---|---|---|
| `rapidocr` | Comes with the `pdf` extra, with onnxruntime. | Nothing: its models are inside the package. | rapidocr 31 MB, onnxruntime 62 MB |
| `ocrmac` | Comes with the `pdf` extra on macOS (macOS only). | Nothing. | 28 MB |
| `easyocr` | `pip install easyocr` | A detector (83 MB) and one model per script: English 15 MB, Latin 15 MB, simplified Chinese 22 MB, traditional Chinese 226 MB, into `~/.EasyOCR/model`. | about 150 MB of packages |
| `tesseract` | The tesseract program and its language files, on PATH: `brew install tesseract` on macOS, `apt install tesseract-ocr tesseract-ocr-chi-sim` on Debian and Ubuntu, the installer from the tesseract project on Windows. | Nothing: add a language by installing its file (`eng` 4 MB, `chi_sim` 2.5 MB, `chi_tra` 2.4 MB in the fast set). | 25 MB program plus its libraries |

All four work off-line once installed and, for easyocr, once its models are downloaded.

An engine you name that is not installed, or `ocrmac` on Linux or Windows, is refused before any page is read, with the line that installs it. `auto` is never refused.

If you keep tesseract's language files in a folder of your own (`TESSDATA_PREFIX`), put tesseract's `configs` folder in it too: without it tesseract cannot write the table docling reads, and the extraction fails.

## Language codes

`--ocr-lang` takes each engine's own codes, or a portable `iso:` tag that works on every engine. When you switch engines, the `iso:` tag is the one to use.

| language | any engine | rapidocr | ocrmac | easyocr | tesseract |
|---|---|---|---|---|---|
| English | `iso:en` | `en` | `en-US` | `en` | `eng` |
| Simplified Chinese | `iso:zh-Hans` (or `iso:zh`) | `ch` | `zh-Hans` | `ch_sim` | `chi_sim` |
| Traditional Chinese | `iso:zh-Hant` | `chinese_cht` | `zh-Hant` | `ch_tra` | `chi_tra` |
| Japanese | `iso:ja` | `japan` | `ja-JP` | `ja` | `jpn` |
| Korean | `iso:ko` | `korean` | `ko-KR` | `ko` | `kor` |

rapidocr reads one language per run (the first). Without `--ocr-lang` each engine reads its own defaults: rapidocr Chinese and English, ocrmac, easyocr and tesseract English, Spanish, French and German. A scan in another script then comes out wrong.

## What was measured

20 scanned pages (14 English, 6 Chinese), the same language asked of each engine, all on the CPU. Error is the character error rate against a typed transcript: 0 is perfect, and it counts a paragraph put in the wrong place as errors.

| pages | rapidocr | ocrmac | easyocr | tesseract |
|---|---|---|---|---|
| English typewriter (3) | 0.206 | 0.086 | 0.100 | **0.082** |
| English magazine and old print (3) | 0.383 | 0.361 | 0.609 | **0.222** |
| English, clean synthetic scan (4) | 0.069 | **0.019** | 0.070 | **0.019** |
| English, degraded synthetic scan (4) | **0.080** | 0.124 | 0.333 | 0.116 |
| Simplified Chinese book scan (1), Han characters | **0.009** | 0.056 | 0.235 | 0.036 |
| Traditional Chinese scan (1), Han characters | 0.060 | **0.043** | 0.724 | 0.350 |
| Simplified Chinese synthetic scans (4), Han characters | **0.066** | 0.107 | 0.709 | 0.136 |
| Time per two pages (median) | 12 s | **6 s** | 37 s | 10 s |

Who won where:

- **English:** tesseract, with ocrmac close behind. On typewriter and clean pages the two are tied (differences under 0.005). tesseract's lead is real on old print. On the degraded synthetic pages rapidocr's lower number comes from reading order: ignoring order, all three read the words equally well.
- **Simplified Chinese:** rapidocr, on the mean and on 3 of 5 pages; ocrmac was ahead on one clean synthetic page, ocrmac and tesseract on one degraded one.
- **Traditional Chinese:** ocrmac and rapidocr, within noise of each other on one page.
- **easyocr** was last on every class but typewriter, the slowest, and the heaviest in memory.

Every number, the pages, the scorer and the commands: [Which local OCR engine reads a scan best](../evaluation/pdf-ocr-engines.md). How a vision model compares with these engines: [Why a vision model reads scans better](../evaluation/pdf-ocr-llm-vs-local.md).
