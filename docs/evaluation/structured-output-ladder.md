# Why the tool probes the endpoint and steps down a ladder instead of trusting the schema flag

## Abstract

A grouped request asks for a JSON object that echoes each unit's id next to its translation. Many OpenAI-compatible endpoints accept the `response_format` field and then ignore it, or refuse it, or return JSON wrapped in a code fence. So the tool does not trust the field. It sends one probe request per endpoint, grades the answer, remembers the verdict, and uses the strongest method the endpoint actually honors: a strict JSON schema, JSON object mode, a delimiter format, or a plain prompt. Three smoke and stability runs and one 210-request study support this. A reseller serving a Claude model over the OpenAI wire format returned 0 of 30 schema-valid replies, while the translations inside them were fine. The failure was the wire format, not the model.

## Setup

- **Schema-drop proxy smoke (September 2, 2026).** A local proxy in front of OpenAI's endpoint that removes `response_format` (drop mode) or answers it with a 400 (reject mode). animal_farm, `--test --test_num 8 --quiet`, `gpt-5.6-luna`. Three runs: drop, reject, and drop with `--use_context session`. Every `id=` and `href=` in the 19 output files was diffed against the source.
- **Router stability (September 2, 2026).** A third-party gateway, `gpt-5.6-terra` and `grok-4.6`, animal_farm, eight scenarios each (baseline, session, a tiny compaction budget, grouping, classification, parallel workers, model listing, temperature).
- **The degradation study (September 4, 2026).** 210 byte-identical requests over five arms, including the same model on the official endpoint and on a reseller, and a Claude model on the reseller. See [Why 16 units per request](grouping-batch-size.md).
- **Marker syntax (September 4, 2026).** Inline markup inside a unit is replaced by numbered markers. Six marker syntaxes, 20 requests of 5 paragraphs each per arm, the production request shape, `gpt-5.6-luna`, 100 marker slots per arm.

## Results

Schema-drop proxy:

| run | proxy | requests | verdict | epub |
|---|---|---|---|---|
| A | drop | 1 GET /models, 1 probe (dropped), 8 translations (no field) | `doesn't apply JSON schema (unsupported), using delimiter method`; `plan mode: off` | 8 Chinese paragraphs, each after its original, same tag and class |
| B | reject | 1 GET, 1 probe -> 400, 8 translations | `doesn't apply JSON schema (request rejected: Error code: 400 ...), using delimiter method` | same |
| C | drop + `--use_context session` | same as A | same as A | same |

- The 400 in reject mode degraded cleanly: no traceback and no retry storm.
- Nothing was lost in any run: every `id` and `href` survived, and no delimiter, marker or JSON residue reached the text.
- In run A only, the book's title came back as a refusal, and the refusal was written into the EPUB as the translation. Runs B and C translated it. **The delimiter rung has no refusal detection**; only the structured path does.

Router stability: every completed run exited 0 with `plan mode: on (endpoint verified strict JSON schema)`, no alignment loss, and all 17 EPUBs clean (ids 52/52, hrefs 60/60, 0 orphans, 0 duplicates). The gateway listed models it could not serve (HTTP 503 `model_not_found`) and stalled some requests with no bytes back. The fixes that came out of it were on our side: a request timeout (300 s, one client retry) where there had been none, and a log that no longer went silent during a stall.

Degradation study, the parts about endpoints:

- The same model (`gpt-5.6-luna`) on the official endpoint and on a reseller: zero structural faults on both sides over byte-identical batches. The reseller's only measured cost was 2.4 to 6 times the latency.
- `claude-sonnet-5` on the reseller over the OpenAI wire format: **0/30 schema-valid JSON**. The replies were fenced in code blocks and carried unescaped quotes inside strings. Re-parsed leniently, the translations were fine.
- `deepseek-v4-flash` through OpenRouter echoed some terse technical units back unchanged (17 slots at 1000 characters), which the id echo cannot see.

Marker syntax, clean slots out of 100:

| arm | syntax | clean slots /100 | faults |
|---|---|---|---|
| wsq (current) | `⟦code1⟧` | 99 | 1 dup |
| mustache | `{{code1}}` | 99 | 1 dup |
| pct | `%%code1%%` | 99 | 1 dup |
| wtor | `⟬code1⟭` | 98 | 1 dup, 1 missing |
| xml | `<code1/>` | 98 | 1 dup, 1 **mangled** |
| dsq | `[[code1]]` | 97 | 1 dup, 2 missing |

The shared duplicate is one paragraph whose Chinese grammar names the referent twice; the tool deduplicates it. At 100 slots per arm the 97 to 99 spread is inside noise (95% CI about ±3). XML-shaped tokens invite the model to treat them as markup: the one true mangle rewrote `<code1/>` into `<a1/>`.

## Decision

- **Probe once, remember, and step down.** Each endpoint gets one small probe request. Its verdict (strict schema, schema shape only, JSON object, none) is stored and printed, for example `… doesn't apply JSON schema (…), using delimiter method`. Translation then uses the strongest method the verdict allows. A reply that still cannot be aligned is retried in halves, down to single units, so the worst case is the old one-paragraph-per-request behavior, not a broken book.
- **Plan classification does not need a schema.** Where there is no usable verdict, the plan is decided over a plain conversation with one-word answers (`plan mode: on (no structured output here; classifying over a plain session)`). In the September 2 smoke, plan mode was still off without a schema; that has changed since.
- **Halve the request on an endpoint without a strict schema:** 8 units, 800 tokens. See [Why 16 units per request](grouping-batch-size.md).
- **Keep `⟦tagN⟧` markers.** No measurable penalty on luna, and a reason to avoid XML-shaped and double-bracket tokens.
- **Do not pre-shrink for a router.** Let the probe grade it; what degrades on a router is the wire format, which the ladder absorbs.

What would change it: an endpoint whose probe verdict and real behavior disagree, or a refusal written into a book on the delimiter rung often enough to justify refusal detection there.

## Limits

- The proxy smoke is 8 paragraphs of one book, three runs.
- The router test covered one gateway and two models.
- The marker eval ran on one strong model; weak models were not measured.
- Refusal detection on the delimiter rung is recorded as missing, not fixed.

Source: docs/260902-eval-SCHEMA_DROP_PROXY_SMOKE.md, docs/260902-eval-ROUTER_STABILITY_TWO_MODELS.md, docs/260904-eval-MARKER_SYNTAX_COMPLIANCE-BATCH_SWEEP.md, docs/260904-eval-GENERAL_BATCH_DEGRADATION-RESULTS.md (repository, dated records)
