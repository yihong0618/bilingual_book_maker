# Tweak the prompt

To tweak the prompt, use the `--prompt` parameter. The placeholders the `user` template may use are `{text}` (required), `{language}` and `{crlf}` (a newline, for the shapes that cannot carry one). Anything else in braces is refused before the run starts; write `{{` and `}}` for a literal brace. It supports a few ways to configure the prompt:

- If you don't need to set the `system` role content, you can simply set it up like this: `--prompt "Translate {text} to {language}."` or `--prompt prompt_template_sample.txt`

        # prompt_template_sample.txt
        Translate the given text to {language}. Be faithful or accurate in translation. Make the translation readable or intelligible. Be elegant or natural in translation. If the text cannot be translated, return the original text as is. Do not translate person's name. Do not add any additional text in the translation. The text to be translated is: 
        {text}
        

- If you need to set the `system` role content, you can use the following format: `--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'` or `--prompt prompt_template.json`

        # prompt_template.json
        {
            "system": "You are a professional book translator. Translate the given paragraphs into {language} and be accurate, faithful, and fluent. Return translated {language} text only.",
            "user": "Translate the following into {language}. Text:{crlf}{crlf}{text}",
            "style": ""
        }

- A third key, `style`, is a standing instruction about how to write. It is said once where a window starts — with the system message on the API routes, with the thread instructions on codex — not repeated on every request. The shipped [`prompt_template.json`](../prompt_template.json) carries all three keys with `style` left empty: write your own voice in, or leave it blank.

- `--prompt` reaches every LLM route, srt books included: there its sections sit on top of the subtitle loader's own prompt, section by section, and replacing the `user` template means saying yourself that the block number and timeline must come back unchanged.

You can also set the `user` and `system` role prompt by setting environment variables: `BBM_CHATGPTAPI_USER_MSG_TEMPLATE` and `BBM_CHATGPTAPI_SYS_MSG` (gemini reads `BBM_GEMINIAPI_USER_MSG_TEMPLATE` and `BBM_GEMINIAPI_SYS_MSG`). `--prompt` outranks all of them. `OPENAI_API_SYS_MSG` is still read as a fallback but is deprecated — it used to outrank `--prompt`'s own system message.

- A `.md` file is read as the [PromptDown](https://github.com/btfranklin/promptdown) **block** form. The format is theirs; the reader is this repo's own, so no extra package is installed. `--prompt prompt_md.prompt.md`

        # Translation Prompt

        ## System Message

        You are a professional translator who specializes in accurate translations.

        ## Conversation

        **User:**

        Please translate the following text into {language}:

        {text}

  Three sections are read: `## System Message` (`## Developer Message` is
  accepted as another name for it), an optional `## Style`, and
  `## Conversation`, whose first `**User:**` turn is the `user` template. A
  conversation written as a `| Role | Content |` table is refused — the
  template's newlines cannot survive a table cell.

## Examples
```sh
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template.json
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"
```
