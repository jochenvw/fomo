# Goal: FOMO Metadata-Agent Hackathon for Contoso Oil

> **Instruction to the implementing coding agent:** Build the *public GitHub repository* described here, including its skills, synthetic data, infrastructure-as-code, runnable software, tests, documentation, and facilitator tooling. **This `goal.md` is a specification, not the workshop skill itself.** Produce working code that the organizer can review and push to a public repo. Resolve implementation details using current official documentation, record decisions, and test the end-to-end path. Do not claim a capability works without validating it.

## 1. Mission and outcome

Create a public, self-contained, reusable repository for a **60-minute, hands-on Microsoft Foundry workshop for approximately 40 people** at **Contoso Oil**, a *fictional* large oil-and-gas company. Each participant has access to a Microsoft Foundry sandbox, possibly in their own subscription. The facilitator has a separate, independently provisionable environment and must be able to dry-run the entire experience from scratch.

Participants learn by building and comparing **three agents, two Azure AI Search indexes, and one classification/enrichment pipeline**:

1. **Simple RAG** — a Foundry **prompt agent** that retrieves from an ordinary, un-enriched index and can be distracted by plausible but incorrect document types.
2. **Metadata Miner** — a Foundry **hosted Python agent** that iterates over that participant's Azure Blob Storage container, extracts text, calls the **Microsoft-Decision-1** decision model to classify documents, and writes classification metadata back to the source (or to an explicitly documented enriched source). The **Azure AI Search indexer**, *not the agent*, subsequently populates the enriched search index. An optional tested push/update adapter is acceptable only if clearly differentiated.
3. **Smart RAG** — a second Foundry **prompt agent** that retrieves from the enriched index and deliberately uses classification metadata to narrow/re-rank its results.

**Learning hypothesis, not promised conclusion:** document-type classification and metadata-aware retrieval *can* increase retrieval precision and improve cited answers for targeted questions. The workshop must test that hypothesis fairly, display evidence, and expose misclassifications and harmful filtering.

Do not turn this into a production oil-and-gas solution or a lecture on architectural complexity. Make it reliable, instructive, approachable, and a little playful. Include appropriate, tasteful terminal ASCII art, funny but professional demo content, and Docker-style **hackernames** such as `sneaky-otter` or `cosmic-wombat`. Logs should be useful first and amusing second.

## 2. The exact architecture to build

```text
                            PUBLIC GITHUB REPOSITORY
           skills | Bicep | Python/uv | sample docs | labels | evals | docs
                                       |
                         preflight / provision / cheatcode
                                       |
        ┌──────────────────── Azure control plane ─────────────────────┐
        │ subscription / RG / Foundry account+project                 │
        │ model deployments: chat + embeddings + Microsoft-Decision-1 │
        │ Blob Storage / Azure AI Search / hosted-agent prerequisites │
        │ identities, RBAC, connections, quotas, diagnostics          │
        └──────────────────────────────────────────────────────────────┘
                                       |
                        One isolated participant: <hackername>
                                       |
                          Blob: <hackername>-inbox
                        (synthetic raw documents)
                                       |
                   ┌───────────────────┴────────────────────┐
                   |                                        |
        AI Search RAW indexer                     Foundry hosted agent
            (file extraction,                        "Metadata Miner"
         chunking + embeddings)                          |
                   |                             list blobs, iterate,
          index: <hackername>-raw             extract text, decision call,
                   |                           persist custom metadata
         Foundry prompt agent                             |
            "Simple RAG"                                  |
                   |                            AI Search ENRICHED indexer
                   |                         (same content/chunks/embeddings
                   |                           + classification metadata)
                   |                                        |
                   |                            index: <hackername>-smart
                   |                                        |
                   |                            Foundry prompt agent
                   |                                 "Smart RAG"
                   └────────────────────┬───────────────────┘
                                        |
                        same questions / retrieval traces
                             precision@k / citations
```

**Hard requirement:** **two distinct AI Search indexes per participant**, with an independently repeatable ingestion process for each; the intended design is **two indexers per participant**. One participant should be able to reset/rebuild their indexes without affecting others. Create separate data sources and skillsets if appropriate, but share immutable configuration/skillsets where this is demonstrably safe and reduces object counts. The baseline index must *not* exploit the classifier metadata even after that metadata exists on the blobs.

The Search indexers should provide equivalent text extraction, chunking, embedding model, search mode, and settings. The enriched version additionally makes the document classification filterable/retrievable and optionally demonstrates supported relevance boosting. Differences in results should be attributable to the experiment, **not silently to different embeddings or chunking**.

Use **Azure AI Search** for indexers. Do not call them “Foundry indexers.” Foundry supplies the models and agents; AI Search supplies the indexes, data sources, skillsets, vectorizers, and indexers.

### Per-participant resource manifest

Use one unique, normalized hackername across the entire experience:

