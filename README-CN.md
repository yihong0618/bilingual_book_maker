# bilingual_book_maker

bilingual_book_maker 是一个 AI 翻译工具，使用 ChatGPT 帮助用户制作多语言版本的 epub/txt/md/srt 文件和图书。该工具仅适用于翻译进入公共版权领域的 epub/txt 图书，不适用于有版权的书籍。请在使用之前阅读项目的 **[免责声明](./disclaimer.md)**。

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## 准备

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md books
3. 能正常联网的环境或 proxy
4. Python 3.10+

## 快速开始

本地放了一个 `test_books/animal_farm.epub` 给大家测试

```shell
pip install -r requirements.txt
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --test
或
pip install -U bbook_maker
bbook_maker --book_name test_books/animal_farm.epub --openai_key ${openai_key} --test
```

## 翻译服务

- 使用 `--openai_key` 指定 OpenAI API key，如果有多个可以用英文逗号分隔(xxx,xxx,xxx)，可以减少接口调用次数限制带来的错误。
  或者，指定环境变量 `BBM_OPENAI_API_KEY` 来略过这个选项。
- 默认用了 [GPT-3.5-turbo](https://openai.com/blog/introducing-chatgpt-and-whisper-apis) 模型，也就是 ChatGPT 正在使用的模型。

* DeepL

  使用 DeepL 封装的 api 进行翻译，需要付费。[DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) 来获得 token

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model deepl --deepl_key ${deepl_key}
  ```

* DeepL free

  使用 DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model deeplfree
  ```

* Claude

  使用 [Claude](https://console.anthropic.com/docs) 模型进行翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model claude --claude_key ${claude_key}
  ```

* 谷歌翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model google
  ```

* 彩云小译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model caiyun --caiyun_key ${caiyun_key}
  ```

* Gemini

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model gemini --gemini_key ${gemini_key}
  ```

* Qwen

  使用 [Qwen](https://www.aliyun.com/product/dashscope) 模型进行翻译，支持 qwen-mt-turbo 和 qwen-mt-plus 模型。

  使用 `--source_lang` 指定源语言，留空为自动检测。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --qwen_key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
  python3 make_book.py --book_name test_books/animal_farm.epub --qwen_key ${qwen_key} --model qwen-mt-plus --language "Japanese" --source_lang "English"
  ```

* 腾讯交互翻译

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model tencentransmart
  ```

* [xAI](https://x.ai)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model xai --xai_key ${xai_key}
  ```

* [Ollama](https://github.com/ollama/ollama)

  使用 [Ollama](https://github.com/ollama/ollama) 自托管模型进行翻译。
  如果 ollama server 不运行在本地，使用 `--api_base http://x.x.x.x:port/v1` 指向 ollama server 地址

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --ollama_model ${ollama_model_name}
  ```

* [Groq](https://console.groq.com/keys)

  GroqCloud 当前支持的模型可以查看[Supported Models](https://console.groq.com/docs/models)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --groq_key [your_key] --model groq --model_list llama3-8b-8192
  ```

* 自定义 API Provider

  内置模型不满足需求时，可以通过 JSON 配置文件自定义 provider。不需要改代码，就能使用任何 OpenAI 兼容的 API（DeepSeek、SiliconFlow、本地代理等）。

  在当前目录创建 `bbm_providers.json`（全局配置放在 `~/.bbm/providers.json`）：

  ```json
  {
    "providers": {
      "deepseek": {
        "api_style": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
        "env_key": "BBM_DEEPSEEK_API_KEY"
      },
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
  | `api_style` | 是 | 翻译器接口风格。支持：`openai`、`claude`、`gemini`、`qwen` |
  | `base_url` | 否 | API 地址。不填则使用该 api_style 的默认地址 |
  | `default_models` | 否 | 默认模型列表。不填则必须通过 `--model_list` 指定 |
  | `env_key` | 否 | 读取 API key 的环境变量名。不填则必须通过 `--api_key` 传入 |

  优先级：项目级 `./bbm_providers.json` 覆盖全局 `~/.bbm/providers.json`。

  `--provider` 和 `--model` 互斥，不能同时使用。

  ```shell
  python3 make_book.py --provider deepseek --api_key sk-xxx --book_name test_books/animal_farm.epub

  export BBM_DEEPSEEK_API_KEY=sk-xxx
  python3 make_book.py --provider deepseek --book_name test_books/animal_farm.epub

  python3 make_book.py --provider deepseek --api_key sk-xxx --model_list deepseek-reasoner --book_name test_books/animal_farm.epub
  ```

## 使用说明

- 翻译完会生成一本 `{book_name}_bilingual.epub` 的双语书
- 如果出现了错误或使用 `CTRL+C` 中断命令，不想接下来继续翻译了，会生成一本 `{book_name}_bilingual_temp.epub` 的书，直接改成你想要的名字就可以了

## 参数说明

- `--model`:

  指定翻译模型。默认：`chatgptapi`。各模型值及其行为：

  | 模型 | Key 来源 | 说明 |
  |------|---------|------|
  | `chatgptapi` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-3.5-turbo，自动检测 API 可用模型 |
  | `gpt4` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-4 系列，自动在可用变体间负载均衡 |
  | `gpt4omini` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-4o-mini |
  | `gpt4o` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-4o |
  | `gpt5mini` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-5-mini |
  | `o1preview` | `--openai_key` / `BBM_OPENAI_API_KEY` | o1-preview |
  | `o1` | `--openai_key` / `BBM_OPENAI_API_KEY` | o1 |
  | `o1mini` | `--openai_key` / `BBM_OPENAI_API_KEY` | o1-mini |
  | `o3mini` | `--openai_key` / `BBM_OPENAI_API_KEY` | o3-mini |
  | `openai` | `--openai_key` / `BBM_OPENAI_API_KEY` | **必须配合 `--model_list`**，可使用任意 OpenAI 兼容模型 |
  | `claude` 及已列出的 `claude-*` ID | `--claude_key` / `BBM_CLAUDE_API_KEY` | 只接受 `--help` 展示的精确内置值；未列出的 ID 需使用 `--provider` 或 `openai` 路由 |
  | `gemini` | `--gemini_key` / `BBM_GOOGLE_GEMINI_KEY` | Gemini Flash，支持 `--model_list` 自定义 |
  | `geminipro` | `--gemini_key` / `BBM_GOOGLE_GEMINI_KEY` | Gemini Pro |
  | `groq` | `--groq_key` / `BBM_GROQ_API_KEY` | **必须配合 `--model_list`** |
  | `xai` | `--xai_key` / `BBM_XAI_API_KEY` | Grok |
  | `qwen-mt-turbo` | `--qwen_key` / `BBM_QWEN_API_KEY` | 通义千问快速翻译模型 |
  | `qwen-mt-plus` | `--qwen_key` / `BBM_QWEN_API_KEY` | 通义千问高质量翻译模型 |
  | `google` | 无需 key | 免费谷歌翻译 |
  | `caiyun` | `--caiyun_key` / `BBM_CAIYUN_API_KEY` | 彩云小译 |
  | `deepl` | `--deepl_key` / `BBM_DEEPL_API_KEY` | DeepL（付费） |
  | `deeplfree` | 无需 key | DeepL 免费版 |
  | `tencentransmart` | 无需 key | 腾讯交互翻译，免费 |
  | `customapi` | `--custom_api` / `BBM_CUSTOM_API` | 自定义翻译 API |

  上表未列出的 OpenAI 兼容 API，请使用 `--provider`（见「自定义 API Provider」章节）。

- `--test`:

  如果大家没付费可以加上这个先看看效果（有 limit 稍微有些慢）。

- `--test_num`:

  配合 `--test` 指定测试翻译的文本单元数量，默认 10。

- `--language`: 指定目标语言

  - 例如： `--language "Simplified Chinese"`，预设值为 `"Simplified Chinese"`.
  - 请阅读 helper message 来查找可用的目标语言： `python make_book.py --help`

- `--proxy`

  方便中国大陆的用户在本地测试时使用代理，传入类似 `http://127.0.0.1:7890` 的字符串

- `--resume`

  手动中断后，加入命令可以从之前中断的位置继续执行。

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model google --resume
  ```

- `--translate-tags`

  指定需要翻译的标签，使用逗号分隔多个标签。epub 由 html 文件组成，默认情况下，只翻译 `<p>` 中的内容。例如: `--translate-tags h1,h2,h3,p,div`

  **计划模式（`--plan-classify`，仅 epub）**：不再由你挑选标签，而是把书中每一个文本节点要么归入某个翻译单元，要么按明确的理由跳过并计入报告（隐藏内容、page-list 目录、纯符号、链接等）。如果一本书的正文并不放在 `<p>` 里——例如每行一个 `<div>` 或 `<blockquote>` 的诗歌，按默认标签会被静默漏掉——那就该用这个模式。连续的短诗行会被合并成诗节窗口（最多 `--poetry-group-size` 行，默认 8 行）一次请求翻译，让模型能看到相邻诗行的上下文。计划模式下 `--translate-tags` 会被忽略。

  取值决定由谁判断哪些标签签名值得翻译：

  - `none`（默认）：不建计划，照常翻译 `--translate-tags` 选中的标签。
  - `most`：翻译整个分区，不做分类。它不提出任何问题，因此不写计划 JSON，也会忽略已有的计划文件；它打印的账本里每个签名都记为明确的 `user` 决定，避免出现“没有人决定却被翻译”的内容。
  - `model`：先让一个 LLM 裁决**每一个**尚未决定的签名，然后继续翻译整本书。可用 `--plan-classify-model X` 指定分类用的模型——指定了就意味着此模式，且分类失败会中止而不是回退。若仍有签名没被裁决，运行会停下并把这些行交给 agent 流程，而不是按默认值直接翻译。
  - `agent`：不调用 API。写出计划 JSON，打印一段可以粘贴进 coding-agent 会话（Claude Code、Codex 等）的指引，然后**在翻译前停下**。按指引把每一行决定好之后，重跑同一条命令即可翻译。

  注意后两者在花费上的区别：`agent` 一定会停下；而 `model` 只要分类全部完成，就会在同一条命令里直接把整本书翻译完。想先小样试跑，请加 `--test --test_num 20`。

  只有 `--plan-classify`（或 `--plan-dry-run`）才会进入计划模式；进入后 `--translate-tags` 会被忽略，计划会对整本书做划分。

  - `--plan-dry-run`：打印按标签签名分组的覆盖率表格，写出 `<book>_plan.json` 后退出。不需要 API key，也不消耗额度。同时遵守 `--only_filelist` / `--exclude_filelist`。它写出的行全部处于未决状态——之后用 `model` 跑会由 LLM 裁决，用 `agent` 跑会把这些行交给 coding agent，你也可以自己改。
  - `<book>_plan.json`：一行要算“已决定”，必须同时填三个字段——`"action"`（`"translate"` 或 `"skip"`）、`"decided_by"`（自己手改就写 `"user"`）、以及说明这段文字是什么的 `"content_type"`。先命名再裁决，命名本身就是理由；没有它的判断无法复核，运行会直接拒绝。每行带最多 5 条真实 `samples`，不用解包 epub 也能判断。你已经做出的决定不会被覆盖；只有当设置变化引入了新的签名时，文件才会被重写以补上这些新行（想完全重新生成请先删除）。
  - `--plan-min-coverage`（默认 0.5）：如果计划覆盖的正文比例低于该阈值，计划模式会直接报错退出，而不是闷头只翻译一小部分。

  ```shell
  # 先免费预览会翻译哪些内容（不需要 key）
  python3 make_book.py --book_name my_book.epub --plan-dry-run
  # 翻译整个分区
  python3 make_book.py --book_name my_book.epub --openai_key ${key} --plan-classify most
  # 先让模型分流一遍版面装置（页眉、页码等）
  python3 make_book.py --book_name my_book.epub --openai_key ${key} --plan-classify model
  # 或交给 coding agent 判断（停下、打印指引，然后重跑）
  python3 make_book.py --book_name my_book.epub --openai_key ${key} --plan-classify agent
  ```

- `--exclude-translate-tags`:

  指定不翻译其内部内容的 HTML 标签，多个标签用逗号分隔，默认 `sup,code`。
  例如 `--exclude-translate-tags code,pre`；传入空字符串
  `--exclude-translate-tags ""` 可取消默认排除。

- `--book_from`

  选项指定电子阅读器类型（现在只有 kobo 可用），并使用 `--device_path` 指定挂载点。

- `--api_base ${url}`

  如果你遇到了墙需要用 Cloudflare Workers 替换 api_base 请使用 `--api_base ${url}` 来替换。
  **请注意，此处你输入的 api 应该是'`https://xxxx/v1`'的字样，域名需要用引号包裹**

- `--allow_navigable_strings`

  如果你想要翻译电子书中的无标签字符串，可以使用 `--allow_navigable_strings` 参数，会将可遍历字符串加入翻译队列，**注意，在条件允许情况下，请寻找更规范的电子书**

- `--prompt`

  如果你想调整 prompt，你可以使用 `--prompt` 参数。有效的占位符包括 `{text}` 和 `{language}`。你可以用以下方式配置 prompt:

  - 如果您不需要设置 `system` 角色，可以这样：`--prompt "Translate {text} to {language}"` 或者 `--prompt prompt_template_sample.txt`（示例文本文件可以在 [./prompt_template_sample.txt](./prompt_template_sample.txt) 找到）。

  - 如果您需要设置 `system` 角色，可以使用以下方式配置：`--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'`，或者 `--prompt prompt_template_sample.json`（示例 JSON 文件可以在 [./prompt_template_sample.json](./prompt_template_sample.json) 找到）。

  - 你也可以用环境以下环境变量来配置 `system` 和 `user` 角色 prompt：`BBM_CHATGPTAPI_USER_MSG_TEMPLATE` 和 `BBM_CHATGPTAPI_SYS_MSG`。
  该参数可以是提示模板字符串，也可以是模板 `.txt` 文件的路径。

- `--batch_size`

  指定批量翻译的行数(默认行数为 10，目前只对 txt 生效)

- `--accumulated_num`:

  达到累计token数开始进行翻译。gpt3.5将total_token限制为4090。
  例如，如果您使用`--accumulation_num 1600`，则可能会输出2200个令牌，另外200个令牌用于系统指令（system_message）和用户指令（user_message），1600+2200+200 = 4000，所以token接近极限。你必须选择一个自己合适的值，我们无法在发送之前判断是否达到限制

- `--use_context`:

  prompts the model to create a three-paragraph summary. If it's the beginning of the translation, it will summarize the entire passage sent (the size depending on `--accumulated_num`).
  For subsequent passages, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work. This improves consistency of flow and tone throughout the translation. This option is available for all ChatGPT-compatible models and Gemini models.

  模型提示词将创建三段摘要。如果是翻译的开始，它将总结发送的整个段落（大小取决于`--accumulated_num`）。
  对于后续的段落，它将修改摘要，以包括最近段落的细节，创建一个完整的段落上下文负载，包含整个翻译作品的重要细节。 这提高了整个翻译过程中的流畅性和语气的一致性。 此选项适用于所有ChatGPT兼容型号和Gemini型号。

  - `--context_paragraph_limit`:

    使用`--use_context`选项时，使用`--context_paragraph_limit`设置上下文段落数限制。

- `--temperature`:

  使用 `--temperature` 设置 `chatgptapi`/`gpt4`/`claude`模型的temperature值.
  如 `--temperature 0.7`.

- `--block_size`:

  使用`--block_size`将多个段落合并到一个块中。这可能会提高准确性并加快处理速度，但可能会干扰原始格式。必须与`--single_translate`一起使用。
  例如：`--block_size 5 --single_translate`。

- `--single_translate`:

  使用`--single_translate`只输出翻译后的图书，不创建双语版本。

- `--translation_style`:

  为 EPUB 译文应用完整 CSS，例如
  `--translation_style "color: #808080; font-style: italic;"`。

- `--translation_color`:

  只设置 EPUB 译文颜色的快捷参数，例如 `--translation_color "#1e90ff"`。
  如果同时传入 `--translation_style`，完整样式优先。

- `--pdf_layout {none,top-bottom,side-by-side,all}`:

  为 PDF 输入选择额外生成的双语 PDF 版式。默认 `none` 不额外生成 PDF；
  `all` 会同时尝试上下对照和左右对照。双语 TXT 和 EPUB 输出不受该参数影响。

- `--sentence_mode`:

  将 EPUB 的每个段落拆成句子逐句翻译，而不是整段翻译。与 EPUB 计划模式不兼容。

- `--batch` / `--batch-use`:

  使用 ChatGPT Batch API 的两阶段 EPUB 流程。先用 `--batch` 提交任务，再以
  `--batch-use` 重跑以等待并使用结果。二者都与计划模式不兼容。

- `--interval`:

  Gemini 请求间隔秒数，默认 `0.01`；其他模型路由不会使用此参数。

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

- `--extra_body`:

  以 JSON 字符串向 ChatGPT/OpenAI 衍生请求路径透传额外参数，包括 OpenAI 风格的
  自定义 provider 和 xAI。Claude、Gemini、Qwen、Groq 等其他翻译器目前会忽略该参数。例如：

  ```shell
  python3 make_book.py --book_name book.epub --extra_body '{"chat_template_kwargs":{"enable_thinking":false}}'
  ```

- `--provider`:

  使用 `bbm_providers.json` 中定义的自定义 provider。与 `--model` 互斥。详见上方「自定义 API Provider」章节。

- `--api_key`:

  自定义 provider 的 API key（与 `--provider` 配合使用）。也可通过配置文件中的 `env_key` 字段指定环境变量。

### 示范用例

**如果使用 `pip install bbook_maker`，以下命令都可以改成 `bbook_maker args`。**

```shell
# 如果你想快速测一下
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --test

# 或翻译完整本书
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --language zh-hans

# Or translate the whole book using Gemini
python3 make_book.py --book_name test_books/animal_farm.epub --gemini_key ${gemini_key} --model gemini

# 指定环境变量来略过 --openai_key
export OPENAI_API_KEY=${your_api_key}

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model deepl --deepl_key ${deepl_key} --language ja

# Use the Claude model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model claude --claude_key ${claude_key} --language ja

# Use the CustomAPI model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model customapi --custom_api ${custom_api} --language ja

# 使用自定义 provider（如 DeepSeek）
python3 make_book.py --book_name test_books/animal_farm.epub --provider deepseek --api_key sk-xxx --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# 计划模式：自动发现要翻译的内容（诗歌、列表、无 <p> 包裹的正文都能覆盖）
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify most

# 修改prompt
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# 或者
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"
# 翻译 kobo e-reader 中，來自 Rakuten Kobo 的书籍
python3 make_book.py --book_from kobo --device_path /tmp/kobo

# 翻译 txt 文件
python3 make_book.py --book_name test_books/the_little_prince.txt --test
# 聚合多行翻译 txt 文件
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20


# 使用彩云小译翻译(彩云api目前只支持: 简体中文 <-> 英文， 简体中文 <-> 日语)
# 彩云提供了测试token（3975l6lr5pcbvidl6jl2）
# 你可以参考这个教程申请自己的token (https://bobtranslate.com/service/translate/caiyun.html)
python3 make_book.py --model caiyun --caiyun_key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub
# 可以在环境变量中设置BBM_CAIYUN_API_KEY，略过--openai_key
export BBM_CAIYUN_API_KEY=${your_api_key}
```

更加小白的示例

```shell
python3 make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'

# 有可能你不需要 python3 而是python
python make_book.py --book_name 'animal_farm.epub' --openai_key sk-XXXXX --api_base 'https://xxxxx/v1'
```

[演示视频](https://www.bilibili.com/video/BV1XX4y1d75D/?t=0h07m08s)
[演示视频 2](https://www.bilibili.com/video/BV1T8411c7iU/)

使用 Azure OpenAI service

```shell
python3 make_book.py --book_name 'animal_farm.epub' --openai_key XXXXX --api_base 'https://example-endpoint.openai.azure.com' --deployment_id 'deployment-name'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --openai_key XXXXX --api_base 'https://example-endpoint.openai.azure.com' --deployment_id 'deployment-name'
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
