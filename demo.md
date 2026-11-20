# Demo: better sources through document metadata

**Lead with the supplier-payment question.** It is the strongest verified contrast:
RAW retrieves policy/training material alongside agreements; SMART restricts retrieval to
documents classified as contracts. This demonstrates retrieval-augmented generation (RAG)
with better source selection, not a better language model.

Evidence below comes from **cosmic-wombat**, corpus **2026.1**, recorded on **2026-11-17**.
Both prompt agents were version **1**, using `gpt-5-mini`, identical instructions, hybrid
search and `top_k=5`. RAW means the CLI's `simple` agent.

## 1. Before presenting

Run PowerShell from the repository root:

```powershell
$participant = "cosmic-wombat"
uv run fomo status --participant $participant
```

Confirm both indexes contain 25 documents, the smart index has six `contract` documents,
and both RAG agents exist. Do not reset/reclassify/redeploy during this demo.
Use another participant only after rehearsing against their actual metadata.

**Cost:** the live commands below invoke paid models. Obtain approval before running them.
Section 5 replays existing answers locally with no model calls.

## 2. Main scene: same question, different source quality

Ask RAW first, then SMART. Keep the question identical:

```powershell
$participant = "cosmic-wombat"
$question = "What payment terms apply under our supplier contracts?"

uv run fomo agents ask simple $question --participant $participant
uv run fomo agents ask smart  $question --participant $participant
```

**Presenter:** "We need the terms in our supplier agreements. A procurement default and a
training example can contain the same words without being the agreement that governs payment."

Look at the **source titles**, then the answer:

| Recorded Foundry answer | RAW / Simple | SMART |
|---|---|---|
| Source scope | PP-07 policy, contract-management training, three agreements | Five agreements; no policy or training |
| Relevant cited documents | 3 of 5 | 5 of 5 |
| Missing agreement terms | Vessel, rig maintenance, helicopter | Helicopter |
| Answer completeness, 1-5 | 3 | 4 |

RAW opened with "Summary - default policy and examples from our contracts" and discussed
the 30-day procurement default and training aspirations. SMART answered from supplier
agreements, including vessel payment within 45 days and rig payment 30 days after month-end.
The consumables agreement is 60 days end of month, with a 2% discount within 10 days.

**Presenter:** "The policy and deck are real documents, but they are the wrong authority for
this question. Metadata lets Search exclude those document types before the answer is written."

Do not call RAW's policy statement fabricated: it was sourced, but it does not substitute for
the actual agreement terms. Do not call SMART exhaustive: there are six agreements and this
recorded answer covered five.

## 3. Show the mechanism, not just the prose

This command runs direct Search comparisons without calling either answer agent or an
evaluation judge. Search query vectorization still uses the embedding deployment.

```powershell
$participant = "cosmic-wombat"
$report = uv run fomo eval --participant $participant --queries q01 --k 5 --agents none --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Retrieval comparison failed; inspect the CLI error." }

$report.retrieval |
    Where-Object { $_.run -in @("raw", "smart_nofilter", "smart_contract") } |
    Select-Object run, precision, recall,
        @{Name="documents"; Expression={ ($_.top_docs.doc_id -join ", ") }} |
    Format-Table -AutoSize
```

The recorded direct-Search result for this question was:

| Scope | Relevant documents in top five | Precision at five |
|---|---|---:|
| RAW, no filter | doc-009, doc-018 | 0.40 |
| SMART, contract filter | doc-009, doc-013, doc-018, doc-021, doc-001 | 1.00 |

RAW's other hits were doc-002 (policy), doc-003 (presentation), and doc-024
(correspondence). These retrieval measurements and the Foundry answer measurements above
are **separate recorded calls**; do not claim identical ranking or document sets.

Show what SMART searched; the CLI prints the document types it retrieved under the answer
(`fomo eval` also shows the filter the knowledge base generated):

```text
retrieved document types: contract
```

**Presenter:** "The miner classifies document content and writes metadata to Blob Storage.
The Search indexer copies `document_type` into a filterable field. SMART's knowledge base reads the
question, decides that payment terms live in contracts, and applies that filter itself. Nothing
is hardcoded: its options are the classifier's labels and definitions. The generative model and
retrieval depth are unchanged."

Use `smart_nofilter` as the control: merely creating another index should not produce a large
gain. The restriction enabled by the metadata is the intervention. Ranking can vary between
calls; investigate a materially different unfiltered control before attributing a win to metadata.

## 4. Optional second scene: remove a misleading source type

```powershell
$participant = "cosmic-wombat"
$question = "Which supplier agreements offer a discount for early payment?"

uv run fomo agents ask simple $question --participant $participant
uv run fomo agents ask smart  $question --participant $participant
```

Look for **Master Purchase Terms for Drilling Consumables**, doc-009: **2% within 10 days**.
It is a contract despite not having "contract" in its title. The raw retrieval also surfaced
policy/training material; SMART's contract filter removes those types.

