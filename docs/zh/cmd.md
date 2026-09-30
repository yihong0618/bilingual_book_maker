# 命令行参数

运行时以 `book_maker/cli.py` 和 `python3 make_book.py --help` 为准。
下面的清单已与 `argparse` 注册的每个长参数逐一核对过；清单之后的各节为部分工作流提供补充说明。

## 完整参数清单

### 输入、范围与输出

| 参数 | 用途 |
|---|---|
| `--book_name PATH` | 输入的 EPUB、TXT、Markdown、SRT 或 PDF 路径（必填）。 |
| `--language LANGUAGE` | 目标语言：标签（`zh-hant`）、语言名（`"Traditional Chinese"`），或在内置表里查不到该语言时用 `TAG:NAME` 同时指定两者——标签用于输出标记和字段名，语言名用于提示词。默认 `zh-hans`；列表见 `docs/en/languages.md`。 |
| `--source_lang LANGUAGE` | 源语言。明确指定时，它会写进每条大模型路线的提示词，在 `qwen`/`customapi` 上还会写进请求体；默认 `auto`（什么都不声明）。 |
| `--single_translate` | 只输出译文，而不是双语文本。 |
| `--no_disclosure` | 不把 epub 标记为 AI 翻译：去掉书籍简介下方那一行署名（“Translated by \<model\>, \<year\>.”）。同时关闭 `--translation-metadata`。 |
| `--translation-metadata` | 在书里写入一个小小的 `bbm_translation_metadata.json`：模型、日期，以及 `--glossary` 文件的 sha256（文件文本一并嵌入；运行自己学到的术语不会写入）。别的都没有——没有命令行、端点、语言或路径，也没有包的元数据：记录的是可能影响译文的东西，而不是谁运行了它。格式转换会保留清单中的文件，所以这份记录能经受住转换。计划模式和会话模式的运行会自行写入它；这个参数让普通的标签模式运行也写入。`--no_disclosure` 同样会关闭它。 |
| `--translate-tags TAGS` | 逗号分隔的 EPUB 标签；默认 `p`，计划模式下忽略。 |
| `--exclude-translate-tags TAGS` | 要排除的 EPUB 祖先标签；默认 `sup,code`；传 `""` 清空。 |
| `--allow_navigable_strings` | 把原本不在标签里的 EPUB 字符串也包括进来；计划模式下多余。 |
| `--only_filelist FILES` | 只包括这些逗号分隔的 EPUB 内部文件。 |
| `--exclude_filelist FILES` | 排除这些逗号分隔的 EPUB 内部文件。 |
| `--translation_style CSS` | 应用于 EPUB 译文条目的 CSS。 |
| `--translation_color COLOR` | 只设颜色的简写；`--translation_style` 优先。 |
| `--pdf_layout MODE` | 额外生成的 PDF 输出：`none`、`top-bottom`、`side-by-side` 或 `all`。 |
| `--to-epub` | 仅限 PDF：用 docling 读取 PDF，翻译得到的 Markdown，在其旁边写出 `<name>_bilingual.epub`；插图保留为图片；包留在 `<name>_book/` 里，便于编辑和续跑。需要 `pdf` 扩展和 PATH 上的 Pandoc 3.1.12+；不需要 Java。见 [installation-pdf.md](installation-pdf.md)。 |
| `--pdf-ocr` | 仅限 PDF，配合 `--to-epub`：读取没有文字层的页面，不加这个参数时这些页面会被拒绝。默认关闭；OCR 要多花好几倍的时间，而且对原生数字 PDF 没有任何改变。无论开不开，版面、标题和表格都会被检测。 |
| `--ocr-replace-layer` | 仅限 PDF，配合 `--to-epub --pdf-ocr`：对每一页做 OCR，并替换 PDF 自带的文字层，而不是保留它。默认关闭；测得它比一个完好的文字层差，是给有错的文字层用的。读出为空的页面保持为空，并会被点名。 |
| `--no-formula-images` | 仅限 PDF，配合 `--to-epub`：让行间公式保留为 `<!-- formula-not-decoded -->` 占位符，而不是把每个公式从页面上裁剪成图片。解析器能找到公式，但从不读取它们，所以这些图片是数学内容能进入书中的唯一途径；它们不花模型费用，也不花可测量的时间。段落里的行内数学不算公式区域，无论如何都不在覆盖范围内。 |
| `--pdf-image-dpi N` | PDF，`--to-epub`：插图清晰度，以 PDF 自身页面尺寸下的 DPI 计；默认 200，标注很小时用 300；换一个值重新运行只会重绘插图 |
| `--img-model MODEL` | 仅限 PDF，配合 `--to-epub`：一个视觉模型，在导出前根据页面图像纠正版面检测器给出的区域角色（正文、标题、书名、图题、脚注、代码）。除非在这里或在提供方条目的 `img_model` 中指定，否则关闭；`none` 会关掉它。绝不会回退为本次运行自己的模型。每页约 3k 提示词 token。 |
| `--img-base-url URL` | `--img-model` 的端点，当它不是本次运行的端点时使用（仅限 OpenAI 兼容）。 |
| `--img-key KEY` | `--img-base-url` 使用的 key。默认：端点是本次运行自己的端点时，用本次运行的 key；否则，端点是提供方条目自己的端点时，用该条目的 `img_env_key`；否则，用该端点格式从环境中读取的 key。key 绝不会发往不是为它指定的地址。 |
| `--device DEVICE` | 仅限 PDF，配合 `--to-epub`：提取模型在哪里运行——`auto`（默认；检测 CUDA 或 MPS，没有则回退到 CPU）、`cpu`、`cuda`、`mps`、`xpu`。CPU 完全受支持，输出相同，只是更慢。 |
| `--ocr-lang LANGS` | 仅限 PDF，配合 `--to-epub --pdf-ocr`：OCR 引擎在没有文字层的页面上（加 `--ocr-replace-layer` 时是每一页）识别的语言，逗号分隔，使用引擎自己的代码（rapidocr：`ch`、`en`、`latin`；easyocr：`ch_sim`、`ja`、`ko`；ocrmac：`zh-Hans`、`ja-JP`），或加 `iso:` 前缀的通用 BCP-47 标签（`iso:zh-Hans`、`iso:zh-Hant`、`iso:ja`、`iso:ko`），docling 2.129 会把它映射到实际运行的引擎上。rapidocr 每次运行只识别一种语言，取第一个。引擎是 docling 在当前安装中选中的那个（装了 PDF 扩展时，Mac 上是 ocrmac，默认识别英语、西班牙语、法语和德语；其他系统上是 rapidocr，默认识别中文和英文），运行会打印它用的引擎和语言。其他文字的扫描件需要这个参数；运行遇到没加它的扫描页时会提示。未知的代码在读取任何页面之前就会被拒绝。每个引擎的代码，以及该选哪个引擎：[选择 OCR 引擎](features/pdf-ocr-engines.md)。 |
| `--ocr-engine ENGINE` | 仅限 PDF，配合 `--to-epub --pdf-ocr`：用于没有文字层的页面（加 `--ocr-replace-layer` 时是每一页）的 OCR 引擎。`auto`（默认）按 ocrmac、rapidocr、easyocr 的顺序取第一个已安装的；`pdf` 扩展会安装带 onnxruntime 的 `rapidocr`（模型已包含），在 macOS 上还会安装 `ocrmac`（Apple 的 Vision 框架）；两者都不下载任何东西，所以 `auto` 在 Mac 上用 ocrmac 识别，在其他系统上用 rapidocr；`easyocr` 首次使用时下载模型（`pip install easyocr`）；`tesseract` 使用 PATH 上的 tesseract 程序及其语言数据。未安装的引擎，或在 macOS 之外指定 `ocrmac`，都会在读取任何页面之前被拒绝。语言代码因引擎而异（`--ocr-lang`）；运行会说明它用的引擎。该选哪个，有测量数据：[选择 OCR 引擎](features/pdf-ocr-engines.md)。 |
| `--pages PAGES` | 仅限 PDF，配合 `--to-epub`：要读取的页，从 1 开始编号（`12-30`、`1,3,5-7`）；其余页不处理。所选页码会写进包名和书名（`<name>_pages-12-30_…`），所以翻译一章永远不会覆盖整本书。如果所选范围从某节中间开始，`source.md` 里会在它的第一段正文上方加一个 `Page N` 标题。 |
| `--retranslate OUT FILE START END` | 在已有的输出中重新翻译一段 EPUB 范围。仅限 EPUB——其他格式会拒绝。 |

