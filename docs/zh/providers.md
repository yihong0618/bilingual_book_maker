# 提供方文件与额外模型

一次运行通过一个端点翻译。有两类步骤可以使用第二个端点：

- **分类。** 计划模式会问模型，EPUB 里哪几种块值得翻译。默认问的就是翻译模型。你可以用 `--classify-model` 另外指定一个。
- **查看页面图像的步骤。** 目前只有一个：在 [PDF 路线](features/pdf-to-epub.md)上，视觉模型可以纠正 docling 给页面各区域判定的角色。只有用 `--img-model` 指定了模型时才会运行。

两者都可以在命令行或提供方条目里指定。本页介绍提供方文件、这两个额外模型，以及哪个 key 发往哪里。

## 提供方文件

`--provider NAME` 从 JSON 文件而不是参数中读取路线。运行会读三个文件，同名时后面的优先：

1. `bbm_providers.example.json`，随 `make_book.py` 一起提供。只有某个名字在另外两个文件里都没有时，运行才会用它，而且每次都会说明，并给出所用的地址和 key 变量。其中的 `FILL-ME` 条目是模板，永远不会被使用。
2. `~/.bbm/providers.json`。
3. 运行目录下的 `bbm_providers.json`。

```json
{
  "providers": {
    "siliconflow": {
      "api_style": "openai",
      "base_url": "https://api.siliconflow.cn/v1",
      "default_models": ["Qwen/Qwen2.5-72B-Instruct"],
      "env_key": "BBM_SILICONFLOW_API_KEY"
    }
  }
}
```

```bash
bbook_maker \
  --book_name my_book.epub \
  --provider siliconflow \
  --use_context session
```

你自己传入的参数优先于条目。

### 字段