| Logical resource | Example name / scope | Notes |
| --- | --- | --- |
| Participant identity | `cosmic-wombat` | Stable, safe, collision-checked; *not* a security principal |
| Input Blob container | `cosmic-wombat-inbox` | Source docs, participant-owned namespace |
| Raw Search index | `cosmic-wombat-raw` | No classifier field used in retrieval |
| Raw Search indexer | `cosmic-wombat-raw-ixr` | Source = this container |
| Smart Search index | `cosmic-wombat-smart` | Classification and confidence fields |
| Smart Search indexer | `cosmic-wombat-smart-ixr` | Run after enrichment |
| Simple RAG agent | `cosmic-wombat-simple-rag` | Foundry prompt agent, raw index |
| Metadata Miner agent | `cosmic-wombat-metadata-miner` | Foundry hosted Python agent |
| Smart RAG agent | `cosmic-wombat-smart-rag` | Foundry prompt agent, smart index |
| Run/report folder | `.workshop/runs/cosmic-wombat/` | Gitignored, metadata and traces only |

Azure resource naming rules differ across resource types; implement validated name derivation and collision detection. Keep a machine-readable per-participant manifest of **exact resource IDs, endpoint names, deployment names, model versions, indexer status, and ownership**. Never infer permission boundaries from hackernames; enforce isolation through actual RBAC and/or separate Azure resources. Tag eligible workshop-owned Azure resources with e.g. `workshop=fomo` and `hackername`.

## 3. Three entry points: skills plus deterministic commands

Prefer **three focused GitHub Copilot agent skills** (smaller context and clearer discoverability) backed by the *same deterministic Python scripts*. If one skill with three explicit operations is technically better after testing, document the reason; the three public operations must remain stable.

Provide real `SKILL.md` files in `.github/skills/fomo-preflight/`, `.github/skills/fomo-provision/`, `.github/skills/fomo-cheatcode/`, with valid metadata and meaningful descriptions. Verify current GitHub Copilot CLI and VS Code skill support. Include documented natural-language invocations such as “Use /fomo-preflight to check my workshop environment.” Do not invent a supported `/command` syntax without verification.

**Essential:** the corresponding commands must also work **without Copilot** (`uv run ...`, or a clearly documented standalone bootstrap), so a failed Copilot installation cannot prevent the user from checking or repairing prerequisites. The Copilot skill is the friendly interface; it must never be the sole implementation of infrastructure operations.

| Operation | Expected result | Approval boundary |
| --- | --- | --- |
| **Preflight** (days in advance) | Readiness report: local tooling, Azure context, capabilities, permissions, quota, networking, and whether *the caller* can provision missing pieces | **Read-only by default**; if remediation is offered, display a plan, then **ASK** before any change |
| **Provision** (start of workshop) | Ensure/reuse approved infrastructure and models; upload this participant's synthetic docs; construct/run raw indexer; create working **Simple RAG** prompt agent; generate editable Metadata Miner source/template and smart-index plan | Produce an itemized `what-if`/action plan and **ASK** before creating/updating anything; no implicit subscription/role changes |
| **Cheatcode** (facilitator/unblocker/faster participant) | Complete the whole demo: the hosted Metadata Miner deployment, classification loop, updated blob metadata, enriched indexer/index, Smart RAG prompt agent, and side-by-side evaluation | **Not a bypass of safety**: require the same permission checks, cost summary, confirmation and namespace isolation |

Implement options/flags `--check`, `--plan`, `--apply` or equivalent with predictable semantics; choose the exact CLI interface and test it. Default to a plan where any write would occur. Support `--participant`, `--subscription`, `--resource-group`, `--project`, `--output json`, `--verbose` and `--dry-run` as applicable. Use mutually consistent configuration names. Provide a status command and explicit `reset-participant` / `cleanup` flows with separate confirmations; cleanup is **never** an automatic side effect of preflight, provision, or cheatcode.

### 3.1 Preflight details — *Can we actually run the workshop?*

Check, report, and where possible harmlessly verify:

**Local / assistant readiness**
- OS and shell; Git; Python and `uv`; Azure CLI and extensions; Bicep; whether `azd` is needed for the selected hosted-agent deployment path; GitHub Copilot CLI installed/authenticated/able to discover the skill, or a documented compatible Copilot client (e.g. Copilot in VS Code) with the required functionality.
- If Copilot CLI isn't installed, explain installation/authentication without assuming the agent can self-install, and keep standalone preflight runnable. If local Docker isn't present, favor Foundry **source-code ZIP deployment** or cloud builds if supported; do not require local Docker unnecessarily.
- Report versions and distinguish **required**, **optional**, and **upgrade recommended**. Do not download or run unreviewed scripts.

**Azure target and operational access**
- `az account show`-equivalent effective tenant, subscription, subscription state, current principal, selected resource group, Foundry account/project and endpoint; check that the *user explicitly confirms* the intended subscription before writes.
- Resource providers, supported regions, provider registration privileges, Azure Policy/deny assignments, existing resources, SKU restrictions, allowed model deployments and quota, and whether provisioning is permitted in principle. Detect PIM/just-in-time access and propagation delays; no false “permission granted” from a simple role-list check.
- **Control-plane permissions:** create/reuse/update storage, Search, Foundry resources, model deployments, project Search connections; assign RBAC roles where needed; deploy hosted agents. Note: Azure `Contributor` alone **does not generally authorize role assignments**. Present admin actions as requests, never auto-elevate.
- **Data-plane permissions:** Blob read/list/write metadata; create/configure/run Search indexes/data sources/skillsets/indexers; query indexes; create and invoke Foundry prompt agents; publish/invoke hosted agents; call chat, embedding and decision models.
- **Forward and reverse service identities:** Foundry account/project → AI Search; **AI Search managed identity → Blob Storage**; **AI Search managed identity → Foundry embedding endpoint**; hosted agent's *own* identity → Blob Storage and decision model; participant (and optionally evaluation runtime) → both Search indexes and required model endpoints. These are distinct principals and trust paths.
- Actual data-path reachability/firewalls and identity-mode compatibility. Even if sandbox networking is expected to be permissive, validate, don't assume. A resource being visible in ARM is insufficient proof of functional access.
- Existence, deployment names, versions, region support, and **real minimal invocations** (with cost warning) of chat model, embedding model, Microsoft-Decision-1 `choice` API, and agent creation/invocation where authorized.
- Search service SKU, capacity/object quotas, indexer throughput, current count of objects, ability to create/run an indexer, and embedding endpoint connectivity. Check quotas/cost for 40 simultaneous participants rather than a single user.

