# 快速开始

本页带你从零开始做出三本双语书：一个 EPUB、一个 TXT 和一个 PDF。这里用的是 OpenAI 的 API 和默认模型 `gpt-5.6-luna`。其他端点见[用大模型翻译](llm-args.md)。

## 1. 安装

需要 Python 3.10 或更新版本，最好用一个虚拟环境。

```bash
pip install -U bbook_maker
```

安装后，`bbook_maker` 命令就在 PATH 上了。在仓库源码里，`python make_book.py` 是同一个程序，见[安装](installation.md)。

## 2. 提供 key

把 key 放进环境变量，这样它就不会出现在命令行上。

```bash
export OPENAI_API_KEY=sk-...
```

也可以改用 `--key` 传入。查找顺序见[用大模型翻译](llm-args.md#api-key)。

## 3. 先拿几段试试

`--test` 只翻译开头几段，出了错也几乎不花钱。`--test_num` 设定翻译多少段（默认 10）。

```bash
bbook_maker \
  --book_name test_books/animal_farm.epub \
  --test \
  --test_num 8
```

示例书放在仓库的 `test_books/` 文件夹里。如果你是用 pip 安装的，就换成你自己的文件。

## 4. 一本 EPUB

```bash
bbook_maker \
  --book_name my_book.epub \
  --language zh-hans \
  --use_context session
```

- 输出：`my_book_bilingual.epub`，就在输入文件旁边。
- `--use_context session` 整本书只保持一段对话，人名和术语因此前后一致。见[会话模式](features/session-mode.md)。
- EPUB 默认按计划翻译：工具找出每一块文字（包括诗行、表格和图题），再问模型哪几类值得翻译。见[计划模式](features/plan-mode.md)。

## 5. 一个 TXT 文件

```bash
bbook_maker \
  --book_name test_books/the_little_prince.txt \
  --language zh-hans
```

- 输出：`the_little_prince_bilingual.txt`，就在输入文件旁边。
- 每次请求发送 `--batch_size` 行（默认 10）。

Markdown 和 SRT 的用法相同，分别生成 `<name>_bilingual.md` 和 `<name>_bilingual.srt`。见[格式](formats/md.md)。

## 6. 一个 PDF

PDF 最好转成带目录的双语 EPUB。这条路线需要 PDF 扩展和 Pandoc 3.1.12 或更新版本，请先按 [PDF 扩展](installation-pdf.md)装好。然后在为整本书付费之前，先读两页：

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

打开 `paper_pages-1-2_book/source.md`，看一遍标题，它们会成为目录。如果标题没问题，就翻译整个文件：

```bash
python make_book.py \
  --book_name paper.pdf \
  --to-epub \
  --use_context session
```

- 输出：工作文件夹 `paper_book/`（里面有 `source.md`、图片、`book_bilingual.md` 和一份清单文件），以及 PDF 旁边的一份成书副本 `paper_bilingual.epub`。
- 扫描版 PDF 需要 `--pdf-ocr`，需要时运行会告诉你。
- 不加 `--to-epub` 时，PDF 走旧路线，生成 `paper_bilingual.txt`。见 [PDF](formats/pdf.md)。

## 运行中断时

随时可以按 Ctrl+C。运行会保存已完成的部分；EPUB 运行会在输入文件旁留下 `my_book_bilingual_temp.epub`。加上 `--resume` 再运行同一条命令即可继续：

```bash
bbook_maker \
  --book_name my_book.epub \
  --use_context session \
  --resume
```

`--to-epub` 运行不需要 `--resume`：重新运行同一条命令，它会从提取和翻译停下的地方接着做。
