# 选择 OCR 引擎

PDF 页面是扫描件时，`--pdf-ocr` 会让一个 OCR 引擎来读它。`--ocr-engine` 决定用哪一个。有四个引擎在你自己的机器上运行；它们擅长识别的内容不同，要安装的东西不同，是否需要下载也不同。

```bash
python make_book.py --book_name scan.pdf --to-epub --pdf-ocr \
  --ocr-engine ocrmac --ocr-lang iso:en --language zh-hans
```

先试两页（`--pages 1-2`），在为翻译付费之前读一读包里的 `source.md`。运行时会打印实际使用的引擎（`OCR engine: …`）。

## 需要 X，就用 Y

| 你的情况 | 使用 | 原因 |
|---|---|---|
| 用的是 Mac，并且不想下载任何东西 | `ocrmac`（装了 `pdf` 扩展就已经有了） | Apple 自己的引擎，是 macOS 的一部分：不用下载模型。测量中最快（每两页 6 s），英文上排第二，在打字机页面和干净页面上与最好的引擎打平。简体中文不如 rapidocr。 |
| 还没想好 | `auto`（默认） | 取 ocrmac、rapidocr、easyocr 中第一个已安装的，并报出它的名字。装了 `pdf` 扩展时，在 Mac 上是 ocrmac，其他系统上是 rapidocr。 |
| 简体中文 | `rapidocr` | 简体中文上最好：平均汉字错误率 0.054，ocrmac 为 0.097，tesseract 为 0.116。 |
| 繁体中文（横排） | `ocrmac` 或 `rapidocr` | 在测量的那一页上两者接近（0.043 和 0.060）。tesseract（0.350）和 easyocr（0.724）远远落后。竖排文字无论用哪个引擎，列的顺序都会出错：请检查 `source.md`。 |
| 日文或韩文 | `ocrmac` 或 `rapidocr`，配合 `--ocr-lang iso:ja` 或 `iso:ko` | 没有测量。两个引擎都支持这两种语言；tesseract 需要安装 `jpn` 或 `kor` 语言文件。 |
| 英文打字机稿或其他质量较差的扫描件 | `tesseract` 或 `ocrmac` | 在打字机页面上打平（错误率 0.082 和 0.086；rapidocr 为 0.206）。在老印刷品和一份杂志上，tesseract 明显领先（0.222；ocrmac 0.361，rapidocr 0.383）。 |
| 没有 GPU 的机器 | 除 `easyocr` 以外的任何一个 | 这里的每个数字都是在 CPU 上测得的。easyocr 每两页要 37 s（其他引擎 6 到 12 s），内存最多用到 6.6 GB。 |
| Linux 或 Windows | 英文用 `tesseract`，中文用 `rapidocr` | ocrmac 只在 macOS 上有。 |
| 上面这些都认不出的文字 | `easyocr` | 它有 80 多种语言的模型。在测量中，它在英文和中文上都排最后，还丢了文字。 |

## 各引擎的安装

| 引擎 | 安装 | 首次使用时下载 | 大小 |
|---|---|---|---|
| `rapidocr` | 随 `pdf` 扩展一起安装，连同 onnxruntime。 | 不下载：它的模型就在包里。 | rapidocr 31 MB，onnxruntime 62 MB |
| `ocrmac` | 在 macOS 上随 `pdf` 扩展一起安装（仅限 macOS）。 | 不下载。 | 28 MB |
| `easyocr` | `pip install easyocr` | 一个检测器（83 MB），外加每种文字一个模型：英文 15 MB，拉丁文 15 MB，简体中文 22 MB，繁体中文 226 MB，下载到 `~/.EasyOCR/model`。 | 约 150 MB 的包 |
| `tesseract` | tesseract 程序及其语言文件，放在 PATH 上：macOS 上 `brew install tesseract`，Debian 和 Ubuntu 上 `apt install tesseract-ocr tesseract-ocr-chi-sim`，Windows 上用 tesseract 项目提供的安装程序。 | 不下载：要加一种语言，就安装它的文件（fast 系列中 `eng` 4 MB，`chi_sim` 2.5 MB，`chi_tra` 2.4 MB）。 | 程序 25 MB，另加它依赖的库 |

