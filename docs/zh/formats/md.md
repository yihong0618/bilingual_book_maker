# Markdown

Markdown 文件按正文块逐块翻译。代码、表格和 front matter 原样保留。

## 文件是怎样读取的

- YAML front matter、围栏代码块、表格以及不含正文的行都原样通过，不翻译。
- 标题和段落是翻译块。
- 这些块按 `--batch_size`（默认 10）分批发送，一批在大约 2000 个字符处也会截止。批次上方的标题路径会作为面包屑一起发送，让模型知道自己在哪一节。
- `--use_context` 在这里可用，窗口模式和会话模式都行。

这也是 [PDF 转双语 EPUB](../features/pdf-to-epub.md) 背后的加载器：PDF 先被转成 Markdown，再在这里翻译。

## 你会得到什么

- 输入文件旁边的 `<name>_bilingual.md`：每一块后面紧跟它的译文。使用 `--single_translate` 时只有译文。
- 会话模式的运行还会生成 `<name>_handoff.md`，里面是最新的交接报告。
- 按 Ctrl+C 或出错时：`<name>_bilingual_temp.txt` 和一个检查点。用 `--resume` 重跑。

## 推荐命令

```bash
bbook_maker \
  --book_name my_doc.md \
  --language zh-hans \
  --prompt prompt_md.json \
  --use_context session \
  --batch_size 20
```

仓库里的 `prompt_md.json` 是专为 Markdown 写的提示词：它要求模型保留标记。Markdown 没有计划模式，所以在会话模式下，每个请求都会重读历史。调大 `--batch_size`，就能用更少、更大的请求发送。

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

### Markdown 参数

| 参数 | 作用 |
|---|---|
| `--batch_size N` | 一个请求发送的正文块数（默认 10；一批在大约 2000 个字符处也会截止）。 |
| `--use_context [window\|session]` | 携带前面的段落：不带值或 `window` 会重发几对原文与译文；`session` 保持一份历史。 |
| `--context_paragraph_limit N` | 窗口模式：重发多少对原文与译文。 |
| `--context-compact-at N` | 会话模式：估算 token 数达到这个值时压缩（默认 8192，最小 1500）。 |
| `--no-context-compact` | 会话模式：滚动到下一个窗口时不写交接报告。 |
| `--glossary FILE` | `term -> translation` 形式钉住的译法（openai 形式的路线和 codex 路线）。`--terminology` 是同一个参数。 |
| `--glossary-auto on\|off` | 保留会话交接报告中确立的译名。除非明确要求，否则关闭。 |
| `--parallel-workers N` | 同时翻译多个批次或分段。与会话同用、以及在 codex 上都会被拒绝。 |

## 不适用于这种格式的参数

- `--accumulated_num`：Markdown 加载器不读它；运行时会给出警告，并提示改用 `--batch_size`。在计划模式之外，会话每个请求都会重读历史，所以更大的 `--batch_size` 能让请求数量少一些。
- `--max-batch-units`、`--plan-classify`、`--plan-dry-run`、`--plan-min-coverage`、`--poetry-group-size`：计划模式只用于 EPUB。
- `--classify-model`、`--classify-base-url`、`--classify-key`（以及旧名称 `--plan-classify-model`）：这种格式上暂时没有任何东西做分类。运行时会警告该参数被忽略。
- `--exclude-translate-tags`：接受且不给警告，但 Markdown 加载器不读它。
- `--translate-tags`、`--allow_navigable_strings`：EPUB 标记选择器；运行时会给出警告。
- `--only_filelist`、`--exclude_filelist`：EPUB 内部文件；忽略。
- `--block_size`、`--sentence_mode`：仅用于 EPUB；忽略。
- `--translation_style`、`--translation_color`：Markdown 没有样式；运行时会给出警告。
- `--no_disclosure`、`--translation-metadata`：输出不带署名行，也不带元数据文件；运行时会给出警告。
- `--retranslate`：仅用于 EPUB；会被拒绝。
- `--quiet`：仅用于 EPUB；运行时会给出警告。
- `--batch`、`--batch-use`：Markdown 加载器没有实现 Batch API。
- `--to-epub`、`--pdf-ocr`、`--ocr-replace-layer`、`--device`、`--ocr-lang`、`--pages`、`--no-formula-images`、`--pdf_layout`、`--img-model`、`--img-base-url`、`--img-key`：仅用于 PDF。在这种格式上，`--to-epub` 会让运行停止；其余的会给出警告或不起作用（三个图像参数：运行时会警告只有 PDF 路线有图像这一步）。
