# 端点、模型与语言

路线由它对接的端点决定，而不是由模型名决定。没有需要随时更新的内置模型列表：`--model` 接受你的端点提供的任何 ID。

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.openai.com/v1 --key sk-... \
  --model gpt-5-mini --language ja
```

旧的 `--model` 命令仍然可用——它们会被改写成这些参数，并打印出替换内容。完整对照表见[从旧参数迁移](migration.md)。

## 路线参数

| 参数 | 含义 |
|---|---|
| `--model` | 模型 ID，按端点自己的写法。在 `openai` 格式下可以省略：默认 `gpt-6-luna`。 |
| `--api_base` | 端点 URL。默认是该格式的官方地址；`…/v1`、`…/v1/` 和 `…/v1/chat/completions` 都可以。 |
| `--key` | API key。用逗号分隔多个 key，可以轮流使用以分摊限流。 |
| `--api_format` | 传输格式。会自动推断；只有推断错了才需要传。 |
| `--provider` | 提供方文件里的一个命名端点，代替上面四个参数。 |

`--model_list a,b` 在多个模型之间轮换，旧命令用的也是它；一个模型只在其中一个参数里写，不要两个都写。

`--api_format` 取值为 `openai`（默认）、`anthropic`、`gemini`、`qwen`、`groq`、`xai`、`litellm`、`codex`，或固定的翻译服务 `google`、`caiyun`、`deepl`、`deeplfree`、`tencent`、`customapi`。

五种厂商格式各自带着自己的端点，所以格式加一个 key 就是一条完整的路线，不必再去查 `--api_base`：

| 格式 | 端点 | `--model` |
|---|---|---|
| `gemini` | Gemini API | 可选，默认 `gemini-flash-latest` |
| `qwen` | DashScope 上的 Qwen-MT | 可选，默认 `qwen-mt-turbo` |
| `groq` | `https://api.groq.com/openai/v1` | 必填 |
| `xai` | `https://api.x.ai/v1` | 必填 |
| `litellm` | `http://localhost:4000` | 必填，填代理配置里的名字 |

`gemini` 和 `qwen` 有各自的协议——Gemini 有原生的约束解码、安全设置和对话历史；Qwen-MT 用一对源/目标语言代替提示词——`--interval` 控制 gemini 路线的请求节奏，免费额度就靠它避开限流。另外三种是换了地址的 OpenAI 路线，保留它的一切功能。这五种都不会从主机名推断出来：要么写明格式，要么给出厂商的 OpenAI 兼容 `--api_base`，走 `openai` 路线。

`codex` 根本不是端点：它驱动本地的 `codex app-server` 侧车进程，本次运行记在你的 ChatGPT 套餐上，所以它不接受 `--key` 和 `--api_base`，`--model` 可选（默认 `gpt-6-luna`）。它从不会被推断出来，必须明确写出。见[用大模型翻译](llm-args.md)。

推断按以下顺序进行：明确写出的 `--api_format` 优先；然后看 `--api_base` 的主机（`anthropic.com` 表示 anthropic 形式，其他都是 OpenAI 形式）；然后，在没有指定端点时，看模型 ID 是否提到 `claude` 或 `anthropic`，`anthropic/claude-sonnet-4-6` 也算。

网关被要求使用它并不提供的 anthropic 形式时，会在 `/v1/messages` 上回 404 或 405。运行会停下并指出解决办法：用 `--api_format openai` 重新运行。在 Anthropic 自己的主机上，404 表示模型不存在，也会照此报告。

凭据依次来自 `--key`、`BBM_API_KEY`，然后是该格式惯用的变量——见[环境变量设置](./env_settings.md)。localhost 上的端点不需要 key。

## 命名端点：`--provider`

用到不止一次的端点可以写下来，而不必每次重敲。先读工作目录下的 `bbm_providers.json`，再读 `~/.bbm/providers.json`；同名时项目里的条目优先。

```json
{
  "nvidia": {
    "api_style": "openai",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "default_models": ["moonshotai/kimi-k2-thinking"],
    "env_key": "NVIDIA_API_KEY"
  }
}
```

```sh
bbook_maker --book_name book.epub --provider nvidia --language ja
```

`api_style` 可以是任何指明端点的 `--api_format`——`openai`、`anthropic`、`gemini`、`qwen`、`groq`、`xai`、`litellm`——另外 `claude` 是 `anthropic` 的旧写法。其他任何 OpenAI 兼容主机都是 `openai`，地址写在 `base_url`。随仓库提供的 `bbm_providers.example.json` 为每种都准备了一个条目。明确传入的参数仍然优先于条目，而条目的 `env_key` 只跟着条目的地址走，不会更远。在条目自己的网关上要求另一种传输格式，并没有移动任何东西，所以仍会读取这个 key。`--api_base` 指向别处就会移动这次运行；在没有 `base_url` 的条目上用 `--api_format` 覆盖格式也会，因为这时地址是由格式提供的——key 不会为另一家厂商的主机读取，运行会说明它在比较哪两个地址。如果你本意是继续用它，请传 `--key`。`default_models` 只有一个 ID 时充当 `--model`，有多个时充当 `--model_list`；`env_key` 指明从哪个变量读取 key；文件里不放任何密钥。明确传入的一切都优先，所以 `--provider nvidia --model <id>` 会保留那个模型。未知的名字会报错，并列出两个文件。

