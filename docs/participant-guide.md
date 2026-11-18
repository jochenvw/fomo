# Participant guide (60 minutes)

You get a **hackername** (e.g. `cosmic-wombat`). Everything you create is prefixed with it.
Every command below **plans first**; add `--apply` to execute. The CLI remembers your
hackername after the first `--participant`.

## 0. Setup (5 min, before the session if possible)

```powershell
git clone <this repo>; cd <repo>
uv sync
az login                      # pick the workshop subscription
uv run contoso env pull --subscription <sub-id> --resource-group <rg>   # or copy .workshop/environment.json from the facilitator
uv run contoso preflight --local-only
```

With Copilot CLI: `copilot`, then *"Use the /contoso-preflight skill"*.

## 1. Baseline: Simple RAG (10 min)

```powershell
uv run contoso provision --participant cosmic-wombat          # read the plan
uv run contoso provision --participant cosmic-wombat --apply
uv run contoso agents ask simple "What payment terms apply under our supplier contracts?"
```

Creates: container `cosmic-wombat-inbox` with 25 documents, index `cosmic-wombat-raw`,
agent `cosmic-wombat-simple-rag`, and your starter folder `.workshop/runs/cosmic-wombat/miner/`.

**Look at the citations.** You will likely see `doc-003` (a *training deck about* contract
management) and `doc-002` (a procurement *policy*). They talk like contracts but are not.

## 2. Build the Metadata Miner (20 min)

Open `.workshop/runs/cosmic-wombat/miner/classifier.yaml`:

| Key | What you decide |
|---|---|
| `instructions` | how Decision-1 should judge a document |
| `criteria` | one key per label, a description each; finish the `TODO`s |
| `thresholds.min_probability`, `min_margin` | when to abstain (`needs_review`) |
| `version` | bump on every change; drives delta re-classification |

Try it locally (read-only, ~1 s/doc):

```powershell
uv run python .workshop/runs/cosmic-wombat/miner/try_classifier.py doc-003 doc-007 doc-018
```

Deploy and run it **as a hosted Foundry agent**:

```powershell
uv run contoso miner deploy --classifier .workshop/runs/cosmic-wombat/miner/classifier.yaml --apply   # ~60-70 s
uv run contoso miner run --hosted --apply                                                           # ~60-90 s
```

The first run may wait 1-2 minutes for fresh role assignments; the CLI retries automatically.
Re-running only classifies changed blobs or a new `version`.

## 3. Smart RAG (10 min)

```powershell
uv run contoso smart --apply
uv run contoso agents ask smart "What payment terms apply under our supplier contracts?"
```

Creates index `cosmic-wombat-smart` (with `document_type`) and agent `cosmic-wombat-smart-rag`
filtered on `document_type eq 'contract'`.

## 4. Compare (10 min)

```powershell
uv run contoso eval
```

Reference run (see [`examples/sample-evaluation.md`](../examples/sample-evaluation.md)):

| Question | Raw P@5 | Smart + contract filter P@5 |
|---|---|---|
| q01 payment terms in supplier contracts | 0.40 | **1.00** |
| non-contract questions (q05, q06, q08) | > 0 | **0.00** |

Discussion: the filter wins where the question is about contracts and **hurts** everything else.
The `smart_desired` row (per-question filter) shows what intent-aware filtering would buy.

### Foundry cloud evaluation

For scored answer quality in the Foundry portal (not just direct Search retrieval), use:

```powershell
uv run contoso eval-foundry --participant cosmic-wombat --suite smoke --plan
uv run contoso eval-foundry --participant cosmic-wombat --suite smoke --apply
uv run contoso eval-foundry --participant cosmic-wombat --suite full --apply
```

Replace `cosmic-wombat` with your participant name. The default is a four-question smoke
suite; the full versioned set in `corpus/rag_eval.json` contains 14 contract questions,
four non-contract controls and two missing-answer cases. Every reference includes actual
corpus evidence, checked against both live indexes before submitting. References go only
to the judges, never into agent prompts.

The Foundry SDK registers the dataset and starts two version-pinned agent-target runs in
one evaluation group. Both use the same relevance, reference-groundedness, similarity and
completeness evaluators (threshold 4/5), dataset and judge deployment. If dataset registration
is unavailable, inline inputs are used and the reason is recorded; no storage/connection
settings are changed. This incurs agent and judge usage charges.

IDs, individual answers, judge reasons, citation metrics, separate category scores and
portal links are saved under `.foundry/results/`; `.foundry/agent-metadata.yaml` tracks the
latest run. Groundedness uses curated reference documents, not the actual retrieved tool
context. Latency is reported only when Foundry supplies it. Do not interpret one small run
as statistical proof or hide non-contract regressions from the fixed contract filter.

To resume polling/downloading without submitting new paid runs:

```powershell
uv run contoso eval-foundry --resume .foundry\results\workshop\<eval-id>\manifest.json
```

### Rigorous comparison (facilitator / after the workshop)

`eval-ladder` answers *which part* of Smart RAG helps or hurts. It compares five variants that
differ only by index/filter (Simple, Smart without filter, fixed contract filter, Decision
1.1-routed filter, oracle filter). It uses about 60 verified questions with a dev/test split,
repeated answers, objective fact/distractor/citation metrics, a different judge model, and
paired bootstrap confidence intervals. Rules are fixed in
[evaluation-protocol.md](evaluation-protocol.md).

```powershell
uv run contoso eval-ladder --participant cosmic-wombat --split dev  --repeats 1           # plan
uv run contoso eval-ladder --participant cosmic-wombat --split dev  --repeats 1 --apply   # pipeline check
uv run contoso eval-ladder --participant cosmic-wombat --split test --repeats 3 --apply   # headline
uv run contoso eval-ladder --resume .foundry\results\ladder\<run>\manifest.json --report-only
```

The first apply creates six evaluation-only prompt agents (`<name>-smart-rag-<type>` and
`-any`); `contoso cleanup` removes them. Every apply needs its own approval.

## 5. Stuck or late?

| Situation | Command |
|---|---|
| Where am I? | `uv run contoso status` |
| Jump to the end | `uv run contoso cheatcode --apply` (~5 min, reference classifier) |
| Redo classification | `uv run contoso reset-participant --scope metadata --apply` |
| Something failed | [troubleshooting.md](troubleshooting.md); logs in `.workshop/runs/<name>/` |
| Done | `uv run contoso cleanup --apply` (asks you to type `DELETE`) |
