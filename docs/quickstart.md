# QuickStart
After successfully install the package, you can see `bbook-maker` is in the output of `pip list`.

## Preparation
1. ChatGPT or OpenAI [token](https://platform.openai.com/account/api-keys)
2. epub/txt books
3. Environment with internet access or proxy
4. Python 3.8+

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

Once the translation is complete, a bilingual book named `${book_name}_bilingual.epub` would be generated.


**Note: If there are any errors or you wish to interrupt the translation by pressing `CTRL+C`. A book named `${book_name}_bilingual_temp.epub` would be generated. You can simply rename it to any desired name.**
