# RAG evaluation protocol (pre-registered)

**Purpose:** show *which part* of Smart RAG changes answer quality and how certain that is, without
selecting favorable examples. This page is written before any test-split result exists. Changing
a rule after seeing test results invalidates the run; start a new dataset version instead.

Command: `uv run fomo eval-ladder` (plan) → `--apply`. Code: `src/fomo/evaluation/ladder.py`.
Dataset: `corpus/rag_eval_v2.json`. Earlier single-run comparison: `fomo eval-foundry` (kept for history).

## 1. Variants: one change at a time

| Variant | Index | Filter | Isolates |
|---|---|---|---|
| `simple` | raw | none | baseline (workshop Simple RAG) |
| `smart_nofilter` | smart | none | re-indexing alone (control) |
| `smart_contract` | smart | `document_type eq 'contract'` | fixed metadata scope (workshop Smart RAG) |
| `smart_routed` | smart | per question, chosen by Microsoft-Decision-1.1 | intent-aware metadata use |
| `smart_oracle` | smart | the question's true target type | ceiling for routing |

Model, instructions, query type (`vector_simple_hybrid`), `top_k=5` and citation policy are
identical; the harness refuses to run if agent definitions differ beyond index/filter or if the two
indexes hold different content. The router sees **only the question text** and falls back to the
unfiltered agent below probability 0.5. `smart_routed` is router + per-type agents orchestrated by
the harness, not one deployed agent.

## 2. Dataset

About 60 questions, every fact mechanically verified against the corpus text on load.

| Stratum | What it tests |
|---|---|
| `contract_distractor` | contract answer; a policy, training deck, invoice, letter or draft has competing wording |
| `contract_clean` | contract answer; no meaningful competition (metadata should not matter) |
| `other_type` | answer lives in an invoice, policy, technical report, letter/email/memo or deck |
| `unanswerable` | absent clause or false premise |

Each row has `required_facts` (each fact with paraphrase alternatives), `distractor_facts` (values
present in competing documents, absent from the sources, query, ground truth and required facts),
and source/distractor document ids. **dev** (≈⅓) is for pipeline checks and router wording;
**test** (≈⅔) is the headline and is not used for any tuning. References never reach agents.

## 3. Metrics

Objective, computed locally per answer:

| Metric | Definition |
|---|---|
| **`clean_answer` (primary)** | no operational failure, every required fact present, no distractor fact present |
| `fact_recall` | share of required facts present (canonical string match, see Amendment 1) |
| `distractor_mention` | any distractor fact present; undefined for operational failures |
| `off_target_citation` | share of cited documents whose true type cannot be the target type |
| `citation_recall` | share of source documents cited |
| `operational_failure` | error, empty, no Search call, or tool placeholder without citations |

Judged in Foundry by a **different, stronger model** than the agents (default `gpt-5.4`):
`builtin.response_completeness` vs the reference answer, and `builtin.groundedness` against the
full text of the documents the answer **actually cited**. Retrieval without a model: direct Search
per variant (P@5, R@5, nDCG@5, off-target share) plus Foundry `builtin.document_retrieval`.

Transport errors (429, 5xx, timeouts) are retried up to three times for every variant. Content is
never retried. Jobs run in seeded random order so no variant gets systematically better rate limits.

## 4. Statistics

- Unit of analysis = **question**. Repeats (3 for test) are averaged per question first.
- Paired difference vs baseline, **95% percentile bootstrap CI** over questions (5000 resamples, seed 2026).
- Verdict from the CI only: `better` / `worse` if the CI excludes 0, else `inconclusive`.
- Win/tie/loss counts per question are shown; ties = equal (objective) or within 0.5 (judge, 1-5 scale).
- Repeat spread (mean within-question standard deviation) shows how noisy each variant is.
- Only the contrasts below test hypotheses. Everything else is descriptive and not corrected for
  multiple comparisons.

## 5. Hypotheses and decision rules (test split)

| | Hypothesis | Contrast | Supported if |
|---|---|---|---|
| H1 | On `contract_distractor`, metadata filtering raises the clean-answer rate | `smart_routed` − `simple` (also reported: `smart_contract`) | CI lower bound > 0 |
| H2 | On `other_type`, the fixed contract filter loses facts; routing does not | `fact_recall`: `smart_contract` − `simple`; `smart_routed` − `simple` | fixed: CI upper < 0; routed: CI lower ≥ −0.10 |
| H3 | Re-indexing alone changes nothing | `smart_nofilter` − `simple`, all strata | CI within ±0.10 |
| H4 | Oracle bounds routing; the gap is router error | `smart_oracle` − `smart_routed`, all strata | CI lower ≥ −0.10 |
| — | Bottom line | `smart_routed` − `simple`, all strata, `clean_answer` | reported, no threshold |

### Amendment 1 (2026-11-18, after the dev pipeline check, before any test-split answer existed)

The dev check (21 questions, 1 repeat; `.foundry/results/ladder/20261118T111634Z-cosmic-wombat-dev-5339b4`)
showed two measurement problems and one ceiling effect. Changes:

1. **Matching.** Facts are matched in a canonical form (number words → digits, `(30)`-style
   parentheticals dropped, `%` → percent, currency symbols → codes, word/number boundaries). Most
   dev "missing facts" were paraphrases such as *first twelve (12) months* vs *first 12 months*.
2. **Dataset v2.1.** Required facts reduced to those necessary for the question as asked, as short
   canonical cores; general rules applied to all rows without access to any test answer.
3. **H5 added.** On `contract_distractor` every variant answered correctly in dev (clean 100%), so
   H1 is likely at ceiling for `gpt-5-mini` on this corpus. H1 stays registered unchanged. H5 tests
   where metadata acted in dev, source hygiene:

| | Hypothesis | Contrast | Supported if |
|---|---|---|---|
| H5 | On `contract_distractor`, filtering lowers wrong-type citations | `off_target_citation`: `smart_routed` − `simple` (also reported: `smart_contract`) | CI upper bound < 0 |

The dev run is a pipeline check and is not evidence for any hypothesis.

A contradicted or inconclusive hypothesis is reported as such. "Smart wins on question X" without
the corresponding CI is not a result.

## 6. Known limitations

1. String matching misses unlisted paraphrases; inspect `missing_facts` before blaming an agent.
2. `distractor_mention` also fires when an answer quotes a wrong value in order to reject it.
3. LLM judges remain opinions; they are secondary to the objective metrics.
4. 25 synthetic documents: effect sizes describe this corpus, not production collections.
5. Agent versions are recorded at start and re-checked at the end; a change aborts the run.
