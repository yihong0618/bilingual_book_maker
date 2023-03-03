# bilingual_book_maker
Make bilingual epub books Using AI translate

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)


## 准备

1. ChatGPT or OpenAI token
2. epub books
3. 能正常联网的环境或 proxy
4. python3.8+


## 使用

1. pip install -r requirements.txt
2. openapi token
3. 本地放了一个 animal_farm.epub 给大家测试
4. 默认用了 ChatGPT 模型，用 `--model gpt3` 来使用 gpt3 模型
5. 加了 `--test` 命令如果大家没付费可以加上这个先看看效果（有 limit 稍微有些慢）
6. Set the target language like `--language "Simplified Chinese"`. 
   Suppot ` "Japanese" / "Traditional Chinese" / "German" / "French" / "Korean"`.
   Default target language is `"Simplified Chinese"`. Support language list please see the LANGUAGES at [utils.py](./utils.py).

e.g.
```shell
# 如果你想快速测一下
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --no_limit --test --language "Simplified Chinese"
# or do it
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key} --language "Simplified Chinese"
# or 用 gpt3 模型
export OPENAI_API_KEY=${your_api_key}
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt3 --no_limit --language "Simplified Chinese"
```

## 注意

1. 有 limit 如果想要速度可以付费
2. 现在是 demo 版本有很多工作要做 PR welcome
3. 尤其是 batch translat 做完效果会好很多
4. DeepL 模型稍后更新


# 感谢

- @[yetone](https://github.com/yetone)

# 贡献

- 任何 issue PR 都欢迎
- Issue 中有些 TODO 没做的都可以选
- 提交代码前请先 `black make_book.py`

## 赞赏

谢谢就够了

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)
