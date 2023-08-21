# Translate from Different Sources

## txt/srt
Txt files and srt files are plain text files. This program can translate plain text.

    python3 make_book.py --book_name test_books/the_little_prince.txt --test --language zh-hans

## epub
epub is made of html files. By default, we only translate contents in `<p>`. Use `--translate-tags` to specify tags need for translation. Use comma to separate multiple tags. For example: `--translate-tags h1,h2,h3,p,div`

    bbook_maker --book_name test_books/animal_farm.epub --openai_key ${openai_key} --translate-tags div,p

If you want to translate strings in an e-book that aren't labeled with any tags, you can use the `--allow_navigable_strings` parameter. This will add the strings to the translation queue. <br>
**Note that it's best to look for e-books that are more standardized if possible.**

## e-reader
Use `--book_from` option to specify e-reader type (Now only `kobo` is available), and use `--device_path` to specify the mounting point.

    # Translate books download from Rakuten Kobo on kobo e-reader
    bbook_maker --book_from kobo --device_path /tmp/kobo
