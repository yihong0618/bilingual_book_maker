# PDF 转双语 EPUB

想把一份 PDF 做成带目录的双语书，先读两页，再跑整个文件：

```bash
python make_book.py --book_name paper.pdf --to-epub --pages 1-2 --test
python make_book.py --book_name paper.pdf --to-epub --use_context session
```

两条命令之间，读一读 `paper_pages-1-2_book/source.md`。[PDF 推荐设置](recommended-pdf.md)按文档类型和操作系统给出了对应的命令。

这条路线还是实验性的。它在 arXiv 论文和一批图书、扫描件、浏览器另存的网页上检验过，并没有覆盖所有形态的 PDF。欢迎提 issue 和 pull request；如果 PDF 可以公开，请附上它，否则附上 `source.md` 里出错的那一页。

## 它做什么

`--to-epub` 用 [docling](https://github.com/docling-project/docling) 的版面模型和表格模型把 PDF 读成 Markdown，用 Markdown 加载器翻译这份 Markdown，再由 Pandoc 生成可重排的双语 EPUB。每一段后面紧跟它的译文，目录按标题生成。标题层级从页面本身读出：先看编号（`1.`、`1.1`、`I.`、`A.`），再看字号和字重。插图保留为图片，每个行间公式都从页面上裁成图片，放回原来的位置。没有文字层的页面（即扫描件）只有在传入 `--pdf-ocr` 时才交给 OCR 模型读取；扫描件自带的文字层有误时，可以用 `--ocr-replace-layer` 替换掉。如果用 `--img-model` 指定一个视觉模型，它会在写出 Markdown 之前逐页查看，纠正 docling 给各个区域定的角色（比如其实是作者行的标题、被读成脚注的代码清单）。

所有文件都放在 PDF 旁边的工作目录 `<name>_book/` 里：`source.md`（提取结果）、`images/`、`book_bilingual.md`（译文）和一份清单。成书会复制到外面，名为 `<name>_bilingual.epub`。重跑同一条命令会复用已有的提取结果和已完成的翻译，所以你可以中途停下，读 `source.md`，改一个标题，再接着跑，不必付两次钱。PDF 是对页面的描述，而不是文档：它只记录字形放在什么位置，对段落和标题一无所知，所以任何提取器都只能把结构猜回来。标题差了一级、表格变成了正文，这是格式本身的局限，在 `source.md` 里花一分钟就能改好。

`source.md` 用一条注释 `<!-- page N -->` 标出每一页的开头。N 是该页在 PDF 文件中的序号，从 1 开始数，也就是 `--pages` 用的页码，而不是页面上印的页码。空白页同样保留标记，所以编号不会错位。极少数情况下，docling 会返回不属于任何页面的条目。这些条目只写一次，放在 `source.md` 末尾的 `<!-- unplaced -->` 下面，运行时会说明有多少条。它们在阅读顺序中的位置已经丢失：翻译之前，把它们移到该在的位置，或者删掉。

## 准备工作

1. 在仓库的克隆里安装 PDF 扩展：`pip install ".[pdf]"`（没有 NVIDIA 显卡的 Linux 和装有 NVIDIA 显卡的 Windows 还要加上 PyTorch 自己的索引，见[安装 PDF 扩展](../installation-pdf.md)）。此外，PATH 上需要有 Pandoc **3.1.12 或更新版本**。不需要 Java。
2. 模型（约 500 MB）在第一次运行时下载。
3. 在跑更长的内容之前先读前两页；在为整份翻译付费之前，至少读一遍 `source.md` 里的标题。它们会成为目录。

这条路线自己的参数：

| 参数 | 作用 |
|---|---|
| `--to-epub` | 走这条路线。 |
| `--pdf-ocr` | 读取没有文字层的页面。默认关闭：原生数字 PDF 本来就能直接读取，OCR 要多花好几倍的时间，读出的内容却不会变。无论开不开，版面、标题和表格检测都会运行。 |
| `--ocr-replace-layer` | 配合 `--pdf-ocr`：对每一页做 OCR，用识别结果代替 PDF 自带的文字层。默认关闭：保留已有的文字层，只对没有文字层的页面做 OCR。适用于文字层有误的扫描件（识别成了别的语言，或者是劣质 OCR 留下的乱码）；在干净的扫描件上，文字层比本地引擎读得更好。打开或关闭它，下一次运行都会重新读取 PDF。 |
| `--ocr-lang LANGS` | 配合 `--pdf-ocr`：OCR 引擎要识别的语言，用引擎自己的代码。`auto` 在 Mac 上选 ocrmac，其他系统上选 rapidocr；pdf 扩展把两者都装好了，都不需要下载任何东西。测量数据见[选择 OCR 引擎](pdf-ocr-engines.md)。各引擎的代码：rapidocr 用 `ch`、`en`、`latin`；easyocr 用 `ch_sim`、`ja`、`ko`；ocrmac 用 `zh-Hans`、`ja-JP`。在 BCP-47 标签前加 `iso:`（`iso:zh`、`iso:ja`、`iso:zh-Hant`）在所有引擎上都有效，所以不确定会用哪个引擎时，这是稳妥的写法。rapidocr 没有 `ch_sim`：中文请写 `iso:zh`。rapidocr 每次运行只识别一种语言，取第一个。运行时会打印实际使用的引擎和语言。 |
| `--ocr-engine ENGINE` | 配合 `--pdf-ocr`：由哪个 OCR 引擎读取页面：`auto`（默认；取 ocrmac、rapidocr、easyocr 中第一个已安装的）、`rapidocr`、`ocrmac`（macOS）、`easyocr` 或 `tesseract`。指定的引擎没有安装时，在读取任何页面之前就会被拒绝。该选哪个，有测量结果：[选择 OCR 引擎](pdf-ocr-engines.md)。 |
| `--device auto\|cpu\|cuda\|mps\|xpu` | 模型在哪里运行。`auto` 会检测 CUDA 或 MPS，找不到就退回 CPU。CPU 得到的文字完全一样，只是更慢。 |
| `--pages 12-30` | 只处理这些页，从 1 开始编号（也可以写 `1,3,5-7`）。这本书会有自己的名字：`<name>_pages-12-30_book/`、`<name>_pages-12-30_bilingual.epub`。 |
| `--no-formula-images` | 把行间公式留作 `<!-- formula-not-decoded -->` 占位符。几乎用不到：解析器从不读取公式，没有这些图片，数学内容就没了。 |
| `--pdf-image-dpi N` | 插图的清晰度，按 PDF 自身页面尺寸计算的每英寸点数（72 到 600）。默认 200：在平板或高像素密度的电子书阅读器上足够清晰；小一些的书用 150，带细小标注的插图用 300。换一个值重跑，只会重画插图，提取结果和译文都保留。公式有自己的分辨率，不受影响。如果一张插图只是一幅嵌入的图片，而且这幅图片自己的分辨率更低，就保留它自己的分辨率；任何插图都不会超过 560 万像素。 |
| `--img-model MODEL` | 根据页面图像纠正区域角色的视觉模型。只有在这里指定，或在提供方条目的 `img_model` 中指定时才开启；绝不会退而使用翻译模型。`none` 关闭条目里指定的模型。见[用视觉模型纠正区域角色](#用视觉模型纠正区域角色)。 |
| `--img-base-url URL` | 这个模型所在的地址，当它不在本次运行的端点上时使用（仅限 OpenAI 兼容接口）。 |
| `--img-key KEY` | `--img-base-url` 所用的 key。默认在本次运行自己的端点上使用本次运行的 key。 |

Markdown 加载器的所有参数都照常可用：`--use_context session`（推荐）、`--glossary`、`--parallel-workers`（不能与会话同用）、`--no-thinking`、`--test`。完整列表见 [PDF 参数与纯文本路线](../formats/pdf.md)。翻译阶段会拒绝的参数组合，例如在 codex 路线上用 `--no-thinking`，在提取开始之前就会被拒绝，所以不会先花钱。`--classify-model` 在这条路线上暂时不起作用，运行时会给出警告。

## 用视觉模型纠正区域角色

docling 把每一页切成若干区域并给它们打上标签：正文、标题、文档标题、图题、脚注、代码、表格、图片。有些标签是错的，成书里就会露出来：作者行出现在目录里，代码清单被排成正文，插图的标注被升格成一节。使用 `--img-model` 时，每一页都会连同 docling 画好并编号的区域一起交给模型。模型为每个区域给出一个角色（正文、标题、文档标题、图题、脚注、代码），或者弃权。被采纳的回答会在写出 Markdown 之前替换原来的标签，这样目录和代码块就能正确生成。文字本身绝不会被改写。

- **它修不了什么。** 列表、表格、图片、公式以及页眉页脚都不在询问范围内。docling 已经把一张扫描页切成碎片时，重新打标签也拼不回来。
- **它的开销。** 每页约 3,000 个提示 token；研究中每页耗时 2.7 到 10.6 s。在它背后的研究里，模型在 12 页上纠正了 66 个错误标签中的 40 个；见[用图像模型判定区域角色](../evaluation/pdf-structure-llm-roles.md)。
- **它需要什么。** 一个能接受图片的 OpenAI 兼容端点。运行时会先检查一次模型能否看懂图片。如果不能，运行时会说明这一点，并按 docling 自己的标签提取。
- **模型要明确指定，绝不自动推定。** 只有用 `--img-model` 或在提供方条目的 `img_model` 中指定了模型，这一步才会运行。自带的 `openai` 条目指定了 `gpt-5.6-luna`，所以 `--provider openai` 会开启这一步；`--img-model none` 可以关掉它。本地运行的翻译模型绝不会被要求去读页面。
- **改动它会重新提取。** 图像模型及其地址属于提取结果的比对依据，所以添加、更换或去掉 `--img-model` 都会重新读取 PDF。如果某个包的角色判定没有完成，而本次运行又要求做这一步，也会重新读取。

判定结果保存在 `<name>_book/.work/extraction/decisions.json`。提取结束时，运行会单独打印一行这一步的 token 用量：`Image model (<model> at <address>): …`。

## 手工修补译文

如果某一段译文有误，在 `<name>_book/book_bilingual.md` 里改正，然后做两步：

1. 在仓库目录下运行 `python tools/pdf_to_book.py export path/to/<name>_book`。它会根据你的修改重新生成 `<name>_book/book_bilingual.epub`；不会重新提取或翻译，也不需要 key。
2. 用 `<name>_book/book_bilingual.epub` 覆盖 PDF 旁边的 `<name>_bilingual.epub`。导出只在工作目录里写文件，所以在你复制之前，PDF 旁边那本书里还是旧的文字。

## 推荐命令

每份 PDF 都从两页和几段译文开始（`--pages 1-2 --test`），然后读一遍 `source.md`。小说、教材、论文、扫描书、文字层有误的扫描件和中文扫描件各自的命令，以及 macOS、带 NVIDIA 显卡的 Linux、Windows、只有 CPU 的机器和 Docker 上的安装方式与设备选择，都在 [PDF 推荐设置](recommended-pdf.md)里。

## 可能出现的问题

这条路线上的每一种失败都会打印一行以 `Error:` 开头的信息，并且尽可能在花钱之前就打印出来。下面列出这些信息，以及该怎么做。

### 提取之前

- **`Pandoc is required for --to-epub. Install it and make sure pandoc is on PATH.`** 从 [pandoc.org](https://pandoc.org/installing.html) 安装 Pandoc 3.1.12 或更新版本。
- **`pandoc 3.1.3 is too old for EPUB export; Pandoc 3.1.12 or newer is required …`** 你的 Pandoc 来自 apt（Ubuntu 24.04 自带 3.1.3，Debian 13 自带 3.1.11）。安装官方发布版，并把它放在 PATH 的最前面；这行信息还提到了另一种办法：分步脚本 `tools/pdf_to_book.py --pandoc PATH`。
- **`reading a PDF needs the pdf extra, which is not installed.`** 在克隆的仓库里运行 `pip install ".[pdf]"`（[安装 PDF 扩展](../installation-pdf.md)的第 3 步）。不是 `pip install "bbook_maker[pdf]"`。
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build.`** 按 CUDA 的方式重新安装。**`… but this machine has no cuda accelerator available.`** 改用 `--device cpu` 或 `--device auto`。
- **`--parallel-workers is not supported with --use_context session …`** 二者选其一。
- **`--no-thinking has no request to travel in on the codex route: …`** 在 codex 上去掉 `--no-thinking`。
- **`--img-model needs an OpenAI-compatible endpoint; … resolves to the … format.`** 图像模型要在本次运行的端点或 `--img-base-url` 上询问，而那个端点不是 OpenAI 形式的。给一个 OpenAI 兼容的 `--img-base-url`（以及 `--img-key`），或者去掉 `--img-model`。
- **`--img-base-url names where --img-model is served, and no --img-model was given. …`** 把模型也写上。
- **`--ocr-replace-layer re-reads pages that already carry a text layer with the OCR engine, so it needs --pdf-ocr.`** 加上 `--pdf-ocr`。

### 提取期间

- **`N of M selected pages have no text layer (page(s) …); rerun with --pdf-ocr to read them with the OCR models.`** 这份 PDF（或其中一部分）是扫描件。加上 `--pdf-ocr`。
- **`No --ocr-lang given: the OCR engine reads its own default languages, which may not be the pages'; …`** 随后是 **`OCR engine: rapidocr (docling's choice on this install), languages: the engine's defaults.`** 检查 `source.md`。如果扫描件不是中文或英文，用 `--ocr-lang` 重跑；包会被重新读取。
- **`The parser produced no text for a document whose pages have no text layer; the OCR pass returned pictures only.`** 或 **`Warning: no text was recognised on page(s) …`** 引擎认不出这种文字。用 `--ocr-lang` 指定页面的语言后重跑。
- **`The parser returned no text for this PDF; there is nothing to translate. If its pages are scans, rerun with --pdf-ocr.`** 照它说的做。
- **`Warning: page N extracted C characters, several times what a printed page holds; inspect source.md before translating.`** 那一页上有什么东西（通常是一张插图）携带的文字远比它显示出来的多。一页的 Markdown 长达几千行，那是垃圾，不是长页面。把它从 `source.md` 里删掉，或者用 `--pages` 把这一页排除在外。
- **`The PDF carries JBIG2 image masks, which docling-parse renders wrongly (docling issue #4329); page images are rendered by pypdfium2 instead, the text layer still by docling-parse.`** 提示信息。Internet Archive 和 ABBYY FineReader 生成的扫描件常用这种蒙版，docling-parse 会把页面画成一片模糊。此时整份文档的页面图像都改由 pypdfium2 生成。
- **`N of M selected pages carry only an invisible OCR text layer (a scanned book with recognised text underneath); that layer is kept as the page's text …`** 提示信息。使用 `--pdf-ocr` 时，这层文字可能与 OCR 引擎读出的内容混在一起，所以请用 `--ocr-lang` 指明语言；语言不对的话，一页清晰可读的内容也会变成乱码。如果这层文字本身就是乱码，用 `--pdf-ocr --ocr-replace-layer` 加上语言重跑。
- **`OCR: replacing the embedded text layer on every page (--ocr-replace-layer)`** 提示信息。没有 `--ocr-lang` 时，后面还会跟着语言提示。
- **`page N: the OCR engine read nothing where the PDF carried a text layer; …`** 在 `--ocr-replace-layer` 下，这一页的识别结果为空，它就保持为空页：不会拿原来的文字层来顶替。这行信息也会写进清单的限制说明；超过十页后，其余的只计数（`... and N more`）。用 `--ocr-lang` 指定页面的语言后重跑，或者去掉 `--ocr-replace-layer` 以保留文字层。
- **`The OCR engine read nothing on any selected page, and with --ocr-replace-layer the PDF's text layer is not used, so there is nothing to translate; …`** 运行在任何翻译付费之前停止。清单会记录这次失败的尝试：原因、空白页和所用的设置。常见原因是 `--ocr-lang` 写错了，或者扫描件的语言不在引擎的默认语言之内却没有写 `--ocr-lang`。写对语言后重跑，或者去掉 `--ocr-replace-layer` 以保留文字层。
- **`N item(s) carry no page number; they are placed after the last page in source.md.`** 很少见。docling 返回了不属于任何页面的条目。它们在 `source.md` 末尾的 `<!-- unplaced -->` 下面，在阅读顺序中的位置已经丢失。翻译之前，把它们移到该在的位置，或者删掉。
- **`… has no model for the OCR language 'ch_sim'. …`** 引擎不认识这个代码；信息里列出了它认识的代码。在 rapidocr 上，中文请写 `iso:zh`。这次什么都没有读取。
- **`… did not read the probe image (…); image steps are skipped this run.`** 这个图像模型在那个端点上看不到图片。提取会继续，使用 docling 自己的标签。
- **`Region roles: A of B asked items changed by <model> (… kept, … rejected, … pages with many changes); overlay at <path>.`** 提示信息：图像模型改了哪些。**`Region roles: C of D asked items on page N changed; read that page in source.md before translating.`** 一页中的大部分都被改了；这些改动会保留，所以请看一看那一页。**`Region roles: …; the detector's own labels stand there.`** 这一步有一部分没有完成（预算用完、回答被拒绝、某个区域没有得到回答）；这些区域保留 docling 的标签。
- **`Extracting again: the bundle's region-role pass is <status>; this run asks for --img-model …, which only a complete pass satisfies.`** 提示信息。之前的判定没有完成，所以重新读取 PDF。
- **`--img-model <model> failed: …`** 图像端点返回了一个等待也解决不了的错误，比如 key 被拒绝。运行停止。修正 key（`--img-key`）或地址。

### 提取之后、翻译之前

- **`The document does not open with a top-level heading, so a heading "<name>" was added above its text; …`** EPUB 的目录要求开头有一个一级标题。愿意的话，在翻译之前到 `source.md` 里给它改个名。
- **`Page 12: the selection starts inside a section, so a heading "Page 12" was added above its prose; …`** 同上，针对从某一节中间开始的 `--pages` 范围。
- **`The page selection is not one run of pages, so pages 1-7 were read and the ones outside the selection dropped afterwards; …`** 有间隔的范围会读取它跨越的所有页。一个连续的范围读取的页更少。
- **`Display formulas kept as images: N. …`** 提示信息：这些公式是图片，不会被翻译。
- **`Warning: a formula region on page N covers S% of the page, which is a layout mistake rather than an equation; …`** 这个区域保留为占位符，而不是整页的图片。其他 `Warning: … formula …` 行表示某个公式无法放置，它的占位符会保留下来。
- **`Extraction: removed N control character(s) that docling read from glyphs on page(s) …; they are not text and would break the EPUB.`** 提示信息。docling 把某些符号（通常是减号或约等号）读成了不可见的控制字符，它们已经从 `source.md` 中删去。这些符号在那几页上就缺失了；如果要紧，在 `source.md` 里补回去。
- **`Unsupported Markdown structure: control character U+0000 at line N. …`** `source.md` 里仍有控制字符（包是在上面那行信息出现之前提取的，或者是手工编辑带进来的）。删掉它再重跑；此时还什么都没有翻译。
- **`Unsupported Markdown structure: raw tex '\s' at block 29 … Normalize the source before translation.`** 提取器不会转义正文中的 Markdown，所以一句含有 `\s`、`[u](y)` 或 `<k>` 的话会被当成原始 TeX、链接或 HTML。在 `source.md` 里把它转义后重跑；提取不会重做。
- **`Reusing the extraction made with docling … on …; this run would use docling … on …. Delete the bundle directory to extract again.`** 提示信息。这份提取结果来自另一个设备或另一个 docling 版本。

### 翻译与导出

- **`Translation reused: … (same source and settings; delete it to translate again).`** 提示信息。删除 `book_bilingual.md` 即可重新翻译。
- **`Error: translate failed: Source or translation settings changed; start a new translation bundle.`** 你在翻译到一半之后改了模型、语言或 `source.md`。用原来的设置重跑，或者把这个包移开，从头开始。
- **`Error: … Bilingual Markdown was edited; rebuild the EPUB from it with: python tools/pdf_to_book.py export <name>_book, then copy <name>_book/book_bilingual.epub over <name>_bilingual.epub.`** 你手工编辑了 `book_bilingual.md`。这是允许的，但运行不会覆盖它。按这行信息说的做两步（信息里带有完整路径）：导出会根据你的修改在工作目录里重新生成成书，复制则把它放到 PDF 旁边；见[手工修补译文](#手工修补译文)。如果想重新翻译，删除 `book_bilingual.md`。
- **`EPUB navigation is invalid: …`** 这些标题构不成可用的目录。在 `source.md` 里修正标题层级（一个 `#` 文档标题，然后是 `##`、`###`）后重跑。
- **`Interrupted. Rerun the same command to resume.`** 按了 Ctrl+C。重跑即可；已完成的阶段不会重复。

### 运行结束后

- **`Image model (<model> at <address>): tokens: …`** 图像模型自己的用量，在提取之后打印，与翻译的用量分开。
- **`Nothing on this route classifies yet, so --classify-model is ignored on a … book.`** 某个分类参数或提供方的 `classify_model` 传到了 `source.md` 的翻译阶段，而那里没有任何东西做分类。无害。

### 需要了解的限制

- 插图保留为图片，其中的标注不翻译。
- EPUB 中没有 `bbm_translation_metadata.json`，这条路线暂时也不支持 `--no_disclosure`：署名行总会加上。
- `--glossary-auto` 只在发生压缩时学习，所以一篇短论文在默认预算下什么也学不到。
- 在带有良好可见文字层的扫描件上，`--ocr-replace-layer` 读出的效果不如文字层（字符更少，还有明显的误读），所以只在文字层有误时使用它。在带有不可见文字层的扫描件上，两者给出的文字几乎相同。
- 带有不可见文字层时，单用 `--pdf-ocr` 就已经会让 OCR 引擎读取页面，这就是上面中文扫描件的数字相近的原因。目前还做不到在开启 OCR 的同时原封不动地保留这样的文字层。
- 清单只在全部为空而停止的情况下记录失败的尝试。