### EPUB 计划模式

| 参数 | 用途 |
|---|---|
| `--plan-dry-run` | 构建并打印 EPUB 计划，写出所有 `action` 仍为 `null` 的 `<book>_plan.json`，然后退出。不需要凭据。 |
| `--plan-classify {auto,none,all,model,agent}` | 不用计划、翻译整个分区、由模型筛选，或由编程代理筛选。默认 `auto`：在任何能作答的 epub 端点上由模型筛选——经验证支持严格 JSON schema 时通过结构化输出，其他情况（包括 codex）通过普通对话（精确回答 `skip`/`translate`，其他回答一律翻译）；只有在根本无法对话的地方才用标签模式。 |
| `--classify-model MODEL` | 每个分类步骤使用的模型（旧名 `--plan-classify-model`），或一个 Jev 兼容的分类器（默认是 TypeSafe 的 Jev；网关需给出其 URL）；在 epub 上意味着 model 模式。其端点经验证支持 JSON schema 时通过 schema 提问，否则通过普通对话。默认：提供方条目的 `classify_model`，其次是本次运行的模型。 |
| `--classify-base-url URL` | `--classify-model` 的端点，当它不是本次运行的端点时使用：一个 OpenAI 兼容端点，或一个 Jev 兼容的分类器的 URL（以 `/systemone` 结尾的路径原样使用）。 |
| `--classify-key KEY` | `--classify-base-url` 使用的 key；默认规则与 `--img-key` 相同。`JEV_API_KEY`/`TYPESAFE_API_KEY` 只在 typesafe.ai 地址上被自动读取；提供方条目仍然可以把某个变量绑定到它自己的地址。 |
| `--classify-min-confidence P` | Jev 兼容的分类器的置信度闸门，0 到 1：概率低于它的 `skip` 改为 `translate`；`translate` 从不受闸门限制。默认 `0.95`，经过测量；低于 `0.5` 时闸门不起作用。`BBM_JEV_MIN_CONFIDENCE` 不用参数也能设置它。 |
| `--plan-min-coverage FRACTION` | 计划选中的文字比例低于此值时失败；默认 `0.5`，必须在 0 到 1 之间（`0` 关闭这道防线，高于 `0.9` 的值多半会中止——两种情况都会警告）。 |
| `--poetry-group-size N` | 已弃用——现在通用分组和会话交接会让短行和相邻内容放在一起，单元数上限是 `--max-batch-units`。仍然可用（默认 `8`，最小 `1`），但会警告。 |