四个引擎装好之后都可以离线工作；easyocr 还需要先把模型下载好。

你指定的引擎如果没有安装，或者在 Linux、Windows 上指定了 `ocrmac`，会在读取任何页面之前被拒绝，并给出安装它的那条命令。`auto` 永远不会被拒绝。

如果你把 tesseract 的语言文件放在自己的文件夹里（`TESSDATA_PREFIX`），也要把 tesseract 的 `configs` 文件夹放进去：没有它，tesseract 写不出 docling 要读的那张表，提取就会失败。

## 语言代码

`--ocr-lang` 接受每个引擎自己的代码，也接受一个在所有引擎上都有效的通用 `iso:` 标签。换引擎时，应该用 `iso:` 标签。

| 语言 | 任何引擎 | rapidocr | ocrmac | easyocr | tesseract |
|---|---|---|---|---|---|
| 英语 | `iso:en` | `en` | `en-US` | `en` | `eng` |
| 简体中文 | `iso:zh-Hans`（或 `iso:zh`） | `ch` | `zh-Hans` | `ch_sim` | `chi_sim` |
| 繁体中文 | `iso:zh-Hant` | `chinese_cht` | `zh-Hant` | `ch_tra` | `chi_tra` |
| 日语 | `iso:ja` | `japan` | `ja-JP` | `ja` | `jpn` |
| 韩语 | `iso:ko` | `korean` | `ko-KR` | `ko` | `kor` |

rapidocr 每次运行只识别一种语言（第一个）。不写 `--ocr-lang` 时，每个引擎都识别它自己的默认语言：rapidocr 是中文和英语，ocrmac、easyocr 和 tesseract 是英语、西班牙语、法语和德语。这时其他文字的扫描件就会读错。

## 测量了什么

20 页扫描页（14 页英文，6 页中文），每个引擎都被要求识别同一种语言，全部在 CPU 上运行。错误率是与人工录入的文本对照得出的字符错误率：0 表示完全正确，段落放错位置也会计为错误。

| 页面 | rapidocr | ocrmac | easyocr | tesseract |
|---|---|---|---|---|
| 英文打字机稿（3） | 0.206 | 0.086 | 0.100 | **0.082** |
| 英文杂志和老印刷品（3） | 0.383 | 0.361 | 0.609 | **0.222** |
| 英文，干净的合成扫描（4） | 0.069 | **0.019** | 0.070 | **0.019** |
| 英文，劣化的合成扫描（4） | **0.080** | 0.124 | 0.333 | 0.116 |
| 简体中文图书扫描（1），汉字 | **0.009** | 0.056 | 0.235 | 0.036 |
| 繁体中文扫描（1），汉字 | 0.060 | **0.043** | 0.724 | 0.350 |
| 简体中文合成扫描（4），汉字 | **0.066** | 0.107 | 0.709 | 0.136 |
| 每两页耗时（中位数） | 12 s | **6 s** | 37 s | 10 s |

各自在哪里胜出：

- **英文**：tesseract，ocrmac 紧随其后。在打字机页面和干净页面上两者打平（差距不到 0.005）。tesseract 在老印刷品上的领先是实打实的。在劣化的合成页面上，rapidocr 数字更低是因为阅读顺序：不计顺序的话，三者对文字的识别一样好。
- **简体中文**：rapidocr，平均值最好，5 页中有 3 页最好；ocrmac 在一张干净的合成页面上领先，ocrmac 和 tesseract 在一张劣化页面上领先。
- **繁体中文**：ocrmac 和 rapidocr，在仅有的一页上差距在噪声范围内。
- **easyocr** 除打字机稿之外在每一类中都排最后，速度最慢，内存占用也最大。

每个数字、所用的页面、评分脚本和命令：[扫描页上的 OCR 引擎](../evaluation/pdf-ocr-engines.md)。