| 字段 | 必填 | 含义 |
|---|---|---|
| `api_style` | 是 | `openai`、`anthropic`、`gemini`、`qwen`、`groq`、`xai` 或 `litellm` |
| `base_url` | 否 | 地址；默认是该格式自己的地址 |
| `default_models` | 否 | 要用的模型；不传 `--model` 时必填 |
| `env_key` | 否 | 存放 key 的环境变量；不传 `--key` 时必填 |
| `prices` | 否 | 每百万 token 的价格，按模型分列：`{"<model>": {"input": …, "output": …, "cached_input": …}}`。本次运行的每个模型都有价格时，进度条显示已花的钱（`spent=$0.012`），而不是 token 数 |
| `currency` | 否 | 默认 `USD`；`EUR`、`GBP`、`CNY`、`JPY` 会带各自的符号打印 |
| `img_model` | 否 | 页面图像步骤所用的视觉模型。相当于 `--img-model` |
| `img_base_url` | 否 | `img_model` 不在本条目自己的地址上时，它所在的地址。仅限 OpenAI 兼容 |
| `img_env_key` | 否 | 存放 `img_model` 所在地址的 key 的变量 |
| `classify_model` | 否 | 分类用的模型。相当于 `--classify-model` |
| `classify_base_url` | 否 | `classify_model` 不在本条目自己的地址上时，它所在的地址。OpenAI 兼容地址，或 [Jev 兼容](#jev-与-jev-兼容分类器)分类器的 URL |
| `classify_env_key` | 否 | 存放 `classify_model` 所在地址的 key 的变量 |

花费金额是按每个请求报告的用量估算的；以厂商的账单为准。

### 随附的 `openai` 条目会开启图像步骤

示例文件的 `openai` 条目设置了 `"img_model": "gpt-5.6-luna"`。所以在 PDF 上用 `--to-epub` 加 `--provider openai`，默认会对每一页运行区域角色校正。即使你没有 `bbm_providers.json` 也一样，因为运行会退回使用示例文件。这一步每页约花 3,000 个提示 token：在它背后的那项研究中，12 页共用了 43,443 个提示 token 和 4,319 个补全 token（[用图像模型判定区域角色](evaluation/pdf-structure-llm-roles.md)）。不想要这一步，就传 `--img-model none`，或者把文件复制一份并删掉那一行。

`openai-jev` 条目就是 `openai` 条目加上 `"classify_model": "jev"` 和 `JEV_API_KEY`，所以 `--provider openai-jev` 用 gpt-5.6-luna 翻译，用 [Jev](#jev-与-jev-兼容分类器) 给 EPUB 的计划分类。`jev` 条目只有 Jev：它只分类、从不翻译，所以 `--provider jev` 会被拒绝，并提示改用 `--classify-model`。其他条目都没有写图像模型或分类模型。

## 两个额外模型

### 各模型从哪里来

| 步骤 | 首先 | 其次 | 最后 |
|---|---|---|---|
| 图像（`--img-model`） | 参数 | 条目的 `img_model` | **关闭** |
| 分类（`--classify-model`） | 参数 | 条目的 `classify_model` | 本次运行自己的模型 |

图像步骤绝不会退回使用翻译模型。只有通过参数或条目为它指定了模型才会运行，所以没有哪个默认设置需要能读图的模型。`--img-model none` 在单次运行中关闭条目里的图像模型。

`--plan-classify-model` 是 `--classify-model` 的旧名。它仍然可用，只是不出现在 `--help` 里。两个都写时，`--classify-model` 优先。

### 各模型在哪里被询问

- **没有 base URL 时**，模型在本次运行自己的端点上被询问，用本次运行的格式和 key。
    - 图像模型要求该端点是 OpenAI 形式。其他任何格式下，运行在开始前就会停下：`--img-model needs an OpenAI-compatible endpoint; … resolves to the … format.`
    - 分类模型在任何能对话的大模型路线上都能用，anthropic 和 codex 路线也包括在内。
    - 在[机器翻译](machine-args.md)运行中，没有可以共用端点的模型。这时不带 base 指定的分类模型，会在它的 ID 所暗示的主机上被询问：`--classify-model gpt-5.6-luna` 发往 OpenAI。
- **有 `--img-base-url` 或 `--classify-base-url` 时**，模型在那里被询问。该地址必须说 OpenAI 形式，对分类器来说也可以是 [Jev 协议](#jev-与-jev-兼容分类器)；其他任何情况都会在花钱之前被拒绝。
- 只写 base URL 而不写对应模型，运行会停下：`--img-base-url names where --img-model is served, and no --img-model was given.`（`--classify-base-url` 同理）。

指定的分类模型总有自己的客户端，即使在本次运行的地址上也是如此，所以它的 token 与翻译的 token 分开计数。

图像步骤运行之前，会先检查一次模型能否读图。不能的话，运行会说 `… did not read the probe image (…); image steps are skipped this run.`，然后跳过这一步继续。

### 哪个 key 发往哪里

key 与地址绑定，永远不会发往它不该去的主机。对图像模型和分类模型，key 按以下顺序查找：

1. `--img-key` 或 `--classify-key`。
2. 本次运行的 key，但仅当模型在本次运行自己的地址上被询问时。本次运行的地址是应用 `--api_base` 之后的那个地址。
3. 条目的 `img_env_key` 或 `classify_env_key`，但仅限该条目为模型指定的地址：它的 `img_base_url` 或 `classify_base_url`，否则是条目的 `base_url`。`--api_base` 把运行移到别处时，不会把条目的 key 一起带过去。
4. 该地址所属格式读取的变量，与本次运行相同（见 [API key](llm-args.md#api-key)）。

以上都没有 key 时，运行会停下，并说出应该传哪个参数。

`--extra_body` 和 `--extra_headers` 只有在图像模型或分类模型在本次运行自己的地址上被询问时才会带给它们。请求头常常带着网关的凭据，所以绝不会发往其他主机。

### 示例

在本地模型上翻译，让托管模型给计划分类并读取页面图像：

```json
{
  "providers": {
    "local-with-helpers": {
      "api_style": "openai",
      "base_url": "http://localhost:11434/v1",
      "default_models": ["qwen3:8b"],
      "classify_model": "gpt-5.6-luna",
      "classify_base_url": "https://api.openai.com/v1",
      "classify_env_key": "OPENAI_API_KEY",
      "img_model": "gpt-5.6-luna",
      "img_base_url": "https://api.openai.com/v1",
      "img_env_key": "OPENAI_API_KEY"
    }
  }
}
```

用参数写是这样：

```bash
bbook_maker \
  --book_name my_book.epub \
  --api_base http://localhost:11434/v1 \
  --model qwen3:8b \
  --classify-model gpt-5.6-luna \
  --classify-base-url https://api.openai.com/v1 \
  --classify-key "$OPENAI_API_KEY" \
  --use_context session
```

本地服务器不需要 key，所以这里本次运行的 key 是空的；分类器的 key 必须指定。

## Jev 与 Jev 兼容分类器

Jev 是 TypeSafe 的分类器：一个专门回答类型化问题、而不是写文字的模型。计划模式对每种块提的问题——翻译还是保留——正是这类问题，Jev 能在一次廉价的往返中回答一整页。它什么都不翻译，所以只能充当分类模型。TypeSafe 前面的网关说同一套协议。

### 命令

| 分类器 | 参数 | key |
|---|---|---|
| TypeSafe 的 Jev | `--classify-model jev` | `JEV_API_KEY` 或 `TYPESAFE_API_KEY`，只发往 `api.typesafe.ai` |
| 经网关访问 Jev | `--classify-model typesafe-ai/jev --classify-base-url https://ai-gateway.vercel.sh/typesafe --classify-key "$GATEWAY_KEY"` | 用 `--classify-key` 指定 |

例如，用 gpt-5.6-luna 翻译、用 Jev 分类：

```bash
bbook_maker \
  --book_name my_book.epub \
  --model gpt-5.6-luna \
  --classify-model jev
```

### 规则

- **key 只为它自己的主机从环境变量读取。** `JEV_API_KEY` 和 `TYPESAFE_API_KEY` 只发往 typesafe.ai 地址。其他任何地方，包括网关，都要用 `--classify-key` 或条目的 `classify_env_key` 指定 key。
- **已经以 `/systemone` 结尾的 base URL 原样使用。** 其他 Jev base 会在 `/v1` 之后加上 `/systemone`。
- 只写 `jev` 时，请求的是 TypeSafe 当前的模型 `jev-latest`。

### 闸门：没把握的跳过按翻译处理

Jev 的每个回答都带一个概率。低于闸门的 `skip` 记为 `translate`，所以不会因为 Jev 没把握的跳过而丢失内容。`translate` 不论概率多少都会采纳。闸门是所选回答的概率 0.95，这是在 45 本 EPUB 的 662 个计划签名上对照 gpt-5.6-luna 测出来的。取这个值时，Jev 的跳过约有十分之九变成 `translate`，Jev 仍然跳过的都是附属内容：版权行、行号、注释标记、索引页码。所以在这个语料上，Jev 比全部翻译省不了多少；见[用 Jev 做计划分类器](evaluation/plan-classifier-jev.md)。计划文件会在每个回退行上注明：`unnamed (jev verdict skip at confidence 0.61, below the gate: translate)`。`--classify-min-confidence P`（0 到 1）为单次运行调整闸门；`BBM_JEV_MIN_CONFIDENCE` 不用参数也能做到同样的事，两者都有时以参数为准。值越低，保留的 Jev 跳过越多：在测过的语料上，低于 0.9 的跳过与参照结果一致的比例只有 7–55%，所以风险由你承担。

### 写在提供方条目里

如果你想让 OpenAI 翻译、Jev 分类，随附的 `openai-jev` 条目已经做到了：`--provider openai-jev`。其他任何组合，请自己写条目。

`classify_model`、`classify_base_url` 和 `classify_env_key` 指定 Jev 分类器的方式与参数相同：

```json
{
  "providers": {
    "openai-with-jev": {
      "api_style": "openai",
      "default_models": ["gpt-5.6-luna"],
      "env_key": "OPENAI_API_KEY",
      "classify_model": "typesafe-ai/jev",
      "classify_base_url": "https://ai-gateway.vercel.sh/typesafe",
      "classify_env_key": "JEV_API_KEY"
    }
  }
}
```

为自己的网关地址写明 `JEV_API_KEY` 的条目，就是为那个地址指定了这个 key，会被遵从。没有这样的条目，Jev 的变量永远不会发往网关。

每个 Jev 请求把页面的候选行发送一次，每个签名附一个简短的问题。见[用 Jev 做计划分类器](evaluation/plan-classifier-jev.md)。

## 运行会打印什么

`--plan-dry-run` 不需要 key，就能显示每个模型会在哪里被询问：

```text
Classifier: gpt-5.6-luna at the openai endpoint's default host (cli)
Image model: off
```

括号里是这个选择的来源：`cli`、`provider` 或 `run`。在 `--plan-classify all` 或 `agent` 下，这一行是 `Classifier: none (--plan-classify agent asks nothing)`。

运行结束时，拥有自己客户端的模型会在翻译用量那一行下面打印自己的用量：

```text
Classifier (gpt-5.6-luna at the endpoint's default host): tokens: in 7.6k, out 1.8k, cached 0 (3 requests)
```

图像模型的那一行是 `Image model (<model> at <address>): …`，在提取之后打印。

以下警告表示某个参数在本次运行中不起作用：

- **`--img-model, --img-base-url and --img-key choose the vision model for the steps that look at a page image, and only the PDF route (--to-epub on a PDF) has one; …`** 你指定了图像模型，但这本书不是走 `--to-epub` 路线的 PDF。
- **`--classify-model names a classifier, and --plan-classify all translates the whole partition without classifying anything; it is ignored this run.`** `--plan-classify agent` 也一样：代理模式把每一行都留给你的代理，不问任何模型。
- **`Nothing on this route classifies yet, so --classify-model is ignored on a … book.`** 目前只有 EPUB 有分类步骤。