### 翻译与执行

| 参数 | 用途 |
|---|---|
| `--test` | 只翻译一小段预览样本。 |
| `--test_num N` | 测试的单元数；默认 `10`。 |
| `--resume` | 从加载器保存的检查点继续。EPUB 检查点会记录本次运行的语言、提示词和模型；不一致时拒绝续跑（旧检查点只警告一次，然后继续）。与 `--parallel-workers` 以及大于 1 的 `--accumulated_num` 同用会被拒绝，因为那两种情况从不写检查点。 |
| `--prompt VALUE_OR_FILE` | 提示词配置：`user`（必须包含 `{text}`）、`system` 和 `style`。`style` 是常驻指令，只在窗口开始时说一次（API 路线上放在 system 消息里，codex 上放在线程指令里），从不在每个请求里重复；用户写的 style 会替换交接报告里模型自己的文风说明。某个部分在某条路线上没有原生位置时（`codex` 格式上的 `system`），会附加到 user 消息里，而不是被丢弃；带 `--prompt` 的运行会打印它采用了哪些部分、放在哪里。示例：`prompt_template.json`（随附的 `style` 为空）。 |
| `--temperature FLOAT` | 采样温度，仅用于接受该参数的格式；默认 `1.0`。anthropic 格式总会发送它。openai 格式在它等于 API 默认值时，以及模型拒绝显式设置时（gpt-5.x、o 系列），不发送它，由 API 默认值生效。codex 格式没有这项设置，会忽略它。 |
| `--use_context [window\|session]` | 把前面的段落作为上下文发送。不带值或 `window`：重发最近几对原文与译文（一贯的行为）。`session`：一份只追加的历史，按端点的提示缓存价重读。 |
| `--context_paragraph_limit N` | 仅窗口模式：上下文历史的上限。解析器默认值 `0` 表示采用翻译器的默认值（ChatGPT 为 3 段），而不是没有历史。 |
| `--context-compact-at N` | 滚动历史的估算 token 预算。会话模式下，历史达到这个大小时会被压缩成交接报告；最小 `1500`。不设时，每个会话运行——无论是否分组，包括 `codex` 格式——都在 `8192` 处压缩，并在开始时打印出来。这个默认值是为连贯性（窗口接缝最少）而选的，不是为价格：会话模式下更低的值更便宜，因为每个请求都会重读携带的历史，所以如果你更在意会话成本而不是接缝，就调低它；如果模型的输入上限更小，就设成那个上限。超过约 `16000` 后成本会急剧上升。测量数据见[会话压缩预算](evaluation/session-compact-budget.md)。它也限定计划分类器在那些通过普通对话分类的端点上的对话长度（在那里重新开始，不写交接报告），无论有没有 `--use_context`。显式给出的值总是优先。 |
| `--no-context-compact` | 仅会话模式：跳过交接报告。窗口仍在达到预算时滚动，但下一个窗口从空白开始。 |
| `--glossary FILE` / `--terminology FILE` | 一个由 `term → translation` 行组成的文件（每行一条；`#` 之后是备注或注释），本次运行必须照此翻译。同一个参数的两个名字。只有出现在某个请求里的术语才会随它发送。文件不存在时，运行在解析参数阶段就会中止。由 openai 系和 codex 系路线在 EPUB、Markdown 和 PDF 书上读取；其他路线会警告并忽略它。 |
| `--glossary-auto on\|off` | 会话运行是否同时保留它自己的交接报告里确立的译名。除非你要求，否则关闭。它需要一个可供学习的会话（`--use_context session`，或 `codex` 格式的那一个线程），以及一个会用名字而不是大段文字作答的模型；`off` 时压缩那一轮只要求写摘要。学到的术语只留在本次运行和 `<book>_handoff.md` 里，不会出现在别处。 |
| `--accumulated_num N` | EPUB 的 token/字符累积量，以及 SRT 字幕块按字符合并的批量（SRT 上限为 512）。在 EPUB 计划模式下，它是每个请求的 token 预算：任意长度的相邻单元在 `N` 个 token 之内合成一次请求（每个请求最多 `--max-batch-units` 个单元；端点经验证支持 JSON 模式但不支持严格 schema 时为其一半）。不设时，每次计划运行都会根据本次运行自己的提示词开销推算默认值——用自带提示词时为 `1200`，自定义 `--prompt` 较长时最多 `1600`——在没有严格 schema 结论的端点上，每个请求的预算减半，但绝不低于 `800` 的下限（所以在那里，无论提示词开销多大，每个请求的预算都是 `800`）；会话运行（包括 `codex`）保持未减半的值。这些是选定的安全余量，低于所有测量中未出现错误的范围；见[每次请求的单元数与 token 数](evaluation/grouping-batch-size.md)。运行会说明这个数字以及路线类别。传 `1` 关闭合并。最小 `1`。 |
| `--max-batch-units N` | 仅 EPUB 计划模式：`--accumulated_num` 的 token 预算最多能放进一个请求的单元数。默认 `16`，即首次出现内容错误的水平（每个请求 64 个单元）的四分之一，作为安全余量。端点经验证支持 JSON 模式但不支持严格 schema 时，携带其一半（`8`），回复数错的情况实际就出在这类端点上。如果你信任自己的模型、想减少请求，就调高它；如果运行一直打印错位恢复的提示，就调低它。 |
| `--batch_size N` | TXT、Markdown 和 PDF（纯文本路线）加载器每个请求发送的行数或段落数。默认 `10`。 |
| `--block_size N` | 把段落合并成用分隔符方式翻译的块。 |
| `--sentence_mode` | 把 EPUB 段落逐句翻译；与计划模式不兼容。 |
| `--parallel-workers N` | 并行处理 EPUB 章节或 Markdown 批次/分节；默认 `1`。与 `--use_context session` 同用（只有一份历史）以及在 `codex` 格式上（只有一个线程）会被拒绝。 |
| `--batch` | 提交一个 ChatGPT Batch API 任务。在 EPUB 上会被拒绝（那里走不到队列路径：运行会按全价实时翻译，并提交一个空任务，而不是写出书），在不支持 Batch API 的路线上也会被拒绝。TXT、SRT 和 Markdown 加载器也没有实现它（运行会实时翻译），所以目前没有任何格式用到它。 |
| `--batch-use` | 使用之前提交的批量任务。在 EPUB 上会被拒绝，与 `--batch` 相同。 |
| `--no-thinking` | 让模型回答前不要先推理。在 OpenAI 形态的路线上，请求字段根据端点自己的拒绝来协商，并按端点和模型缓存；如果每种写法都被拒绝，运行会警告一次，然后不带该字段继续。在 `anthropic` 上是 `thinking: {"type": "disabled"}`。在 `codex` 上会被拒绝（子进程没有请求体）；在自行构建请求的路线上会被警告为不起作用。`--extra_body` 中设置的字段优先。 |
| `--extra_body JSON` | 附加到每个请求体上的字段，适用于构建请求体的路线（`openai`、`groq`、`xai`、`litellm`、`--model orcarouter`、`anthropic`）；其他路线会忽略它并说明。它也会进入能力探测和 JSON 各级请求，所以端点是按本次运行实际发出的请求来评定的。它合并在具名参数之上，所以这里的字段优先于对应的参数。 |
| `--extra_headers JSON` | 附加到每个请求上的 HTTP 头，适用路线同上。它设在客户端上，所以能力探测、模型检查和模型列表都会带上它们。值必须是字符串。 |
| `--quiet` | 不显示 EPUB 进度条和段落回显，报告和错误照常显示。 |
| `--proxy URL` | 为本次运行设置 HTTP/HTTPS 代理环境变量。 |

