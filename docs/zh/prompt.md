# 调整提示词

要调整提示词，请使用 `--prompt` 参数。`user` 模板可用的占位符有 `{text}`（必填）、`{language}` 和 `{crlf}`（换行符，供无法携带换行的形式使用）。花括号里的其他任何内容都会在运行开始前被拒绝；要写字面的花括号，请用 `{{` 和 `}}`。配置提示词有以下几种方式：

- 如果不需要设置 `system` 角色的内容，可以简单地这样写：`--prompt "Translate {text} to {language}."` 或 `--prompt prompt_template_sample.txt`

        # prompt_template_sample.txt
        Translate the given text to {language}. Be faithful or accurate in translation. Make the translation readable or intelligible. Be elegant or natural in translation. If the text cannot be translated, return the original text as is. Do not translate person's name. Do not add any additional text in the translation. The text to be translated is: 
        {text}
        

- 如果需要设置 `system` 角色的内容，可以使用以下格式：`--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'` 或 `--prompt prompt_template.json`

        # prompt_template.json
        {
            "system": "You are a professional book translator. Translate the given paragraphs into {language} and be accurate, faithful, and fluent. Return translated {language} text only.",
            "user": "Translate the following into {language}. Text:{crlf}{crlf}{text}",
            "style": ""
        }

- 第三个键 `style` 是关于怎么写的常驻指令。它在窗口开始时说一次——在 API 路线上随系统消息发出，在 codex 上随线程指令发出——而不是每个请求都重复。随仓库提供的 [`prompt_template.json`](https://github.com/yihong0618/bilingual_book_maker/blob/main/prompt_template.json) 带有全部三个键，`style` 留空：写进你自己想要的文风，或者就让它空着。

- `--prompt` 适用于每一条大模型路线，srt 书也不例外：在那里，它的各个部分逐一叠加在字幕加载器自己的提示词之上；替换 `user` 模板，就意味着你得自己说明块编号和时间轴必须原样返回。

你也可以通过环境变量设置 `user` 和 `system` 角色的提示词：`BBM_CHATGPTAPI_USER_MSG_TEMPLATE` 和 `BBM_CHATGPTAPI_SYS_MSG`（gemini 读取 `BBM_GEMINIAPI_USER_MSG_TEMPLATE` 和 `BBM_GEMINIAPI_SYS_MSG`）。`--prompt` 优先于它们全部。`OPENAI_API_SYS_MSG` 仍会作为后备被读取，但已弃用；`--prompt` 自己的系统消息优先于它。

- `.md` 文件按 [PromptDown](https://github.com/btfranklin/promptdown) 的**块**形式读取。格式是他们定的；读取器是本仓库自己写的，所以不用额外安装包。`--prompt prompt_md.prompt.md`

        # Translation Prompt

        ## System Message

        You are a professional translator who specializes in accurate translations.

        ## Conversation

        **User:**

        Please translate the following text into {language}:

        {text}

  读取三个部分：`## System Message`（也接受 `## Developer Message` 作为它的另一个名字）、可选的 `## Style`，以及
  `## Conversation`，其中第一个 `**User:**` 轮次就是 `user` 模板。写成
  `| Role | Content |` 表格的对话会被拒绝——模板里的换行无法在表格单元格中保留。

## 示例
```sh
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template.json
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"
```
