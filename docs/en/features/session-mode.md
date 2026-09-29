# Session mode

## What it does

`--use_context session` keeps one conversation for the whole book. Every request adds to it, and every request carries it, so the model reads the last chapter or so before it translates the next paragraph. That is how names, register and terminology stay the same from page to page. On an endpoint with prompt caching, the history that was already sent is billed at the cache rate, so a long history costs much less than its size. The other context mode, bare `--use_context` (window mode), re-sends only the last few source/translation pairs with each request.

A history cannot grow for ever. When it reaches `--context-compact-at` (default 8192 estimated tokens, the opening summary included), the model writes a short handoff report, about 300 tokens: the names, the register and the terminology decisions so far. That report opens the next window. The latest one is saved in `<book>_handoff.md`, and `--resume` reads it back. The default was chosen for continuity, not for price; [Why the session compacts at 8192](../evaluation/session-compact-budget.md) has the measurements, including the fact that a lower value is cheaper.

## Setup

Nothing to install. Session mode needs:

- an EPUB, Markdown or PDF book (a PDF on either route: `--to-epub` translates Markdown, and the text route takes a session too). TXT and SRT never hand context to the model.
- a route that keeps a history: the openai-shaped routes (OpenAI, gateways, groq, xai, litellm, local servers) and anthropic. Gemini and Qwen keep their own history and take bare `--use_context` instead; they refuse `session`. The codex route is a session whether you ask or not: its thread is the history.

The flags:

| flag | what it does |
|---|---|
| `--use_context session` | Turn it on. |
| `--context-compact-at N` | The window budget in estimated tokens, handoff included. Default 8192, minimum 1500. Set it to your model's input limit when that is smaller. |
| `--no-context-compact` | Never ask for a handoff report. The window still rolls over at the budget, but the next one starts empty. |
| `--glossary FILE` | Pin renderings you stand behind (`term -> translation` lines). Only the terms that occur in a request are sent with it. |
| `--glossary-auto on` | Also keep the renderings the handoff reports establish. Off by default. It needs a model that answers with names rather than prose. |

Session mode is refused with `--parallel-workers` (one history cannot be shared between workers) and with `--model_list` (each model has its own cache, and one conversation would be written by several). `--context_paragraph_limit` belongs to window mode and is ignored here.

## Recommended commands

If you translate a novel, add `--use_context session` and, when a recurring name drifts after a window seam, `--glossary names.txt`. If your model runs locally with a small context, set `--context-compact-at` to its input limit. If the endpoint has no prompt cache, use bare `--use_context` instead. The full list, by kind of book, endpoint and system, is on [Recommended settings for EPUB](recommended-epub.md); for a PDF, on [Recommended settings for PDF](recommended-pdf.md).

## What can go wrong

- **`session: compacting at 8192 estimated tokens (the default; --context-compact-at overrides)`**. Not a problem. The run says the budget it uses.
- **`cached=` on the progress bar stays at 0 after a dozen requests.** The endpoint has no prompt cache, and every request pays for the whole history at full price. Press Ctrl+C and rerun with bare `--use_context`.
- **`--use_context session outside plan mode leaves grouping off, so every paragraph is its own request and each one re-reads the whole history.`** An EPUB outside plan mode (for example `--plan-classify none`), or an SRT book on the codex route. Raise `--accumulated_num`. Markdown and PDF runs do not print this; there, a larger `--batch_size` sends fewer requests.
- **`Error: --use_context session is not implemented for the gemini format; it would be accepted and ignored.`** Use bare `--use_context` on Gemini and Qwen.
- **`--use_context session is not supported for txt books; it will be ignored.`** TXT and SRT carry no context.
- **`--parallel-workers is not supported with --use_context session: one history is the context, and a worker cannot share it.`** Choose one. Bare `--use_context` keeps the workers.
- **`a compact budget of N is too small for a session; use at least 1500 estimated tokens.`** Below 1500 a window is mostly the handoff that opens it. Use bare `--use_context` if you want less context than that.
- **`ℹ handoff report failed (…); starting the next context window without a summary`**, or `… keeping the current context and retrying on the next paragraph`. One compaction produced no usable report. Translation continues. If the text after that seam drifts, pin the terms with `--glossary`.
- **`--glossary-auto on learns renderings from the handoff report a session writes when it compacts, and this run keeps no session to compact.`** Add `--use_context session`, or use `--glossary`, which needs no session.
- **The text drifts after a compaction on a small model.** The handoff is written by the model, and a small model can write a poor one. Pin the names with `--glossary`, or pass `--no-context-compact` and accept an empty window at each seam.
- **`--context-compact-at` or `--no-context-compact` printed as `only applies to --use_context session; ignoring it.`** You gave a compaction flag without a session.
