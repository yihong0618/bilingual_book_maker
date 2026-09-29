# 各类文件对应的页面

文件的扩展名决定它怎么被读取。找到你的那一行：

| 如果你的文件是…… | 先运行 | 再阅读 |
|---|---|---|
| EPUB | `bbook_maker --book_name book.epub --use_context session` | [EPUB](formats/epub.md)、[EPUB 推荐设置](features/recommended-epub.md) |
| PDF | `python make_book.py --book_name book.pdf --to-epub --pages 1-2 --test` | [PDF 转双语 EPUB](features/pdf-to-epub.md)、[PDF 推荐设置](features/recommended-pdf.md) |
| 纯文本文件 | `bbook_maker --book_name book.txt --batch_size 20` | [TXT](formats/txt.md) |
| 字幕 | `bbook_maker --book_name talk.srt --accumulated_num 400` | [SRT](formats/srt.md) |
| Markdown 文档 | `bbook_maker --book_name doc.md --prompt prompt_md.json --use_context session` | [Markdown](formats/md.md) |

PromptDown 格式的 `.md` 文件是提示词，不是书：它交给 `--prompt`，而 Markdown 格式的书交给 `--book_name`。

## 不用计划翻译 EPUB

如果只想翻译指定的标签，就关掉计划并列出这些标签：

```bash
bbook_maker --book_name test_books/animal_farm.epub --plan-classify none --translate-tags div,p
```

如果一本书有不在任何标签里的文字，`--allow_navigable_strings` 会把它们也加进来。计划本来就同时覆盖这两种情况，所以这些参数是给想精确控制的书用的。无论哪种方式，结构规范的 EPUB 效果都更好。
