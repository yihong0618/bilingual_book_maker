# 用 Jev 做计划分类器：`--classify-model`，对照 gpt-5.6-luna 测量

## 摘要

计划模式会就 EPUB 里的每一种块问模型：值不值得翻译。这是贴标签的活，不是写作的活，所以专门的分类器可以胜任。Jev 是 TypeSafe 的 System One 分类器，它回答类型化的选择题，并附带置信度。测试书的同样 31 个签名分别由 Jev 和 gpt-5.6-luna 分类。两者在 27 个上一致。4 处分歧全都是 luna 选了 `translate` 而 Jev 选了 `skip`，且每一处 Jev 的置信度都很低。Jev 用的提示 token 约为 luna 的两倍。第二轮让每页的共享文本只发送一次：Jev 的提示 token 减半，它与 luna 的一致程度的变化，没有超出同一份代码两次 Jev 运行之间的差异。同一轮还发现，Jev 概率上 0.5 的闸门在二选一的问题上永远不会触发；随后在一个 45 本 EPUB 的语料上测量了闸门，定为 0.95。结论：Jev 可以用作分类模型；默认仍是翻译模型。

## 实验设置

- **书：** `test_books/animal_farm.epub`，仓库里的测试书。它的计划有 31 个标签签名。
- **运行：** `--test --test_num 8 --quiet --language zh-hans`，用 gpt-5.6-luna 翻译，内存上限 1500 MB。
- **luna 组：** `--model gpt-5.6-luna --classify-model gpt-5.6-luna`。该端点校验严格的 JSON schema，所以签名按页发送，每个请求包含若干个。
- **Jev 组：** `--model gpt-5.6-luna --classify-model typesafe-ai/jev --classify-base-url https://ai-gateway.vercel.sh/typesafe`。每个签名一个 Choice 问题。在第一轮中，首选答案的置信度低于 0.5 时，Jev 回答 `unsure`。
- **固定引擎单元格：** `--api_format google --classify-model gpt-5.6-luna`，检查机器翻译路线能否从大模型分类器得到计划。
- **比较内容：** 两组在每个签名上的判定。没有人评判哪个判定是对的。

## 结果

照抄自记录：

| 单元格 | 分类 | 分类器用量行 |
|---|---|---|
| luna | 31 个签名，3 个请求，4 个跳过 | `Classifier (gpt-5.6-luna at the endpoint's default host): tokens: in 7.6k, out 1.8k, cached 0 (3 requests)` |
| Jev | 一次 503 重试，然后 31 个判定，8 个跳过 | `Classifier (typesafe-ai/jev at https://ai-gateway.vercel.sh/typesafe): tokens: in 14.4k, out 1.1k, cached 0 (3 requests)` |

**31 个中有 27 个一致。** 4 处分歧全都是 luna 选了 translate 而 Jev 选了 skip，而且 4 处都是 Jev 的低置信度判定（0.58–0.61）：

| 签名 | 样本文本 |
|---|---|
| `inline:span.underline` | www.ericseat.com |
| `inline:span.calibre_4` | www.ericseat.com |
| `inline:span.calibre_16` | "M" |
| `inline:span.calibre3` | George Orwell |

在两者一致的地方，Jev 的置信度大多在 0.9 以上。它用的提示 token 约为 luna 的两倍（14.4k 对 7.6k），因为每个签名都作为单独的问题发送。

**固定引擎加大模型分类器。** 谷歌翻译，以 gpt-5.6-luna 作分类器：`llm classification: 31 verdict(s), 4 skip(s)`、`Translation plan: … coverage 99.8%`，以及分类器的那一行 `Classifier (gpt-5.6-luna …): tokens: in 7.6k, out 1.6k (3 requests)`。译文紧挨着原文，并且与原文对应（George Orwell → 乔治·奥威尔）。

修正 key 路由之后的第二次 Jev 运行，重试了三次 503，最后是 `llm classification: 31 verdict(s), 8 skip(s)` 和 `Translation plan: 20 documents, 202560 chars, coverage 99.7%`。

### 第二轮：精简请求与闸门

原先每个 Jev 问题都会重复整段按签名生成的提示。精简映射把页面的共享文本作为 state 只发送一次，每个问题只带它自己的指令。没把握的 `skip` 现在回退为 `translate`，而不是 `unsure`。同一本书、同样的参数。照抄自记录：

