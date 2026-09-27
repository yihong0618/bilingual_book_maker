# Which page for which file

The file's extension picks how it is read. Find yours:

| if your file is… | start with | then read |
|---|---|---|
| an EPUB | `bbook_maker --book_name book.epub --use_context session` | [EPUB](formats/epub.md), [Recommended settings for EPUB](features/recommended-epub.md) |
| a PDF | `python make_book.py --book_name book.pdf --to-epub --pages 1-2 --test` | [PDF to bilingual EPUB](features/pdf-to-epub.md), [Recommended settings for PDF](features/recommended-pdf.md) |
| a plain-text file | `bbook_maker --book_name book.txt --batch_size 20` | [TXT](formats/txt.md) |
| subtitles | `bbook_maker --book_name talk.srt --accumulated_num 400` | [SRT](formats/srt.md) |
| a Markdown document | `bbook_maker --book_name doc.md --prompt prompt_md.json --use_context session` | [Markdown](formats/md.md) |

A PromptDown `.md` file is a prompt, not a book: it goes to `--prompt`, and a Markdown book goes to `--book_name`.

## An EPUB without a plan

If you want only chosen tags translated, turn the plan off and name the tags:

```bash
bbook_maker --book_name test_books/animal_farm.epub --plan-classify none --translate-tags div,p
```

If a book keeps text outside any tag, `--allow_navigable_strings` adds it too. A plan already covers both, so these are for books where you want exact control. A well-formed EPUB gives better results either way.
