# 扫描页上的 OCR 引擎：`--ocr-engine` rapidocr、ocrmac、easyocr、tesseract

在这次测量之后，`pdf` 扩展开始安装 onnxruntime，并在 macOS 上安装 ocrmac，所以在全新安装上 rapidocr 和 ocrmac 都不再下载任何东西；下文关于安装的说明描述的是测量当时的安装情况。

## 摘要

`--ocr-engine` 选择 docling 在没有文字层的页面上运行哪个引擎。docling 能在本地运行的四个引擎，在同样的 20 页扫描页（14 页英文，6 页中文）上接受了测量：评分方法相同，要求识别的语言相同，全部在 CPU 上运行。在英文上，tesseract 的错误率最低（平均 CER 0.104），ocrmac 其次（0.136）；rapidocr（0.169）和 easyocr（0.267）落在后面。在简体中文上，rapidocr 最好（五页的平均汉字 CER 为 0.054，ocrmac 为 0.097，tesseract 为 0.116）；在仅有的一页繁体页面上，ocrmac 和 rapidocr 接近（0.043 和 0.060）。easyocr 几乎在所有地方都排最后，耗时是其他引擎的三倍，内存最多用到 6.6 GB，还在中文页面上丢了文字。Apple 自己的引擎 ocrmac 最快（每两页中位数 6 s），而且不需要下载任何东西。

默认值仍是 `auto`。本页是[选择 OCR 引擎](../features/pdf-ocr-engines.md)背后的依据。

## 实验设置

- **路线**：PDF 路线自己的提取，通过分步脚本运行（`tools/pdf_to_book.py extract`），docling 2.129.0，`--pdf-ocr --device cpu --pages 1-2`，默认 OCR 模式（在 docling 保留页面内嵌文字层的地方，文字层会被保留）。一次只做一个转换，每个都在内存上限之下运行，`HF_HUB_OFFLINE=1`。
- **引擎**：rapidocr 3.9.2，运行在 onnxruntime 1.23.2 上（PP-OCRv6 small 模型，随 rapidocr 包一起提供）；ocrmac 1.0.1（Apple Vision，macOS 26）；easyocr 1.7.2，运行在 PyTorch 2.9.0（CPU）上；tesseract 5.5.3（Homebrew），使用 `tessdata_fast` 语言文件（`eng`、`chi_sim`、`chi_tra`、`osd`）。
- **语言**：所有引擎都用同一个 `iso:` 标签，由 docling 映射成各引擎自己的代码：英文页面用 `iso:en`，简体中文用 `iso:zh-Hans`，繁体中文用 `iso:zh-Hant`。每个单元格中，运行自己打印的那一行都确认了引擎和语言。
- **设备**：每个单元格都用 `--device cpu`，所以四组的版面模型和表格模型都在 CPU 上运行。rapidocr（onnxruntime）、easyocr（PyTorch）和 tesseract 在 CPU 上运行；ocrmac 在 Apple 的 Vision 框架里运行，由框架自己选择硬件。
- **机器**：Apple 芯片，16 GB，Python 3.12.12。
- **页面**：每份都截成两页；计分的是有标准答案的那些页：

| 样本 | 计分页 | 内容 | 语言参数 |
|---|---|---|---|
| scan_cia_typewriter | 1, 2 | 质量较差的打字机稿扫描（CCITT） | `iso:en` |
| scan_cia_typewriter_mid | 2 | 打字机稿扫描，文档中部 | `iso:en` |
| scan_forward1963 | 1 | 1963 年的杂志，双栏，带脚注 | `iso:en` |
| scan_en_degraded_1_mid | 1 | 带表格的彩色小册子 | `iso:en` |
| scan_en_degraded_2_mid | 1 | 黑白二值的旧小说页面 | `iso:en` |
| scan_zh_hans_qm_mid | 1 | Internet Archive 的图书扫描，简体中文，带不可见 OCR 文字层和 JBIG2 蒙版 | `iso:zh-Hans` |
| zh_hant_scan_2_mid | 2 | 繁体中文，横排，带表格 | `iso:zh-Hant` |
| syn_arxiv_1706_03762_{clean,deg} | 1, 2 | 渲染成扫描件的原生数字论文 | `iso:en` |
| syn_wps_eula_{clean,deg} | 1, 2 | 渲染成扫描件的 WPS 许可协议 | `iso:en` |
| syn_web_zh_report_chrome_{clean,deg} | 1, 2 | 渲染成扫描件的中文网页报告 | `iso:zh-Hans` |

合成扫描件：200 dpi；干净版是无损 PNG，劣化版是灰度图，旋转 1.5°，模糊（r=0.8），JPEG 质量 q40。它们的标准答案是精确的（来自原页面的文字层）。真实扫描件的标准答案是对照页面图像人工录入的。中文图书扫描件带有 JBIG2 蒙版，所以每一组的页面图像都来自 pypdfium2（运行时打印了 JBIG2 那一行）。

