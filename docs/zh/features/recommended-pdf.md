# PDF 推荐设置

每份 PDF 都用同样的方式开始：两页、几段译文，然后读一读产出的内容。

```bash
python make_book.py \
  --book_name book.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

它只提取这两页，几乎不花钱。打开 `book_pages-1-2_book/source.md`，至少读一遍标题：它们会成为目录。一页的 Markdown 长达几千行，那是某张插图带出来的垃圾，不是长页面。看起来没问题，就运行下面与你的文档对应的命令。如果有不对的地方，在 `source.md` 里改好再重跑；提取结果会被复用。

这条路线需要 [PDF 扩展](../installation-pdf.md)和 Pandoc 3.1.12 或更新版本。每个参数的作用见 [PDF 转双语 EPUB](pdf-to-epub.md)。

## 需要什么，就传什么

| 如果你的 PDF 是… | 传入 | 原因 | 详见 |
|---|---|---|---|
| 任何想做成书的 PDF | `--to-epub --use_context session` | 生成带目录的可重排 EPUB；会话让每个短小的文本块都能看到它前面的内容 | [PDF 转双语 EPUB](pdf-to-epub.md) |
| 扫描件（没有文字层） | `--pdf-ocr` | 不传它，没有文字的页面会被拒绝，所以你永远不用猜 | [OCR 参数](pdf-to-epub.md#准备工作) |
| 中文、日文、韩文或其他非拉丁字母文字的扫描件 | `--pdf-ocr --ocr-lang iso:ja`（或 `iso:ko`，…） | 默认的 OCR 引擎只识别它自己的语言：在 Mac 上（ocrmac）是英语、西班牙语、法语和德语，其他系统上（rapidocr）是中文和英语 | [OCR 语言](pdf-to-epub.md#准备工作) |
| 中文扫描件 | `--pdf-ocr --ocr-lang iso:zh`（繁体用 `iso:zh-Hant`） | 不是 `ch_sim`：rapidocr 会拒绝它 | [中文扫描件](#按文档类型) |
| 简体中文扫描件 | `--pdf-ocr --ocr-engine rapidocr --ocr-lang iso:zh` | 测量中汉字错误率最低 | [选择 OCR 引擎](pdf-ocr-engines.md) |
| 在 Mac 上，并且不想下载任何东西 | `--pdf-ocr`（auto 会选 ocrmac，pdf 扩展在 Mac 上会安装它；`--ocr-engine ocrmac` 可以直接指定它） | Apple 的 Vision 框架，无需下载；在英文和繁体中文上与最好的引擎只差一点，简体中文扫描件上 rapidocr 更好 | [选择 OCR 引擎](pdf-ocr-engines.md) |
| 已经带有 OCR 文字层的扫描件（Internet Archive、ABBYY） | 不用额外传什么 | 运行时会使用这层文字，并说明这一点 | [扫描书](#按文档类型) |
| 文字层是乱码的扫描件 | `--pdf-ocr --ocr-replace-layer --ocr-lang <lang>` | 每一页都由 OCR 引擎重新读取 | [文字层有误的扫描件](#按文档类型) |
| 论文，或任何带代码清单的文档 | `--img-model gpt-6-luna` | 视觉模型会纠正被当成标题的作者行和被读成脚注的代码清单，每页约 3,000 个提示 token | [纠正区域角色](pdf-to-epub.md#用视觉模型纠正区域角色) |
| 想一次翻译一章的长书 | `--pages 12-30` | 每个范围都有自己的书，互不覆盖 | [PDF 参数](../formats/pdf.md) |
| 满是数学公式 | 不用额外传什么 | 行间公式默认保留为图片 | [PDF 参数](../formats/pdf.md) |
| 人名或术语的译法必须固定 | `--glossary terms.txt` | 钉住的译法，只随出现它们的文本块一起发送 | [会话模式](session-mode.md) |
| 在一台 GPU 不太正常的机器上 | `--device cpu` | 文字完全一样，只是更慢 | [按系统](#按系统) |

## 按文档类型

=== "小说"

    排印的小说标题少，也没有表格。不想要的书前内容，用 `--pages` 排除。

    ```bash
    python make_book.py \
      --book_name novel.pdf \
      --to-epub \
      --language zh-hans \
      --use_context session \
      --quiet
    ```

    先检查 `source.md` 里的章节标题：小说的章节常常不带编号，这时它们的层级只能靠字号来判断。区域角色判定是在论文、网页和代码清单上测量的，没有在小说上测量过；使用 `--provider openai` 时它是开启的，用 `--img-model none` 可以省下这部分 token。

=== "带表格和公式的教材"

    不用 OCR 也能检测表格。行间公式会变成图片；公式周围的文字会翻译，公式本身不翻译。

    ```bash
    python make_book.py \
      --book_name textbook.pdf \
      --to-epub \
      --pages 12-30 \
      --language zh-hans \
      --use_context session \
      --glossary terms.txt
    ```

    用 `--pages` 一次翻译一章；每个范围都有自己的书。带代码清单的教材能从 `--img-model gpt-6-luna` 中受益：清单的各行会以代码的形式输出，而不是变成脚注。句子里的行内数学式不是公式区域，不在处理范围内：文字层或 OCR 把它读成什么样，它就是什么样。

=== "论文"

    论文会被提取成许多短小的文本块；会话让每个文本块都能看到它前面的内容。

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --language zh-hans \
      --use_context session \
      --img-model gpt-6-luna
    ```

    在大多数论文上，标题层级都完全正确（20 篇 arXiv 论文中，195 个标题有 187 个正确）。`--img-model` 会把被当成标题的作者行或插图标注降级，每页约 3,000 个提示 token；不传它就不在这上面花钱。如果不想为参考文献付费，用 `--pages` 把它排除在外。