## OrcaRouter

```sh
bbook_maker --book_name book.epub --model orcarouter --language ja
```

`--model orcarouter` 把运行交给 OrcaRouter 网关，并请求它的智能路由模型 `orcarouter/auto`。它不需要 `--api_base`，你自己传的会优先。key 先从 `BBM_ORCAROUTER_API_KEY` 读取，然后才是通常的备用变量。这是一条受支持的路线，而不是旧别名，所以不会被改写。要在网关上固定一个模型，就像其他端点一样写明：`--api_base https://api.orcarouter.ai/v1 --model <id>`。

## OpenAI 兼容端点

下面这些都是同一条路线，只是 `--api_base` 不同。结构化输出、`--use_context`、并行 worker、异步和 Batch API 在它们上面都可用，程度取决于端点本身的支持——支持情况在运行时探测，而不是根据模型名假定。

`--use_context session` 还要求端点对缓存的提示 token 收费更低。注意进度条上的 `cached=` 计数：十几个请求之后仍为零，说明端点没有缓存，窗口模式更便宜。

| 厂商 | `--api_base` |
|---|---|
| OpenAI | `https://api.openai.com/v1`（默认） |
| Groq | `https://api.groq.com/openai/v1`，或 `--api_format groq` |
| xAI | `https://api.x.ai/v1`，或 `--api_format xai` |
| Gemini（兼容模式） | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| 阿里 Qwen（DashScope） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| SiliconFlow | `https://api.siliconflow.cn/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Azure OpenAI | 部署的 OpenAI 兼容 URL；`--model` 写部署名 |
| Ollama | `http://localhost:11434/v1` |
| vLLM / LM Studio / llama.cpp | 它们提供服务的任何主机 |

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.groq.com/openai/v1 --key gsk-... \
  --model llama-3.3-70b-versatile
```

在这条路线上，`--extra_body` 用来传递厂商特有的请求字段。

## Anthropic

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.anthropic.com --key sk-ant-... \
  --model claude-sonnet-4-6 --language zh-hans
```

端点提供的任何模型 ID 都接受。Claude 每次运行只用一个模型，所以多出来的 `--model_list` 条目会被说明并忽略，而不是悄悄丢掉。从 OpenAI 风格的 `/v1` base 提供 anthropic 形式的网关也能处理：末尾的 `/v1` 会被去掉，因为 SDK 会自己加上。

通过这种格式分类时使用提示词档位——不会要求端点编译 schema。

## 翻译服务

这些引擎说自己的协议，不接受模型，所以指定模型会报错，而不是悄悄不起作用。

| `--api_format` | 凭据 |
|---|---|
| `google` | 无 |
| `deeplfree` | 无 |
| `tencent` | 无 |
| `customapi` | 无；端点 URL 写在 `--api_base` |
| `caiyun` | 必填 |
| `deepl` | 必填（RapidAPI DeepL Translator） |

它们只翻译文本，别的什么都不做：没有上下文窗口，也没有结构化输出。它们自己无法为计划模式给 EPUB 分类；用 `--classify-model` 指定一个能分类的模型，EPUB 在这些引擎上也能用计划模式。`--source_lang` 会传给 `customapi`（写进请求体）；其他引擎自己检测源语言。（在大模型路线上，这个参数进入提示词——见下文的“语言”。）

## 语言

`--language LANGUAGE` 设置目标语言，默认 `zh-hans`。它接受标签（`zh-hant`）、名称（`"Traditional Chinese"`），或者对于内置表里没有的语言，两者一起写——`--language "zh-hant:Traditional Chinese"`。标签部分是机械用途：标在插入的标记和 `dc:language` 上，记录在翻译元数据里，并作为结构化输出字段的名字。名称部分是提示词里向模型提要求时的说法。单独的标签或名称照旧通过表格解析；不匹配任何标签的值仍然可以运行，启动时打印一条 `Note:`，不标注任何东西。完整的表：[语言标签与名称](languages.md)。

```sh
bbook_maker --help
bbook_maker --book_name book.epub --api_format google --language ja
```

`--source_lang` 直接指定源语言，而不是自动检测；一旦指定，它会进入每条大模型路线的提示词，在 `qwen`/`customapi` 上还会写进请求体。默认值 `auto` 什么都不指定。并不是每个端点都支持解析器接受的每一种语言。