This is a **source-selection illustration, not a verified large answer-quality win**: both
agents answered this correctly in the full Foundry run. Skip it if it adds no visible contrast.
The vessel-interest question is also not a strong failure demo: both agents found 1.5% per month.

## 5. Reliable fallback: replay the recorded paired answers

Use this if live answers converge, a tool returns a placeholder, or Azure is unavailable.
Say explicitly: "These are the saved outputs of the paired Foundry run."
Do not keep retrying and silently show only a favorable attempt.

```powershell
$file = ".foundry\results\workshop\eval_7449a865508b4eea874ebcd9af29277e\comparison.json"
if (-not (Test-Path -LiteralPath $file)) {
    throw "Recorded Foundry outputs are not on this machine; use the portal links below."
}
$recorded = Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
foreach ($role in @("simple", "smart")) {
    $item = $recorded.runs.$role.rows | Where-Object { $_.id -eq "rag01" }
    if (-not $item) { throw "rag01 is missing from the $role recording." }
    Write-Host "`n=== $role : $($item.query) ==="
    $item.answer
    Write-Host "Cited documents: $($item.cited_doc_ids -join ', ')"
    Write-Host "Completeness: $($item.scores.response_completeness.score)/5"
}
```

The cache is ignored by Git and is not included in a fresh clone. Existing cloud results:
[Simple RAG](https://ai.azure.com/nextgen/r/kTTuk3YzQSKuiCQAXDldSw,rg-contoso-oil-dev,,contosooil-xd4qoy4xkasx4,contoso-oil/build/evaluations/eval_7449a865508b4eea874ebcd9af29277e/run/evalrun_be2550738e504181bfe99d300d4b4758)
and
[Smart RAG](https://ai.azure.com/nextgen/r/kTTuk3YzQSKuiCQAXDldSw,rg-contoso-oil-dev,,contosooil-xd4qoy4xkasx4,contoso-oil/build/evaluations/eval_7449a865508b4eea874ebcd9af29277e/run/evalrun_dee226162c3d42c0993cfbf4388f0f0b).
Open `rag01`; the portal requires access to the project.

## 6. Close with the boundary

**Presenter:** "Metadata improves source selection when its filter matches the question.
That is why SMART picks the filter per question instead of using one fixed scope."

The recorded 20-question Foundry comparison below predates routing: SMART then used a **fixed
contract filter** and did **not** show an overall win:
overall completeness was **4.55 RAW vs 3.45 SMART**; contract-only means were
**4.36 vs 4.29**. SMART excluded invoice, policy and technical-report evidence and had more
tool-placeholder responses. One SMART similarity grade was skipped; the completeness
figures above were fully scored. Rerun `fomo eval-foundry` before quoting numbers for the
routed agent.

For a quick routing check, ask SMART:
"How much were we invoiced for the emergency coffee airlift in March?"
The evidence is invoice doc-019, **GBP 4,880**; SMART should choose
`document_type eq 'invoice'`. A fixed contract filter would exclude it (section 7).

## 7. The rigorous answer: which part of "smart" helps

Pre-registered five-variant comparison ([protocol](docs/evaluation-protocol.md)): 40 held-out
test questions × 3 repeats, objective fact/distractor/citation checks, `gpt-5.4` judge,
paired bootstrap 95% CIs over questions. Run `20261118T120640Z-cosmic-wombat-test-ad5708`.

| Finding (test split) | Simple | Fixed contract filter | Decision 1.1-routed filter |
|---|---:|---:|---:|
| Wrong-type citations, contract questions with distractors (H5) | 28% | **0%** | **5%** |
| Facts found, non-contract questions (H2) | 97% | **4%** | **100%** |
| Clean answers, all strata (bottom line) | 87% | 57% | **93%** (+7 pts, CI 0.01-0.13, 4 wins / 0 losses) |

**Presenter:** "Same model, same content. Metadata stops wrong-type documents reaching the
answer. Used as one fixed filter it breaks other questions; routed per question it helps
across the board."

Also measured: re-indexing alone changes nothing (H3, CI ±0.04); the routed variant reaches the
oracle filter (H4); clean answers on distractor-heavy contract questions rose 69% → 82-85%,
but that CI touches 0 (H1 inconclusive, 13 questions). In the ladder the routed variant is a
Decision 1.1 router + per-type agents orchestrated by the harness; the deployed SMART agent does
the same routing inside its Foundry IQ knowledge base (query planner + `document_type` hint).
Metadata labels in these indexes were produced by Decision 1; the router used Decision 1.1.

Decision 1.1 updates the **classifier**, not the generative RAG model. These recordings used
Decision 1 metadata. Do not relabel them as Decision 1.1 results or promise stronger answers
from the model upgrade without reclassifying, reindexing and evaluating again.
