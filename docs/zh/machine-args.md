# 机器翻译

这些路线调用的是翻译服务，而不是语言模型。它们不需要模型，常常也不需要 key。它们也无法回答问题，所以本工具里一切需要问模型的功能，在这些路线上都是关闭的。

## 路线

用 `--api_format` 选择一条。

| `--api_format` | 服务 | key | 目标语言 |
|---|---|---|---|
| `google` | 谷歌翻译的公开网页端点 | 无 | 大多数语言；源语言自动检测 |
| `deepl` | 通过 RapidAPI 上的 [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) 使用 DeepL | `--key`，或 `BBM_DEEPL_API_KEY` | DeepL 支持的语言；其他语言会以 `DeepL do not support …` 拒绝 |
| `deeplfree` | DeepL 的免费网页端点 | 无 | DeepL 支持的语言 |
| `caiyun` | [彩云小译](https://fanyi.caiyunapp.com) | `--key`，或 `BBM_CAIYUN_API_KEY` | 简体中文、英语、日语 |
| `tencent` | [腾讯交互翻译](https://transmart.qq.com) | 无 | 中文或英语 |
| `customapi` | 你自己的 HTTP 服务 | 无；地址写在 `--api_base` | 取决于你的服务 |

两条 DeepL 路线都把源语言当作英语发送。请用它们翻译英文书。

彩云提供了一个测试 token `3975l6lr5pcbvidl6jl2`，用来试用这条路线；申请自己的 token，请参考[这个教程](https://bobtranslate.com/service/translate/caiyun.html)。

## 命令

=== "谷歌"

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

=== "DeepL 免费版"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format deeplfree \
      --language de
    ```

=== "彩云小译"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format caiyun \
      --key ${caiyun_key} \
      --language zh-hans
    ```

=== "腾讯交互翻译"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format tencent \
      --language zh-hans
    ```

=== "自定义 API"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format customapi \
      --api_base https://your.host/translate \
      --language ja
    ```

## 自定义 API 的约定

工具对每个段落向 `--api_base` 发送一个 POST 请求，采用表单编码，带三个字段：`text`、`source_lang` 和 `target_lang`。它从 JSON 回复的 `data` 字段读取译文。每个请求 10 秒超时，请求之间等待 5 秒。`--source_lang` 就是在这里传给你的服务。

## 这些路线做不到的事

- **没有提示词。** `--prompt` 无处可去；文本是单独发送的。
- **没有会话。** `--use_context session` 会被拒绝：没有可以保持的对话。
- **引擎本身不做计划分类。** 在 EPUB 上，运行会退回到 `--translate-tags` 的选择（默认 `p`），所以 `<p>` 之外的诗歌或表格不会被翻译。指定一个大模型分类器，引擎翻译的同时这本书也能得到完整的计划：`--classify-model gpt-5.6-luna`（它读取 `OPENAI_API_KEY`），或提供方条目的 `classify_model`。这在谷歌翻译上端到端跑通过。没有分类器时，`--plan-classify model` 会被拒绝，而 `--plan-classify all` 仍会翻译计划找到的每一个块，不问任何人。见[计划模式](features/plan-mode.md#选择分类器)。
- **没有术语表。** 只有 OpenAI 形式和 Codex 形式的路线会读 `--glossary`；在这里它会被忽略并给出警告。
- 在 `google`、`deepl`、`deeplfree`、`caiyun` 和 `tencent` 上**不能指定源语言**。`--source_lang` 会被忽略并给出警告；只有 `customapi`（以及 `qwen` 大模型路线）会发送它。

其他都能用：`--test`、`--resume`、`--single_translate`，以及[格式](formats/epub.md)各页上的各格式参数。

## 限流

彩云被限流时会打印 `will sleep 60s for the time limit` 并等待。DeepL 免费版和腾讯会自行在请求之间暂停。[耐心重试](llm-args.md#重试耐心是什么意思)策略适用于每一条路线。
