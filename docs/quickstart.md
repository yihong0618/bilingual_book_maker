# QuickStart
After successfully install the package, you can see `bbook-maker` is in the output of `pip list`.

## Preparation
1. ChatGPT or OpenAI [token](https://platform.openai.com/account/api-keys)
2. EPUB, TXT, Markdown, SRT, or PDF input
3. Environment with internet access or proxy
4. Python 3.10+

## Use
You can use by command `bbook_maker`. A sample book, `test_books/animal_farm.epub`, is provided for testing purposes.
```sh
bbook_maker --book_name ${path of a book} --openai_key ${openai_key}

# Example
bbook_maker --book_name test_books/animal_farm.epub --openai_key ${openai_key}
```
Or, you can use the [script](https://github.com/yihong0618/bilingual_book_maker/blob/main/make_book.py) provided by repository.
```sh
python3 make_book.py --book_name ${path of a book} --openai_key ${openai_key}

# Example
python3 make_book.py --book_name test_books/animal_farm.epub --openai_key ${openai_key}
```

The output extension depends on the input loader. EPUB inputs produce
`${book_name}_bilingual.epub`; TXT, Markdown, and SRT use their corresponding text/subtitle
formats. PDF inputs always keep a bilingual TXT fallback and also attempt an EPUB, with
optional PDF layouts selected by `--pdf_layout`.

Use `--resume` after an interruption. Loader-specific temporary output and checkpoint files
are generated beside the source; do not rename a partial result as complete without checking
it.
