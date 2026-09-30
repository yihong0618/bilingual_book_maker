# 用大模型翻译

有 OpenAI key 的话，默认路线什么都不用再配：

```bash
export OPENAI_API_KEY=sk-...
bbook_maker --book_name my_book.epub --use_context session
```

本页介绍决定请求发往*哪里*、请求*要什么*的参数。它们对每种输入格式都一样；各格式额外的参数见[格式](formats/epub.md)各页。

## 路线：模型、端点、格式

路线指的是端点，而不是模型名。三个参数确定一条路线。

| 参数 | 作用 |
|---|---|
| `--model`（`-m`） | 模型 ID，按端点自己的写法：`gpt-5-mini`、`claude-sonnet-4-6`、`openai/gpt-5-mini`。`openai` 格式下默认 `gpt-5.6-luna`。`anthropic` 格式必须写。 |
| `--api_base` | 端点 URL。默认是该格式的官方地址。OpenAI 形式的端点以 `/v1` 结尾。粘贴进来的 `…/v1/chat/completions` 或末尾的斜杠会被去掉。 |
| `--api_format` | 端点说的 API：`openai`、`anthropic`、`codex`、`gemini`、`qwen`、`groq`、`xai`、`litellm`，或某个[机器翻译](machine-args.md)引擎。不写时由 `--api_base` 推断：Anthropic 的地址是 `anthropic`，其余都是 `openai`。没写 `--api_base` 而模型 ID 是 `claude-*` 时，也会选 `anthropic`。 |

每种常见情况一个示例：

=== "OpenAI"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --model gpt-5.6-luna \
      --use_context session
    ```

=== "Anthropic"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --model claude-sonnet-4-6 \
      --use_context session
    ```

=== "任意 OpenAI 兼容端点"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_base https://gateway.example.com/v1 \
      --model provider/model-id \
      --use_context session
    ```

=== "Gemini"

    ```bash
    bbook_maker \
      --book_name my_book.epub \
      --api_format gemini \
      --model gemini-flash-latest \
      --use_context
    ```

    Gemini 自己维护对话历史，所以用窗口模式（`--use_context`），而不是会话模式。`--interval` 设置请求之间的间隔，免费额度就靠它避开限流。

=== "Codex（ChatGPT 套餐）"

    ```bash
    python make_book.py \
      --book_name my_book.epub \
      --api_format codex
    ```

    通过 [Codex CLI](https://developers.openai.com/codex/cli) 消耗你的 ChatGPT 套餐额度，需要先安装并登录（`codex login`）。默认运行 `gpt-5.6-luna`，换模型加 `--model <id>`。线程本身就是上下文，所以不需要 `--use_context`。它忽略 `--api_base` 和 `--key`。Codex 回答所选模型已满载时，运行会打印一行 `codex: … retrying in 60 s` 并再次请求；这种回答通常是 Codex 在对它不信任的网络限流，换个网络或账号会有帮助。

提供 Claude 模型的网关通常说的是 OpenAI 形式。如果网关对 anthropic 形式回 404，运行会停下，并指出改用 `--api_format openai` 即可。

`--api_format groq`、`xai` 和 `litellm` 分别是 Groq、xAI 和 LiteLLM 代理（`http://localhost:4000`）上的 OpenAI 形式。每个格式都知道自己的地址，所以格式、key 和 `--model` 就是完整的路线。这几个格式必须写 `--model`。

`--model orcarouter` 把运行交给 OrcaRouter 网关及其 `orcarouter/auto` 模型。key 来自 `--key` 或 `BBM_ORCAROUTER_API_KEY`。

## 每家厂商一条命令

