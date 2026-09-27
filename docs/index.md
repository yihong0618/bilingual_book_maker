# bilingual book maker

bilingual book maker translates a book with a language model and writes a bilingual edition: every paragraph of the original, followed by its translation. It reads EPUB, TXT, Markdown, SRT and PDF files. It talks to OpenAI, Anthropic, Gemini, Qwen, any OpenAI-compatible endpoint (a gateway, a reseller, or a model on your own machine), a Codex subscription, and a few machine-translation services.

```bash
pip install -U bbook_maker
export OPENAI_API_KEY=sk-...
bbook_maker --book_name my_book.epub --use_context session
```

That writes `my_book_bilingual.epub` next to `my_book.epub`. The default model is `gpt-5.6-luna`; the default target language is Simplified Chinese (`--language zh-hans`).

## Where to go next

- **Start here:** [Quick start](quickstart.md) (one EPUB, one TXT, one PDF) and [Installation](installation.md) (pip, a checkout, [Docker](docker.md)).
- **EPUB:** [the command most people need](formats/epub.md), then [recommended settings](features/recommended-epub.md) for textbooks, small models and local servers; [plan mode](features/plan-mode.md) and [session mode](features/session-mode.md) explain what happens.
- **PDF:** [PDF to bilingual EPUB](features/pdf-to-epub.md) (two pages first, then the whole file), [recommended settings](features/recommended-pdf.md) per document type and system, and [installing the PDF extra](installation-pdf.md).
- **Other formats:** [TXT](formats/txt.md), [SRT](formats/srt.md) and [Markdown](formats/md.md), one command each; [which page for which file](book_source.md).
- **Endpoints and models:** [Translating with an LLM](llm-args.md) (model, key, endpoint, retries, on-device models), the [provider file](providers.md), [machine translation](machine-args.md), [prompt files](prompt.md) and [environment variables](env_settings.md).
- **Reference:** [every command line option](cmd.md), and [the measurements behind the defaults](evaluation/index.md).

## Use it on material you may translate

Use it only with material you have the right to translate — works for which you hold the necessary rights, suitably licensed or permitted works, public-domain books, or uses otherwise allowed by applicable law. Before using this tool, please review the project's **[disclaimer](disclaimer.md)**.
