# Translate with an agent

You have a book and want it translated well. The shortest path is to hand it to a coding agent that carries this repository's `bbm-plan` skill. The agent asks what you have, picks the flags, shows you the command and the cost, runs the tool quietly, and hands back the bilingual book. You are never asked to learn a flag.

## What you need

- **A checkout of this repository.** The skill runs `make_book.py` from the repository root, so a `pip install bbook_maker` alone is not enough:

    ```bash
    git clone https://github.com/yihong0618/bilingual_book_maker.git
    cd bilingual_book_maker
    pip install -r requirements.txt   # pip install ".[pdf]" if you have PDFs
    ```

- **Credentials for the model you want**: an API key in `.env` or the environment, a model on your own machine, or a ChatGPT plan through the codex route. The agent asks which; see [Translating with an LLM](llm-args.md) for what each needs.
- **A coding agent that reads skills**: Codex, Claude Code, or any agent the `skills` CLI installs into.

## Codex

Codex reads `.agents/skills/` from the repository, so nothing is installed. Start `codex` in the checkout and describe the job, or name the skill:

```text
$bbm-plan translate ~/Books/my_book.epub to Japanese
```

## Claude Code and other agents

Install the skill once. The command finds the agents on your machine and asks which to install into:

```bash
npx skills add yihong0618/bilingual_book_maker --skill bbm-plan
```

Then start the agent in the checkout and describe the job. In Claude Code the skill also answers to its name:

```text
/bbm-plan translate ~/Books/my_book.epub to Japanese
```

## What the agent does

1. **Asks what you have.** The route (a key, a local model, a ChatGPT plan), the target language, a prompt file if you keep one, and anything you want changed from the defaults. Bilingual output is assumed: every original paragraph followed by its translation. Say "just the Chinese" if you want the translation alone.
2. **Probes the endpoint** with a request costing less than a cent, before anything is paid for.
3. **States the command**, one line per flag with the reason, the checkpoint you will see, and whose allowance it spends. Then it waits for your approval.
4. **Follows the flow for the file.**
    - An EPUB goes through [plan mode](features/plan-mode.md): the tool partitions the book, the agent itself decides what a running head, a page number or a footnote marker is against the real samples, and shows you the plan before the full run.
    - A PDF becomes a [bilingual EPUB](features/pdf-to-epub.md): two pages first, the agent reads what came out, then the whole file.
    - TXT, SRT and Markdown take one command each.
5. **Runs quietly**, logging to `run.log`, and resumes where it stopped after a crash or a new session. Everything it needs is on disk, nothing lives only in the conversation.
6. **Delivers** the `_bilingual` file next to your book.

## Reading the skill

The skill is the router `.agents/skills/bbm-plan/SKILL.md` plus one reference file per flow (route setup, EPUB plan mode, the PDF route, plain formats, prompt files, providers), loaded only when that step runs. Read it on GitHub: [`.agents/skills/bbm-plan`](https://github.com/yihong0618/bilingual_book_maker/tree/main/.agents/skills/bbm-plan). It points at the same pages as this wiki for anything it does not spell out.
