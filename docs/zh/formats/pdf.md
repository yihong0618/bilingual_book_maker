# PDF

PDF 可以走两条路线。除非你只想要纯文本，否则选第一条。

| 路线 | 参数 | 输出 | 需要 |
|---|---|---|---|
| **PDF 转双语 EPUB** | `--to-epub` | 带目录的可重排 EPUB，插图和行间公式保留为图片 | [PDF 扩展](../installation-pdf.md)和 Pandoc 3.1.12+ |
| **纯文本路线**（旧） | 无 | 一个双语 `.txt`，另外尝试生成一个 EPUB | 基础安装 |

## `--to-epub` 路线

### 文件是怎样读取的

1. [docling](https://github.com/docling-project/docling) 用它的版面模型和表格模型读取 PDF 的文字层，写出 Markdown：`<name>_book/source.md` 和图片。
2. 没有文字层的页面（扫描件）会被拒绝，除非你传入 `--pdf-ocr`，这时由 OCR 模型读取它们。有文字层的页面保留自己的文字层，除非你同时传入 `--ocr-replace-layer`：这时每一页都做 OCR，PDF 自带的文字层不再使用。
3. 使用 `--img-model` 时，视觉模型会根据页面图像，纠正 docling 给页面各区域定的角色（标题、图题、脚注、代码）。
4. 标题层级从页面上读出（先看编号，再看字号和字重）。行间公式从页面上裁成图片。
5. [Markdown 加载器](md.md)把 `source.md` 翻译成 `book_bilingual.md`。
6. Pandoc 生成 EPUB。它的导航按标题生成。

重跑同一条命令会复用提取结果和已完成的翻译。如果有内容出错，在翻译开始之前编辑 `source.md`。删除 `book_bilingual.md` 即可重新翻译。推荐的命令和要留意的终端信息见 [PDF 转双语 EPUB](../features/pdf-to-epub.md)。

### 你会得到什么

- PDF 旁边的 `<name>_book/`：`source.md`、`images/`、`book_bilingual.md`、一份清单，以及 `.work/`。
- PDF 旁边的 `<name>_bilingual.epub`，是成书的一份副本。
- `source.md` 中，每一页开头都有一条 `<!-- page N -->` 注释，编号与 PDF 文件中的一致（从 1 开始，与 `--pages` 的计数相同），空白页也包括在内。docling 没有给出页码的条目（很少见）放在最后，位于 `<!-- unplaced -->` 下面；运行时会说明有多少条。
- 使用 `--pages 12-30` 时：`<name>_pages-12-30_book/` 和 `<name>_pages-12-30_bilingual.epub`，所以某一章永远不会覆盖整本书。

### 适用的参数

这条路线自己的参数：

| 参数 | 作用 |
|---|---|
| `--to-epub` | 走这条路线。 |
| `--pdf-ocr` | 用 OCR 模型读取没有文字层的页面。默认关闭；不开的话，这些页面会被拒绝。 |
| `--ocr-replace-layer` | 配合 `--pdf-ocr`：对每一页做 OCR，用识别结果代替 PDF 的文字层。默认关闭：保留已有的文字层，只对没有文字层的页面做 OCR。适用于有误的文字层（别的语言、乱码）；在干净的扫描件上，文字层读得更好。切换它会重新读取 PDF。 |
| `--ocr-lang LANGS` | 配合 `--pdf-ocr`：OCR 引擎要识别的语言，用逗号分隔，写引擎自己的代码（rapidocr：`ch`、`en`、`latin`；easyocr：`ch_sim`、`ja`、`ko`；ocrmac：`zh-Hans`、`ja-JP`），或在 BCP-47 标签前加 `iso:`（`iso:zh`、`iso:ja`），所有引擎都接受。rapidocr 只用第一种语言，而且拒绝 `ch_sim`：中文请写 `iso:zh`。 |
| `--device auto\|cpu\|cuda\|mps\|xpu` | 提取模型在哪里运行。`auto`（默认）会检测加速器，找不到就退回 CPU。CPU 的输出完全一样，只是更慢。 |
| `--pages PAGES` | 只处理这些页，从 1 开始编号（`12-30`、`1,3,5-7`）。 |
| `--no-formula-images` | 把行间公式留作 `<!-- formula-not-decoded -->` 占位符，而不是图片。 |
| `--img-model MODEL` | 根据页面图像纠正区域角色的视觉模型。只有在这里指定，或在提供方条目的 `img_model` 中指定时才开启（自带的 `openai` 条目指定了一个）；`none` 可以关掉它。绝不会退而使用翻译模型。每页约 3,000 个提示 token。 |
| `--img-base-url URL` | 这个模型所在的地址，当它不在本次运行的端点上时使用（仅限 OpenAI 兼容接口）。 |
| `--img-key KEY` | `--img-base-url` 所用的 key；默认在本次运行自己的端点上使用本次运行的 key。 |
| `--quiet` | 提取期间不显示实时进度行。 |

其他所有参数都原样传给 Markdown 翻译。下面这些在那里会起作用：

| 参数 | 作用 |
|---|---|
| `--book_name PATH` | 要翻译的文件。扩展名决定格式。 |
| `-m`, `--model MODEL` | 模型 ID，按端点自己的写法原样填写。openai 格式下默认 `gpt-6-luna`。 |
| `--key KEY` | API key；用逗号分隔的多个 key 会轮流使用。不写时先读 `BBM_API_KEY`，再读该格式自己的变量。 |
| `--api_base URL` | 端点地址。默认是该格式的官方地址。 |
| `--api_format FORMAT` | 端点使用的 API，或者一个翻译服务。不写时根据 `--api_base` 推断。 |
| `--provider NAME` | `bbm_providers.json` 中的一个具名端点。 |
| `--model_list IDS` | 轮流使用的多个模型。与 `--use_context session` 同用时会被拒绝。 |
| `--language LANGUAGE` | 目标语言：一个标签、一个名称，或 `TAG:NAME`。默认 `zh-hans`。 |
| `--source_lang LANGUAGE` | 明确给出源语言。会写进每一个大模型提示词；在 `qwen` 和 `customapi` 上作为一个字段发送。 |
| `--prompt VALUE_OR_FILE` | 自定义提示词：`user` 模板（必须包含 `{text}`）、`system`、`style`。 |
| `--temperature FLOAT` | 采样温度，用于接受这一参数的格式。 |
| `--no-thinking` | 要求模型回答之前不做推理。在 OpenAI 形式的路线上，这个字段会自动协商；anthropic 上是 `thinking: disabled`；codex 上会被拒绝。 |
| `--extra_body JSON` | openai 和 anthropic 路线上额外的请求体字段。 |
| `--extra_headers JSON` | openai 和 anthropic 路线上额外的 HTTP 头。 |
| `--interval SECONDS` | 请求之间的间隔。只有 gemini 格式使用它。 |
| `-p`, `--proxy URL` | 本次运行使用的 HTTP 代理。 |
| `--test` | 只翻译开头的若干段。 |
| `--test_num N` | 配合 `--test`：翻译多少段（默认 10）。 |
| `--resume` | 从检查点继续一次中断的运行。 |
| `--single_translate` | 只写出译文，不带原文。 |
| `--batch_size N` | 每个请求包含的正文块数（默认 10）。 |
| `--use_context [window\|session]` | 携带前文。推荐 `session`：PDF 会被提取成许多短小的文本块。 |
| `--context_paragraph_limit N` | 窗口模式：重发多少对原文与译文。 |
| `--context-compact-at N` | 会话模式：估算 token 数达到这个值时压缩（默认 8192，最小 1500）。 |
| `--no-context-compact` | 会话模式：滚动到下一个窗口时不写交接报告。 |
| `--glossary FILE` | `term -> translation` 形式钉住的译法（openai 形式的路线和 codex 路线）。 |
| `--glossary-auto on\|off` | 保留交接报告中确立的译名。只在发生压缩时学习，所以一篇短论文在默认预算下什么也学不到。 |
| `--parallel-workers N` | 同时处理多个批次。与会话同用时会被拒绝。 |

这条路线的旧写法 `--with-ocr` 和 `--no-gpu` 在一个发布周期内仍然有效，分别等同于 `--pdf-ocr` 和 `--device cpu`，并会打印一条提示。它们不出现在 `--help` 里。

### 不适用于 `--to-epub` 路线的参数

- `--no_disclosure`：暂不支持；署名行总会加上。
- `--translation-metadata`：这个 EPUB 由 Pandoc 生成，不带元数据文件，也不嵌入术语表。
- `--pdf_layout`：属于纯文本路线。
- `--accumulated_num`、`--max-batch-units` 以及计划模式参数（`--plan-classify`、`--plan-dry-run`、`--plan-min-coverage`）：计划模式只用于 EPUB；Markdown 加载器用 `--batch_size` 分组。
- `--classify-model`、`--classify-base-url`、`--classify-key`：这条路线上暂时没有任何东西做分类。运行时会警告该参数被忽略。
- `--translate-tags`、`--exclude-translate-tags`、`--allow_navigable_strings`、`--only_filelist`、`--exclude_filelist`、`--block_size`、`--sentence_mode`、`--translation_style`、`--translation_color`、`--retranslate`：仅用于 EPUB 输入。
- `--batch`、`--batch-use`：这条路线没有实现。

## 纯文本路线（不加 `--to-epub`）

### 文件是怎样读取的

每一页的文字由 PyMuPDF 取出，再切分成行。这些行按 `--batch_size`（默认 10）分组发送；开启 `--use_context`（window 或 `session`）时带上前文，使用 `--glossary` 时带上钉住的译法。没有任何结构：没有标题，没有插图，没有表格。

### 你会得到什么

- PDF 旁边的 `<name>_bilingual.txt`。
- 用同样的文字尝试生成 `<name>_bilingual.epub`。如果失败，TXT 仍然保留，所以不会重复翻译。
- 使用 `--pdf_layout` 时：`<name>_bilingual_<layout>.pdf`，对应 `top-bottom`、`side-by-side`，或用 `all` 两者都生成。这需要 `reportlab` 包，它不在依赖列表里；没有它时，运行会打印 `pdf creation skipped: install reportlab first` 并继续。
- 按 Ctrl+C 或出错时：`<name>_bilingual_temp.txt`。用 `--resume` 重跑。

```bash
bbook_maker \
  --book_name paper.pdf \
  --language zh-hans \
  --batch_size 20
```

### 适用的参数

上表中的路线参数和运行参数，另外还有：

| 参数 | 作用 |
|---|---|
| `--batch_size N` | 每个请求包含的行数（默认 10）。 |
| `--pdf_layout none\|top-bottom\|side-by-side\|all` | 另外按这种版式写出一个双语 PDF（需要 `reportlab`）。 |

### 不适用于纯文本路线的参数

- `--parallel-workers`：运行保持串行。运行时会给出警告。
- `--pdf-ocr`、`--ocr-replace-layer`、`--device`、`--ocr-lang`、`--pages`、`--no-formula-images`：仅用于 `--to-epub`。运行时会给出警告，并读取整个文件。
- `--img-model`、`--img-base-url`、`--img-key`：图像这一步属于 `--to-epub`。运行时会给出警告。
- `--classify-model` 及其两个配套参数：这里没有任何东西做分类。运行时会给出警告。
- 上面为 `--to-epub` 路线列出的所有仅限 EPUB 的参数，以及 `--accumulated_num`、`--quiet`、`--no_disclosure`、`--translation-metadata`。
