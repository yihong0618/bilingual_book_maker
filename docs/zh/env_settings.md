# 环境变量设置

把凭据写进环境变量，就可以省掉 `--key`。

## API key

`BBM_API_KEY` 对每种格式都有效，所以通常一个变量就够了：

```
export BBM_API_KEY=${your_api_key}
```

没有设置它时，每种格式还会去看人们为该厂商习惯导出的那个变量：

| `--api_format` | 备用变量 |
|---|---|
| `openai` | `OPENAI_API_KEY`、`BBM_OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY`、`BBM_CLAUDE_API_KEY` |
| `gemini` | `BBM_GOOGLE_GEMINI_KEY`、`GEMINI_API_KEY` |
| `qwen` | `BBM_QWEN_API_KEY`、`DASHSCOPE_API_KEY` |
| `groq` | `BBM_GROQ_API_KEY`、`GROQ_API_KEY` |
| `xai` | `BBM_XAI_API_KEY`、`XAI_API_KEY` |
| `litellm` | `BBM_LITELLM_API_KEY`、`LITELLM_MASTER_KEY` |
| `caiyun` | `BBM_CAIYUN_API_KEY` |
| `deepl` | `BBM_DEEPL_API_KEY` |

`google`、`deeplfree`、`tencent` 和 `customapi` 不需要 key，localhost 上的端点也不需要——包括那里的 LiteLLM 代理，除非 `--api_base` 另有指定，`--api_format litellm` 指向的就是它。

命令行工具不读 `.env` 文件。请先导出变量，或者在运行前 source 一个被 git 忽略的文件：`set -a; source .env; set +a; bbook_maker ...`

## 提供方自己的变量

`--provider NAME` 从工作目录下的 `bbm_providers.json` 读取端点，没有则读 `~/.bbm/providers.json`。条目里有 `env_key` 时，会先查它指定的变量，再查 `BBM_API_KEY` 和上面的备用变量——它指明的是正在调用的端点，所以它自己的 key 才是对的。只有在运行仍然调用那个端点时才会查它：`--api_base` 指向别处，或者在没有写 `base_url` 的条目上用 `--api_format` 覆盖格式，都会把请求移到另一台主机，条目的 key 不会发到那里。运行会说明这一点；如果你本意是继续用它，请传 `--key`。文件里只有地址和变量名，从不存放密钥。见[端点、模型与语言](./model_lang.md#命名端点-provider)。`--model orcarouter` 以同样方式读取 `BBM_ORCAROUTER_API_KEY`。`--model apiroute` 读取 `BBM_APIROUTE_API_KEY`，也可使用 `APIROUTE_API_KEY`。

## 分类器变量

[Jev 兼容分类器](providers.md#jev-与-jev-兼容分类器)只为它自己的主机读取 key：

| 变量 | 用于 |
|---|---|
| `JEV_API_KEY`、`TYPESAFE_API_KEY` | typesafe.ai 地址上 TypeSafe 的 Jev |
| `CF_AIG_TOKEN` | gateway.ai.cloudflare.com 上开启认证的 Cloudflare AI Gateway |

通过网关访问时，用 `--classify-key` 或提供方条目的 `classify_env_key` 指定 key。

`BBM_JEV_MIN_CONFIDENCE` 是 0 到 1 之间的数，在不写 `--classify-min-confidence` 时为单次运行设置 Jev 的闸门（两者都有时以参数为准）：低于它的 `skip` 会被翻译。默认是 0.95。不是 0 到 1 之间的数时，运行会停下。

## 旧的各厂商变量

旧式命令暗示了对应路线时，上面四个厂商变量同样会被读取——`--model gemini` 会查 `BBM_GOOGLE_GEMINI_KEY`，尽管改写后的命令写的是格式（见[从旧参数迁移](migration.md)）。它们绝不会被用于 `--api_base` 指向其他端点的命令：那种情况下 key 必须与地址相符，只有 `BBM_API_KEY` 和该格式自己的变量适用。

## 覆盖提示词

```
export BBM_CHATGPTAPI_USER_MSG_TEMPLATE=${your_prompt_template}
export BBM_CHATGPTAPI_SYS_MSG=${your_system_message}
```
