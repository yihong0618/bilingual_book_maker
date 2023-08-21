# Tweak the prompt

To tweak the prompt, use the `--prompt` parameter. Valid placeholders for the `user` role template include `{text}` and `{language}`. It supports a few ways to configure the prompt:

- If you don't need to set the `system` role content, you can simply set it up like this: `--prompt "Translate {text} to {language}."` or `--prompt prompt_template_sample.txt`

        # prompt_template_sample.txt
        Translate the given text to {language}. Be faithful or accurate in translation. Make the translation readable or intelligible. Be elegant or natural in translation. If the text cannot be translated, return the original text as is. Do not translate person's name. Do not add any additional text in the translation. The text to be translated is: 
        {text}
        

- If you need to set the `system` role content, you can use the following format: `--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'` or `--prompt prompt_template_sample.json`

        # prompt_template_sample.json
        {
            "system": "You are a professional translator.", 
            "user": "Translate the given text to {language}. Be faithful or accurate in translation. Make the translation readable or intelligible. Be elegant or natural in translation. If the text cannot be translated, return the original text as is. Do not translate person's name. Do not add any additional text in the translation. The text to be translated is:\n{text}"
        }

You can also set the `user` and `system` role prompt by setting environment variables: `BBM_CHATGPTAPI_USER_MSG_TEMPLATE` and `BBM_CHATGPTAPI_SYS_MSG`.

## Examples
```sh
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.json
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"
```
