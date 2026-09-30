# 用代理翻译

你有一本书，想把它翻译好。最省事的办法是交给一个带有本仓库 `bbm-plan` 技能的编程代理。代理会问清你手头有什么、选好参数，给你看命令和费用，安静地运行工具，最后交回双语书。你不需要学任何一个参数。

## 你需要准备

- **本仓库的源码。**技能在仓库根目录运行 `make_book.py`，所以只 `pip install bbook_maker` 是不够的：

    ```bash
    git clone https://github.com/yihong0618/bilingual_book_maker.git
    cd bilingual_book_maker
    pip install -r requirements.txt   # pip install ".[pdf]" if you have PDFs
    ```

- **你要用的模型的凭据**：放在 `.env` 或环境变量里的 API key、跑在你自己机器上的模型，或者经由 codex 路线使用的 ChatGPT 套餐。代理会问你用哪一种；每种各需要什么，见[用大模型翻译](llm-args.md)。
- **一个能读取技能的编程代理**：Codex、Claude Code，或者 `skills` CLI 能安装到的任何代理。

## Codex

Codex 会从仓库读取 `.agents/skills/`，所以什么都不用装。在仓库目录里启动 `codex`，描述任务，或者直接点名技能：

```text
$bbm-plan translate ~/Books/my_book.epub to Japanese
```

## Claude Code 和其他代理

先安装一次技能。这条命令会找出你机器上的代理，并问你装到哪一个：

```bash
npx skills add yihong0618/bilingual_book_maker --skill bbm-plan
```

然后在仓库目录里启动代理，描述任务。在 Claude Code 里，技能也可以直接用名字调用：

```text
/bbm-plan translate ~/Books/my_book.epub to Japanese
```

## 代理会做什么

1. **问清你手头有什么。**路线（key、本地模型、ChatGPT 套餐）、目标语言、你常用的提示词文件（如果有），以及你想改动的默认设置。默认输出双语：原文每段后面紧跟译文。如果只想要译文，就说“只要中文”。
2. **探测端点**：在花任何钱之前，先发一个花费不到一美分的请求。
3. **说明命令**：每个参数一行并附理由，告诉你会看到的检查点，以及花的是谁的额度。然后等你批准。
4. **按文件类型走对应流程。**
    - EPUB 走[计划模式](features/plan-mode.md)：工具先把书切分好，代理根据真实样本自己判断哪些是页眉、页码或脚注标记，并在完整运行之前把计划给你看。
    - PDF 转成[双语 EPUB](features/pdf-to-epub.md)：先翻两页，代理读过产出之后，再翻整个文件。
    - TXT、SRT 和 Markdown 各只需一条命令。
5. **安静地运行**，日志写到 `run.log`；崩溃后或开新会话时，从停下的地方继续。它需要的一切都在磁盘上，没有任何东西只存在于对话中。
6. **交付**：把 `_bilingual` 文件放在你的书旁边。

## 阅读技能内容

技能由路由文件 `.agents/skills/bbm-plan/SKILL.md` 加上每个流程一份参考文件组成（路线设置、EPUB 计划模式、PDF 路线、纯文本格式、提示词文件、提供方），只在运行到那一步时才加载。可以在 GitHub 上阅读：[`.agents/skills/bbm-plan`](https://github.com/yihong0618/bilingual_book_maker/tree/main/.agents/skills/bbm-plan)。凡是它没有写明的内容，它指向的都是本 wiki 里的同一批页面。
