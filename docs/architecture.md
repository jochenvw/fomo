# Architecture

## 1. Story in one paragraph

Contoso Oil asks a RAG agent "What payment terms apply under our supplier contracts?". Plain
retrieval returns a **training deck about contract management** (doc-003) and a **procurement
policy** (doc-002) next to real contracts, because they share the vocabulary. The fix is not a
better prompt but **better metadata**: a hosted *Metadata Miner* classifies every document with
**Microsoft-Decision-1.1**, writes `document_type` to Blob metadata, a second Azure AI Search
index makes it filterable, and *Smart RAG* decides per question which `document_type` holds the
answer ("payment terms" → `contract`) and searches only that type.

Smart RAG's search tool is an **OpenAPI tool** over the Search REST API, not the built-in Search
tool: the built-in tool fixes its filter per agent version and allows one tool per agent. The tool's
`filter` parameter is an enum of `document_type eq '<label>'`; each option carries the classifier's
own definition of that label, restricted to labels present in the smart index. The same text that
labelled the documents tells the agent when to search them. The tool authenticates through the
project connection `fomo-search-openapi` (project managed identity, Search Index Data Reader).

## 2. Components

| Layer | Owner | What | How it is created |
|---|---|---|---|
| Control plane (shared) | facilitator | Foundry account + project, 3 model deployments, Storage account, AI Search service, project→Search connection, RBAC | Bicep (`infra/`) |
| Search data plane (per participant) | participant | 1 data source, 2 skillsets, 2 indexes, 2 indexers | CLI → Search REST `2025-09-01` |
| Blob data (per participant) | participant | container `<name>-inbox`, 25 synthetic docs, classification metadata | CLI → Blob SDK (Entra ID) |
| Agents (per participant) | participant | Simple RAG + Smart RAG (prompt), Metadata Miner (hosted) | CLI → `azure-ai-projects` 2.x |
| Grants for the hosted agent | participant, ABAC-limited | Blob Data Contributor (container), Foundry User (account) | CLI post-deploy step |

Why the split: Bicep cannot create Search indexes/indexers, Blob contents or agent versions, and
the hosted agent's identity exists only after it is deployed.

## 3. Data flow

```
corpus/originals (PDF, PPTX, DOCX, TXT) --upload, checksummed--> Blob <name>-inbox
     |                                                               |
     |                         raw indexer --> <name>-raw   --> Simple RAG (no filter)
     |                                                               |
     |   Metadata Miner (Foundry hosted agent, own identity)         |
     |     list blobs -> extract text -> Decision-1 choice ->        |
     |     policy (threshold, margin, abstain) -> ETag-guarded       |
     |     metadata merge + checkpoint                               v
     |                         smart indexer --> <name>-smart --> Smart RAG (document_type chosen per question)
     v
 evaluation: direct Search top-k over 5 run specs (raw, smart no filter / contract /
             desired / boost) + both agents' citations vs ground truth
```

## 4. Fairness of the comparison

Both indexes use the **same** data source, text extraction, chunking (`SplitSkill`, 2000 chars,
200 overlap), embedding deployment (`text-embedding-3-small`, 1536 dims), vectorizer, hybrid
query and agent `top_k`. Differences:

- raw index has **no** classification fields, so the baseline cannot exploit metadata even after
  the miner writes it to the blobs;
- smart index maps `document_type`, `classification_confidence`, `classification_version` from
  blob metadata (filterable, facetable) and adds an optional `boost-contracts` scoring profile.

The evaluation also runs the smart index **without** a filter as a control: if that differs from
raw, the difference is not caused by classification.

## 5. Metadata Miner design

- **Hosted, not local.** Deployed from a deterministic source ZIP (`create_version_from_code`,
  Python 3.13, `invocations` protocol); no Docker or container registry. `miner run --local`
  exists as a clearly labeled learning fallback only.
- **Decision API.** `POST https://<account>.openai.azure.com/providers/microsoft/v1/systemone`
  with one `choice` question; options = criteria keys; per-option probabilities returned.
- **Policy.** Accept if `p >= min_probability` and `p - second >= min_margin`, else
  `needs_review`/`cannot_tell`. Labels validated against the configured option set.
- **Idempotency / delta.** Metadata stores source SHA-256 and classifier version. Unchanged
  blob + same version → skipped. Writes use ETag `if_match`; unrelated metadata keys survive.
- **Untrusted input.** Document text is data inside the `state`; the instructions tell the model
  to ignore embedded instructions; text is truncated to `max_chars`.
- **Participant surface.** Only `classifier.yaml` (instructions, criteria, thresholds) is meant
  to be edited; Azure plumbing is library code. `try_classifier.py` runs it read-only locally.

## 6. Topologies

| | A: isolated sandbox | B: shared cohort (default) |
|---|---|---|
| Bicep | `participant.bicepparam` per participant | `facilitator-new.bicepparam` once |
| Isolation | separate resources | namespaces + RBAC; see [security](security-and-rbac.md) |
| Search SKU | basic per participant | from `fomo capacity` (40 → S2) |
| Cost | ~N × fixed costs | one set of fixed costs |
| Lead time | each participant deploys (10-15 min) | facilitator deploys days ahead |

Both run the same CLI; only `.workshop/environment.json` differs. `reuse-existing.bicep` adds
only the connection, RBAC and missing deployments to customer-owned resources.

## 7. Code map

| Package | Responsibility |
|---|---|
| `cli.py` | argparse entry `fomo`; plan → confirm → apply wrapper |
| `plan.py` | `Plan`/`Action`, context verification, typed confirmation |
| `preflight/` | read-only checks, scrubbed JSON report, remediation plan |
| `provision/` | `plan_*`/`apply_*` for provision, miner, smart, eval, cheatcode; status/stage |
| `search/` | index/skillset/indexer definitions, REST client, indexer polling |
| `data/` | corpus manifest, extraction (PDF/PPTX/DOCX/TXT), Blob client, ingest |
| `decision/` | Decision-1 adapter, classifier config, acceptance policy |
| `agents/` | prompt agents (Search tool, filter), hosted miner packaging/deploy/invoke, `miner_app/` |
| `miner/` | the classification pipeline shared by hosted and local mode |
| `evaluation/` | metrics, runs, side-by-side, Markdown report |
| `cleanup/` | participant-scoped inventory, cleanup, reset |
| `telemetry/` | JSONL logs, redaction, traces, ASCII banners |