### 端点与凭据

路线指的是端点，而不是模型名。

| 参数 | 用途 |
|---|---|
| `--model MODEL` | 模型 id，按端点自己的写法（`gpt-5-mini`、`claude-sonnet-4-6`、`openai/gpt-5-mini`）。`openai` 格式下默认 `gpt-5.6-luna`；`anthropic` 格式必须指定。旧的别名值会被改写，并附一条说明。 |
| `--api_base URL` | 端点地址。默认是该格式的官方地址。粘贴进来的 `…/v1/chat/completions` 或末尾的斜杠会被去掉。 |
| `--key KEY` | API key；用逗号分隔多个 key 可以轮换使用。更推荐用 `BBM_API_KEY` 或该格式自己的变量。 |
| `--api_format FORMAT` | 端点所用的 API：`openai`（默认）、`anthropic`、`gemini`、`qwen`、`groq`、`xai`、`litellm`、`codex`、`google`、`caiyun`、`deepl`、`deeplfree`、`tencent`、`customapi`。根据 `--api_base` 的主机推断，否则根据含 `claude`/`anthropic` 的模型 id 推断——各厂商格式从不推断，所以要写明。 |
| `--api_format gemini` \| `qwen` | Google 和阿里巴巴各自的协议，各有自己的翻译器：Gemini 原生的约束解码和对话历史，以及 Qwen-MT 的语言对请求。默认分别为 `gemini-flash-latest` 和 `qwen-mt-turbo`。 |
| `--api_format groq` \| `xai` \| `litellm` | Groq、xAI 和 LiteLLM 代理（`http://localhost:4000`）上的 OpenAI 形态。每种都自带地址，所以格式加一个 key 就是完整的路线。必须写 `--model`：这些模型目录更新很快，所以不预设任何模型。 |
| `--model codex` | 使用 ChatGPT 套餐的 Codex CLI 侧车，等同于 `--api_format codex`。它运行 `gpt-5.6-luna`；`--api_format codex --model <id>` 可以指定其他模型（侧车还提供 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.5`、`gpt-5.2`）。 |
| `--model orcarouter` | OrcaRouter 网关及其智能路由模型 `orcarouter/auto`。不需要 `--api_base`；如果你传了，以你的为准。key 来自 `BBM_ORCAROUTER_API_KEY`。它不是旧别名：不会改写任何东西。 |
| `--model_list IDS` | 轮换使用的多个模型 id，逗号分隔。单个模型应该写在 `--model` 里；在两个参数里都写模型会报错。与 `--use_context session` 同用会被拒绝：轮换会让每个请求都成为全价的缓存未命中，还会在一段对话里混用多个模型。 |
| `--source_lang LANG` | 源语言。明确指定时，它会作为依据写进每条大模型路线的提示词，在 `qwen`/`customapi` 上还会写进请求本身；默认 `auto`。 |
| `--interval SECONDS` | 请求之间的间隔，默认 `0.01`。只有 `gemini` 路线用它控制节奏。 |
| `--provider NAME` | `bbm_providers.json`（当前目录）或 `~/.bbm/providers.json` 中的一个具名端点；同名时项目文件优先，两处都没有的名字会回退到随附的 `bbm_providers.example.json`，并给出警告，说明它用的地址和 key 变量（不包括其中的 `FILL-ME` 模板）。条目中的 `base_url`、`api_style`（`openai`、`anthropic`、`gemini`、`qwen`、`groq`、`xai` 或 `litellm`）、`default_models` 和 `env_key` 分别代替 `--api_base`、`--api_format`、`--model`/`--model_list` 和 key；`img_model`/`img_base_url`/`img_env_key` 和 `classify_model`/`classify_base_url`/`classify_env_key` 分别代替 `--img-model`/`--img-base-url`/`--img-key` 和 `--classify-model`/`--classify-base-url`/`--classify-key` 参数（见[提供方文件与额外模型](providers.md)）。你自己传入的参数优先。 |

提供 Claude 模型的网关说的是 OpenAI 形态，给了网关的 `--api_base` 时也按这种形态处理。
只有在 Anthropic 自己的主机上，或者没有 `--api_base` 而模型 id 含 `claude` 时，才会推断为 anthropic 格式。
如果向一个不提供 anthropic 形态的网关请求这种形态，它会返回 404，运行随即中止，并指出修正方法是 `--api_format openai`。

key 的查找顺序：`--key`（`--api_key` 是同一个参数），然后是 `BBM_API_KEY`，
再然后是该格式自己的变量：`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、
`BBM_GOOGLE_GEMINI_KEY`、`BBM_QWEN_API_KEY`、`BBM_GROQ_API_KEY`、
`BBM_XAI_API_KEY`、`BBM_CAIYUN_API_KEY`、`BBM_DEEPL_API_KEY`（每个之后还有该厂商惯用的变量）。
localhost 上的端点不需要 key。