**Report:** PASS / WARNING / BLOCKED per check, evidence, precise scope/principal/role/permission, recommended fix, who must act (participant versus facilitator/admin), estimated cost if relevant, and scrubbed machine-readable JSON. Flag unsupported preview/model/region features as **BLOCKED**, not as fabricated success. Read-only checks must not create hidden test resources. Optional functional smoke tests must be conspicuously opt-in if they change Azure state or incur charges.

**Remediation contract:** `plan → show scope/resources/identities/roles/cost → ask yes/no → apply → independently re-check`. Refusing must leave a useful report. Reuse existing compatible resources by default; never modify customer-owned settings, enable public network access, grant broad subscription roles, change tenant policy, or activate PIM automatically.

### 3.2 Provision details — *Get everyone to the same baseline*

1. Resolve the user's selected subscription, project, approved topology, models, and hackername from a validated manifest. Clearly distinguish shared/customer-owned versus participant-owned assets.
2. Reuse infrastructure if compliant; otherwise call the **same Bicep modules** the organizer would deploy, after presenting a plan and obtaining confirmation. Stop if central-only changes are required.
3. Verify the three model deployments and identity paths; **do not deploy three models 40 times** when facilitator-managed shared deployments are available. Permit separately owned models if that's the chosen topology.
4. Load the repo's versioned, synthetic corpus to the participant's Blob container, using checksums and idempotent uploads. Retain immutable source IDs; don't leak documents or outputs across participants. Default to repository-local files; optionally fetch from a **pinned public GitHub commit/release** and verify hashes.
5. Create the raw Search index, data source, skillset/vectorization and indexer. Trigger ingestion, poll for successful completion and actual nonzero result counts; show indexed document and cited source URL. **Avoid relying on a portal wizard.**
6. Create the **Simple RAG** Foundry prompt agent using the participant's raw index, a verified Foundry–Search project connection and approved generative deployment. Smoke-test the canonical question and surface the retrieved document IDs/citations.
7. Generate/fork an editable, runnable **Metadata Miner** Python starter with clear TODOs for class descriptions, thresholds, and ambiguity handling. Provide a checked-in working reference implementation for cheatcode. Make the participant's primary task improving and operating the classifier, not manually writing all Azure plumbing.
8. Prepare the smart index **schema** and supported indexer configuration as an idempotent pending stage, but do not pre-fill classification results or create Smart RAG as “already solved” in normal Provision. Show the next participant command/step.
9. Emit a short checklist of what works now, what the participant edits next, and how to recover from failed partial setup.

### 3.3 Cheatcode details — *Show the complete end-to-end system*

Using the **same modules and code paths**, and only after approval:

1. Finish/deliver the Metadata Miner agent code, deploy as a **real Foundry hosted agent**, poll for readiness, and invoke it. Do **not** label a local Python job a hosted agent; have a separate **local developer mode** if useful.
2. Loop deterministically over *only that participant's blobs*, extract text, call the decision API with documented `choice` options, normalize/validate the result, apply confidence/abstention policy, and persist metadata with versioning/checkpoints.
3. Start or restart the smart Azure AI Search indexer. Wait until enriched fields are actually available/searchable/filterable and verify indexed counts and classifications.
4. Create **Smart RAG** as a Foundry prompt agent bound to that participant's smart index, with a demonstrated metadata filter for a contracts question; optionally add a supported scoring profile to demonstrate boosting.
5. Run the **same evaluation questions** against both prompt agents and, critically, show direct Azure AI Search top-k hits/IDs, filters, labels, scores, and citations to explain why responses differ.
6. Print a concise, readable before/after table and links/IDs to the three agent resources, both indexes, source container and logs. Leave the resources intact unless the caller separately requests cleanup.

**Do not secretly substitute a local classification script** if the hosted deployment fails. Present the exact blocker and offer a clearly labeled local fallback for learning continuity, without claiming the architecture requirement was met.

## 4. Infrastructure-as-code and boundaries

The public repo must enable the **facilitator to deploy a fresh working environment from scratch**, even with no existing Search/Storage/Foundry setup, and must also work in **reuse existing customer sandbox** mode.

Use **modular Bicep as the authoritative Azure infrastructure-as-code** (ARM resource control plane). Prefer deterministic `az deployment ... what-if` and a tested apply path. Include parameter examples for clean-room facilitator, per-participant isolated sandboxes, and reuse-only customer provisioned resources. Resource groups/subscriptions may be independently managed, so support explicit resource IDs and cross-resource-group references where permitted.

