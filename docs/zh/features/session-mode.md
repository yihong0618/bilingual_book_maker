# 会话模式

## 它做什么

`--use_context session` 让整本书保持一段对话。每个请求都往对话里追加内容，也都携带整段对话，所以模型在翻译下一段之前，读过的是前面大约一章的内容。人名、语域和术语就是这样从一页到另一页保持一致的。在支持提示缓存的端点上，已经发送过的历史按缓存价计费，所以很长的历史实际花费远低于它的体量。另一种上下文模式是不带值的 `--use_context`（窗口模式），每个请求只重发最近几对原文与译文。

历史不能无限增长。当它达到 `--context-compact-at`（默认 8192 个估算 token，包括开头的摘要）时，模型会写一份简短的交接报告，约 300 个 token：到目前为止的人名、语域和术语决定。这份报告作为下一个窗口的开头。最新的一份保存在 `<book>_handoff.md` 里，`--resume` 会把它读回来。这个默认值是为连贯性选的，而不是为价格；测量数据见[会话压缩预算](../evaluation/session-compact-budget.md)，其中也说明了更低的值其实更便宜。

## 准备

不需要安装任何东西。会话模式需要：

- 一本 EPUB、Markdown 或 PDF 书（PDF 两条路线都行：`--to-epub` 翻译的是 Markdown，纯文本路线也接受会话）。TXT 和 SRT 从不把上下文交给模型。
- 一条能保留历史的路线：OpenAI 系路线（OpenAI、网关、groq、xai、litellm、本地服务器）和 anthropic。Gemini 和 Qwen 自己维护历史，用的是不带值的 `--use_context`，会拒绝 `session`。codex 路线不管你要不要，都是会话：它的线程就是历史。

参数：

| 参数 | 作用 |
|---|---|
| `--use_context session` | 开启会话模式。 |
| `--context-compact-at N` | 窗口预算，以估算 token 计，包括交接报告。默认 8192，最小 1500。如果你的模型输入上限更小，就设成那个上限。 |
| `--no-context-compact` | 从不要求写交接报告。窗口仍在达到预算时滚动，但下一个窗口从空白开始。 |
| `--glossary FILE` | 固定你能负责的译法（`term -> translation` 形式的行）。只有出现在某个请求里的术语才会随它发送。 |
| `--glossary-auto on` | 同时保留交接报告里确立的译名。默认关闭。它需要一个会用名字而不是大段文字作答的模型。 |

会话模式与 `--parallel-workers` 同用会被拒绝（一份历史无法在多个 worker 之间共享），与 `--model_list` 同用也会被拒绝（每个模型有自己的缓存，而且一段对话会由几个模型轮流书写）。`--context_paragraph_limit` 属于窗口模式，在这里会被忽略。

## 推荐命令

翻译小说时，加上 `--use_context session`；如果某个反复出现的人名在窗口接缝之后译法漂移了，再加上 `--glossary names.txt`。如果模型在本地运行、上下文较小，就把 `--context-compact-at` 设为它的输入上限。如果端点没有提示缓存，就改用不带值的 `--use_context`。按书的类型、端点和系统列出的完整清单见 [EPUB 推荐设置](recommended-epub.md)；PDF 见 [PDF 推荐设置](recommended-pdf.md)。

## 可能出现的问题

- **`session: compacting at 8192 estimated tokens (the default; --context-compact-at overrides)`**。不是问题，运行只是告诉你它用的预算。
- **进度条上的 `cached=` 在十几个请求之后仍然是 0。**端点没有提示缓存，每个请求都在按全价为整段历史付费。按 Ctrl+C，改用不带值的 `--use_context` 重新运行。
- **`--use_context session outside plan mode leaves grouping off, so every paragraph is its own request and each one re-reads the whole history.`** 不在计划模式下的 EPUB（例如 `--plan-classify none`），或者 codex 路线上的 SRT 书。调高 `--accumulated_num`。Markdown 和 PDF 运行不会打印这一行；在那里，调大 `--batch_size` 可以减少请求数。
- **`Error: --use_context session is not implemented for the gemini format; it would be accepted and ignored.`** 在 Gemini 和 Qwen 上使用不带值的 `--use_context`。
- **`--use_context session is not supported for txt books; it will be ignored.`** TXT 和 SRT 不携带上下文。
- **`--parallel-workers is not supported with --use_context session: one history is the context, and a worker cannot share it.`** 二选一。不带值的 `--use_context` 可以保留多个 worker。
- **`a compact budget of N is too small for a session; use at least 1500 estimated tokens.`** 低于 1500 时，一个窗口的大部分都是开头的那份交接报告。如果你想要比这更少的上下文，就用不带值的 `--use_context`。
- **`ℹ handoff report failed (…); starting the next context window without a summary`**，或 `… keeping the current context and retrying on the next paragraph`。某次压缩没有产出可用的报告。翻译会继续。如果接缝之后的文字出现漂移，就用 `--glossary` 固定术语。
- **`--glossary-auto on learns renderings from the handoff report a session writes when it compacts, and this run keeps no session to compact.`** 加上 `--use_context session`，或者改用不需要会话的 `--glossary`。
- **小模型压缩之后文字出现漂移。**交接报告由模型来写，小模型可能写得不好。用 `--glossary` 固定人名，或者传入 `--no-context-compact`，接受每个接缝处从空白窗口开始。
- **`--context-compact-at` 或 `--no-context-compact` 被打印为 `only applies to --use_context session; ignoring it.`** 你给了压缩参数，却没有开启会话模式。
