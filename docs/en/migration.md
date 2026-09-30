# Migrating from the old flags

Commands written for the old CLI keep working. Each removed flag is rewritten
into the new flags before the run starts, and a line says what it became:

```
$ bbook_maker --book_name book.epub --model gpt4omini --openai_key sk-...
deprecated: --openai_key is now --key
deprecated: --model gpt4omini is now --model gpt-4o-mini
```

| Old | Rewritten to |
|---|---|
| `--model gpt4o` / `gpt4omini` / `o3mini` | `--model` with that model's id |
| `--model chatgptapi` / `openai` | dropped: the openai format is the default, and `--model` names a model when you want one |
| `--model openai --model_list X` | `--model_list X` |
| `--model claude` | `--model claude-haiku-4-5-20251001` |
| an exact `claude-*` id | unchanged; the anthropic format is inferred from the id |
| `--model gemini` / `geminipro` | `--api_format gemini --model gemini-flash-latest` / `gemini-pro-latest` |
| `--model qwen` / `qwen-mt-turbo` / `qwen-mt-plus` | `--api_format qwen --model qwen-mt-*` |
| `--model groq --model_list X` | `--api_format groq --model_list X` |
| `--model xai` | `--api_format xai --model grok-4.3` |
| `--model codex` | `--api_format codex` (the sidecar's default model; add `--model` to name one) |
| `--model google` / `caiyun` / `deepl` / `deeplfree` / `tencentransmart` | `--api_format google` / `caiyun` / `deepl` / `deeplfree` / `tencent` |
| `--custom_api URL` | `--api_format customapi --api_base URL` |
| `--openai_key` / `--claude_key` / `--gemini_key` / `--groq_key` / `--xai_key` / `--qwen_key` / `--caiyun_key` / `--deepl_key` / `--orcarouter_key` | `--key` (`--api_key` is the same flag and was never renamed) |
| `--ollama_model M` | `--api_base http://localhost:11434/v1 --model M` |
| `--deployment_id D` | `--model D`, with `--api_base` rewritten to the deployment's `/openai/v1` path |

Notes:

- The old key variables still work for the route that used them:
  `BBM_GROQ_API_KEY` for a rewritten `--model groq`, `BBM_GOOGLE_GEMINI_KEY`
  for a rewritten `--model gemini`, and so on.
- Flags you pass yourself win. `--model gemini --api_base https://my-gateway/v1`
  keeps your gateway.
- `--interval` is not a legacy flag: it is still in the parser and still
  paces the gemini route, which is still a route.
- `qwen-mt-turbo` and `qwen-mt-plus` are real model ids, so a command that
  also passes `--api_format` is left alone and nothing is printed about it.
- The other route aliases are not model ids anywhere. `--model gemini`
  together with an `--api_format` naming a different route is refused rather
  than resolved one way or the other: honouring the format would send that
  alias's key to a host it does not belong to, and honouring the alias would
  ignore what you typed. The error names both and the two ways out.
- A rewritten command runs the model it used to run, taken from the old
  preset list, not a newer one. Some of those models have since been
  retired, and the endpoint's model check says so.
- A `--model` value that is not an old alias passes through as a model id,
  which is the normal case now.
- The aliases for retired OpenAI models (`gpt4`, `gpt5mini`, `o1`, `o1mini`,
  `o1preview`) are gone. They now pass through as model ids and the endpoint
  rejects them by name, which is the same failure one step earlier and with
  a clearer message.