Control-plane modules should cover as appropriate:
- Resource group inputs/deployment scope, Microsoft Foundry account and project using the **current resource model** (no legacy hub assumptions), identity and necessary project/account settings.
- Blob Storage account/container and secure defaults consistent with the selected sandbox configuration.
- Azure AI Search service with **SKU determined by the actual topology and object-count/throughput calculations**, managed identity, RBAC authentication and diagnostics.
- Chat, embedding, and **Microsoft-Decision-1** model deployments on the current supported Foundry resource; constrain SKUs, quota, region and deployment names by config. Deploy models **ahead of the workshop** wherever possible. Don't silently replace Microsoft-Decision-1 with a generative model.
- Any hosting prerequisites actually required by the chosen supported deployment path (container registry for image deployments; source ZIP path where supported, avoiding local Docker/ACR if not necessary), deployment diagnostics and optional Application Insights / OpenTelemetry plumbing.
- Least-privilege role assignments with concrete scope and intended principal; links/definitions for shared and participant-specific assignments. Where a principal only exists after agent creation, complete that RBAC assignment in an explicit, approval-controlled **post-deployment** step; document why it cannot be purely Bicep-first.
- Foundry → Azure AI Search **project connection**, if the current resource/API allows declarative provisioning; otherwise implement a deterministic SDK/REST post-provision step and document the boundary.

**Separation of responsibilities:** Azure control plane via Bicep; Azure AI Search **data-plane objects** (indexes, data sources, skillsets, indexers), document uploads, and Foundry **agent versions** via tested SDK/REST/`azd` steps as appropriate. Do not pretend Bicep can create every data-plane object. The CLI/skill orchestrates the layers; do not duplicate provisioning logic in prose prompts.

### Capacity must be calculated, not hand-waved

Forty participants × **two Search indexes and two indexers** = **80 indexes and 80 indexers**, plus possibly 80 data sources/skillsets, **80 prompt agents** and **40 hosted agents**. A **single shared S1 Search service is insufficient** (50 indexes, 50 indexers). As of this specification, **S2 supports 200 indexes/indexers**, while Free/Basic have far lower limits; check current limits before committing to any tier. A shared S2 might meet *object-count* requirements, but not automatically throughput, hosting, model quota, cost, or security constraints. Source: https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity

Implement and validate **two topologies**:

- **Topology A — isolated participant sandbox:** participant-specific Foundry project, storage/Search resources if permitted. Two indexes fit per service where the SKU supports the required indexer/vectorization features. Higher total cost; simpler separation.
- **Topology B — shared facilitator provisioned services:** reuse chat/decision/embedding deployments and possibly Search/Storage. Preserve per-hackername **two-index**, two-indexer, one-container (or deliberately isolated storage) namespaces; assess ability to enforce object-level permissions. **Azure Search RBAC is not automatically per-index isolation.** Do not imply hackername prefixes are a security boundary. Shared Search may require trusted cohort-level data access or stronger separate services.

Make the chosen topology explicit before any provisioning. Preflight must estimate object counts and live contention, and warn/fail if they exceed service limits. Provide bounded concurrency and staged model/indexer work to avoid 40 simultaneous indexing and embedding bursts. Document resource budgets and cleanup ownership. Never attempt to “fix” quota by silently upgrading a shared resource.

### Identity / RBAC matrix (validate against current docs)

Produce a **tested matrix** with concrete principal IDs, resource scope, action, recommended least-privileged roles, and how to verify effective access. At minimum cover:

| Caller / principal | Target | Needs | Indicative guidance to verify |
| --- | --- | --- | --- |
| Participant / facilitator | Azure control plane | create/reuse resources, model deployments, role assignments, connection | separate resource Contributor, `Cognitive Services Contributor` for model deployments, and `roleAssignments/write` authority if RBAC grants needed |
| Participant | Search service and indexes | create index/data source/skillset/indexer, run indexer, query | Search Service Contributor / Search Index Data Contributor / Search Index Data Reader as actually applicable |
| Participant / uploader | Own Blob container | list, upload, inspect metadata | Storage Blob Data Contributor at minimum needed scope |
| **Search service managed identity** | Own Blob container | indexer reads blobs and custom metadata | Storage Blob Data Reader |
| **Search service managed identity** | Foundry embedding deployment | invoke embedding skill/vectorizer | Cognitive Services OpenAI User for Azure OpenAI Embedding skill, or documented role for a chosen Foundry catalog model path |
| **Foundry account/project managed identity** | Search service | Foundry Azure AI Search tool/connection | Verify current documentation; it may require **both Search Index Data Contributor and Search Service Contributor**, even if that seems broader than query-only access |
| **Hosted Metadata Miner agent identity** | Its Blob container | read and write classification metadata | minimum functional Blob data role; assign **after** identity is created if necessary |
| **Hosted agent identity** | Decision deployment | invoke Microsoft-Decision-1 through provider-specific decision API | verify effective Foundry model inference authorization; distinct from ordinary chat completion API |
| Prompt agent/runtime | Chat deployment and Search connection | model inference + grounded retrieval | verify actual project/runtime identity chain, not just participant's user RBAC |
| Evaluator | Search + model endpoints | direct query/traces / generation | strictly scoped read/invoke permissions |

**Explicitly test these two arrows:** (1) Foundry agent → Search, (2) Search indexer/vectorizer → Foundry embeddings. These are **different service identities and different RBAC requirements**. Also test Search → Storage. Include private-network complications in diagnostics but do not make private networking a core workshop task.

