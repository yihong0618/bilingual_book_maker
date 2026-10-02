# EPUB

如果你想要一本 EPUB 的双语版，大多数人需要的就是这条命令：

```bash
bbook_maker --book_name my_book.epub --language zh-hans --use_context session --quiet
```

它会在输入文件旁边生成 `my_book_bilingual.epub`。遇到教材、小模型或本地服务器时要改什么，见 [EPUB 推荐设置](../features/recommended-epub.md)。

EPUB 是本工具最熟悉的格式，所有功能都能用在它上面。

## 文件是怎么读取的

EPUB 是一个由 XHTML 页面组成的 zip 包。工具会读取书的 spine 里列出的每一页。

- **使用大模型路线时（默认）：**书会经过[计划模式](../features/plan-mode.md)。加载器找出每一个承载文字的块（段落、标题、列表项、表格单元格、引文、诗行、图题），按类型分组，再问模型哪几类值得翻译。答案保存在 `<book>_plan.json` 里，之后的运行会复用它。随后，相邻的块在 token 预算和单元数上限之内合成一次请求。
- **使用翻译服务路线且没有 `--classify-model`，或使用 `--plan-classify none` 时：**只翻译 `--translate-tags` 选中的标签，默认是 `<p>`。这时 `<p>` 之外的诗歌或表格会保留源语言。
- 段落里的链接、强调和其他行内标记在翻译前被替换成编号标记，翻译后再放回原处，所以模型永远看不到原始 HTML。
- `--exclude-translate-tags` 里的内容（默认 `sup` 和 `code`）从不发送。

## 你会得到什么

- 输入文件旁边的 `<name>_bilingual.epub`。每段译文紧跟在它的原文后面，使用相同的标签和 class。id 和内部链接都会保留。
- 书籍简介下方的一行署名，内容为“Translated by \<model\>, \<year\>.”；`--no_disclosure` 可以去掉它。
- 计划模式和会话模式的运行会在书里写入 `bbm_translation_metadata.json`：模型、日期，以及 `--glossary` 文件的校验和。
- 输入文件旁边的 `<book>_plan.json`（计划），以及会话模式运行时的 `<book>_handoff.md`（最新的交接报告）。
- 按 Ctrl+C 或出错时：`<name>_bilingual_temp.epub` 和一个检查点。加上 `--resume` 重新运行即可。

## 推荐命令

```bash
bbook_maker \
  --book_name my_book.epub \
  --language zh-hans \
  --use_context session \
  --quiet
```

遇到不寻常的书（教材、双语版、正文不在 `<p>` 里的书），先预览计划。这一步不需要 key，也不翻译任何内容：

```bash
bbook_maker \
  --book_name my_book.epub \
  --plan-dry-run
```

## 适用的参数

### 路线与运行

这些参数在所有格式上作用相同。

| 参数 | 作用 |
|---|---|
| `--book_name PATH` | 要翻译的文件。扩展名决定格式。 |
| `-m`, `--model MODEL` | 模型 id，按端点自己的写法。openai 格式下默认 `gpt-6-luna`。 |
| `--key KEY` | API key；用逗号分隔多个 key 可以轮换使用。未提供时依次读取 `BBM_API_KEY` 和该格式自己的变量。 |
| `--api_base URL` | 端点地址。默认是该格式的官方地址。 |
| `--api_format FORMAT` | 端点所用的 API，或一个翻译服务。省略时根据 `--api_base` 推断。 |
| `--provider NAME` | `bbm_providers.json` 里的一个具名端点。 |
| `--model_list IDS` | 轮换使用的多个模型。与 `--use_context session` 同用会被拒绝。 |
| `--language LANGUAGE` | 目标语言：标签、语言名，或 `TAG:NAME`。默认 `zh-hans`。 |
| `--source_lang LANGUAGE` | 明确指定源语言。会写进每个大模型提示词；在 `qwen` 和 `customapi` 上作为请求字段发送。 |
| `--prompt VALUE_OR_FILE` | 自定义提示词：`user` 模板（必须包含 `{text}`）、`system`、`style`。 |
| `--temperature FLOAT` | 采样温度，仅用于接受该参数的格式。 |
| `--no-thinking` | 让模型回答前不要先推理。在 OpenAI 形态的路线上，请求字段通过协商确定；在 anthropic 上是 `thinking: disabled`；在 codex 上会被拒绝。 |
| `--extra_body JSON` | 在 openai 和 anthropic 路线上附加的请求体字段。 |
| `--extra_headers JSON` | 在 openai 和 anthropic 路线上附加的 HTTP 头。 |
| `--interval SECONDS` | 请求之间的间隔。只有 gemini 格式使用它。 |
| `-p`, `--proxy URL` | 本次运行使用的 HTTP 代理。 |
| `--test` | 只翻译开头几段。 |
| `--test_num N` | 配合 `--test`，翻译多少段（默认 10）。 |
| `--resume` | 从检查点继续一次中断的运行。 |
| `--single_translate` | 只写出译文，不带原文。 |

