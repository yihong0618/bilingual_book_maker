<div align="left">

# Bilingual Book Maker

**中文 | [English](./README.md)**

bilingual_book_maker 是一个 AI 翻译工具，使用 ChatGPT 帮助用户制作多语言版本的 epub/txt/md/srt/pdf 文件和图书。请仅将其用于您有权翻译的内容——您持有必要权利的作品、许可或授权允许您翻译的作品、公有领域图书，或适用法律另行允许的使用方式。请在使用之前阅读项目的 **[免责声明](./disclaimer.md)**。

各类文件的使用指南、全部参数，以及默认值背后的测量，见[文档站](https://yihong0618.github.io/bilingual_book_maker/)。

[![Stars](https://img.shields.io/github/stars/yihong0618/bilingual_book_maker)](https://github.com/yihong0618/bilingual_book_maker/stargazers)
[![CI](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml/badge.svg)](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml)
[![PyPI](https://img.shields.io/pypi/v/bbook-maker.svg)](https://pypi.org/project/bbook-maker/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![litellm](https://img.shields.io/badge/%20%F0%9F%9A%85%20liteLLM-OpenAI%7CAzure%7CAnthropic%7CPalm%7CCohere%7CReplicate%7CHugging%20Face-blue?color=green)](https://github.com/BerriAI/litellm)

</div>

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## 支持的接口

支持 OpenAI 和 Anthropic 格式的 API 接口。
通常需要三个字段，使用官方接口时两个，模型如 `gpt-5.6-luna`（默认）
或 `claude-sonnet-4-6`。
在 `--api_format` 填 `openai` 或 `anthropic` 即可指定 API 请求格式。
该参数也可以选择常规翻译引擎（`google`、`caiyun`、`deepl`、`deeplfree`、
`tencent`、`customapi`（非 OpenAI 格式），或填 `codex` 以使用你的 Codex 额度。

`--provider` 是另一种传凭据的方式，通过 JSON 配置文件 `bbm_providers.json`。

epub 标签分类在支持 JSON Schema 的接口上自动开启，在其他任何能对话的接口（含 codex 路由和普通转售代理）上也会开启，改为让模型直接回答 `skip`/`translate`；只有完全不能对话的路由（机器翻译引擎）才只翻译 `p` 标签，
因此诗歌等内容可能不会被翻译。详见[计划模式](#计划模式)。

旧参数（`--model gpt4o`、`--model gemini`、`--openai_key` 等）仍然可用：详见
[模型与语言](https://yihong0618.github.io/bilingual_book_maker/model_lang/)。

## 准备

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md books
3. 能正常联网的环境或 proxy
4. Python 3.10+

## 快速开始

本地放了一个 `test_books/animal_farm.epub` 给大家测试，加上`--test` 表示只翻开头几段。

```shell
pip install -r requirements.txt      # 或：pip install -U bbook_maker
```

然后：

```shell
cp bbm_providers.example.json bbm_providers.json
# 在 ./bbm_providers.json 改 base_url、default_models、env_key
python3 make_book.py --book_name test_books/animal_farm.epub --provider openai --test --use_context session
```

也可直接在CLI里传 key：

```shell
python3 make_book.py --book_name test_books/animal_farm.epub \
  --key sk-... --model gpt-5.6-luna --api_base https://api.openai.com/v1 --test --use_context session
```

使用[Codex](https://developers.openai.com/codex/cli)订阅：

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt-5.6-luna --api_format codex --test
```

或者交给 coding agent

```shell
git clone https://github.com/yihong0618/bilingual_book_maker.git
cd bilingual_book_maker
codex "你好，请使用bbm-plan帮我将这本书：test_books/animal_farm.epub，翻译为中英双语，谢谢。"
```

[^token]: 你可以在 [OpenAI](https://platform.openai.com/account/api-keys) 或 [Anthropic](https://console.anthropic.com/account/api-keys) 申请到 token。

## 接口参数

- `--api_format` 指定接口说的 API：`openai`、`anthropic`、`gemini`、`qwen`、
  `groq`、`xai`、`litellm`、`codex`，或常规翻译引擎（`google`、`caiyun`、
  `deepl`、`deeplfree`、`tencent`、`customapi`）。属于某一家的格式本身就带着
  那家的地址，所以格式加一个 `--key` 就是一条完整命令。
- **其他 OpenAI 兼容 API**: `--api_base`（以 `/v1` 结尾）、
  `--key`即 API key，以及模型标识符 `--model`。省略 `--api_base`即使用openai官方API，
  省略`--model`即使用 gpt-5.6-luna。
- 或使用`--provider`进行翻译: `bbm_providers.example.json` 里预设了以下厂家（Gemini、Qwen、xAI、Groq、OrcaRouter、Ollama、LiteLLM、
  SiliconFlow、OpenRouter）：复制为 `bbm_providers.json`，并修改其中的key，
  例如`--provider gemini` 就是使用其中 Gemini 的api。
- `--use_context session` 使用会话模式翻译；历史默认在 8k 时压缩（`--context-compact-at` 可改）。它维护一份缓存的历史以保持前后一致，使人名、术语全书统一，也可以从交接报告中积累术语表（`--glossary-auto on`，默认关闭）——是 OpenAI 兼容接口的推荐用法，下方示例均已带上。
- 旧的预设名和 key 参数仍然可用，见 [从旧参数迁移](https://yihong0618.github.io/bilingual_book_maker/migration/)。

## 支持的翻译服务
* DeepL

  使用 DeepL 封装的 api 进行翻译，需要付费。[DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) 来获得 token

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key}
  ```

* DeepL free

  使用 DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deeplfree
  ```

* [Claude](https://console.anthropic.com/docs)

  `claude-*` 的模型 ID 会自动选中 anthropic 格式。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key}
  ```

* 谷歌翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google
  ```

* 彩云小译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format caiyun --key ${caiyun_key}
  ```

* Gemini

  走 Gemini 官方接口。可以指定任意 Gemini 模型 ID，不写 `--model` 就是
  `gemini-flash-latest`。`--interval` 设置请求间隔，免费额度靠它避开限流。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format gemini --key ${gemini_key} --model gemini-flash-latest
  ```

* Qwen

  百炼上的 [Qwen-MT](https://www.aliyun.com/product/dashscope)：它是翻译模型，请求里写的是源语言和目标语言。支持 qwen-mt-turbo（默认）
  和 qwen-mt-plus，不想自动检测源语言时用 `--source_lang` 指定。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format qwen --key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
  ```

* [腾讯交互翻译](https://transmart.qq.com)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format tencent
  ```

* [xAI](https://x.ai)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format xai --key ${xai_key} --model grok-4.3 --use_context session
  ```

* [OrcaRouter](https://www.orcarouter.ai)

  [OrcaRouter](https://www.orcarouter.ai) 网关，默认使用 `orcarouter/auto` 智能路由。
  地址随该路由自带，无需 `--api_base`；key 用 `--key` 或 `BBM_ORCAROUTER_API_KEY`。
  `--provider orcarouter` 指向同一处。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model orcarouter --key ${orcarouter_key} --use_context session
  ```

  若要指定具体模型：`--provider orcarouter --model <模型 id>`。

* [Ollama](https://github.com/ollama/ollama)

  使用 [Ollama](https://github.com/ollama/ollama) 自托管模型进行翻译。
  如果 ollama server 不运行在本地，使用 `--api_base http://x.x.x.x:port/v1` 指向 ollama server 地址

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base http://localhost:11434/v1 --model ${ollama_model_name} --use_context session
  ```

* [Groq](https://console.groq.com/keys)

  必须写 `--model`：GroqCloud 的模型表更新很快，当前支持的模型见
  [Supported Models](https://console.groq.com/docs/models)。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format groq --key [your_key] --model llama-3.3-70b-versatile --use_context session
  ```

* [LiteLLM](https://docs.litellm.ai/docs/simple_proxy)

  LiteLLM 代理，后端由它自己的配置决定，`--model` 写的是那份配置里的名字。
  默认地址是本机上代理的默认端口，代理在别处就用 `--api_base` 指定。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format litellm --model ${name_in_your_litellm_config} --use_context session
  ```

* [Codex](https://developers.openai.com/codex/cli)

  使用 ChatGPT/Codex 订阅额度。需要安装
  [Codex CLI](https://developers.openai.com/codex/cli) 默认使用`gpt-5.6-luna`，可使用 `--api_format codex --model <id>`指定模型。整本书只开一个 session 并复用，到达 `--context-compact-at` 时压缩；
  运行在沙箱中，shell、MCP 服务器、浏览全部关闭。但hooks可能仍会触发。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format codex --language zh-hans
  ```

## 自定义 API Provider

  内置模型不满足需求时，可以通过 JSON 配置文件自定义 provider。不需要改代码，就能使用任何 OpenAI 兼容 / Anthropic 格式的 API（SiliconFlow、本地代理等）。

  在当前目录创建 `bbm_providers.json`（也可放在 `~/.bbm/providers.json`）：

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

  配置字段说明：

  | 字段 | 必填 | 说明 |
  |------|------|------|
  | `api_style` | 是 | API请求格式：`openai`、`anthropic`、`gemini`、`qwen`、`groq`、`xai` 或 `litellm` |
  | `base_url` | 否 | API 地址。不填则使用该 api_style 的默认地址 |
  | `default_models` | 否 | 默认模型列表。不填则必须通过 `--model` 指定 |
  | `env_key` | 否 | 读取 API key 的环境变量名。不填则必须通过 `--key` 传入 |

  优先级：项目级 `./bbm_providers.json` 覆盖全局 `~/.bbm/providers.json`。

  `--model` 指定该 provider 下的模型；不写就用 `default_models` 的第一个。

  ```shell
  python3 make_book.py --provider siliconflow --key sk-xxx --book_name test_books/animal_farm.epub --use_context session

  export BBM_SILICONFLOW_API_KEY=sk-xxx
  python3 make_book.py --provider siliconflow --book_name test_books/animal_farm.epub --use_context session
  ```

## 使用说明

- 翻译完会生成一本 `{book_name}_bilingual.epub` 的双语书；TXT、MD、SRT 输入分别生成 `{book_name}_bilingual.txt`、`{book_name}_bilingual.md`、`{book_name}_bilingual.srt`
- 如果出现了错误或使用 `CTRL+C` 中断命令，不想接下来继续翻译了，会生成一本 `{book_name}_bilingual_temp.epub` 的书，直接改成你想要的名字就可以了

## 功能

这三个开关改变的是一本书被怎样读取和翻译，而不是请求发往哪里。每个都只是一个参数；本节说的是什么时候该用、用的时候盯着什么。

### 计划模式

**做什么。** EPUB 默认通过一份计划来翻译：加载器把整本书切分成翻译单元（段落、标题、列表项、表格单元格、引用块、诗行、图注，凡是带文字的块级元素），按标签签名分组，问模型哪些签名值得翻译，把答案写进 `<book>_plan.json`。随后连续的单元合并进同一个请求，直到 token 预算（`--accumulated_num`）或单元上限（`--max-batch-units`），诗行和短句因此变得便宜。没有计划时只翻译 `--translate-tags` 选中的标签，默认是 `<p>`，不在 `<p>` 里的诗歌或表格会悄悄留在原文。

**什么时候用。** 只要是 EPUB 加 LLM 路由，就一直用：它是默认值，不需要参数。书不寻常时（教材、双语版、正文不在 `<p>` 里的书）先预览：`--plan-dry-run` 打印按签名分组的覆盖表并写出计划文件，不需要 key，不翻译任何东西，付费之前就能看到哪些会被跳过；它遵守 `--only_filelist` / `--exclude_filelist`。想要和旧的 `--translate-tags` 完全一样的行为，用 `--plan-classify none` 关掉。

```shell
# 预览：哪些会翻、哪些会跳过（不需要 key）
python3 make_book.py --book_name my_book.epub --plan-dry-run
# 默认运行：用翻译模型分类，然后翻译
python3 make_book.py --book_name my_book.epub --key ${key}
# 不分类：翻译分区里的每一个单元
python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify all
# 自己决定计划，或交给 coding agent：写出计划、打印指引后停下；
# 之后再跑同一条命令即翻译
python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify agent
```

- `--plan-classify` 决定计划怎么判定：`auto`（默认：由翻译模型判定，在能验证接口严格执行 JSON schema 时走结构化输出，否则走普通对话，要求精确回答 `skip`/`translate`/`unsure`；unsure 和解析不了的行一律翻译）、`none`、`all`、`model`（同 auto，但有未判定的行时停止而不是回退）、`agent`。`--classify-model X` 用另一个模型分类（旧名 `--plan-classify-model`），或用一个 Jev 兼容的分类器（默认 TypeSafe 的 Jev；Simple Jev 用它的 URL），`--classify-min-confidence` 调整门槛（默认 0.95，实测值）；显式指定后，分类失败会中止而不是回退。`--classify-base-url URL` 与 `--classify-key KEY` 让它在另一个 OpenAI 兼容端点上判定。
- `--plan-min-coverage`（默认 0.5）：计划覆盖的正文比例低于该值时中止。`0` 关闭该闸门；高于 0.9 的值多半会在分类已付费之后才中止。
- `<book>_plan.json` 会被同一本书之后的每次运行复用，`--test` 也一样；想重新分类先删掉它。`--test` 分类的是整本书而不是那一小段，运行时会说明。

**注意事项。**

- 仅限 EPUB。Markdown、txt、srt 没有标签可以规划，计划参数在那里会被报告为忽略。
- 不能对话的路由（`google`、`deepl`、`caiyun`、`tencent`、`customapi`）没有模型可问，运行回退到 `--translate-tags` 选中的标签；在这些路由上要求 `--plan-classify model` 会直接停止。
- 自动开启的计划在建不起来时、或续跑一个标签模式写下的断点时，会打印原因并退回标签模式；显式要求的计划则停止。`--retranslate`、`--batch`、`--sentence_mode` 与计划相矛盾，不能同用。
- 小模型和本地模型：普通对话分类只要求一个词的回答，其余一律按策略翻译，所以弱模型的偏差是翻得太多，不是翻得太少。它判定的行在计划文件里标为 `unnamed (…)`。分类反复失败时，`--plan-classify all` 跳过分类。合并请求的默认值也出于同一原因刻意偏小；强端点上可以调高 `--accumulated_num` 和 `--max-batch-units`，运行开始打印错位恢复提示时再调回去。
- `--parallel-workers` 加合并请求（`--accumulated_num` 大于 1）不记录进度，`--resume` 无从续跑；三者同用会被拒绝。

更多：[计划模式](https://yihong0618.github.io/bilingual_book_maker/features/plan-mode/)、[EPUB 推荐设置](https://yihong0618.github.io/bilingual_book_maker/features/recommended-epub/)。

### 会话模式

**做什么。** `--use_context session` 为整本书维护一份只追加的对话，而不是每次请求重发最近几对原译文（不带值的 `--use_context`，即 window 模式）。每个请求都带着整段历史，所以在支持提示缓存的端点上，模型以缓存价重读大约一章的内容，人名、语域和术语得以前后一致。历史达到 `--context-compact-at`（默认 `8192` 估算 token，含种子）时，模型写一份约 300 token 的交接报告，其摘要开启下一个窗口；`<book>_handoff.md` 保存最新一份，`--resume` 会读回它。

**什么时候用。** 小说和任何同一批名字、术语反复出现的长文本，且端点支持提示缓存（OpenAI、Anthropic，以及它们前面的多数网关）。PDF 路由上翻译论文也应当用它，PDF 会被提取成大量短块。端点没有缓存、模型上下文很小、或者想用 `--parallel-workers` 时，用 window 模式。codex 路由不论是否要求都是会话：它的线程就是历史。

```shell
python3 make_book.py --book_name my_book.epub --key ${key} --use_context session
# 输入上限很小的模型：把窗口限制到它的上限（最小 1500）
python3 make_book.py --book_name my_book.epub --key ${key} --use_context session --context-compact-at 4000
# 不写交接报告直接滚动（更省，接缝处没有连续性）
python3 make_book.py --book_name my_book.epub --key ${key} --use_context session --no-context-compact
```

- `--context-compact-at N`：整个窗口的预算，含交接种子，所以可以直接设成模型的输入上限。最小 `1500`；再小的窗口几乎全是种子和接缝，改用 window 模式。在通过普通对话分类计划的端点上，它也限制分类器自己的线程（那里只重启，没有交接）。
- `--no-context-compact`：从不索要报告；下一个窗口从空白开始。
- `--glossary-auto on` 保留每份交接报告确立的译法，让反复出现的名字跨过接缝。它依赖模型准确报告自己的译法，所以需要一个够强的模型；摘要本身已经带着反复出现的名字，`--glossary` 可以钉住要紧的那几个，两者都不需要。

**注意事项。**

- 盯着进度条上的 `cached=`。十几个请求之后仍是 0，说明端点没有提示缓存，每个请求都在按全价付整段历史：Ctrl+C，改用 window 模式重跑。
- 与 `--parallel-workers` 同用会被拒绝（一条历史不能在 worker 之间共享），与 `--model_list` 同用也会（缓存按模型计，一场对话会由几个模型来写）。
- 仅限 EPUB、Markdown 和 PDF；txt 和 srt 加载器不带上下文，压缩参数在那里会被报告为忽略。
- 计划模式之外（Markdown 书，或 `--plan-classify none`）不合并请求，每个段落单独一个请求，每次都重读整段历史。调高 `--accumulated_num` 让几个段落共用一个请求；运行开始时会警告这一点。
- 交接报告由模型来写。小模型写出的报告可能很差；压缩之后译文漂移，就用 `--glossary` 钉住术语，或者加 `--no-context-compact` 接受一个空白的接缝。
- Ctrl+C 留下常规断点；`--resume` 续跑并读回 `<book>_handoff.md`，下一个窗口仍然继承摘要。

更多：[会话模式](https://yihong0618.github.io/bilingual_book_maker/features/session-mode/)。

### PDF 转 **双语** EPUB (实验性)

**做什么。** `--to-epub` 用 [docling](https://github.com/docling-project/docling) 的版面和表格模型把 PDF 读成 Markdown，用 Markdown 加载器翻译它，再由 Pandoc 生成一本可重排的**双语** EPUB，导航跟随标题：论文的每一段后面紧跟它的译文，成书可以重排、带目录。工作目录 `<name>_book/` 在 PDF 旁边：`source.md`、提取出的图片、`book_bilingual.md` 和一份清单；成书复制为 `<name>_bilingual.epub`。重跑同一条命令会复用提取结果和已完成的翻译；想重新翻译删掉 `book_bilingual.md`，想改原文就在翻译之前编辑 `source.md`。不加该参数时 PDF 走旧路由，输出双语 `.txt` 和 `--pdf_layout` 的版式。

**什么时候用。** 想在电子书阅读器上读、带目录的论文或文字版书籍。该路由原样接受 Markdown 加载器的全部参数：`--use_context session`（推荐，PDF 会被提取成大量短块）、`--glossary`、`--parallel-workers`（不能与会话同用）、`--test` 用来便宜地看一眼。

```shell
# 先看一眼：提取后只翻译开头几个块
python3 make_book.py --book_name paper.pdf --to-epub --key ${key} --test
# 完整运行
python3 make_book.py --book_name paper.pdf --to-epub --key ${key} --use_context session
# 扫描版 PDF，或者表格要紧的文字版 PDF
python3 make_book.py --book_name scan.pdf --to-epub --pdf-ocr --key ${key} --use_context session
# 只要一章：第 12 到 30 页，成书是 paper_pages-12-30_bilingual.epub
python3 make_book.py --book_name paper.pdf --to-epub --pages 12-30 --key ${key} --use_context session
# 中文扫描件：告诉 OCR 模型要认的文字
python3 make_book.py --book_name scan.pdf --to-epub --pdf-ocr --ocr-lang iso:zh --key ${key} --use_context session
```

- `--pdf-ocr` 读取**没有文字层**的页面，也就是扫描件。不加它时这样的页面会被拒绝，绝不会被悄悄跳过。默认关闭：原生数字版 PDF 本来就能读，OCR 会让耗时翻上几倍，读到的东西却没有变化。版面、标题和表格识别无论加不加它都会运行——OCR 并不是提取质量的来源。
- `--device` 决定模型在哪里运行：`auto`（默认）自动检测加速器——NVIDIA CUDA，或本机安装下 Apple 芯片的 MPS——没有时自行回退到 CPU。`--device cpu` 强制用处理器。**CPU 是完整支持的，产出的文字完全一样**，只是更慢，区别仅此而已。在无法提供 CUDA 的机器或 PyTorch 构建上使用 `--device cuda` 会被明确拒绝，并区分这两种情况。
- `--ocr-lang` 指定 OCR 引擎在没有文字层的页面上识别的语言，逗号分隔，可以写通用的 `iso:` 标签（`iso:zh-Hans` 简体、`iso:zh-Hant` 繁体、`iso:ja`、`iso:ko`、`iso:en`），也可以写引擎自己的代码。引擎由 `--ocr-engine` 决定（见下条），每次 OCR 运行都会打印用了哪个引擎、哪些语言。rapidocr 默认认中文和英文，一次只用一种语言（取第一个代码），所以日文、韩文、西里尔或阿拉伯文的扫描件需要这个参数，不加会识别成空白或错字；遇到扫描页而没有这个参数时，运行会提醒。Mac 上的引擎 ocrmac 默认认英、西、法、德文，所以在 Mac 上读中文扫描件要加 `--ocr-lang iso:zh`。rapidocr 和 ocrmac 不下载任何东西；easyocr 在某种语言第一次使用时下载模型。引擎没有模型的代码在读任何页面之前就被拒绝，消息里附引擎自己的列表。文字版 PDF 上它不起作用；换语言重跑扫描件，或改变 `--pdf-ocr`，都会重新提取。
- `--ocr-engine`（需同时加 `--pdf-ocr`）选择 OCR 引擎：`auto`（默认）依次取已安装的 ocrmac、rapidocr、easyocr。`pdf` 依赖自带 rapidocr，在 macOS 上还带苹果自己的 ocrmac，所以 Mac 上是 ocrmac，其他系统是 rapidocr；`easyocr` 需要 `pip install easyocr`，`tesseract` 需要 PATH 上的 tesseract 程序。指定的引擎没有安装时，在读任何页面之前就会被拒绝。怎么选：[选哪个 OCR 引擎](https://yihong0618.github.io/bilingual_book_maker/features/pdf-ocr-engines/)。
- `--ocr-replace-layer`（需同时加 `--pdf-ocr`）让 OCR 引擎重读每一页，丢弃 PDF 自带的文字层。默认关闭：已有文字层会保留。实测它不如完好的文字层，只在文字层本身有误时使用。引擎在某页什么也没读到时，该页留空，终端会逐页指出。
- `--pages` 只读指定的页，从 1 数起（`12-30`，或 `1,3,5-7`）；PDF 其余部分不进书，也不会被提取或付费。页码选择会写进文件名，所以单章运行和整本运行并排放着，不会互相覆盖：`<name>_pages-12-30_book/` 和 `<name>_pages-12-30_bilingual.epub`。用同一选择重跑会续用那个工作目录。目录只剩这些页里的标题；选择从某一节中间开始时，第一个标题之前的正文会得到一个以页码命名的标题（`Page 12`），在翻译前就写进 `source.md`，想改名就在那里改。
- **行间公式以图片保留。** 解析器能找到公式但读不出它，所以每个公式都会从页面上裁下来，放回原来的位置；公式周围的正文照常翻译，公式本身不翻译。数学书能走通这条路全靠这一点——否则每个行间公式都只是一个 `<!-- formula-not-decoded -->` 占位符，数学内容会整个消失。它不需要模型、不联网，耗时可以忽略。`--no-formula-images` 可以关掉它，退回占位符。段落**行内**的数学不属于公式区域，不在覆盖范围内：扫描件上它是 OCR 认成什么就是什么。
- **插图按 PDF 自身页面尺寸的 200 DPI 绘制。** `--pdf-image-dpi N`（72–600）可以改：标签很小的图用 300，小一点的书用 150。换个值重跑只重绘插图，提取和翻译都保留。
- **视觉模型可以纠正版面识别器的区块角色**（`--img-model MODEL`，或 provider 条目里的 `img_model`；不指定就不用，绝不会悄悄拿本次运行的翻译模型来看图；`--img-model none` 关掉 provider 里指定的图像模型）。docling 有时把作者行当成标题、把代码清单的行当成脚注、把图题当成小节；加上这个参数后，每一页会连同识别器画出的框一起交给指定模型，由它给每个区块一个角色（正文、标题、书名、图题、脚注、代码，或弃权）。采纳的答案在导出前应用，目录和代码块因此正确；文字本身从不改写。需要一个接受图片输入的 OpenAI 兼容端点（本次运行的端点，或 `--img-base-url URL` 加 `--img-key KEY`；只探测一次）；模型弃权或端点看不到页面时，保留识别器原来的标签并在终端说明。在 gpt-5.6-luna 上测得：66 处已登记的标签错误修好 40 处，每页约 3k 提示词 token。
- 需要：**`pdf` extra**，它不在基础安装里。已发布的包里还没有这个路由，请从代码库安装——`pip install ".[pdf]"`，已经装好的 PyTorch 会被沿用。它会带来 docling 和 PyTorch，所以在没有 NVIDIA 显卡的 Linux 上请从 PyTorch 的 CPU 源安装（约 380 MB，而不是约 3.2 GB）；而在**有 NVIDIA 显卡的 Windows** 上要从 CUDA 源安装——PyPI 的 Windows wheel 只有 CPU 版，直接装会不声不响地让你留在处理器上，这种情况还需要装 NVIDIA 驱动（<https://www.nvidia.com/en-us/drivers/>）。macOS 上没得选。模型本身（约 500 MB）在首次运行时下载。**[安装 PDF 依赖](https://yihong0618.github.io/bilingual_book_maker/installation-pdf/) 给出了每种情况的准确命令。** 另外 PATH 中要有 [Pandoc](https://pandoc.org/installing.html) **3.1.12 或更新版本**（`pandoc -v` 检查；Ubuntu 24.04 和 Debian 13 的 apt 版本太旧，请从 pandoc.org 下载发行版）。**不需要 Java**——这条路由曾经用过的 Java 引擎已于 2026 年 9 月退役。缺哪个，都会在打开 PDF 之前被拒绝，消息里指明是哪一个。

**注意事项。**

- **付费翻译整本之前先读 `source.md`**，至少读标题：它们会变成目录。标题识别在论文上不错，在其他生成器上要弱得多；Word 导出的 PDF 可能几乎没有标题。在工作目录里改好 Markdown 再重跑，提取不会重做。
- 图表保留为图片，图中标注不翻译。一页提取出的文字远超印刷页容量时会警告，检查那一页。
- 提取器不转义正文里的 Markdown 语法。含 `\s`、`[u](y)` 或 `<k>` 的句子可能在翻译前被当作原始 TeX、缺失的链接目标或原始 HTML 而拒绝；消息会指出是哪一块。在 `source.md` 里转义后重跑。
- EPUB 不带 `bbm_translation_metadata.json`，也不内嵌术语表（书由 Pandoc 生成），`--no_disclosure` 在该路由上暂未生效：署名行总会加上。`--glossary-auto` 只在压缩发生时学习，短论文在默认预算下学不到任何东西。
- 除 `--to-epub` 外的每个 PDF 参数在没走该路由时都会被报告为忽略；在非 PDF 书上加 `--to-epub` 会停止运行。

该路由仍是实验性的：只在 arXiv 论文和少数几种其他生成器的 PDF 上核过，并未覆盖所有 PDF 形态。欢迎提 issue 和 PR；能分享的话请附上 PDF，或者 `source.md` 里出错的那一页。

对结果的预期要按格式来定。PDF 是页面描述，不是文档：它只存字形和坐标，不知道什么是段落、标题、分栏和阅读顺序，所有提取器都只能把结构猜回来。能从中得到一本可重排、目录能用的双语 EPUB，已经是很好的结果；某个标题差了一级、某张表格变成了正文，是格式本身的局限，不是这次运行出了错，两者在 `source.md` 里改一下也就一分钟的事。

![一篇 arXiv 论文的阅读版：按标题生成的目录、双语正文、保留为图片的图表](./docs/img/pdf_reading_edition.webp)

更多：[PDF 转双语 EPUB](https://yihong0618.github.io/bilingual_book_maker/features/pdf-to-epub/)、[PDF 推荐设置](https://yihong0618.github.io/bilingual_book_maker/features/recommended-pdf/)、[选哪个 OCR 引擎](https://yihong0618.github.io/bilingual_book_maker/features/pdf-ocr-engines/)。

## 参数说明

- `--model`:

  接口所用的模型 ID，按接口自己的拼写。openai 格式下默认 `gpt-5.6-luna`。第二列是该 ID 需要的 `--api_format`：

  | 模型 | `--api_format` | 说明 |
  |------|---------------|------|
  | `gpt-5.6-luna` | `openai` | 默认值，OpenAI 官方地址 |
  | `claude-sonnet-4-6` | `anthropic` | Anthropic 官方地址 |
  | `gpt-4o-mini` | `openai` | OpenAI |

  旧的预设值仍然可以写，会被改写成真实模型 ID 并打印说明，对照表见[从旧参数迁移](https://yihong0618.github.io/bilingual_book_maker/migration/)。其他任何接口：`--api_base <url> --key <key> --model <id>`，或一条 `--provider` 配置（见「自定义 API Provider」章节）。

- `--key`:

  接口的 API key。不写时依次读取 `$BBM_API_KEY`，再读取该格式自己的变量。同 `--api_key` 。

- `--api_format`:

  接口说的 API。省略时自动推断：`anthropic.com` 的地址，或没写 `--api_base` 且模型 ID 含 `claude`，视为 `anthropic`；其余为 `openai`。推断不对、想直接点名某一家而不写地址、或者要选引擎时才需要写。

  | 格式 | key | 说明 |
  |------|-----|------|
  | `openai`（默认） | 需要：`--key`，或 `$BBM_API_KEY`、`$OPENAI_API_KEY`；本地地址（如 Ollama）不需要 | 任何 OpenAI 兼容接口：OpenAI 官方、OpenRouter、Ollama…… 地址写在 `--api_base` |
  | `anthropic` | 需要：`--key`，或 `$BBM_API_KEY`、`$ANTHROPIC_API_KEY` | Anthropic 官方，以及说 Messages API 的网关 |
  | `gemini` | 需要：`--key`，或 `$BBM_API_KEY`、`$BBM_GOOGLE_GEMINI_KEY`、`$GEMINI_API_KEY` | Gemini 官方接口，默认 `gemini-flash-latest`；`--interval` 控制节奏 |
  | `qwen` | 需要：`--key`，或 `$BBM_API_KEY`、`$BBM_QWEN_API_KEY`、`$DASHSCOPE_API_KEY` | 百炼上的 Qwen-MT，默认 `qwen-mt-turbo`；读 `--source_lang` |
  | `groq` | 需要：`--key`，或 `$BBM_API_KEY`、`$BBM_GROQ_API_KEY`、`$GROQ_API_KEY` | GroqCloud；必须写 `--model` |
  | `xai` | 需要：`--key`，或 `$BBM_API_KEY`、`$BBM_XAI_API_KEY`、`$XAI_API_KEY` | xAI；必须写 `--model` |
  | `litellm` | 本机代理不需要；否则 `--key` 或 `$LITELLM_MASTER_KEY` | LiteLLM 代理，不写 `--api_base` 就是 `http://localhost:4000`；必须写 `--model` |
  | `codex` | 不需要：`codex login`（Codex CLI） | 本地 `codex app-server` 侧车，消耗 ChatGPT/Codex 套餐额度，默认 `gpt-5.6-luna` |
  | `orcarouter` | 需要：`--key` 或 `$BBM_ORCAROUTER_API_KEY` | 使用OrcaRouter |
  | `google` | 不需要 | 免费谷歌翻译 |
  | `caiyun` | 需要：`--key` 或 `$BBM_CAIYUN_API_KEY` | 彩云小译 |
  | `deepl` | 需要：`--key` 或 `$BBM_DEEPL_API_KEY` | DeepL（付费） |
  | `deeplfree` | 不需要 | DeepL 免费版 |
  | `tencent` | 不需要 | 腾讯交互翻译，免费 |
  | `customapi` | 不需要 |  `{text, source_lang, target_lang}` 格式的API |

- `--interval`: 请求之间等待的秒数，例如 `--interval 0.1` 就是 100ms。只有 `--api_format gemini` 会按它控制节奏，其余路线忽略。默认 `0.01`。

- `--test`:

  如果大家没付费可以加上这个先看看效果（有 limit 稍微有些慢）。

- `--test_num`:

  配合 `--test` 指定测试翻译的文本单元数量，默认 10。

- `--language`: 指定目标语言

  - 可以写语言标签（`--language zh-hant`）、语言名（`--language "Traditional Chinese"`），或用 `--language "zh-hant:Traditional Chinese"` 同时指定两者——冒号前的标签用于 JSON 结构化输出字段名，冒号后的名字是发给模型的说法。预设值 `zh-hans`。另见[可用标签](./docs/languages.md)。

- `--source_lang`: 源语言。写了就会附加提示词（"Translate from English"），在 `--api_format qwen`（请求里就是一对语言）和 `--api_format customapi` 还会写进请求本身；默认自动检测。

- `--proxy`

  方便中国大陆的用户在本地测试时使用代理，传入类似 `http://127.0.0.1:7890` 的字符串

- `--resume`

  手动中断后，加入命令可以从之前中断的位置继续执行。与`--parallel-workers` 互斥。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google --resume
  ```

- `--translate-tags`

  指定需要翻译的标签，使用逗号分隔多个标签。epub 由 html 文件组成，默认情况下，只翻译 `<p>` 中的内容。例如: `--translate-tags h1,h2,h3,p,div`

- `--plan-classify`（仅 epub）、`--plan-dry-run`、`--plan-min-coverage`、`--max-batch-units`：

  计划模式：整本书切分后由模型决定哪些标签签名要翻译。EPUB 默认开启；取值、预览和注意事项见[计划模式](#计划模式)。

- `--exclude-translate-tags`:

  指定不翻译其内部内容的 HTML 标签，多个标签用逗号分隔，默认 `sup,code`。
  例如 `--exclude-translate-tags code,pre`；传入空字符串
  `--exclude-translate-tags ""` 可取消默认排除。

- `--api_base ${url}`

  如果你遇到了墙需要用 Cloudflare Workers 替换 api_base 请使用 `--api_base ${url}` 来替换。
  **请注意，此处你输入的 api 应该是'`https://xxxx/v1`'的字样，域名需要用引号包裹**

- `--allow_navigable_strings`

  如果你想要翻译电子书中的无标签字符串，可以使用 `--allow_navigable_strings` 参数，会将可遍历字符串加入翻译队列。

- `--prompt`

  如果你想调整 prompt，你可以使用 `--prompt` 参数。有效的占位符包括 `{text}` 和 `{language}`。你可以用以下方式配置 prompt:

  - 如果您不需要设置 `system` 角色，可以这样：`--prompt "Translate {text} to {language}"` 或者 `--prompt prompt_template_sample.txt`

  - 如果您需要设置 `system` 角色，可以使用以下方式配置：`--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'`，或者 `--prompt prompt_template.json`。

  - 第三个键 `style` 是关于文风的常驻指令——语域、语气、用词——只在**每个窗口开始时**随其他常驻指令发出一次。

  - 你也可以用环境以下环境变量来配置 `system` 和 `user` 角色 prompt：`BBM_CHATGPTAPI_USER_MSG_TEMPLATE` 和 `BBM_CHATGPTAPI_SYS_MSG`。
  该参数可以是提示模板字符串，也可以是模板 `.txt` 文件的路径。

  - 示例 JSON 文件可以在 [./prompt_template.json](./prompt_template.json) 找到。

- `--batch_size`

  指定批量翻译的行数(默认行数为 10，目前只对 txt 生效)

- `--accumulated_num`:

  达到累计token数开始进行翻译。
  例如，如果您使用`--accumulated_num 1600`，则可能会输出2200个令牌，另外200个令牌用于系统指令（system_message）和用户指令（user_message），1600+2200+200 = 4000，在某些本地模型中token接近极限。你必须选择一个自己合适的值，我们无法在发送之前判断是否达到限制。

  在 EPUB 计划模式下这是每个请求的 token 预算：连续的段落（不限长度）合并进同一个请求，直到累计 `N` 个 token。传 `1` 可关闭合并，即每分段单独发送。

- `--use_context`:
  使用上下文模式翻译。

  - `--context_paragraph_limit`:

    使用`--use_context`选项时，使用`--context_paragraph_limit`设置上下文段落数限制（仅 window 模式）。

- `--use_context session`、`--context-compact-at`、`--no-context-compact`：

  会话模式：一份不断增长的历史代替重发的窗口，达到预算时压缩成交接报告。何时划算、何时不划算见[会话模式](#会话模式)。

- `--glossary` / `--terminology`:

  一个 `term → translation` 术语文件（每行一条，`#` 之后是注释，txt格式）。
  仅 openai 系与 codex 路由、且书籍为 EPUB、Markdown 或 PDF 时生效。
  
  钉住一个术语就等于让译文照此表述，所以只钉你能负责的译法。

  - `--glossary-auto on|off`:

    格式化保留交接报告中的译名，使跨窗口的重复人名、术语保持一致。仅 session 模式。

- `--temperature`:

  设置 openai / anthropic 格式的采样温度（旧模型）。
  如 `--temperature 0.7`。

- `--block_size`:

  使用`--block_size`将多个段落合并到一个块中。这可能会提高准确性并加快处理速度，但可能会干扰原始格式。必须与`--single_translate`一起使用。
  例如：`--block_size 5 --single_translate`。

- `--single_translate`:

  使用`--single_translate`只输出翻译后的图书，不创建双语版本。

- `--no_disclosure`:

  epub 输出会在书籍简介下方加 "Translated by gpt-5.6-luna, 2026."；附带该参数则不加。同时关闭翻译元数据（`--translation-metadata`，包含模型、日期和词汇表）。

- `--translation_style`:

  为 EPUB 译文应用完整 CSS，例如
  `--translation_style "color: #808080; font-style: italic;"`。

- `--translation_color`:

  只设置 EPUB 译文颜色的快捷参数，例如 `--translation_color "#1e90ff"`。
  如果同时传入 `--translation_style`，完整样式优先。

- `--pdf_layout {none,top-bottom,side-by-side,all}`:

  为 PDF 输入选择额外生成的双语 PDF 版式。默认 `none` 不额外生成 PDF；
  `all` 会同时尝试上下对照和左右对照。双语 TXT 和 EPUB 输出不受该参数影响。

- `--to-epub`、`--pdf-ocr`、`--ocr-lang`、`--ocr-engine`、`--ocr-replace-layer`、`--pages`、`--device`、`--pdf-image-dpi`（仅限 PDF）：

  PDF 阅读版：文字层变成 Markdown，Markdown 变成带导航的双语 EPUB。工作目录、OCR 以及 `source.md` 里该核对什么，见 [PDF 转双语 EPUB](#pdf-转-双语-epub-实验性)。

- `--sentence_mode`:

  将 EPUB 的每个段落拆成句子逐句翻译，而不是整段翻译。与 EPUB 计划模式不兼容。

- `--batch` / `--batch-use`:

  使用 ChatGPT Batch API 的两阶段 EPUB 流程。先用 `--batch` 提交任务，再以
  `--batch-use` 重跑以等待并使用结果。二者都与计划模式不兼容。

- `--parallel-workers`:

  并行处理 EPUB 章节或 Markdown 批次/分段，默认 1，建议 2–4。其他输入加载器目前
  虽然接受这个共享参数，但不会并行执行。EPUB 的 `--use_context` 在并行模式下是
  章节内上下文，而不是全书共享上下文。

- `--quiet`:

  关闭 EPUB 进度条和逐段原文/译文输出，但保留报告与错误。适合日志文件和 Agent
  非交互运行。

- `--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"`:

  - 重新翻译，从 start_str 到 end_str 的标记:

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'
  ```

  - 只重新翻译包含 `start_str` 的标签时，第四个参数传入空字符串：

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''
  ```

- `--no-thinking`:

  让模型回答前不要先思考——翻译一段散文，思考带不来质量，只多花 token 和时间。各家
  端点关闭思考的字段名互不相同且互相拒绝，因此在 OpenAI 请求格式的路径上，字段由端点
  自己的报错协商得出并在本次运行中记住；若全部被拒，会提示一次并照常继续（不带该字
  段）。`anthropic` 路径上固定为 `thinking: {"type": "disabled"}`。`codex` 路径会直接
  拒绝该选项——它以子进程方式调用 codex CLI，没有可写入的请求体。你在 `--extra_body`
  里自己写的字段优先于本选项。

  ```shell
  python3 make_book.py --book_name book.epub --no-thinking
  ```

- `--extra_body`:

  以 JSON 字符串向 ChatGPT/OpenAI 衍生请求路径透传额外参数，包括 OpenAI 请求格式的
  自定义 provider，还有 `anthropic` 路径。
  例：

  ```shell
  python3 make_book.py --book_name book.epub --extra_body '{"chat_template_kwargs":{"enable_thinking":false}}'
  ```

- `--extra_headers`:

  以 JSON 字符串为每次请求追加 HTTP 头，适用范围同上。值必须是字符串。

  ```shell
  python3 make_book.py --book_name book.epub --key ${openrouter_key} --api_base https://openrouter.ai/api/v1 --model anthropic/claude-haiku-4.5 --extra_headers '{"HTTP-Referer":"https://example.com","X-Title":"bilingual_book_maker"}'
  ```

  常见写法，供参考：

  ```shell
  # openai 路径 —— 关闭本地/vLLM chat template 的思考块
  --extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
  # openai 路径（chat completions）—— 推理力度与 token 上限，二者都没有独立 flag（是否支持视模型而定）
  --extra_body '{"reasoning_effort": "low", "max_completion_tokens": 2000}'
  # anthropic 路径 —— 关闭扩展思考；对翻译来说思考主要带来偏离原文的风险，
  # 且收益甚微
  --extra_body '{"thinking": {"type": "disabled"}}'

  # OpenRouter 归属标识（显示在其后台）
  --extra_headers '{"HTTP-Referer": "https://example.com", "X-Title": "bilingual_book_maker"}'
  # 网关自身的鉴权或路由头（其值不会进日志）
  --extra_headers '{"X-API-Key": "sk-gateway-..."}'
  ```

- `--provider`:

  使用 `bbm_providers.json` 中定义的自定义 provider，`--model` 指定其下的模型。详见上方「自定义 API Provider」章节。

- `--api_key`:

  同 `--key` 。

### 示范用例

**如果使用 `pip install bbook_maker`，以下命令都可以改成 `bbook_maker args`。**

```shell
# 如果你想快速测一下
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --test --use_context session

# 或翻译完整本书
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --language zh-hans --use_context session

# 用 Gemini 翻译整本书
python3 make_book.py --book_name test_books/animal_farm.epub --api_format gemini --key ${gemini_key} --model gemini-flash-latest

# 指定环境变量来略过 --key
export OPENAI_API_KEY=${your_api_key}

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key} --language ja

# Use the Claude model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key} --language ja

# Use the CustomAPI model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format customapi --api_base ${custom_api} --language ja

# 使用自定义 provider（如 SiliconFlow）
python3 make_book.py --book_name test_books/animal_farm.epub --provider siliconflow --language ja --use_context session

# 在多个模型之间轮换
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model_list gpt-5-mini,gpt-4o-mini

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# 计划模式：自动发现要翻译的内容（诗歌、列表、无 <p> 包裹的正文都能覆盖）
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify all

# 修改prompt
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# 或者
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"
# 翻译 txt 文件
python3 make_book.py --book_name test_books/the_little_prince.txt --test
# 聚合多行翻译 txt 文件
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20


# 使用彩云小译翻译(彩云api目前只支持: 简体中文 <-> 英文， 简体中文 <-> 日语)
# 彩云提供了测试token（3975l6lr5pcbvidl6jl2）
# 你可以参考这个教程申请自己的token (https://bobtranslate.com/service/translate/caiyun.html)
python3 make_book.py --api_format caiyun --key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub
# 可以在环境变量中设置BBM_CAIYUN_API_KEY，略过--key
export BBM_CAIYUN_API_KEY=${your_api_key}
```

更加小白的示例

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1' --use_context session

# 有可能你不需要 python3 而是python
python make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1' --use_context session
```

[演示视频](https://www.bilibili.com/video/BV1XX4y1d75D/?t=0h07m08s)
[演示视频 2](https://www.bilibili.com/video/BV1T8411c7iU/)

使用 Azure OpenAI service

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name' --use_context session

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name' --use_context session
```

## Docker

如果不想配置本地环境，可以直接使用 [Docker](https://www.docker.com/)。每次合并到 `main`（对应 `latest` 标签）以及每次发布版本标签时，都会自动构建镜像并发布到 GitHub Container Registry：

```shell
docker pull ghcr.io/yihong0618/bilingual_book_maker:latest
```

把书所在的文件夹挂载到 `/book`，其余参数与 `make_book.py` 完全一致（所有命令行参数都支持），翻译结果会写回同一文件夹：

```shell
# Linux / macOS
export folder_path=/path/to/your/books
export book_name=animal_farm.epub
export openai_key=sk-XXX
export language=zh-hans   # 语言列表见 book_maker/utils.py

docker run --rm -v "${folder_path}":/book ghcr.io/yihong0618/bilingual_book_maker:latest --book_name "/book/${book_name}" --key "${openai_key}" --language "${language}"
```

```powershell
# Windows PowerShell
$folder_path="C:\Users\user\mybook"
$book_name="animal_farm.epub"
$openai_key="sk-xxx"
$language="zh-hans"

docker run --rm -v ${folder_path}:/book ghcr.io/yihong0618/bilingual_book_maker:latest --book_name "/book/$book_name" --key $openai_key --language $language
```

例如，走免费的 Google 翻译路线做个不需要任何 key 的快速测试：

```shell
docker run --rm -v /home/user/my_books:/book ghcr.io/yihong0618/bilingual_book_maker:latest --book_name /book/animal_farm.epub --api_format google --test --test_num 1 --language zh-hant
```

容器以 root 运行，所以往挂载的文件夹里写东西总是可以的；在 Linux 上写出的文件归 root 所有（事后 `chown` 一下，或者加 `--user $(id -u)`）。API key 也可以用环境变量传入（`-e OPENAI_API_KEY=sk-XXX`）来代替 `--key`。

**Docker 里的 PDF 路由是 `pdf` 标签。** 默认镜像（`latest`，也发布为 `basic`）没有 Pandoc 和 PDF 相关的包，跑不了这条路由，所以只有几百 MB。`ghcr.io/yihong0618/bilingual_book_maker:pdf` 把 Pandoc 和 PDF 运行时都加上——docling 和 PyTorch，在 amd64 上是 CUDA 版，有好几个 GB，`--to-epub` 加不加 `--pdf-ocr` 都能跑：

```shell
docker run --rm -v "${folder_path}":/book -v bbm-models:/root/.cache ghcr.io/yihong0618/bilingual_book_maker:pdf --book_name /book/paper.pdf --to-epub --key "${openai_key}" --use_context session
```

具名卷 `bbm-models` 让 docling 模型在多次运行之间保留下来；模型在第一次 `--to-epub` 运行时下载。用之前要知道两个限制：

- **GPU** 指的只有 NVIDIA CUDA，**Linux 和 Windows 都可以**，macOS 不行。Linux 上宿主机装好 NVIDIA Container Toolkit，再加 `--gpus all`；torch 的 wheel 自带 CUDA 运行时，别的不用装。Windows 上通过 Docker Desktop 的 **WSL2 后端**同样可用，NVIDIA 驱动装在 Windows 本身而不是 WSL 里面，同样不需要 CUDA Toolkit；Windows 容器模式则做不到。macOS 上容器不管传什么都只用 CPU，因为 Docker 跑在一个看不见 Metal 加速器的 Linux 虚拟机里。想用 Apple 芯片加速，请在本机直接运行。
- **arm64 上这个镜像没有 GPU**，哪怕机器上有显卡。镜像两种架构都发布，但 PyPI 的 PyTorch 只有 x86_64 才是 CUDA 版——2.7.1 在那边是 821.0 MB，而 aarch64 只有 98.9 MB，完全不含 CUDA kernel。所以带显卡的 arm64 Linux 主机（GH200、Jetson）默认拉到的是 arm64 镜像，`--gpus` 传多少都还是跑在处理器上。那里要加 `--platform linux/amd64` 才能拉到 CUDA 镜像。
- **codex 路由**两个镜像里都没有：它驱动的是宿主机上已登录的 `codex` 程序，程序和登录状态都不在容器里。Docker 里请用 API 路由。

如果想自己构建镜像而不是拉取：

```shell
docker build --tag bilingual_book_maker .
docker run --rm -v /path/to/your/books:/book bilingual_book_maker --book_name /book/animal_farm.epub --key sk-XXX --language zh-hans
```

## 注意

1. Free trail 的 API token 有所限制，如果想要更快的速度，可以考虑付费方案
2. 欢迎提交 PR

# 感谢

- @[yetone](https://github.com/yetone)

# 贡献

- 任何 issue PR 都欢迎
- Issue 中有些 TODO 没做的都可以选
- 提交代码前请先执行 `black make_book.py` [^black]

# 其它推荐项目

- 书译 BookTranslator -> [Book Translator](https://www.booktranslator.app)

## 赞赏

谢谢就够了

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: https://platform.openai.com/account/api-keys
[^black]: https://github.com/psf/black
