# TXT

```bash
bbook_maker --book_name my_book.txt --language zh-hans --batch_size 20
```

这会在输入文件旁边写出 `my_book_bilingual.txt`。SRT 字幕和 Markdown 在下面各有自己的页面；PDF 有[单独的一节](../features/pdf-to-epub.md)。

纯文本文件按行分组逐组翻译。没有要保留的结构，所以可选项也不多。

## 文件是怎样读取的

- 文件按 UTF-8 读取，并切分成行。
- 这些行按 `--batch_size`（默认 10）分组发送，组内用换行符连接。
- 空的、只有空白或只有一个数字的组会原样复制，不发请求。
- 请求不携带任何前文：TXT 加载器没有上下文模式。

## 你会得到什么

- 输入文件旁边的 `<name>_bilingual.txt`：每组原文行后面紧跟它的译文。使用 `--single_translate` 时只有译文。
- 按 Ctrl+C 或出错时：`<name>_bilingual_temp.txt` 和一个检查点。用 `--resume` 重跑。

## 推荐命令

```bash
bbook_maker \
  --book_name test_books/the_little_prince.txt \
  --language zh-hans \
  --batch_size 20
```

`--batch_size` 越大，请求越少，每个请求的上下文越多。如果模型开始把行合并或漏掉，就把它调小。

## 适用的参数

### 路线与运行

这些参数在所有格式上的作用都一样。

| 参数 | 作用 |
|---|---|
| `--book_name PATH` | 要翻译的文件。扩展名决定格式。 |
| `-m`, `--model MODEL` | 模型 ID，按端点自己的写法原样填写。openai 格式下默认 `gpt-5.6-luna`。 |
| `--key KEY` | API key；用逗号分隔的多个 key 会轮流使用。不写时先读 `BBM_API_KEY`，再读该格式自己的变量。 |
| `--api_base URL` | 端点地址。默认是该格式的官方地址。 |
| `--api_format FORMAT` | 端点使用的 API，或者一个机器翻译引擎。不写时根据 `--api_base` 推断。 |
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

### TXT 参数

| 参数 | 作用 |
|---|---|
| `--batch_size N` | 一个请求发送的行数（默认 10）。 |

## 不适用于这种格式的参数

- `--use_context`：TXT 加载器在两种模式下都不会把上下文交给模型。运行时会给出警告；对会话模式，它打印 `--use_context session is not supported for txt books; it will be ignored.`
- `--context_paragraph_limit`、`--context-compact-at`、`--no-context-compact`：上下文参数；这里没有东西读取它们。
- `--glossary`、`--glossary-auto`：只有 EPUB、Markdown 和 PDF 加载器会转发它们；运行时会给出警告。
- `--accumulated_num`：TXT 加载器不读它；运行时会给出警告，并提示改用 `--batch_size`。
- `--max-batch-units`、`--plan-classify`、`--plan-dry-run`、`--plan-min-coverage`、`--poetry-group-size`：计划模式只用于 EPUB。
- `--classify-model`、`--classify-base-url`、`--classify-key`（以及旧名称 `--plan-classify-model`）：这种格式上暂时没有任何东西做分类。运行时会警告该参数被忽略。
- `--translate-tags`、`--exclude-translate-tags`、`--allow_navigable_strings`：EPUB 标记选择器；运行时会给出警告。
- `--only_filelist`、`--exclude_filelist`：EPUB 内部文件；忽略。
- `--block_size`、`--sentence_mode`：仅用于 EPUB；忽略。
- `--parallel-workers`：TXT 的运行保持串行；运行时会给出警告。
- `--translation_style`、`--translation_color`：纯文本没有样式；运行时会给出警告。
- `--no_disclosure`、`--translation-metadata`：输出不带署名行，也不带元数据文件；运行时会给出警告。
- `--retranslate`：仅用于 EPUB；会被拒绝。
- `--quiet`：仅用于 EPUB；运行时会给出警告。
- `--batch`、`--batch-use`：TXT 加载器没有实现 Batch API。
- `--to-epub`、`--pdf-ocr`、`--ocr-replace-layer`、`--device`、`--ocr-lang`、`--pages`、`--no-formula-images`、`--pdf_layout`、`--img-model`、`--img-base-url`、`--img-key`：仅用于 PDF。在这种格式上，`--to-epub` 会让运行停止；其余的会给出警告或不起作用（三个图像参数：运行时会警告只有 PDF 路线有图像这一步）。