=== "扫描书"

    没有文字层的页面，在你传入 `--pdf-ocr` 之前都会被拒绝。

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --language zh-hans \
      --use_context session
    ```

    不写 `--ocr-lang` 时，OCR 引擎只识别它自己的默认语言：在 Mac 上（ocrmac）是英语、西班牙语、法语和德语，其他系统上（rapidocr）是中文和英语。其他语言的扫描件需要 `--ocr-lang`。

    **如果扫描件已经带有 OCR 文字层**（Internet Archive 和 ABBYY FineReader 生成的文件常常如此），先不加 `--pdf-ocr` 试一次：运行会打印 `… selected pages carry only an invisible OCR text layer …` 并使用这层文字，它通常比本地重新做一遍 OCR 读得更好。如果这层文字其实是乱码，见下一个标签页。

    `--img-model` 对扫描件作用不大：版面检测器已经切成碎片的页面，它拼不回来。

=== "文字层有误的扫描件"

    如果页面图像很清晰，`source.md` 读起来却不知所云，说明扫描件的文字层有误：识别成了别的语言，或者是劣质 OCR 留下的乱码。`--ocr-replace-layer` 会丢弃这层文字，改用 OCR 引擎读取每一页。

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --ocr-replace-layer \
      --ocr-lang iso:zh \
      --language en \
      --use_context session
    ```

    在 `--ocr-lang` 里写扫描件本身的语言（这里是 `iso:zh`，对应中文扫描件）：语言不对，引擎就什么也读不出来。先试两页。引擎一无所获的页面会被点名并留空；如果一页都没读出来，运行会在任何翻译付费之前停止。文字层读起来没问题的扫描件不要用它：重新做的 OCR 不如一层好的文字层。

=== "中文扫描件"

    指明 OCR 引擎要识别的语言。`iso:` 标签无论运行哪个引擎都有效：简体用 `iso:zh`，繁体用 `iso:zh-Hant`。不是 `ch_sim`：rapidocr 会在读取任何页面之前拒绝它。

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --ocr-lang iso:zh \
      --language en \
      --use_context session
    ```

    横排文字读得很好。**竖排文字**（从上到下、从右到左排印的繁体书）输出时列的顺序是错的。翻译竖排扫描件之前，先检查 `source.md`。

## 按系统

这条路线不需要 GPU。设备只影响速度，从不影响文字。从仓库的克隆安装；[安装 PDF 扩展](../installation-pdf.md)逐行解释了每一条命令。

=== "macOS（Apple 芯片）"

    ```bash
    pip install ".[pdf]"
    ```

    直接在本机安装；`--device auto` 会找到 MPS，运行时打印 `PDF extraction device: mps.`。Mac 上的 Docker 用不到 MPS。

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --use_context session
    ```

=== "带 NVIDIA 显卡的 Linux"

    ```bash
    pip install ".[pdf]"
    ```

    `--device auto` 会找到 CUDA，运行时打印 `PDF extraction device: cuda.`。想确保用上，就明确指定它；如果用不了，运行会拒绝并说明原因：

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --device cuda \
      --use_context session
    ```

=== "带 NVIDIA 显卡的 Windows"

    ```bat
    pip install ".[pdf]" ^
        --extra-index-url https://download.pytorch.org/whl/cu126
    ```

    PyPI 上的 Windows wheel 只支持 CPU，所以要指定 PyTorch 的 CUDA 索引，并安装 NVIDIA 驱动。之后的运行方式与 Linux 相同。

=== "只有 CPU"

    在 Linux 上，加上 PyTorch 的 CPU 索引（约 380 MB，而不是约 3.2 GB 的 CUDA）：

    ```bash
    pip install ".[pdf]" \
        --extra-index-url https://download.pytorch.org/whl/cpu
    ```

    在 Windows 上，普通的 `pip install ".[pdf]"` 装的就是 CPU 版。文字与 GPU 上完全一样，只是提取更慢（一份两页的 OCR 扫描件：CPU 上 26.3 s，Apple 芯片的 GPU 上 10.6 s）。

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

=== "Docker"

    `pdf` 镜像标签带有 Pandoc 和 PDF 相关的包。把模型放在一个卷里，这样只需下载一次。

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --use_context session
    ```

    在带 NVIDIA 显卡的 Linux 或 Windows（WSL2）上，改用 `pdf-cuda` 标签（只有 amd64）并加 `--gpus all`。见 [Docker 安装](../docker.md)。

## 对任何 PDF 都该有的预期

- 插图保留为图片，其中的标注不翻译。
- 行间公式是从页面上裁下来的图片：在原来的位置可以看到，但不翻译，也搜索不到。行内数学式不在处理范围内。
- 含有 `\s`、`[u](y)` 或 `<k>` 的句子可能在翻译之前被拒绝；在 `source.md` 里把它转义后重跑。
- 在这条路线上，EPUB 总是带有那一行 AI 翻译署名；这里不支持 `--no_disclosure`。
- PDF 是对页面的描述，而不是文档：标题差了一级、表格变成了正文，这是格式本身的局限，在 `source.md` 里花一分钟就能改好。