- **评分**：归一化：去掉 Markdown 语法（图片链接、注释、`#`、表格竖线和分隔线、`**`、转义符、列表符号），反转义 HTML，NFKC，所有引号变体都映射为 `"`，所有连字符和破折号都删去，空白合并（中文则完全删去）。**CER** 对顺序敏感：一个段落读的顺序不同，也会计为错误。**Han CER** 是只计汉字的 CER。**line_err** 不计顺序：对标准答案的每一行取（1 − 部分匹配度），再按长度加权平均，所以它只衡量识别和检测本身。对于开头没有标题的提取结果，路线会在上方加一个标题（`# <file name>`），计分前把它去掉了。

命令，每个单元格一条（`$E` 是评测目录，`$ENGINE` 是四个引擎之一，`$LANG` 取自上表）：

```bash
HF_HUB_OFFLINE=1 TESSDATA_PREFIX=$E/tessdata \
  python tools/pdf_to_book.py extract $E/fixtures/p12/scan_cia_typewriter.pdf \
  --output $E/runs/K/$ENGINE_cia/bundle \
  --pdf-ocr --ocr-engine $ENGINE --ocr-lang iso:en --device cpu --pages 1-2
```

另外在每种文字各取一份样本，用 `--ocr-engine auto` 跑同样的命令，看看 `auto` 会选哪个。

## 结果

每页的 CER，越低越好；中文页面同时给出只计汉字的 CER：

| 页面 | rapidocr | ocrmac | easyocr | tesseract | auto |
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

每页的 line_err（不计顺序）：

| 页面 | rapidocr | ocrmac | easyocr | tesseract | auto |
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

按类别求平均，CER（中文为 Han CER）/ line_err：

| 类别 | n | rapidocr | ocrmac | easyocr | tesseract |
|---|---|---|---|---|---|
| 打字机稿扫描，英文（cia p1、p2，ciamid p2） | 3 | 0.206 / 0.120 | 0.086 / 0.028 | 0.100 / 0.064 | 0.082 / 0.027 |
| 杂志和老印刷品，英文（fwd、deg1、deg2） | 3 | 0.383 / 0.024 | 0.361 / 0.060 | 0.609 / 0.301 | 0.222 / 0.020 |
| 合成扫描，英文，干净 | 4 | 0.069 / 0.042 | 0.019 / 0.009 | 0.070 / 0.039 | 0.019 / 0.010 |
| 合成扫描，英文，劣化 | 4 | 0.080 / 0.021 | 0.124 / 0.020 | 0.333 / 0.196 | 0.116 / 0.023 |
| 图书扫描，简体中文（zh，JBIG2） | 1 | 0.009 / 0.036 | 0.056 / 0.144 | 0.235 / 0.142 | 0.036 / 0.119 |
| 扫描，繁体中文，横排（hant2） | 1 | 0.060 / 0.050 | 0.043 / 0.037 | 0.724 / 0.536 | 0.350 / 0.272 |
| 合成扫描，简体中文，干净 | 2 | 0.027 / 0.045 | 0.001 / 0.033 | 0.562 / 0.523 | 0.020 / 0.065 |
| 合成扫描，简体中文，劣化 | 2 | 0.105 / 0.068 | 0.213 / 0.044 | 0.855 / 0.706 | 0.252 / 0.096 |

按引擎：

| 引擎 | 页数 | 平均 CER，英文（n） | 平均 Han CER，中文（n） | 平均 line_err，英文 | 平均 line_err，中文 | 每个两页单元格的提取耗时中位数（s） | 内存峰值 MB（最大） |
|---|---|---|---|---|---|---|---|
| rapidocr | 20 | 0.169 (14) | 0.055 (6) | 0.049 | 0.052 | 12 | 2248 |
| ocrmac | 20 | 0.136 (14) | 0.088 (6) | 0.027 | 0.056 | 6 | 1661 |
| easyocr | 20 | 0.267 (14) | 0.632 (6) | 0.145 | 0.523 | 37 | 6596 |
| tesseract | 20 | 0.104 (14) | 0.155 (6) | 0.019 | 0.119 | 10 | 1902 |
| auto | 4 | 0.114 (2) | 0.050 (2) | 0.039 | 0.090 | 12 | 1977 |

提取耗时取自运行自己打印的 `PDF extracted: 2 pages, …, N s.` 这一行：包括加载模型，以及两页的版面、表格和 OCR。各单元格：rapidocr 9–19 s，ocrmac 4–11 s，tesseract 7–21 s，easyocr 32–90 s（90 s 的那个单元格包含首次使用时的模型下载）。

**`auto` 选了什么**：3 个单元格中 3 次都选了 ocrmac（`OCR engine: ocrmac (docling's choice on this install), …`），因为装了 ocrmac；它的文字与 ocrmac 单元格完全相同。

**有几处 CER 差距要用阅读顺序而不是识别来解释**。synarxd p2：ocrmac 和 tesseract 的 CER 为 0.269 和 0.272，rapidocr 为 0.002，但 line_err 为 0.003 和 0.007：文字是对的，只是有一块放到了别处。deg1：rapidocr 和 ocrmac 的 CER 为 0.355 和 0.360，line_err 为 0.010 和 0.043。对一本书来说，段落放错位置是实实在在的缺陷，所以 CER 是主要分数；line_err 说明文字本身是否读对了。

