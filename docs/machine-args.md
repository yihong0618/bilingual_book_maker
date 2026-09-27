# Machine translation

These routes call a translation service instead of a language model. They need no model and often no key. They also cannot answer a question, so everything in this tool that asks the model something is off on them.

## The routes

Pick one with `--api_format`.

| `--api_format` | service | key | target languages |
|---|---|---|---|
| `google` | Google Translate's public web endpoint | none | most; the source is detected |
| `deepl` | DeepL through the RapidAPI [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) | `--key`, or `BBM_DEEPL_API_KEY` | DeepL's list; others are refused with `DeepL do not support …` |
| `deeplfree` | DeepL's free web endpoint | none | DeepL's list |
| `caiyun` | [Caiyun](https://fanyi.caiyunapp.com) | `--key`, or `BBM_CAIYUN_API_KEY` | Simplified Chinese, English, Japanese |
| `tencent` | [Tencent TranSmart](https://transmart.qq.com) | none | Chinese or English |
| `customapi` | your own HTTP service | none; the address goes in `--api_base` | whatever your service does |

Both DeepL routes send the source as English. Use them for English books.

Caiyun publishes a test token, `3975l6lr5pcbvidl6jl2`, for trying the route; for your own, follow [this tutorial](https://bobtranslate.com/service/translate/caiyun.html).

## Commands

=== "Google"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format google \
      --language zh-hant
    ```

=== "DeepL"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format deepl \
      --key ${deepl_key} \
      --language ja
    ```

=== "DeepL free"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format deeplfree \
      --language de
    ```

=== "Caiyun"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format caiyun \
      --key ${caiyun_key} \
      --language zh-hans
    ```

=== "Tencent"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format tencent \
      --language zh-hans
    ```

=== "Custom API"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format customapi \
      --api_base https://your.host/translate \
      --language ja
    ```

## The custom API contract

The tool sends one POST per paragraph to `--api_base`, form-encoded, with three fields: `text`, `source_lang` and `target_lang`. It reads the translation from the `data` field of the JSON answer. Each request times out after 10 seconds, and the tool waits 5 seconds between requests. `--source_lang` reaches your service here.

## What these routes cannot do

- **No prompt.** `--prompt` has no place to go; the text is sent alone.
- **No session.** `--use_context session` is refused: there is no conversation to keep.
- **No plan classification by the engine itself.** On an EPUB the run falls back to the `--translate-tags` selection (`p` by default), so verse or tables outside `<p>` stay untranslated. Name an LLM classifier and the book gets a full plan while the engine translates: `--classify-model gpt-5.6-luna` (it reads `OPENAI_API_KEY`), or a provider entry's `classify_model`. This was run end to end on Google Translate. Without one, `--plan-classify model` is refused, and `--plan-classify all` still translates every block the plan finds without asking anyone. See [Plan mode](features/plan-mode.md#choosing-the-classifier).
- **No glossary.** `--glossary` is read only by the OpenAI- and Codex-shaped routes; here it is ignored with a warning.
- **No stated source language** on `google`, `deepl`, `deeplfree`, `caiyun` and `tencent`. `--source_lang` is ignored with a warning; only `customapi` (and the `qwen` LLM route) send it.

Everything else works: `--test`, `--resume`, `--single_translate`, the per-format flags on the [Formats](formats/epub.md) pages.

## Rate limits

Caiyun prints `will sleep 60s for the time limit` when it is rate-limited, and waits. DeepL free and Tencent pause between requests on their own. The [patient retry](llm-args.md#retries-what-patient-means) policy applies to every route.
