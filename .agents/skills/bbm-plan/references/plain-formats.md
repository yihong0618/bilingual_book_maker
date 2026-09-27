# TXT, SRT and Markdown: one command each

Loaded from SKILL.md when the book is a `.txt`, `.srt` or `.md` file (or a
PDF the user wants as plain text, without `--to-epub`). `ROUTE` and
`CONTEXT` come from `references/route-setup.md`. There is no plan step and
nothing to classify on these formats; `--plan-classify`, `--plan-dry-run`,
`--classify-model` and the other plan and classify flags are ignored with a
warning, so never pass them.

## The recommended command per format

| file | command (add `"${ROUTE[@]}" --language "$LANG"`) | why | user page |
|---|---|---|---|
| `.txt` | `--batch_size 20` | lines are sent in groups; 20 gives the model more context per request and halves the request count. Lower it if the model starts merging or dropping lines | `docs/formats/txt.md` |
| `.srt` | `--accumulated_num 400` | consecutive subtitle blocks share one request up to that many characters (capped at 512) | `docs/formats/srt.md` |
| `.md` | `--prompt prompt_md.json "${CONTEXT[@]}" --batch_size 20` | the shipped Markdown prompt keeps the markup; a session keeps names consistent; outside plan mode each request re-reads the history, so a larger `--batch_size` keeps the request count down | `docs/formats/md.md` |
| `.pdf`, as text (only when the user asked for txt) | `--batch_size 20` | the old text route: a bilingual `.txt` with no structure, plus an attempted EPUB | `docs/formats/pdf.md#the-text-route-no-to-epub` |

```bash
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" --language "$LANG" --batch_size 20 > run.log 2>&1
```

What does not apply, so do not pass it:

- **TXT and SRT carry no context**: `--use_context` (either mode),
  `--context-compact-at`, `--glossary` are ignored with a warning. Only
  Markdown and the PDF text route take the `CONTEXT` array and `--glossary`.
- `--quiet` is EPUB only; the run warns. Keep the log redirect.
- `--parallel-workers` works on Markdown only (not with a session); TXT and
  SRT stay serial.
- On an SRT book a replaced `user` template must itself say that each
  block's number and timeline come back unchanged, or the answer will not
  parse as SRT; the run warns. Check this when linting the user's prompt.

A run can be tried on the first few lines with `--test --test_num 8`; the
resume checkpoint then carries into the full run (`--resume`).

## Deliver

Read the output back before handing it over: `<name>_bilingual.txt`,
`<name>_bilingual.srt` or `<name>_bilingual.md` beside the input.

- is the translation in the target language, after each original group?
- SRT: does every block keep its number and timeline, in order?
- Markdown: are code blocks, tables and front matter untouched, and the
  headings still headings?
- any delimiter or JSON residue?

## Failure lines

| symptom | meaning |
|---|---|
| `--use_context session is not supported for txt books; it will be ignored.` (or srt) | these loaders never hand context to the model; drop the flag |
| `--use_context session outside plan mode leaves grouping off, …` | a Markdown session: raise `--batch_size` so fewer requests re-read the history |
| a warning naming `--accumulated_num` on a TXT or Markdown book | not read there; group with `--batch_size` |
| `can not load resume file` | `--resume` on a first run; drop it |

Route-wide lines (schema verdicts, retries, codex) are in
`references/route-setup.md`.
