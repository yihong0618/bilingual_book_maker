# bilingual_book_maker

bilingual_book_maker 是一个 AI 翻译工具，使用 ChatGPT 帮助用户制作多语言版本的 epub/txt 文件和图书。该工具仅适用于翻译进入公共版权领域的 epub/txt 图书，不适用于有版权的书籍。请在使用之前阅读项目的 **[免责声明](./disclaimer.md)**。

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)


## 准备

1. ChatGPT or OpenAI token [^token]
2. epub/txt books
3. 能正常联网的环境或 proxy
4. python3.8+


## 使用

- `pip install -r requirements.txt` 或 `pip install -U bbook_maker`
- 使用 `--openai_key` 指定 OpenAI API key，如果有多个可以用英文逗号分隔(xxx,xxx,xxx)，可以减少接口调用次数限制带来的错误。  
   或者，指定环境变量 `BMM_OPENAI_API_KEY` 来略过这个选项。
- 本地放了一个 `test_books/animal_farm.epub` 给大家测试
- 默认用了 [GPT-3.5-turbo](https://openai.com/blog/introducing-chatgpt-and-whisper-apis) 模型，也就是 ChatGPT 正在使用的模型，用 `--model gpt3` 来使用 gpt3 模型
- 可以使用 DeepL 封装的 api 进行翻译，需要付费，[DeepL Translator](https://rapidapi.com/splintPRO/api/deepl-translator) 来获得 token  `--model deepl --deepl_key ${deepl_key}`
- 可以使用 google 来翻译 `--model google`
- 可用使用彩云进行翻译 `--model caiyun --caiyun_key ${caiyun_key}`
- 使用 `--test` 命令如果大家没付费可以加上这个先看看效果（有 limit 稍微有些慢）
- 使用 `--language` 指定目标语言，例如： `--language "Simplified Chinese"`，预设值为 `"Simplified Chinese"`.  
   请阅读 helper message 来查找可用的目标语言：  `python make_book.py --help`
- 使用 `--proxy` 参数，方便中国大陆的用户在本地测试时使用代理，传入类似 `http://127.0.0.1:7890` 的字符串
- 使用 `--resume` 命令，可以手动中断后，加入命令继续执行。
- epub 由 html 文件组成。默认情况下，我们只翻译 `<p>` 中的内容。
   使用 `--translate-tags` 指定需要翻译的标签。使用逗号分隔多个标签。例如：
   `--translate-tags h1,h2,h3,p,div`
- 请使用 --book_from 选项指定电子阅读器类型（现在只有 kobo 可用），并使用 --device_path 指定挂载点。
- 如果你遇到了墙需要用 Cloudflare Workers 替换 api_base 请使用 `--api_base ${url}` 来替换。  
   **请注意，此处你输入的api应该是'`https://xxxx/v1`'的字样，域名需要用引号包裹**
- 翻译完会生成一本 ${book_name}_bilingual.epub 的双语书
- 如果出现了错误或使用 `CTRL+C` 中断命令，不想接下来继续翻译了，会生成一本 ${book_name}_bilingual_temp.epub 的书，直接改成你想要的名字就可以了
- 如果你想要翻译电子书中的无标签字符串，可以使用 `--allow_navigable_strings` 参数，会将可遍历字符串加入翻译队列，**注意，在条件允许情况下，请寻找更规范的电子书**
- 如果你想调整 prompt，你可以使用 `--prompt` 参数。有效的占位符包括 `{text}` 和 `{language}`。你可以用以下方式配置 prompt：
   如果您不需要设置 `system` 角色，可以这样：`--prompt "Translate {text} to {language}" 或者 `--prompt prompt_template_sample.txt`（示例文本文件可以在 [./prompt_template_sample.txt](./prompt_template_sample.txt) 找到）。
   如果您需要设置 `system` 角色，可以使用以下方式配置：`--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'`，或者 `--prompt prompt_template_sample.json`（示例 JSON 文件可以在 [./prompt_template_sample.json](./prompt_template_sample.json) 找到）。
   你也可以用环境以下环境变量来配置 `system` 和 `user` 角色 prompt：`BBM_CHATGPTAPI_USER_MSG_TEMPLATE` 和 `BBM_CHATGPTAPI_SYS_MSG`。
该参数可以是提示模板字符串，也可以是模板 `.txt` 文件的路径。
- 使用`--batch_size` 参数，指定批量翻译的行数(默认行数为10，目前只对txt生效)


### 示范用例

**如果使用 `pip install bbook_maker` 以下命令都可以改成 `bbook args`**

```shell
# 如果你想快速测一下
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --test

# 或翻译完整本书
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --language zh-hans

# 指定环境变量来略过 --openai_key
export OPENAI_API_KEY=${your_api_key}

# 或使用 gpt3 模型
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt3 --language ja

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model deepl --deepl_token ${deepl_key}--language ja


# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

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

## 注意

1. Free trail 的 API token 有所限制，如果想要更快的速度，可以考虑付费方案
2. 欢迎提交 PR


# 感谢

- @[yetone](https://github.com/yetone)

# 贡献

- 任何 issue PR 都欢迎
- Issue 中有些 TODO 没做的都可以选
- 提交代码前请先执行 `black make_book.py` [^black]

## 赞赏

谢谢就够了

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: https://platform.openai.com/account/api-keys
[^black]: https://github.com/psf/black