旧的 `--model` 预设名、各厂商的 `--*_key` 参数、`--ollama_model` 和 `--deployment_id` 已经不在解析器里了，
但旧命令行仍然能运行：`book_maker/legacy_cli.py` 会在运行开始前把它们改写成上面的参数，并打印每一处改写。
对照表见[从旧参数迁移](migration.md)。
旧的各厂商 key 变量（`BBM_GROQ_API_KEY`、`BBM_GOOGLE_GEMINI_KEY`……）在原来使用它们的路线上仍然会被读取。

不要把密钥直接写在共享的命令行上。对代理和 CI 来说，环境变量更安全。
CLI 自己**不会**加载 `.env` 文件：请先导出变量，或者在运行前 source 一个被 git 忽略的本地文件，例如
`set -a; source .env; set +a; bbook_maker ...`。

## 测试翻译
`--test` <br>

如果你还没为服务付费，或者只想试一试，可以用这个参数预览结果。注意会有限制，可能需要一些时间。

```sh
bbook_maker --book_name test_books/Lex_Fridman_episode_322.srt --key ${openai_key} --model gpt-5-mini  --test
```

```sh
bbook_maker --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini  --test --language zh-hans
```

`--test_num <TEST_NUM>`<br>

用这个参数设置测试时要翻译多少段。默认是 10。