你的厂商如果在下面列出，这就是完整的命令（key 也可以来自环境变量，见 [API key](#api-key)）：

```bash
# Claude: a claude-* model id selects the anthropic format on its own
bbook_maker --book_name my_book.epub --model claude-sonnet-4-6 --key ${claude_key} --use_context session
# Gemini, over the Gemini API; --interval paces the free tier
bbook_maker --book_name my_book.epub --api_format gemini --key ${gemini_key} --model gemini-flash-latest --use_context
# Qwen-MT on DashScope: a translation model with a source/target pair
bbook_maker --book_name my_book.epub --api_format qwen --key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
# xAI
bbook_maker --book_name my_book.epub --api_format xai --key ${xai_key} --model grok-4.3 --use_context session
# Groq: --model is required, pick a current id from console.groq.com/docs/models
bbook_maker --book_name my_book.epub --api_format groq --key ${groq_key} --model llama-3.3-70b-versatile --use_context session
# a LiteLLM proxy on this machine: --model is the name its config gives a backend
bbook_maker --book_name my_book.epub --api_format litellm --model ${name_in_your_litellm_config} --use_context session
# OrcaRouter's smart routing (orcarouter/auto); --provider orcarouter --model <id> pins one model
bbook_maker --book_name my_book.epub --model orcarouter --key ${orcarouter_key} --use_context session
# Ollama; point --api_base at another host if the server is not local
bbook_maker --book_name my_book.epub --api_base http://localhost:11434/v1 --model ${ollama_model_name} --use_context session
# any other OpenAI-compatible endpoint: base URL, key, and the model id it uses
bbook_maker --book_name my_book.epub --api_base "https://api.lingyiwanwu.com/v1" --key ${key} --model yi-34b-chat-0205 --use_context session
# Azure OpenAI: the deployment's OpenAI-compatible URL, the deployment name as the model
bbook_maker --book_name my_book.epub --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --key ${azure_key} --model 'deployment-name' --use_context session
# your ChatGPT plan through the Codex CLI (codex login first)
bbook_maker --book_name my_book.epub --api_format codex --language zh-hans
```

机器翻译服务（谷歌、DeepL、彩云、腾讯、自定义 API）见[机器翻译](machine-args.md)。常用的厂商最好写进[提供方文件](providers.md)。

## API key

key 按以下顺序查找：

1. `--key`（`--api_key` 是同一个参数）。用逗号分隔的多个 key 会轮流使用，以绕过单个 key 的限流。
2. `BBM_API_KEY`。
3. 该格式自己的变量：`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`BBM_GOOGLE_GEMINI_KEY`、`BBM_QWEN_API_KEY`、`BBM_GROQ_API_KEY`、`BBM_XAI_API_KEY`。

localhost 上的端点不需要 key。写在命令行里的 key 会出现在进程列表中，本机其他用户都能看到；尽量用环境变量。本工具自己不读 `.env` 文件，请先加载：

```bash
set -a; source .env; set +a
```

更多变量见[环境变量](env_settings.md)。

## 提供方文件与额外模型

`--provider NAME` 从 JSON 文件而不是参数中读取路线：运行目录下的 `bbm_providers.json`，或 `~/.bbm/providers.json`。两处都没有的名字会退回到随仓库提供的 `bbm_providers.example.json`，运行时会说明这一点。

```bash
bbook_maker \
  --book_name my_book.epub \
  --provider siliconflow \
  --use_context session
```

有两个步骤可以使用翻译模型以外的模型：

| 参数 | 作用 |
|---|---|
| `--classify-model MODEL` | 决定 EPUB 计划的模型，或一个 Jev 兼容的分类器（`jev` 即 TypeSafe 的分类器）。默认：条目的 `classify_model`，否则用翻译模型。`--plan-classify-model` 是它的旧名。 |
| `--classify-base-url URL`、`--classify-key KEY` | 该模型不在本次运行的端点上时，它的地址和 key：一个 OpenAI 兼容地址，或 Jev 兼容分类器的 URL。 |
| `--img-model MODEL` | 查看页面图像的步骤所用的视觉模型（目前是 [PDF 路线](features/pdf-to-epub.md)上纠正区域角色）。默认：条目的 `img_model`，否则关闭。绝不会退回使用翻译模型。`none` 关闭条目里的图像模型。 |
| `--img-base-url URL`、`--img-key KEY` | 该模型的地址和 key（仅限 OpenAI 兼容）。 |

字段、随附的条目、哪个 key 发往哪里，以及 Jev 和 Jev 兼容分类器，见[提供方文件与额外模型](providers.md)。注意随附的 `openai` 条目写了图像模型，所以 `--provider openai` 会开启 PDF 路线的图像步骤。

## 语言

| 参数 | 作用 |
|---|---|
| `--language` | 目标语言。可以是标签（`zh-hant`）、名称（`"Traditional Chinese"`），表里没有该语言时用 `TAG:NAME`（`--language "zh-hant:Traditional Chinese"`）。标签写进输出文件，名称是向模型提要求时的说法。默认 `zh-hans`。完整列表见[语言标签](languages.md)。 |
| `--source_lang` | 源语言，直接指定而不是自动检测。在每条大模型路线上都会写进提示词。默认：自动检测。 |

## 提示词

`--prompt` 接受模板字符串、JSON 字符串，或 `.json`、`.txt`、`.md` 文件。它有三个部分：

- `user`：模板。必填。必须包含 `{text}`；`{language}` 和 `{crlf}` 也会被填入。其他任何占位符都会在运行开始时被拒绝。
- `system`：系统消息。
- `style`：关于语域和语气的说明。它在每个上下文窗口开始处说一次，会话模式下会传给每一个窗口。

裸字符串或 `.txt` 文件就是 `user` 模板。运行时会打印用了哪些部分、各自来自哪里。格式见[提示词文件](prompt.md)。

```bash
bbook_maker \
  --book_name my_book.epub \
  --prompt prompt_template.json \
  --use_context session
```

## 试运行与续跑

| 参数 | 作用 |
|---|---|
| `--test` | 只翻译开头几段，用来检查配置。 |
| `--test_num N` | 翻译多少段（默认 10）。 |
| `--resume` | 从检查点继续一次中断的运行。EPUB 检查点记录了语言、提示词和模型；不一致时续跑会停下。 |

换了新端点，务必先跑一次 `--test`。它几乎不花钱，能让你看到输出格式和运行选中的路线。

## 重试：“耐心”是什么意思

很多人用本工具对接的服务商又慢又限流，首个 token 或一次 429 退避可能要等上几分钟。所以只要错误有可能靠等待消除，工具就绝不放弃。

- 超时、连接中断、429 或 5xx 会不限次数地重试。等待从 1 秒开始，逐次翻倍，涨到 5 分钟后不再增加。
- 每次重试打印一行，长时间等待也不会像是卡住了：
  `retrying after RateLimitError (…) — attempt 4, waiting 8s`
- 等待也不会消除的错误会立刻停止运行：key 被拒、权限错误、端点拒绝的请求、不存在的模型。
- Ctrl+C 总能停止运行，即使正在长时间等待。进度会保存，用 `--resume` 重新运行即可。

如果同一行重试信息持续很久，说明服务商宕机了或正在对你限流。它恢复后，运行会自己继续。

## 本地模型（Ollama、llama.cpp、LM Studio）

本地模型就是你自己机器上的一个 OpenAI 兼容端点。把 `--api_base` 指向它即可，不需要 key。

```bash
bbook_maker \
  --book_name my_book.epub \
  --api_base http://localhost:11434/v1 \
  --model qwen3:8b \
  --use_context session
```

8B 或 16B 模型的预期表现：

- **没有严格的 JSON schema。** 多数本地服务器不强制 schema。运行会用一次探测请求发现这一点并换成分隔符格式，打印类似 `doesn't apply JSON schema … using delimiter method` 的一行。这不是错误。
- **请求更小。** 在没有严格 schema 的端点上，每个请求最多携带 8 个单元，是默认 16 个的一半；不在会话模式下时，文本约 800 token。运行会打印它选定的数字。这些默认值就是按小模型能承受的量选的。
- **计划分类只答一个词。** 计划模式每轮就五种块问模型 `skip` 还是 `translate`。如果模型连续两次没按回答格式作答，运行改为每轮问三种，再改为一种。仍然不行就停止分类，其余部分全部翻译。任何读不懂的回答都按 `translate` 处理，所以弱模型宁可多翻，不会少翻。你也可以用 `--classify-model` 把分类交给托管模型，翻译仍在本地进行；见[提供方文件与额外模型](providers.md#示例)。
- **上下文短。** 把 `--context-compact-at` 设为模型的输入上限（最小 1500），会话窗口就不会溢出。
- **思考型模型。** 回答前先推理的模型可以用 `--no-thinking` 让它别想。运行会从服务器自己的拒绝回复中找出它接受的请求字段。如果你的服务器需要它自己的字段，`--extra_body` 优先，例如 `--extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'`。
- **没有图像步骤。** PDF 路线的图像步骤只在你用 `--img-model` 指定的模型上运行，所以本地模型永远不会被要求读页面。

小模型上应避免的参数：

- `--glossary-auto on`。它需要一个用名称而不是大段文字作答的模型。
- 调高 `--accumulated_num` 或 `--max-batch-units`。运行打印 `misaligned batches this run` 时，应当调低它们。
- `--model_list`。每个模型有自己的缓存，在一段对话里混用模型会损害一致性。

如果你的模型上计划分类总是失败，`--plan-classify all` 会跳过分类、翻译每一个块。见[计划模式](features/plan-mode.md)。

## 其他请求选项

| 参数 | 作用 |
|---|---|
| `--temperature` | 采样温度。anthropic 格式总会发送；openai 格式在它等于 API 默认值或模型不接受温度时不发送；codex 忽略它。 |
| `--no-thinking` | 让模型回答前不要推理；翻译一段文字，思考带不来任何好处，只花 token 和时间。在 OpenAI 形式的路线上，请求字段从端点自己的拒绝回复中找出并记住；如果端点拒绝所有写法，运行警告一次，然后不带它继续。在 anthropic 上是 `thinking: {"type": "disabled"}`。在 codex 上会被拒绝。图像请求也会带上它。 |
| `--extra_body JSON` | 附加到每个请求体的字段（openai 和 anthropic 路线）。这里的字段优先于对应的参数，`--no-thinking` 也不例外。 |
| `--extra_headers JSON` | 附加到每个请求的 HTTP 头（openai 和 anthropic 路线）。 |
| `--model_list IDS` | 轮流使用的多个模型，用来分摊限流。与 `--use_context session` 一起使用会被拒绝。 |
| `--interval SECONDS` | 请求之间的间隔。只有 gemini 格式使用。 |
| `-p`、`--proxy URL` | 本次运行的 HTTP 代理，例如 `http://127.0.0.1:7890`。 |
| `--batch`、`--batch-use` | OpenAI 的 Batch API：先提交任务，之后再用结果生成书。不适用于 EPUB。 |

常见的 `--extra_body` 和 `--extra_headers` 取值：

```bash
# openai route: turn off a local/vLLM chat template's thinking block
--extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
# openai route (chat completions): reasoning effort and a token ceiling, neither of which has a flag
--extra_body '{"reasoning_effort": "low", "max_completion_tokens": 2000}'
# anthropic route: keep extended thinking off
--extra_body '{"thinking": {"type": "disabled"}}'
# OpenRouter attribution, shown on its dashboard
--extra_headers '{"HTTP-Referer": "https://example.com", "X-Title": "bilingual_book_maker"}'
# a gateway's own auth or routing header
--extra_headers '{"X-API-Key": "sk-gateway-..."}'
```
