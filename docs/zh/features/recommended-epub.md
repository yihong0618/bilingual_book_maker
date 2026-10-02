# EPUB 推荐设置

对大多数书来说，整条命令就是这样：

```bash
bbook_maker \
  --book_name my_book.epub \
  --language zh-hans \
  --use_context session \
  --quiet
```

书会按[计划](plan-mode.md)翻译（找出每一块文字，由模型决定哪几类值得翻译），[会话](session-mode.md)让人名和术语保持一致。本页其余部分讲的是：当你的书、你的模型或你的机器不属于常见情况时，该改什么。

## 需要什么，就传什么

| 如果你需要…… | 传入 | 延伸阅读 |
|---|---|---|
| 几乎不花钱先看一眼 | `--test --test_num 8` | [快速开始](../quickstart.md) |
| 付费之前先看哪些会翻译、哪些会跳过（不需要 key） | `--plan-dry-run` | [计划模式](plan-mode.md) |
| 每一块都翻译，不做分类 | `--plan-classify all` | [计划模式](plan-mode.md) |
| 自己或借助编程智能体决定计划 | `--plan-classify agent`，然后重新运行同一条命令 | [计划模式](plan-mode.md) |
| 全书必须统一的人名和术语 | `--glossary names.txt` | [会话模式](session-mode.md) |
| 你自己的文风或语域 | `--prompt my_prompt.json` | [提示词文件](../prompt.md) |
| 只翻译部分章节 | `--only_filelist ch03.xhtml,ch04.xhtml`（EPUB 内部的文件名） | [EPUB](../formats/epub.md) |
| 长书翻得更快，一致性其次 | `--parallel-workers 4` 配合不带值的 `--use_context`（会话无法在多个 worker 之间共享） | [EPUB](../formats/epub.md) |
| 在视觉上把译文区分出来 | `--translation_style "color:#808080;font-style:italic"` | [命令行参数](../cmd.md) |
| 只要译文，不要原文 | `--single_translate` | [EPUB](../formats/epub.md) |
| 不要 AI 翻译署名行 | `--no_disclosure`（这样读者就无法再分辨译文是否出自人手） | [EPUB](../formats/epub.md) |
| 按了 Ctrl+C 或崩溃后继续 | 同一条命令加上 `--resume` | [快速开始](../quickstart.md#运行中断时) |

## 按书的类型

=== "小说"

    就用本页开头的命令。如果某个反复出现的人名在窗口接缝之后译法漂移了，用术语表文件把它固定下来：

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --language zh-hans \
      --use_context session \
      --glossary names.txt \
      --quiet
    ```

=== "带表格和公式的教材"

    先预览计划。教材会把文字放在表格单元格、图题和侧栏里，你会希望在付费之前看到哪些会被跳过。

    ```bash
    bbook_maker \
      --book_name textbook.epub \
      --plan-dry-run
    ```

    如果表格里有你想要却没被选中的文字类型，就改为翻译所有单元而不做分类，并把需要在几百页里保持统一的术语固定下来：

    ```bash
    bbook_maker \
      --book_name textbook.epub \
      --language zh-hans \
      --plan-classify all \
      --use_context session \
      --glossary terms.txt \
      --quiet
    ```

    EPUB 里的公式是 MathML 或图片。MathML（`<math>`）和 SVG 从不发给模型，所以公式保持原样。

=== "论文"

    EPUB 格式的论文用与小说相同的命令。PDF 格式的论文见 [PDF 推荐设置](recommended-pdf.md)。

=== "词典或校勘本"

    一本以校勘材料为主的书（词头、版本符号、行号），需要翻译的文字本来就不到一半，运行会在覆盖率检查处中止。先用 `--plan-dry-run` 预览，再有意识地调低这项检查：

    ```bash
    bbook_maker \
      --book_name edition.epub \
      --language zh-hans \
      --plan-min-coverage 0.2 \
      --use_context session
    ```

=== "扫描件，或中文扫描件"

    扫描件是 PDF，而计划模式只用于 EPUB，见 [PDF 推荐设置](recommended-pdf.md)。把中文 EPUB 译成英文，按小说处理即可：

    ```bash
    bbook_maker \
      --book_name chinese_novel.epub \
      --language en \
      --use_context session
    ```

## 按模型和端点

| 如果你的端点是…… | 传入 | 原因 |
|---|---|---|
| OpenAI、Anthropic，或支持提示缓存的网关 | `--use_context session` | 历史按缓存价重读 |
| 不支持提示缓存的端点（进度条上的 `cached=` 一直是 0） | 不带值的 `--use_context` | 会话会在每个请求里为整段历史付全价 |
| Gemini 或 Qwen | `--api_format gemini --use_context`（或 `qwen`） | 它们自己维护历史，并拒绝会话模式 |
| 你的 ChatGPT 套餐 | `--api_format codex`，不加上下文参数 | 线程本身就是上下文；见[用大模型翻译](../llm-args.md) |
| 翻译服务（Google、DeepL……） | `--classify-model gpt-6-luna` | 引擎无法分类，没有大模型分类器时只翻译 `<p>`；见[翻译服务](../machine-args.md) |
| 小型本地模型（8B 到 16B） | 用默认值；推理模型加 `--no-thinking`，并把 `--context-compact-at` 设为模型的输入上限 | 在不支持严格 schema 的端点上，运行会自动把请求大小减半；见[本地模型](../llm-args.md#本地模型ollamallamacpplm-studio) |
| 总是错位的模型（`N misaligned batches this run`） | `--max-batch-units 8`，然后 `4` | 请求更小；见[每次请求的单元数与 token 数](../evaluation/grouping-batch-size.md) |
| 计划分类总是失败的小模型 | `--plan-classify all` | 跳过分类，翻译每一块 |

## 按系统

活是在端点上干的，所以用托管端点时，每个系统上的命令都一样。只有模型在本地运行时，系统才有影响。

=== "macOS（Apple 芯片）"

    通过 Ollama 运行本地模型，它使用 Metal。把会话窗口限制在小模型的上下文长度之内：

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_base http://localhost:11434/v1 \
      --model qwen3:8b \
      --use_context session \
      --context-compact-at 4000
    ```

    小型本地模型不支持严格的 JSON schema，所以运行会自动把单元数上限减半到 8。

=== "带 NVIDIA 显卡的 Linux"

    在 GPU 上运行一个提供 OpenAI 兼容 API 的本地服务器（vLLM、llama.cpp、Ollama）：

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_base http://localhost:8000/v1 \
      --model your-model-id \
      --use_context session \
      --context-compact-at 4000
    ```

=== "仅 CPU"

    在 CPU 上用本地模型翻完一整本书很慢，而每个请求都重读一遍历史就更慢了。请使用支持提示缓存的托管端点：

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-6-luna \
      --use_context session
    ```

=== "Docker"

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:latest \
      --book_name /book/novel.epub \
      --use_context session \
      --quiet
    ```

    见 [Docker](../docker.md)。