## 续跑
`--resume` <br>

中断后，用这个参数手动继续处理。

## 重新翻译（仅 epub）
`--retranslate <translated_filepath> <file_name_in_epub> <start_str> <end_str>`<br>

如果 EPUB 中某个文件翻译得不好，可以用它单独重新翻译其中一部分。
argparse 要求四个值都给出。`end_str` 传空值时只重新翻译起始标签；
`file_name_in_epub` 传空值时会自动查找文件名。

- 从 start_str 重新翻译到 end_str 所在的标签：

        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'

- 只重新翻译 `start_str` 所在的标签（第四个值为空）：

        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''

- 重新翻译 `start_str` 所在的标签，并自动查找文件名（第二个和第四个值为空）：

        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' '' 'in spite of the present book shortage which' ''

**警告：**

**它会从成书中 start_str 所在的标签开始，删到 end_str 所在的下一个标签为止，然后重新翻译。**

**因此，请确保 `end_str` 之后的那个标签是译文内容。`end_str` 为空字符串时，使用 `start_str` 之后的那个标签。两个字符串之间可以有漏译，但结束边界若不是译文就会出问题。**

## 自定义输出样式（仅 epub）
`--translation_style <TRANSLATION_STYLE>`<br>

支持修改 epub 文件的输出样式。

    bbook_maker --book_name test_books/animal_farm.epub --translation_style "color: #4a4a4a; font-style: normal; background-color: #f7f7f7; padding: 5px; margin: 10px 0; border-radius: 5px;"

