# 计划模式

## 它做什么

EPUB 把文字放在多种标记里：段落、标题、列表项、表格单元格、引文、诗行、图题。计划模式会把它们全部找出来。加载器把整本书切分成单元，按标签签名（标签及其 class）给单元分组，然后问模型哪些签名值得翻译。答案写进 `<book>_plan.json`，同一本书之后的每次运行都会复用它。没有计划时，只翻译 `--translate-tags` 选中的标签（默认 `<p>`），`<p>` 之外的诗歌或表格会一声不响地保留源语言。

计划模式还会给工作分组。相邻的单元在 token 预算（`--accumulated_num`）和单元数上限（`--max-batch-units`，默认 16）之内合成一次请求。每个请求都要求按 id 返回各个单元，所以回复里漏掉、合并或打乱了单元时，能被发现并拆成更小的部分重试，而不会让后面每一条译文都错开一个位置。对任何大模型路线上的 EPUB，计划模式默认开启，不需要加参数。这些默认值背后的数据见[每次请求的单元数与 token 数](../evaluation/grouping-batch-size.md)。

## 准备

不需要安装任何东西。计划模式需要一本 EPUB 和一个用来分类的模型。在任何大模型路线上，这个模型就是翻译用的模型。[机器翻译](../machine-args.md)路线上没有模型可问，所以运行会回退到 `--translate-tags` 选中的标签，除非你用 `--classify-model` 指定一个分类器（见[选择分类器](#选择分类器)）。

参数：

| 参数 | 作用 |
|---|---|
| `--plan-classify auto` | 默认值。由翻译用的模型决定：端点经验证确实执行 JSON schema 时通过 schema，否则通过普通对话，用单个词 `skip`/`translate`/`unsure` 作答。回答 unsure 或无法解析时都翻译。 |
| `--plan-classify model` | 与 `auto` 相同，但有无法确定的行时中止运行，而不是回退。 |
| `--plan-classify all` | 翻译所有单元；不分类，不写计划文件。 |
| `--plan-classify none` | 不用计划：翻译 `--translate-tags` 选中的标签，与计划模式出现之前一样。 |
| `--plan-classify agent` | 写出带样本的计划，为编程代理（或你本人）打印说明，然后停止。重新运行同一条命令开始翻译。 |
| `--classify-model MODEL` | 用另一个模型，或一个 Jev 兼容的分类器（`jev`）来分类。在命令行上写明时，运行进入 `model` 模式，分类失败会中止运行。`--plan-classify-model` 是旧名字，仍然可用。 |
| `--classify-base-url URL` | 该模型的服务地址，当它不是本次运行的端点时使用：一个 OpenAI 兼容地址，或一个 Jev 兼容的分类器的 URL。 |
| `--classify-key KEY` | `--classify-base-url` 使用的 key。 |
| `--plan-dry-run` | 打印按签名统计的覆盖率表，写出所有决定都为空的计划，然后退出。不需要 key。 |
| `--plan-min-coverage FRACTION` | 计划覆盖的文字比例低于这个值时中止（默认 0.5；`0` 关闭这项检查）。 |
| `--accumulated_num N` | 每个请求的 token 预算。不设时由运行自动推算：用自带提示词时为 1200，自定义 `--prompt` 较长时最多 1600，在不支持严格 schema 的端点上为 800（会话模式运行保持未减半的值）。`1` 关闭合并。 |
| `--max-batch-units N` | 每个请求最多包含的单元数。默认 16；在不支持严格 schema 的端点上为 8。 |
| `--exclude-translate-tags TAGS` | 其内容从不发送的标签（默认 `sup,code`）。 |
| `--only_filelist`, `--exclude_filelist` | 只为这些内部文件做计划，或跳过这些文件。预览运行同样遵守它们。 |

在计划模式下不要传这些参数：`--translate-tags`（会被忽略，计划已经覆盖一切）、`--allow_navigable_strings`（会被忽略）、`--block_size` 和 `--batch_size`（它们会重新切分计划已经切好的文字）。`--retranslate`、`--batch` 和 `--sentence_mode` 与计划相矛盾，会被拒绝。

## 选择分类器

分类器按以下顺序确定：`--classify-model`，然后是提供方条目的 `classify_model`，最后是翻译用的模型。

- **怎么问。**分类器的端点经验证支持严格 JSON schema 时，一个请求携带一页签名，回复受 schema 约束。其他情况下是普通对话：每轮五个签名，每个回答 `skip`、`translate` 或 `unsure`。连续两次回复不合格式，就降到每轮三个，再降到每轮一个。连一个都不行时，分类停止，剩下的签名全部翻译。每降一级都会打印一行。
- **单独的分类器**（通过参数或提供方条目指定）会为整本书做计划，无论翻译路线本身能做什么。运行会打印 `plan mode: on (classified by …)`。机器翻译路线就是这样用上计划的。用 gpt-5.6-luna 当分类器、Google 翻译负责翻译，在测试书上完整跑过：31 个签名全部有了决定，覆盖率 99.8%。
- **agent 模式不问任何模型。**`--plan-classify agent` 把所有未决定的行交给你或你的编程代理。指定的分类器不会预先填写计划，因为代理面对预填的答案判断得更差；运行会警告它被忽略了。`--plan-classify all` 也会忽略它。
- **在哪里问、用哪个 key**，见[提供方文件与额外模型](../providers.md#各模型在哪里被询问)。在单独地址上的分类器，会在运行结束时打印自己的用量行。
- **Jev** 是 TypeSafe 的分类器，专为这个问题而生：翻译还是跳过，每个请求一页签名，一次廉价的往返就够。`--classify-model jev` 就会使用它；TypeSafe 前面的网关也可以。哪个 key 发往哪里，以及 URL 规则，见[提供方文件与额外模型](../providers.md#jev-与-jev-兼容分类器)。
- **Jev 的闸门。**拿不准的 `skip` 会改为 `translate`，所以不会因它丢失内容。闸门设在 Jev 所选答案的概率 0.95 处，这个值是以 gpt-5.6-luna 为参照，在 45 本 EPUB 的 662 个计划签名上测出来的。在这个值下，Jev 的跳过约有十分之九会回退为 `translate`；它仍然跳过的是校勘材料（版权行、行号、注释标记、索引页码）。在这个语料上，Jev 比全部翻译省不了多少。计划文件会在每个回退的行上注明（`… below the gate: translate`）。`--classify-min-confidence P` 可以为一次运行调整闸门（`BBM_JEV_MIN_CONFIDENCE` 不用参数也能做到）；调低会保留更多 Jev 的跳过，风险自负。见[用 Jev 做计划分类器](../evaluation/plan-classifier-jev.md)。

=== "机器翻译路线加大模型分类器"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_format google \
      --classify-model gpt-5.6-luna \
      --language zh-hans
    ```

    分类器请求发往 OpenAI，读取 `OPENAI_API_KEY`。翻译仍由 Google 完成。

=== "随附提供方文件里的 Jev"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --provider openai-jev \
      --use_context session
    ```

    一行搞定：用 OpenAI 的 gpt-5.6-luna 翻译（`OPENAI_API_KEY`），用 Jev 为计划分类（`JEV_API_KEY`），两者都来自随附的 `bbm_providers.example.json` 里的 `openai-jev` 条目。

=== "TypeSafe 上的 Jev"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
      --classify-model jev \
      --use_context session
    ```

    读取 `JEV_API_KEY` 或 `TYPESAFE_API_KEY`。

=== "经网关使用 Jev"

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
      --classify-model typesafe-ai/jev \
      --classify-base-url https://ai-gateway.vercel.sh/typesafe \
      --classify-key "$GATEWAY_KEY" \
      --use_context session
    ```

    必须写明 key：Jev 的环境变量只会自动发往 typesafe.ai 的地址。

## 推荐命令

按书的类型（小说、教材、论文、词典）、按端点（托管、本地、机器翻译）和按系统给出的命令，见 [EPUB 推荐设置](recommended-epub.md)。

## 可能出现的问题

下面是运行会打印的提示、它们的含义，以及该怎么做。

- **`plan mode: on (endpoint verified strict JSON schema)`**、`plan mode: on (no structured output here; classifying over a plain session)` 或 `plan mode: on (classified by …)`。不是问题，只是说明计划将如何决定。
- **`plan: this endpoint missed the reply format twice at 5 per turn; continuing at 3 per turn`**。普通对话分类器降了一级。什么都不用做，只是多花几轮。**`plan: this endpoint missed the reply format twice even one at a time; classification stops here and the remaining N signature(s) are translated`**。模型守不住单个词的回答格式。书照样会翻译，而且宁多勿少。想要更好的计划，就用 `--classify-model` 指定一个更强的分类器，或者使用 `--plan-classify agent`。
- **`… doesn't apply JSON schema (…), using delimiter method`** 或 **`… honors JSON schema shape but not value constraints; using the delimiter method for translation, schema kept for classification`**。不是问题。端点不做严格的 schema 解码，所以翻译改用分隔符格式。在 anthropic 路线、大多数代理和本地服务器上，这是预期行为。
- **`plan mode: off (…)`**。运行翻译 `--translate-tags` 选中的标签。括号里写明了原因：路线没有模型、给了 `--translate-tags`，或者探测失败。如果这不在你意料之中，就看看原因。在不会自己做计划的路线上，运行会建议使用 `--plan-classify model`。
- **`N misaligned batches this run — if this keeps happening, a lower --max-batch-units or --accumulated_num may fit this model better`**。从第三个恢复的批次起打印。模型在大请求上总是数错。没有丢失任何内容（每个出错的批次都拆成两半重试过了），但会多花请求。下次运行把单元数上限减半：`--max-batch-units 8`，然后 `4`。
- **`Plan coverage X% is below the required 50.0% — refusing to translate a fraction of the book silently.`** 计划会跳过书的大部分内容。打开 `<book>_plan.json`，看看哪些被标成了 `skip`。词典或带大量校勘材料的书可能确实只需翻译较少的部分；这时就有意识地调低 `--plan-min-coverage`。
- **`--classify-model names a classifier, and --plan-classify agent leaves every row to your agent and asks no model; it is ignored this run.`**（或 `… all translates the whole partition …`）。如其所言；去掉这个参数或换个模式。
- **`--plan-classify model asks an LLM to rule on every plan signature, and the google format translates through one fixed engine with no model to ask. …`** 固定引擎的运行处于 `model` 模式，却没有自己的分类器。加上 `--classify-model`，或者改用 `agent` 或 `all`。
- **`--classify-model needs an OpenAI-compatible endpoint; … resolves to the … format.`** `--classify-base-url` 不是 OpenAI 形态。**`--classify-base-url names where --classify-model is served, and no --classify-model was given. …`** 把模型也写上。
- **`No API key for the jev classifier at … Pass --classify-key, …`** Jev 的环境变量只在 typesafe.ai 地址上读取。经网关使用时，用 `--classify-key` 写明 key。
- **`BBM_JEV_MIN_CONFIDENCE must be a number from 0 to 1; got …`** 改正或取消这个变量。
- **`<book>_plan.json has N undecided signature(s) …`**。用过 `--plan-classify agent` 之后，还有一些行没有决定。把每个 `action` 都填成 `translate` 或 `skip`，然后重新运行。
- **`…: invalid action '…' — use …`**。手工编辑计划时打错了字。改正 JSON 后重新运行。
- **续跑因指纹信息被拒绝。**写下检查点之后，书或计划发生了变化。用原来的参数重新运行，或者删掉检查点从头开始——前提是清楚哪些部分要重新付费。
- **计划跳过了你想要的内容。**删掉 `<book>_plan.json` 再运行一次，或者手工编辑其中的 `action` 字段。`--test` 运行会为整本书分类，而不只是那一小段，所以它得出的计划就是真正的计划。
