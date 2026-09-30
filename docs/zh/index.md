# bilingual book maker

bilingual book maker 用语言模型翻译一本书，生成双语版：原文的每一段后面紧跟它的译文。它能读取 EPUB、TXT、Markdown、SRT 和 PDF 文件，可以对接 OpenAI、Anthropic、Gemini、Qwen、任何 OpenAI 兼容端点（网关、转售商，或跑在你自己机器上的模型）、Codex 订阅，以及几家翻译服务。

```bash
pip install -U bbook_maker
export OPENAI_API_KEY=sk-...
bbook_maker --book_name my_book.epub --use_context session
```

这会在 `my_book.epub` 旁边生成 `my_book_bilingual.epub`。默认模型是 `gpt-6-luna`，默认目标语言是简体中文（`--language zh-hans`）。

## 接下来看哪里

- **从这里开始：**[快速开始](quickstart.md)（一本 EPUB、一个 TXT、一个 PDF）和[安装](installation.md)（pip、仓库源码、[Docker](docker.md)）。
- **有编程智能体？**[用智能体翻译](agent-skill.md)：Codex 或 Claude Code 加载本仓库的技能后，会问清你手头有什么，替你选好参数并运行工具。
- **EPUB：**先看[大多数人需要的那条命令](formats/epub.md)，再看面向教材、小模型和本地服务器的[推荐设置](features/recommended-epub.md)；[计划模式](features/plan-mode.md)讲解其中发生了什么。
- **PDF：**[PDF 转双语 EPUB](features/pdf-to-epub.md)（先翻两页，再翻整个文件）、按文档类型和系统给出的[推荐设置](features/recommended-pdf.md)，以及[安装 PDF 扩展](installation-pdf.md)。
- **其他格式：**[TXT](formats/txt.md)、[SRT](formats/srt.md) 和 [Markdown](formats/md.md)，各只需一条命令；另见[各类文件对应的页面](book_source.md)。
- **端点与模型：**[用大模型翻译](llm-args.md)（模型、key、端点、重试、本地模型）、[会话模式](features/session-mode.md)（整本书一段对话，适用于每种会传上下文的格式）、[提供方文件](providers.md)、[翻译服务](machine-args.md)、[提示词文件](prompt.md)和[环境变量](env_settings.md)。
- **评测：**[参数背后的测量](evaluation/index.md)，每个参数一页。
- **参考：**[全部命令行参数](cmd.md)。

## 只用于你有权翻译的内容

请仅将本工具用于您有权翻译的内容——您持有必要权利的作品、许可或授权允许您翻译的作品、公有领域图书，或适用法律另行允许的使用方式。使用本工具之前，请阅读项目的 **[免责声明](disclaimer.md)**。