![两页 EPUB：左边使用上面的样式，每段译文放在原文后面的灰色框里；右边是默认样式](../img/output_style.jpg)

## 代理
`--proxy <PROXY>` <br>

用这个参数指定访问互联网的代理服务器。传入类似 `http://127.0.0.1:7890` 的字符串。

## API 地址
`--api_base <API_BASE_URL>`<br>

如果你想更换 api_base，比如使用 Cloudflare Workers，可以用这个参数。<br>

    bbook_maker --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'
**注意：api 地址应该是 '`https://xxxx/v1`' 这样的形式，必须加引号。**

## Microsoft Azure 端点

Azure 没有专门的参数。把 `--api_base` 指向部署的 OpenAI 兼容 URL，
并在 `--model` 里写上部署名：

    bbook_maker --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'

## 批量大小（TXT、Markdown、PDF 纯文本路线）
`--batch_size`<br>

用这个参数指定批量翻译的行数。默认是 10。由 TXT、Markdown 和 PDF（纯文本路线）加载器读取；EPUB 用的是 `--accumulated_num`。
```sh
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20
```

## 累积 token 数
`--accumulated_num <ACCUMULATED_NUM>`<br>

累积到多少个 token 之后才开始翻译。gpt3.5 把 total_token 限制在 4090。

在 EPUB 计划模式下你很少需要它：运行会自动推算每个请求的预算（用自带提示词时为 1200，在不支持严格 schema 的端点上为 800），并把它打印出来。见上面 `--accumulated_num` 那一行和[每次请求的单元数与 token 数](evaluation/grouping-batch-size.md)。

例如，如果你用 --accumulated_num 1600，openai 可能会输出 2200 个 token，
system 消息和 user 消息里的其他内容可能还要 200 个 token。1600+2200+200=4000，已经接近上限了。

你得自己选一个合适的值，在发送请求之前无法判断是否已经达到上限。