### EPUB 参数

| 参数 | 作用 |
|---|---|
| `--translate-tags TAGS` | 没有计划时要翻译的标签（默认 `p`）。计划模式下忽略。 |
| `--exclude-translate-tags TAGS` | 其内容从不翻译的标签（默认 `sup,code`；传 `""` 清空）。 |
| `--allow_navigable_strings` | 连不在任何标签里的文字也一起翻译。计划模式下多余。 |
| `--only_filelist FILES` | 只翻译这些内部文件（相对于 OPF 的名字，逗号分隔）。 |
| `--exclude_filelist FILES` | 跳过这些内部文件。给了 `--only_filelist` 时忽略。 |
| `--plan-classify MODE` | 计划由谁决定：`auto`（默认）、`none`、`all`、`model`、`agent`。见[计划模式](../features/plan-mode.md)。 |
| `--classify-model MODEL` | 用另一个模型分类（默认：提供方条目的 `classify_model`，其次是翻译用的模型）。在命令行上写明时，运行进入 `model` 模式，分类失败会中止运行；它也能让翻译服务路线用上计划。`--plan-classify-model` 是旧名字。在 `--plan-classify all` 或 `agent` 下忽略。 |
| `--classify-base-url URL` | 该模型的服务地址，当它不是本次运行的端点时使用（仅限 OpenAI 兼容）。 |
| `--classify-key KEY` | `--classify-base-url` 使用的 key。见[哪个 key 发往哪里](../providers.md#哪个-key-发往哪里)。 |
| `--plan-dry-run` | 打印计划并写出 `<book>_plan.json`，不翻译。不需要 key。 |
| `--plan-min-coverage FRACTION` | 计划覆盖的文字比例低于这个值时中止（默认 0.5）。 |
| `--poetry-group-size N` | 已弃用；仍然可用，但会警告。请改用 `--max-batch-units`。 |
| `--accumulated_num N` | 计划模式下是每个请求的 token 预算（不设时自动推算；`1` 关闭合并）。没有计划时，是每个请求累积的字符数。 |
| `--max-batch-units N` | 计划模式：一个请求最多包含的单元数（默认 16；在不支持严格 schema 的端点上为 8）。 |
| `--block_size N` | 没有计划时：把段落合并成用分隔符方式翻译的块。`--accumulated_num` 大于 1 时忽略。 |
| `--sentence_mode` | 逐句翻译。不能与计划同用；`--accumulated_num` 大于 1 时忽略。 |
| `--use_context [window\|session]` | 带上前文：不带值或 `window` 会重发最近几对原文与译文；`session` 保持一份历史。见[会话模式](../features/session-mode.md)。 |
| `--context_paragraph_limit N` | 窗口模式：重发多少对。 |
| `--context-compact-at N` | 会话模式：历史达到这么多估算 token 时压缩（默认 8192，最小 1500）。 |
| `--no-context-compact` | 会话模式：窗口滚动时不写交接报告。 |
| `--glossary FILE` | `term -> translation` 形式的固定译法，只随包含这些术语的请求发送。适用于 openai 系和 codex 路线。`--terminology` 是同一个参数。 |
| `--glossary-auto on\|off` | 保留会话的交接报告里确立的译名。除非你要求，否则关闭。 |
| `--parallel-workers N` | 同时翻译几个章节（2 到 4 比较有用）。与会话模式同用或在 codex 上会被拒绝。 |
| `--translation_style CSS` | 译文段落的 CSS。 |
| `--translation_color COLOR` | 只设颜色；`--translation_style` 优先。 |
| `--no_disclosure` | 去掉那一行 AI 翻译署名，同时不写元数据文件。 |
| `--translation-metadata` | 在普通的标签模式运行中也写入 `bbm_translation_metadata.json`（计划模式和会话模式的运行本来就会写）。 |
| `--retranslate OUT FILE START END` | 重新翻译一本已有双语 EPUB 中的一段范围。 |
| `--quiet` | 不显示进度条，也不回显段落；报告和错误照常打印。 |

## 不适用于本格式

- `--batch_size`：EPUB 加载器用 `--accumulated_num` 合并请求；运行会给出警告。
- `--batch`、`--batch-use`：在 EPUB 上会被拒绝，因为那里永远走不到 Batch API 的路径。
- `--to-epub`、`--pdf-ocr`、`--ocr-replace-layer`、`--device`、`--ocr-lang`、`--pages`、`--no-formula-images`、`--pdf_layout`、`--img-model`、`--img-base-url`、`--img-key`：仅限 PDF。在 EPUB 上用 `--to-epub` 会中止运行；其他参数会给出警告或不起作用（三个图像参数：运行会警告只有 PDF 路线才有图像步骤）。
