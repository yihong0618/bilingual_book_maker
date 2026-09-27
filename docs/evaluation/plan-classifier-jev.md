# Can a dedicated classifier decide the plan? Jev against gpt-5.6-luna

## Abstract

Plan mode asks a model, for each kind of block in an EPUB, whether it is worth translating. That is a labeling job, not a writing job, so a dedicated classifier could do it. Jev, TypeSafe's System One classifier, answers typed multiple-choice questions with a confidence. The same 31 signatures of the test book were classified by Jev and by gpt-5.6-luna. They agreed on 27. All 4 disagreements were Jev choosing `skip` where luna chose `translate`, each at low confidence. Jev used about twice luna's prompt tokens. A second round sent each page's shared text once: Jev's prompt tokens halved, and its agreement with luna moved no more than between two Jev runs of the same code. The same round found that a gate at 0.5 on Jev's probability could never fire on a two-option question; the gate was then measured on a 45-EPUB corpus and set to 0.95. Verdict: Jev works as a classify model; the default stays the translating model.

## Setup

- **Book:** `test_books/animal_farm.epub`, the repository's test book. Its plan has 31 tag signatures.
- **Run:** `--test --test_num 8 --quiet --language zh-hans`, translating with gpt-5.6-luna, under a 1500 MB memory cap.
- **Luna arm:** `--model gpt-5.6-luna --classify-model gpt-5.6-luna`. The endpoint verifies a strict JSON schema, so the signatures go in pages of several per request.
- **Jev arm:** `--model gpt-5.6-luna --classify-model typesafe-ai/jev --classify-base-url https://ai-gateway.vercel.sh/typesafe`. One Choice question per signature. In this first round, below 0.5 confidence in its top answer, Jev answered `unsure`.
- **Fixed-engine cell:** `--api_format google --classify-model gpt-5.6-luna`, to check that a machine-translation route gets a plan from an LLM classifier.
- **Compared:** each signature's verdict between the two arms. Nobody judged which verdict was right.

## Results

Copied from the record:

| cell | classification | classifier usage line |
|---|---|---|
| luna | 31 signatures in 3 requests, 4 skips | `Classifier (gpt-5.6-luna at the endpoint's default host): tokens: in 7.6k, out 1.8k, cached 0 (3 requests)` |
| Jev | one 503 retry, then 31 verdicts, 8 skips | `Classifier (typesafe-ai/jev at https://ai-gateway.vercel.sh/typesafe): tokens: in 14.4k, out 1.1k, cached 0 (3 requests)` |

**27 of 31 agree.** All 4 disagreements are Jev choosing skip where luna chose translate, and all 4 were Jev's low-confidence calls (0.58–0.61):

| Signature | Sample text |
|---|---|
| `inline:span.underline` | www.ericseat.com |
| `inline:span.calibre_4` | www.ericseat.com |
| `inline:span.calibre_16` | "M" |
| `inline:span.calibre3` | George Orwell |

Where the two agree, Jev's confidence is mostly above 0.9. It used about twice luna's prompt tokens (14.4k vs 7.6k), since each signature is sent as its own question.

**Fixed engine with an LLM classifier.** Google Translate with gpt-5.6-luna as the classifier: `llm classification: 31 verdict(s), 4 skip(s)`, `Translation plan: … coverage 99.8%`, and the classifier's line `Classifier (gpt-5.6-luna …): tokens: in 7.6k, out 1.6k (3 requests)`. The translations sat beside their originals and matched them (George Orwell → 乔治·奥威尔).

A second Jev run, after the key-routing fixes, retried three 503s and ended with `llm classification: 31 verdict(s), 8 skip(s)` and `Translation plan: 20 documents, 202560 chars, coverage 99.7%`.

### Second round: lean requests and the gate

Each Jev question used to repeat the whole per-signature prompt. The lean mapping sends the page's shared text once as the state, and each question carries only its own instruction. A doubtful `skip` now falls back to `translate` instead of `unsure`. Same book, same flags, plus the Simple Jev demo. Copied from the record:

