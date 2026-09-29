# Environment Settings

You can write credentials into the environment to skip `--key`.

## API key

`BBM_API_KEY` covers every format, so one variable is usually enough:

```
export BBM_API_KEY=${your_api_key}
```

If it is not set, each format also looks at the variable people already
export for that vendor:

| `--api_format` | Fallback variables |
|---|---|
| `openai` | `OPENAI_API_KEY`, `BBM_OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY`, `BBM_CLAUDE_API_KEY` |
| `gemini` | `BBM_GOOGLE_GEMINI_KEY`, `GEMINI_API_KEY` |
| `qwen` | `BBM_QWEN_API_KEY`, `DASHSCOPE_API_KEY` |
| `groq` | `BBM_GROQ_API_KEY`, `GROQ_API_KEY` |
| `xai` | `BBM_XAI_API_KEY`, `XAI_API_KEY` |
| `litellm` | `BBM_LITELLM_API_KEY`, `LITELLM_MASTER_KEY` |
| `caiyun` | `BBM_CAIYUN_API_KEY` |
| `deepl` | `BBM_DEEPL_API_KEY` |

`google`, `deeplfree`, `tencent` and `customapi` need no key, and neither
does an endpoint on localhost — a LiteLLM proxy there included, which is
where `--api_format litellm` points unless `--api_base` says otherwise.

The CLI does not read `.env` files. Export the variables first, or source a
git-ignored file before running: `set -a; source .env; set +a; bbook_maker ...`

## A provider's own variable

`--provider NAME` reads the endpoint from `bbm_providers.json` in the working
directory, else `~/.bbm/providers.json`. When that entry has an `env_key`,
the named variable is consulted for the key ahead of `BBM_API_KEY` and the
fallbacks above — it names the endpoint being called, so its own key is the
right one. It is consulted only while the run still calls that endpoint: an
`--api_base` elsewhere, or an `--api_format` override on an entry that names
no `base_url`, moves the request to another host, and the entry's key is not
sent there. The run says so; pass `--key` when you meant to reuse it. The file holds the address and the variable name, never a secret.
See [Endpoints, models and languages](./model_lang.md#named-endpoints-provider).
`--model orcarouter` reads `BBM_ORCAROUTER_API_KEY` the same way.

## Classifier variables

A [Jev-compatible classifier](providers.md#jev-and-jev-compatible-classifiers) reads its key only for its own host:

| variable | read for |
|---|---|
| `JEV_API_KEY`, `TYPESAFE_API_KEY` | TypeSafe's Jev at a typesafe.ai address |
| `FEATHERLESS_API_KEY` | Simple Jev at a featherless.ai address |

Through a gateway, name the key with `--classify-key` or a provider entry's `classify_env_key`.

`BBM_JEV_MIN_CONFIDENCE`, a number from 0 to 1, sets Jev's gate for a run without `--classify-min-confidence` (the flag wins): a `skip` below it is translated. The default is 0.95. A value that is not a number from 0 to 1 stops the run.

## Old per-vendor variables

The four vendor variables above are also read when an old-style command
implies that route — `--model gemini` consults `BBM_GOOGLE_GEMINI_KEY` even
though the rewritten command names the format (see [Migrating from the old
flags](migration.md)). What they are never consulted for is a command whose
`--api_base` names some other endpoint: there the key must match the address,
and only `BBM_API_KEY` and the format's own variables apply.

## Prompt overrides

```
export BBM_CHATGPTAPI_USER_MSG_TEMPLATE=${your_prompt_template}
export BBM_CHATGPTAPI_SYS_MSG=${your_system_message}
```