**easyocr 丢了文字**。在中文合成页面上，753 个汉字它只返回了 108 到 370 个；在 deg1 上，3,518 个字符它只返回了 133 个。它也是唯一内存超过 4 GB 的引擎：简体中文那个单元格超过了 6,000 MB，被上限杀掉，改用 8,000 MB 上限重试时峰值为 6,596 MB。

**安装与首次使用时的下载**，在这台机器上测得：

| 引擎 | `pdf` 扩展之外还需安装的 | 首次使用时下载的 | 之后能否离线运行 |
|---|---|---|---|
| rapidocr | 无：rapidocr 3.9.2（31 MB）在扩展里，它的 onnxruntime 模型就在包里；**onnxruntime（62 MB）不在扩展里** | 用 onnxruntime 时不下载（扫描过程中没有出现任何文件，无论 `iso:en`、`iso:zh-Hans` 还是 `iso:zh-Hant`）；用 PyTorch 时（没有 onnxruntime 时 `auto` 退回的方式）：从 modelscope.cn 下载 9.8 + 0.6 + 20.3 MB 的模型，观察到一次 | 能 |
| ocrmac | ocrmac 1.0.1 和 pyobjc（28 MB），仅限 macOS | 无：Apple 的 Vision 框架是 macOS 的一部分 | 能 |
| easyocr | easyocr 1.7.2（15 MB），外加 opencv-python-headless（98 MB）、scikit-image（28 MB）、networkx（7 MB）和一些更小的包：约 150 MB | 总会下载检测器 `craft_mlt_25k.pth`（83 MB），然后每种文字一个识别器：`english_g2.pth` 15 MB，`latin_g2.pth` 15 MB，`zh_sim_g2.pth` 22 MB，`chinese.pth`（繁体）226 MB，都是在这次测试中下载的。由 easyocr 自己从 GitHub 下载：`HF_HUB_OFFLINE` 拦不住它 | 能，从 `~/.EasyOCR/model` 读取 |
| tesseract | tesseract 程序（Homebrew：25 MB，外加 leptonica 7 MB 和它依赖的约 35 个库） | 无：语言文件要手动安装，`tessdata_fast` 中的大小为 `eng` 4.1 MB，`chi_sim` 2.5 MB，`chi_tra` 2.4 MB，`osd` 10.6 MB | 能 |

## 决定

- 默认值仍是 `auto`：docling 取 ocrmac、rapidocr 和 easyocr 中第一个已安装的，并报出它用的是哪一个。`--ocr-engine` 用来明确指定一个；没有安装的引擎，或在 macOS 以外指定的 ocrmac，会在读取任何页面之前被拒绝，并给出安装它的那条命令。
- [选择 OCR 引擎](../features/pdf-ocr-engines.md)上的建议依据的是按类别的那张表：英文用 tesseract 或 ocrmac（老印刷品上 tesseract 领先），简体中文用 rapidocr，繁体中文用 ocrmac 或 rapidocr，只有在别的引擎都认不出那种文字时才用 easyocr。
- `pdf` 扩展在 macOS 上安装 ocrmac，所以 `auto` 会选它，无需下载任何东西；扩展还安装 onnxruntime，所以 rapidocr 用它自带的模型运行，而不用去下载 PyTorch 的模型。

## 局限

- 共 20 页，每个类别一到四页；真实中文扫描的几行各只有一页。同一类别上两个引擎的 CER 相差不到约 0.02 时，换一页就可能改变，所以都算作打平。这些引擎的结果是确定的：rapidocr 精确复现了之前那项研究中 CIA 页面的数字（0.158、0.272），`auto` 也精确复现了 ocrmac 单元格的结果。
- 没有测量日文、韩文和其他文字。这里也没有测量竖排中文。
- 每个单元格只运行一次；没有用 GPU。在 GPU 上，easyocr 的耗时会下降，内存则不会。
- rapidocr 是在 onnxruntime 上测量的。在 PyTorch 上（没有 onnxruntime 的安装中 `auto` 退回的方式）没有计分：唯一的一次检查在下载之后就停止了。
- tesseract 需要把它的 `configs` 文件夹放在语言文件旁边：当 `TESSDATA_PREFIX` 指向一个只有 `.traineddata` 文件的文件夹时，所有 tesseract 单元格都失败了（`KeyError: 'text'`，tesseract 打印 `read_params_file: Can't open tsv`）。把 Homebrew 的 `configs` 和 `tessconfigs` 文件夹链接进去就解决了；上面的数字来自重跑。
- 默认 OCR 模式：在简体中文扫描件上，docling 会同时读取它的不可见 OCR 文字层，所以引擎读出的文字与这层文字混在一起。这里没有测量 `--ocr-replace-layer`。
- 真实扫描件的标准答案由一个人录入。