| arm | classifier tokens in / out | requests, latency each | non-translate verdicts | agreement with c_final (luna) | agreement with c_before (luna) |
|---|---|---|---|---|---|
| (a) before, F's mapping, gateway | 14.4k / 1.1k | 3, after four 503 retries (wall 36 s) | 7 | 27/31 | 26/31 |
| (a) after lean mapping only (`a_lean`) | 7.8k / 1.1k | 3 (wall 14 s) | 9 | 25/31 | not computed |
| (a) final, lean + gate | 7.8k / 1.1k | 3: 0.87, 0.37, 0.67 s | 9 | 25/31 | 26/31 |
| (b) Simple Jev keyless demo | 16.7k / 31 | 3: 2.26, 2.05, 0.90 s | 4 | 30/31 | 29/31 |
| (c) luna schema (`c_final`) | 7.6k / ~1.4k | 3: 5.07, 6.03, 4.15 s | 5 | — | 30/31 |

The two luna runs agree 30/31 with each other, and the two Jev runs on the same code (`a_lean`, `a_final`) agree 29/31.

**The lean mapping halves Jev's prompt tokens (14.4k → 7.8k) and cuts wall time (36 s → 14 s incl. no retries); agreement did not move beyond Jev's own run-to-run variation.**

**The gate finding**, copied from the record:

> The official Jev's `confidence` field is not the chosen option's probability. On two options it is about `2p − 1` (0.51 → 0.02, 0.61 → 0.22, 0.76 → 0.53). Simple Jev's is `max p`. The code keeps F's choice and reads `probabilities[choice]` first. That is the same measure on both servers, but on two options it never falls below 0.5, so a 0.5 gate does nothing.
>
> Replaying `a_final` offline: a gate at 0.75 on the probability, which is the same as 0.5 on Jev's own `confidence`, would turn all 5 low-confidence skips into translate and keep the 4 confident ones. Agreement with luna would go to 30/31. That is one book and 31 signatures.

That was one book and 31 signatures, so 0.75 was a hint, not a setting. The gate was then measured on a corpus: 0.95 on the chosen option's probability, over the 662 plan signatures of the 45-EPUB corpus against gpt-5.6-luna, with a lost skip costing 10 and an extra translate 1. At that value about nine of ten of Jev's skips fall back to `translate`, and what survives is apparatus: copyright lines, line numbers, note marks, index locators. Never skipping would cost 140 against 115 gated, so Jev's saving over translating everything is modest on this corpus.

## Decision

- Jev is available as a classify model: `--classify-model jev` at TypeSafe's own address, a gateway's id with `--classify-base-url` and `--classify-key`, or a Jev-compatible server such as Simple Jev. See [Provider file and extra models](../providers.md#jev-and-jev-compatible-classifiers).
- The default classifier stays the translating model.
- First round: the abstain threshold stayed at 0.5; the four low-confidence skips were recorded, not judged (two URLs, a single letter and a bare author name, which a reading edition could keep or translate).
- Second round: the requests are lean (the shared text sent once), and the threshold became an asymmetric gate. A `skip` below it becomes `translate`; a `translate` is taken at any probability. Content is never lost to a doubtful skip.
- The gate is 0.95 on the chosen option's probability, from the corpus measurement above. `--classify-min-confidence P` moves it for a run (`BBM_JEV_MIN_CONFIDENCE` does the same without the flag; the flag wins) for a run.

What would change it: a comparison on more books, with verdicts judged against what a reader wants, and a price for both arms.

## Limits

- One book, 31 signatures, one run per arm in each round. Both rounds' agreement numbers, the lean mapping's token halving and the 0.75 replay rest on that one book.
- Only the gate value comes from the 45-EPUB corpus, and agreement there is still agreement with luna. The Simple Jev demo used about twice luna's prompt tokens (16.7k); it was run once.
- The key was a gateway key, so TypeSafe's own address was not exercised live (it answered 401 to that key).
- Token counts only; no money comparison.
- Agreement with luna is not correctness: luna's verdicts were not judged either.

Source: docs/260923-feat-ENDPOINT_OVERRIDES_CLASSIFIER_JEV.md (first round); docs/260924-feat-JEV_LEAN_REQUESTS_GATE_COMPATIBLE_ENDPOINTS.md (second round, the gate finding); docs/260924-eval-JEV_CONFIDENCE_THRESHOLD.md (the gate's value) (repository, dated records)