| 实验组 | 分类器 token 输入 / 输出 | 请求数、每个的延迟 | 非 translate 判定 | 与 c_final（luna）一致 | 与 c_before（luna）一致 |
|---|---|---|---|---|---|
| (a) 之前，F 的映射，网关 | 14.4k / 1.1k | 3，经四次 503 重试之后（总耗时 36 s） | 7 | 27/31 | 26/31 |
| (a) 仅精简映射之后（`a_lean`） | 7.8k / 1.1k | 3（总耗时 14 s） | 9 | 25/31 | 未计算 |
| (a) 最终，精简 + 闸门 | 7.8k / 1.1k | 3：0.87、0.37、0.67 s | 9 | 25/31 | 26/31 |
| (b) luna schema（`c_final`） | 7.6k / ~1.4k | 3：5.07、6.03、4.15 s | 5 | — | 30/31 |

两次 luna 运行彼此一致 30/31，同一份代码的两次 Jev 运行（`a_lean`、`a_final`）一致 29/31。

**精简映射让 Jev 的提示 token 减半（14.4k → 7.8k），并缩短了总耗时（36 s → 14 s，含无重试这一因素）；一致程度的变化没有超出 Jev 自身运行之间的差异。**

**闸门的发现**，照抄自记录：

> 官方 Jev 的 `confidence` 字段不是所选选项的概率。在两个选项时，它约为 `2p − 1`（0.51 → 0.02，0.61 → 0.22，0.76 → 0.53）。代码保留了 F 的选择，优先读取 `probabilities[choice]`。在两个选项时它永远不会低于 0.5，所以 0.5 的闸门不起任何作用。
>
> 离线重放 `a_final`：在概率上设 0.75 的闸门——相当于在 Jev 自己的 `confidence` 上设 0.5——会把全部 5 个低置信度的跳过变成翻译，保留 4 个有把握的。与 luna 的一致会升到 30/31。这只是一本书、31 个签名。

那只是一本书、31 个签名，所以 0.75 只是一个提示，不是设定值。随后在语料上测量了闸门：在所选选项的概率上取 0.95，依据是 45 本 EPUB 语料的 662 个计划签名对照 gpt-5.6-luna 的结果，丢失一个跳过记成本 10，多翻译一个记成本 1。取这个值时，Jev 的跳过约有十分之九回退为 `translate`，留下来的都是附属内容：版权行、行号、注释标记、索引页码。从不跳过的成本是 140，加闸门后是 115，所以在这个语料上，Jev 比全部翻译省得不多。

## 决定

- Jev 可以用作分类模型：在 TypeSafe 自己的地址上用 `--classify-model jev`，用网关的 ID 加 `--classify-base-url` 和 `--classify-key`。见[提供方文件与额外模型](../providers.md#jev-与-jev-兼容分类器)。
- 默认分类器仍是翻译模型。
- 第一轮：弃权阈值保持 0.5；四个低置信度的跳过只做了记录，没有评判（两个 URL、一个单独的字母和一个光秃秃的作者名，阅读版保留或翻译都说得通）。
- 第二轮：请求精简了（共享文本只发一次），阈值变成了不对称的闸门。低于它的 `skip` 变成 `translate`；`translate` 不论概率多少都采纳。内容绝不会因为没把握的跳过而丢失。
- 闸门取所选选项概率的 0.95，来自上面的语料测量。`--classify-min-confidence P` 为单次运行调整它（`BBM_JEV_MIN_CONFIDENCE` 不用参数也能做到同样的事；两者都有时以参数为准）。

什么会改变它：在更多的书上做比较，按读者想要的结果评判判定，并给出两组的价格。

## 局限

- 一本书、31 个签名，每轮每组只跑一次。两轮的一致率、精简映射带来的 token 减半，以及 0.75 的重放，都只基于这一本书。
- 只有闸门的取值来自 45 本 EPUB 的语料，而那里的一致也仍然只是与 luna 的一致。
- key 是网关的 key，所以 TypeSafe 自己的地址没有实际跑过（它对这个 key 回了 401）。
- 只比较了 token 数，没有比较金额。
- 与 luna 一致不等于正确：luna 的判定也没有被评判。