Sources to consult before implementing:
- https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/ai-search
- https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-azure-openai-embedding
- https://learn.microsoft.com/en-us/azure/search/search-howto-managed-identities-storage
- https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent
- https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent-code

## 5. The dataset: fictional Contoso Oil document chaos

Commit a modest, **fully synthetic, redistributable** dataset to the repo, ideally **20–30 files**, plus a generator and immutable manifest. Include actual text-bearing PDFs and PowerPoints (not scanned images); plain-text versions for tests; no real company files, personal data, contracts, confidential geology, external copyrighted material, or live customer facts. The dataset should work end-to-end with **Azure AI Search's built-in document cracking** and tested Python text extraction without requiring OCR or Document Intelligence. PDF/PPTX extraction quality is part of tests.

Useful categories: `contract`, `invoice`, `policy`, `presentation`, `technical_report`, `correspondence`, `unknown` (or `needs_review`). Include procurement contracts, rig-maintenance agreements, offshore supply terms, pipeline-inspection reports, HSE policy documents, supplier invoices, drilling presentations, and finance notes. Make the content plausibly oil-and-gas-specific but fictional. Use lively titles (e.g. “The Great Offshore Coffee Supply Debacle”) sparingly without undermining realistic retrieval.

**Mandatory adversarial example:** a PowerPoint or deck about “contract management” that repeats *contract* and contains compelling payment-terms language, but **is not an actual contract**. Include actual agreements whose clauses support the canonical question. Also include converse traps: contracts with little use of the literal term *contract*, a policy quoting contract language, an invoice referencing contract numbers, and a deliberately ambiguous item. Do **not** rig every query to produce a victory: include questions where filtering makes no difference or harms recall.

Every source must have stable ID, file path, SHA-256, ground-truth document type, intended entity/metadata labels, source title, and evidence spans for expected answers. Avoid revealing the category in trivial filenames (`doc-001.pdf`, rather than `obvious-contract.pdf`); don't leak labels into the classification prompt or baseline index. Provide an evaluation query file with expected document IDs, answer evidence and optional desired filter. Make corpus regeneration stable via seed and snapshots.

## 6. Enrichment pipeline: deterministic code + decision model

The hosted Metadata Miner must expose a **manual invocation** suitable for the core hour (no Routine required). Prefer the Foundry **Invocations protocol** for batch/job-style work if supported and validated; an equivalent supported protocol is fine. Provide a local development mode and a deployed hosted mode, clearly distinguished. Support a small test invocation of one document and a full-container invocation.

Pipeline for one participant:

1. List this participant's blobs only; skip manifests/outputs. Use stable IDs and checksums/version markers. For a fresh run, process all; for delta mode, process only changed/new files. Deletion handling must be explicit and tested.
2. Extract text from supported formats; record extraction warnings. Do not send raw file bytes to the decision model or assume it does OCR. Avoid filename-only classification.
3. Call **Microsoft-Decision-1** using the official provider **decision API**, a `choice` question with meaningful criteria and tunable task-specific instructions, and the *deployment name*. Do **not** call it using a generic chat completions API or assume it generates explanatory text.
4. Validate chosen label, per-choice probability scores where available, model/deployment, latency, token usage, and provenance. Provide an `unknown`/human-review path when confidence is too low; thresholds must be user-adjustable and measurable, not magically calibrated.
5. Persist **actual Blob custom metadata** (e.g., `document_type`, `classification_confidence`, `classification_version`, optionally controlled `business_unit`/`region`) without destroying pre-existing metadata. Respect Blob metadata limitations and optimistic concurrency/ETags. Alternative: a clearly versioned enriched source container if updating source metadata is not appropriate; document implications for both indexes.
6. Retry transient failures with exponential backoff, bound concurrency and request rate, and checkpoint each successful item. Handle 403, 429, 5xx, empty text, ambiguous classes and reruns gracefully. No silent broad access or upload overwrites.
7. Request/re-run the **smart Search indexer**, poll status, verify that custom metadata is indexed and filterable. Metadata must be defined in index schema and mapped correctly; merely setting Blob metadata **does not automatically make it queryable**. Confirm re-index behavior for metadata-only changes with a real test.

**Important boundary:** a decision model classifies among known options and returns numerical decisions; it does **not** extract arbitrary free-form entities/clauses or write prose. Additional open-ended metadata extraction should be optional and use an explicitly identified generative model or deterministic parser, not be misrepresented as Microsoft-Decision-1 output. Document the decision model endpoint and request schema from https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision .

Search's own text extraction/indexing and hosted agent text extraction are separate flows that must agree on source IDs. The raw indexer should ignore classification metadata even if the same blobs later get enriched. Avoid accidental data leakage from ground-truth files. Ideally use index projections with chunk-to-source linkage and stable URLs so evidence remains traceable.

Useful official docs:
- https://learn.microsoft.com/en-us/azure/search/search-how-to-index-azure-blob-storage
- https://learn.microsoft.com/en-us/azure/search/search-how-to-integrated-vectorization
- https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-azure-openai-embedding

## 7. RAG comparison: two real Foundry prompt agents

