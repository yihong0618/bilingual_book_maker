# 从旧参数迁移

为旧命令行写的命令仍然可用。每个已移除的参数都会在运行开始前被改写成新参数，并打印一行说明它变成了什么：

```
$ bbook_maker --book_name book.epub --model gpt4omini --openai_key sk-...
deprecated: --openai_key is now --key
deprecated: --model gpt4omini is now --model gpt-4o-mini
```

| 旧写法 | 改写为 |
|---|---|
| `--model gpt4o` / `gpt4omini` / `o3mini` | `--model` 加该模型的 ID |
| `--model chatgptapi` / `openai` | 去掉：openai 格式就是默认值，想指定模型时用 `--model` 写模型 |
| `--model openai --model_list X` | `--model_list X` |
| `--model claude` | `--model claude-haiku-4-5-20251001` |
| 确切的 `claude-*` ID | 不变；anthropic 格式由 ID 推断 |
| `--model gemini` / `geminipro` | `--api_format gemini --model gemini-flash-latest` / `gemini-pro-latest` |
| `--model qwen` / `qwen-mt-turbo` / `qwen-mt-plus` | `--api_format qwen --model qwen-mt-*` |
| `--model groq --model_list X` | `--api_format groq --model_list X` |
| `--model xai` | `--api_format xai --model grok-4.3` |
| `--model codex` | `--api_format codex`（侧车进程的默认模型；要指定模型就加 `--model`） |
| `--model google` / `caiyun` / `deepl` / `deeplfree` / `tencentransmart` | `--api_format google` / `caiyun` / `deepl` / `deeplfree` / `tencent` |
| `--custom_api URL` | `--api_format customapi --api_base URL` |
| `--openai_key` / `--claude_key` / `--gemini_key` / `--groq_key` / `--xai_key` / `--qwen_key` / `--caiyun_key` / `--deepl_key` / `--orcarouter_key` | `--key`（`--api_key` 是同一个参数，从未改名） |
| `--ollama_model M` | `--api_base http://localhost:11434/v1 --model M` |
| `--deployment_id D` | `--model D`，同时 `--api_base` 被改写为该部署的 `/openai/v1` 路径 |

说明：

- 旧的 key 变量对使用它们的那条路线仍然有效：改写后的 `--model groq` 用 `BBM_GROQ_API_KEY`，改写后的 `--model gemini` 用 `BBM_GOOGLE_GEMINI_KEY`，以此类推。
- 你自己传的参数优先。`--model gemini --api_base https://my-gateway/v1` 会保留你的网关。
- `--interval` 不是旧参数：它仍在解析器里，仍然控制 gemini 路线的请求节奏，而 gemini 仍是一条路线。
- `qwen-mt-turbo` 和 `qwen-mt-plus` 是真实的模型 ID，所以同时传了 `--api_format` 的命令保持原样，也不会为此打印任何东西。
- 其他路线别名在任何地方都不是模型 ID。`--model gemini` 与指向另一条路线的 `--api_format` 一起使用会被拒绝，而不是按其中一方解析：遵从格式，会把该别名的 key 发往不属于它的主机；遵从别名，又会无视你输入的内容。错误信息会列出两者以及两种解决办法。
- 改写后的命令运行的是它过去运行的模型，取自旧的预设列表，而不是更新的模型。其中一些模型已经下线，端点的模型检查会指出这一点。
- 不是旧别名的 `--model` 值会作为模型 ID 原样传递，这在现在是常态。
- 已下线 OpenAI 模型的别名（`gpt4`、`gpt5mini`、`o1`、`o1mini`、`o1preview`）已经去掉。它们现在作为模型 ID 原样传递，由端点按名字拒绝——还是同样的失败，只是早一步发生，信息也更清楚。
