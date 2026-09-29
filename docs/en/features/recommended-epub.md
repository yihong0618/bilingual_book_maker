# Recommended settings for EPUB

For most books this is the whole command:

```bash
bbook_maker \
  --book_name my_book.epub \
  --language zh-hans \
  --use_context session \
  --quiet
```

The book is translated through a [plan](plan-mode.md) (every block of text is found, and the model decides which kinds are worth translating), and a [session](session-mode.md) keeps names and terms consistent. The rest of this page is what to change when your book, your model or your machine is not the common case.

## If you need this, pass that

| if you need… | pass | read more |
|---|---|---|
| a first look for almost nothing | `--test --test_num 8` | [Quick start](../quickstart.md) |
| to see what will be translated and skipped before paying (no key needed) | `--plan-dry-run` | [Plan mode](plan-mode.md) |
| every block translated, no classification | `--plan-classify all` | [Plan mode](plan-mode.md) |
| to decide the plan yourself, or with a coding agent | `--plan-classify agent`, then rerun the same command | [Plan mode](plan-mode.md) |
| names and terms that must hold across the book | `--glossary names.txt` | [Session mode](session-mode.md) |
| your own voice or register | `--prompt my_prompt.json` | [Prompt files](../prompt.md) |
| only some chapters | `--only_filelist ch03.xhtml,ch04.xhtml` (names inside the EPUB) | [EPUB](../formats/epub.md) |
| a long book done faster, consistency second | `--parallel-workers 4` with bare `--use_context` (a session cannot be shared between workers) | [EPUB](../formats/epub.md) |
| the translation visually set apart | `--translation_style "color:#808080;font-style:italic"` | [Command line options](../cmd.md) |
| the translation only, without the original | `--single_translate` | [EPUB](../formats/epub.md) |
| no AI-translation credit line | `--no_disclosure` (a reader can then no longer tell the translation from a human one) | [EPUB](../formats/epub.md) |
| to continue after Ctrl+C or a crash | the same command plus `--resume` | [Quick start](../quickstart.md#if-a-run-stops) |

## By kind of book

=== "Novel"

    The command at the top of this page. If a recurring name drifts after a window seam, pin it with a glossary file:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --language zh-hans \
      --use_context session \
      --glossary names.txt \
      --quiet
    ```

=== "Textbook with tables and formulas"

    Preview the plan first. A textbook puts text in table cells, captions and sidebars, and you want to see what will be skipped before you pay.

    ```bash
    bbook_maker \
      --book_name textbook.epub \
      --plan-dry-run
    ```

    If the table shows kinds of text you want that were not selected, translate every unit instead of classifying, and pin the terms that must hold across hundreds of pages:

    ```bash
    bbook_maker \
      --book_name textbook.epub \
      --language zh-hans \
      --plan-classify all \
      --use_context session \
      --glossary terms.txt \
      --quiet
    ```

    Formulas in an EPUB are MathML or images. MathML (`<math>`) and SVG are never sent to the model, so equations stay as they are.

=== "Paper"

    A paper as an EPUB takes the same command as a novel. A paper as a PDF: see [Recommended settings for PDF](recommended-pdf.md).

=== "Dictionary or critical edition"

    A book that is mostly apparatus (headwords, sigla, line numbers) legitimately translates less than half its text, and the run stops at the coverage check. Preview with `--plan-dry-run`, then lower the check on purpose:

    ```bash
    bbook_maker \
      --book_name edition.epub \
      --language zh-hans \
      --plan-min-coverage 0.2 \
      --use_context session
    ```

=== "A scan, or a Chinese scan"

    A scan is a PDF, and plan mode is EPUB only. See [Recommended settings for PDF](recommended-pdf.md). A Chinese EPUB translated to English works as a novel:

    ```bash
    bbook_maker \
      --book_name chinese_novel.epub \
      --language en \
      --use_context session
    ```

## By model and endpoint

| if your endpoint is… | pass | why |
|---|---|---|
| OpenAI, Anthropic, or a gateway with prompt caching | `--use_context session` | the history is re-read at the cache rate |
| an endpoint without prompt caching (`cached=` on the progress bar stays 0) | bare `--use_context` | a session would pay for the whole history on every request |
| Gemini or Qwen | `--api_format gemini --use_context` (or `qwen`) | they keep their own history and refuse a session |
| your ChatGPT plan | `--api_format codex`, no context flag | the thread is the context; see [Translating with an LLM](../llm-args.md) |
| a machine-translation engine (Google, DeepL, …) | `--classify-model gpt-5.6-luna` | the engine cannot classify, so without an LLM classifier only `<p>` is translated; see [Machine translation](../machine-args.md) |
| a small on-device model (8B to 16B) | the defaults; add `--no-thinking` for a reasoning model, and `--context-compact-at` at the model's input limit | the run halves its request sizes by itself on an endpoint without a strict schema; see [On-device models](../llm-args.md#on-device-models-ollama-llamacpp-lm-studio) |
| a model that keeps misaligning (`N misaligned batches this run`) | `--max-batch-units 8`, then `4` | smaller requests; see [Why 16 units per request](../evaluation/grouping-batch-size.md) |
| a small model whose plan classification keeps failing | `--plan-classify all` | skips classification and translates every block |

## By system

The work happens on the endpoint, so a hosted endpoint needs the same command on every system. The system matters when the model runs locally.

=== "macOS (Apple silicon)"

    A local model through Ollama, which uses Metal. Bound the session window to a small model's context:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_base http://localhost:11434/v1 \
      --model qwen3:8b \
      --use_context session \
      --context-compact-at 4000
    ```

    A small local model has no strict JSON schema, so the run halves the unit cap to 8 by itself.

=== "Linux with NVIDIA"

    A local server with an OpenAI-compatible API (vLLM, llama.cpp, Ollama) on the GPU:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_base http://localhost:8000/v1 \
      --model your-model-id \
      --use_context session \
      --context-compact-at 4000
    ```

=== "CPU only"

    A local model on the CPU is slow for a whole book, and re-reading a history on every request is slower still. Use a hosted endpoint with prompt caching:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
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

    See [Docker](../docker.md).