Use current **Microsoft Foundry prompt agent** APIs with a real **Azure AI Search tool / project connection**, not a local chatbot pretending to be an agent. Generate a tested baseline agent and a tested smart agent. Both use the **same generative model deployment**, comparable prompts/system policies, top-k, search strategy, and citation policy. Use vector-capable index schemas compatible with the Foundry tool: searchable text, retrievable original/source URLs, and vector fields with dimensions matching the deployed embedding model.

**Simple RAG:** queries `<hackername>-raw` without classification filtering. Ask e.g. **“What payment terms apply under our supplier contracts?”** Report when irrelevant decks/policies displace actual contracts. Never fabricate citations.

**Smart RAG:** queries `<hackername>-smart` and actively applies `document_type eq 'contract'` for the canonical contracts comparison; demonstrate a real OData filter, not just “ignore non-contracts” in the agent prompt. The Azure AI Search tool currently supports a `filter` associated with its configured index/tool; **do not assume the prompt agent can dynamically change this for every arbitrary user question**. For a reliable 60-minute demonstration, a smart prompt agent with an explicit fixed *contracts scope* is acceptable and preferable to speculative dynamic filtering. If implementing general dynamic filtering, verify and test the supported per-query/tool-call mechanism; otherwise expose a deliberate scope selector or separate query mode and document its limitation.

Offer **metadata boosting as an optional, second experiment** after a working metadata **filter**. Implement scoring profiles only through documented Azure AI Search semantics and verify actual ranking effects. Don't promise the same scoring behavior across keyword, hybrid, vector, and semantic pipelines. Capture and display the exact query type/filter/scoring profile used for every measured run.

**Fair evaluation:** two indexes serve the same versioned corpus and same chunking/vectorization/retrieval mode; only the metadata application differs. Include direct Search SDK queries independently of the prompt agents so you can compare raw top-k documents and classify retrieval errors separately from answer-generation errors. Compute precision@5 (and ideally recall@5), classification accuracy/confusion matrix, false exclusions, and answer evidence/citation correctness for a small labeled query set. Display model token counts, latency and cost estimates where available. Classify a baseline result as wrong only against ground truth, not because it is inconvenient. Preserve before/after traces as portable JSON and concise text/Markdown reports.

## 8. Facilitation and one-hour experience

The skill is the **technical workshop buddy**, not the thing being taught. It scaffolds infrastructure and fixes Azure plumbing but leaves genuine learning: tuning category definitions and `choice` instructions, inspecting probabilities/false positives, deciding when to abstain, testing filters, and interpreting the RAG results.

Suggested timing (adjust after dry-run):

- **Days beforehand:** facilitator provisions shared models and expensive services; sends public repository + preflight instructions. Participants run read-only preflight and provide scrubbed results; admin resolves blocked checks.
- **0–10 min:** demo the problem: a contract-themed PowerPoint appearing in ordinary RAG results. Participants invoke Provision and reach baseline using preprovisioned resources.
- **10–25 min:** explain and inspect Microsoft-Decision-1 classification; improve categories/instructions, run small samples, interpret probabilities.
- **25–40 min:** run or deploy Metadata Miner; inspect Blob metadata, smart indexer run and resulting fields.
- **40–55 min:** create/use Smart RAG, enforce `contract` filter, compare citations and retrieval precision.
- **55–60 min:** discussion of misses, limitations and trade-offs. Cheatcode restores the completed reference flow for anyone blocked.

**One hour is a hard design constraint:** no mandatory waiting for slow ARM/Foundry model deployment, quota approvals, registry setup, or the first ever build of 40 hosted-agent containers. Provision shared infrastructure ahead of time; use hosted-agent **source ZIP** deployment or a proven cloud-build route where it simplifies the user experience. If 40 independent hosted deployments cannot complete reliably, state that *before* the workshop and design a truthful, labeled fallback/staging method without erasing the requirement for the final full architecture.

Facilitator needs a dashboard/report aggregating participant readiness without collecting credentials: green/yellow/red, unique hackername, known blockers, Search/Foundry capacity and approximate cost. Include a runbook for 40-user load simulation, throttling, brownout recovery, staggered indexing, quota failure and cleanup.

## 9. Engineering requirements, repository shape, and safeguards

Use **Python + `uv`**, type hints, small modules, documented dependencies, structured exception types, resilient retries/timeouts and modular integration clients. Use native SDKs/REST/Bicep rather than brittle portal automation. Test authentication against real target resources where possible and respect current SDK API revisions. Prefer the new Foundry account/project API model; do not build around legacy Foundry hubs or a separate, obsolete Azure OpenAI service resource unless a specifically supported endpoint requires it.

Proposed repo layout (agent may improve layout while retaining discoverability):

```text
README.md
LICENSE
CONTRIBUTING.md
goal.md
.github/
  skills/
    fomo-preflight/SKILL.md
    fomo-provision/SKILL.md
    fomo-cheatcode/SKILL.md
  workflows/ci.yml
infra/
  main.bicep
  modules/                      # Foundry, Search, Storage, models, RBAC, etc.
  parameters/                   # facilitator-new, participant, reuse-existing
src/contoso_workshop/
  cli.py                        # deterministic, Copilot-independent entry points
  config.py
  preflight/                    # local/cloud checks, plan, approval/report
  provision/                    # control-plane orchestrator + data-plane resources
  data/                         # upload, metadata, text extraction
  agents/                       # prompt agents + hosted miner starter/reference
  search/                       # schemas, skillsets, indexers, querying
  decision/                     # Microsoft-Decision-1 API adapter
  evaluation/                   # metrics, retrieval traces, side-by-side
  telemetry/
  cleanup/
corpus/
  originals/                    # synthetic PDF/PPTX and other supported files
  manifest.json                 # checksums + document IDs (no visible answer labels)
  ground_truth/                 # withheld from classifier; labels + expected citations
  eval_queries.json
  generator/
examples/
  config.example.yaml
  sample-readiness.json
  sample-evaluation.md
tests/
  unit/
  integration/
  end_to_end/
docs/
  architecture.md
  security-and-rbac.md
  prerequisites.md
  participant-guide.md
  facilitator-runbook.md
  troubleshooting.md
  cost-capacity-cleanup.md
  verified-apis-and-decisions.md
```

Add additional files for deployment/source packaging as necessary. Include `.gitignore`, `.env.example`, lock file and a `Makefile`/PowerShell-friendly equivalent for discoverability. Scripts must work on Windows (PowerShell) and Linux where reasonably practical; avoid mandatory bash-only setup paths. No customer tenant IDs, user identities, secrets, SAS tokens, keys, or data in the public repository or logs.

### Logging and instrumentation

- Structured logging with UTC timestamps, operation, participant hackername, stage, correlation/run ID, resource ID (where safe), elapsed time and status. Console output terse; detailed logs and JSON trace files in an ignored workspace directory. Allow `--verbose`/`--json` output.
- Emit useful task-level progress, e.g. `COSMIC-WOMBAT | [7/24] classified doc-007 => presentation (p=0.86) | 420ms`, with secret-safe redaction.
- Capture Azure request IDs, Search indexer execution info, model calls/usage, retrieval top-k and answer citations. Include optional OpenTelemetry/Application Insights where available, but **do not make an APM platform mandatory**.
- Use occasional thematic ASCII art for startup/report headings (oil rig, barrel, tiny robot), readable on monochrome terminals; avoid flooding logs, spinners that break screen readers, or jokes in errors.
- Support recording a portable reproducible demonstration from a fixed seed, without fabricating telemetry or hardcoding positive evaluation results.

### Safety / repeatability

- All changes **plan → explicit confirm → apply**; approval for one operation does not silently authorize other subscriptions, cost-impacting modifications, or destructive cleanup.
- Verify tenant/subscription/ownership on every write; bounded rate and concurrency; retry transient failures only. Handle interrupted deployment and idempotent partial reruns.
- Sanitize source documents as **untrusted input**; do not execute instructions embedded in PDFs/PPTX. Validate classifier labels and all generated resource names.
- Source hashes, classification versions, and per-blob checkpoints make delta-processing and fresh-start possible. Do not upload over modified participant documents unless explicitly approved.
- Check service quota, current usage, cost estimate and approval before bulk or repeated embedding runs. Alert when Search/Foundry managed identities have broad access; no persistent storage keys if Entra auth works.
- All workshop-generated assets clearly tagged/manifested; cleanup defaults to **this participant's workshop-owned assets only**, never the facilitator's shared models/Search/Foundry account or unrelated resources. Require explicit destructive confirmation, and support a dry-run inventory.
- Include a version/compatibility matrix and unsupported-feature error messages. Never invent working scripts for preview APIs; fail clearly and show supported alternatives.

## 10. Testing strategy and acceptance criteria

Implementation order should prioritize **vertical, runnable slices**, not just generating a directory of files:

1. **Verify currently supported APIs** and commit `docs/verified-apis-and-decisions.md`: Copilot skill discovery, Microsoft-Decision-1 `systemone` API/availability, Foundry prompt agent Search tool/filter semantics, hosted agent source/container deployment, Azure AI Search integrated vectorization/metadata indexing, Bicep deployment shape and RBAC. Record exact tested versions and unresolved blockers.
2. Build stable synthetic corpus with expected labels/answers and exercise the adversarial PowerPoint case. Keep test ground truth separate from exposed classifier inputs.
3. Build fresh Bicep deployment for facilitator and prove it can provision the real control plane. Add reuse-existing mode and `what-if`.
4. Implement stand-alone Preflight first: read-only checks, JSON report, approvals and permission-aware remediation plans.
5. Implement the raw indexer, index and real Simple RAG prompt agent. Verify Search → Blob, Search → embedding model, Foundry → Search, citations, query results.
6. Implement the hosted Metadata Miner **through the deployed Foundry agent protocol**, decision API adapter, metadata updates, and smart indexer. Verify real document metadata flow and handling of an unchanged blob on rerun.
7. Implement Smart RAG agent with verified metadata filter and two-index evaluation. Build reliable Cheatcode by orchestrating **the exact same components**, not a parallel toy demo.
8. Wrap operations in three discoverable Copilot CLI skills, add tests, sample reports, documentation and playful but useful UX.
9. Dry-run clean-room fresh deployment and reuse-existing deployment; simulate two separate hackernames in the *same* Search/Storage to test collisions and isolation; inspect full 40-participant quota/cost model and perform a realistic load test where permitted.
10. Time the participant happy path from Provision to before/after evaluation. Adapt sample corpus/packaging to fit a 60-minute workshop.

**Required automated tests**
- Unit: name generation/collisions; prompt/category config; decision response normalization/probabilities; Blob metadata merge/ETags; text extraction (PDF, PPTX); indexing field mappings; idempotency; evaluation metrics; pricing/limit math; report sanitization.
- Offline CI: mock Azure SDK/model/decision calls; lint, format, type check and security scans; no Azure credentials required on public pull requests.
- Gated integration (credentialed): list/write/read Blob metadata; real decision `choice` call; embedding and indexer run; Search filters; create/invoke real prompt agents; deploy/invoke real hosted agent; verify participant namespace collision behavior.
- End-to-end: one participant using only normal Provision + exercise steps, one using Cheatcode, and a rerun with unchanged documents; cleanup dry run.

**Definition of done**

- [ ] Public repo includes tested Bicep to create **all required Azure control-plane resources** from scratch, with explicit, safe approval gates and a reuse-existing mode.
- [ ] Preflight works independently of Copilot, checks Copilot/CLI and Azure access, and distinguishes existing rights from **ability to provision and grant required roles**; emits actionable PASS/WARNING/BLOCKED reports.
- [ ] Copilot-compatible Preflight, Provision and Cheatcode skills are discoverable and invoke the same actual deterministic operations.
- [ ] Each participant gets **one hackername namespace, one Blob container, two indexes, two indexers and three real Foundry agents** (two prompt; one hosted) after Cheatcode; no cross-participant overwrite.
- [ ] All three required model deployments are confirmed or provisioned with approval: **generative/chat, embedding, Microsoft-Decision-1**; decision invocation uses its real provider API.
- [ ] Search indexers call the Foundry embedding deployment with correct reverse managed identity authorization, and Foundry agents use a working project Search connection.
- [ ] Provision creates an actually queryable raw index and Simple RAG agent. Cheatcode completes actual hosted classification, persisted Blob metadata, smart indexing and the Smart RAG agent.
- [ ] Sample PowerPoint/non-contract trap is present and evaluation reports **actual** top-k hits, filters and cited evidence. Comparison is fair; if it doesn't improve, report that fact.
- [ ] Running twice is safe; changing one document reprocesses only appropriate data; cleanup never deletes shared/customer-owned assets.
- [ ] Organizer can run **fresh-start facilitator deployment** and 40-participant readiness/capacity calculations; participants can complete the core exercise within one hour with prep done ahead of time.
- [ ] README is a 5-minute entry point, with copy-paste commands for preflight, provision, cheatcode, status, reset and cleanup; docs state costs, roles, limits and known limitations.

## 11. Explicit non-goals and unresolved choices

Not in the mandatory core: OCR of scanned documents, complex extraction of free-form contract entities, enterprise private networking, a fully event-driven production ingest, a persistent Foundry Routine, fine-tuning, automated CI/CD promotion of infrastructure, or actual Contoso business data. Document these as possible extensions only.

Implementer must make and explain these choices, **without removing required outputs**:
- Default participant topology (one subscription each vs shared services); facilitator must support both.
- Hosted-agent deployment route: source ZIP/`azd` versus container/ACR; favor the one proven fastest and least prerequisite-heavy.
- Single Blob source container with two distinct indexer schemas versus a derived enriched container; preserve a fair, non-leaking baseline.
- Fixed contract filter for the Smart RAG prompt agent versus verified dynamic query-dependent filters; **fixed is an acceptable tested core**.
- Scoring profile as stretch versus mandatory filtering; **real metadata filter is mandatory**.
- Three separate skill folders versus one skill dispatcher; three entry points remain mandatory.

**Critical implementation principle:** infrastructure and model deployment are expensive, operationally sensitive, and often slow; preflight and pre-provision them **before** the hour. Give participants a strong, honest technical scaffold while keeping the classification and retrieval experimentation theirs to explore.

## 12. References to check before coding (official sources)

The technologies are changing quickly. Revisit these before implementation, cite the chosen API versions in the repo, and do not rely on older hub/agent interfaces:

- GitHub Copilot agent skills: https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills
- Microsoft-Decision-1 and its `systemone` API: https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision
- Foundry Azure AI Search tool, connections and filters: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/ai-search
- Deploy Foundry hosted agents: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent
- Deploy hosted agents from source: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/deploy-hosted-agent-code
- Hosted-agent protocol and Microsoft Agent Framework: https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/framework-hosted-agents
- Search Blob indexer and custom metadata: https://learn.microsoft.com/en-us/azure/search/search-how-to-index-azure-blob-storage
- Search Azure OpenAI Embedding skill with Foundry project endpoints: https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-azure-openai-embedding
- Search integrated vectorization: https://learn.microsoft.com/en-us/azure/search/search-how-to-integrated-vectorization
- Search managed identity to Blob: https://learn.microsoft.com/en-us/azure/search/search-howto-managed-identities-storage
- Azure AI Search service/object limits: https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity
- Search ranking/scoring profiles: https://learn.microsoft.com/en-us/azure/search/search-relevance-overview

---

### Final instruction to the coding agent

**Implement the repository end-to-end**, not a superficial scaffold or an essay. Produce working Bicep, real compatible SDK clients, synthetic PDF/PPTX data, three skills, three agents per completed participant, two independent Search indexes/indexers, tests, and runnable documentation. Use a small, proven vertical slice before increasing scope. Ask the human for authorization before making Azure changes in any generated interactive tooling. If any live capability or permission cannot be verified, produce the clearest possible blocker and a tested fallback—**never fabricate successful provisioning, model inference, agent deployment or retrieval results**.
