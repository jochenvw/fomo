# Verified APIs and decisions

Everything below was exercised **live** (Sweden Central, MCAP sandbox subscription, 2026) unless
marked *unverified*. Nothing in this repo fakes a provisioning, inference, agent or retrieval result.

## 1. Version matrix (tested)

| Component | Version | Notes |
|---|---|---|
| Python | 3.11+ (tested 3.14 local, 3.13 hosted) | `uv` manages it |
| `azure-ai-projects` | 2.11.0 | 2.x = new Foundry agents API; 1.x hub-era API not used |
| `azure-search-documents` | 12.1.0 | data-plane objects via REST `2025-09-01` |
| `azure-storage-blob` | 12.31.1 | Entra ID only |
| `azure-identity` | 1.27.0 | `AzureCliCredential` → `DefaultAzureCredential` |
| `azure-mgmt-authorization` | 4.0.0 | hosted agent grants |
| Azure CLI / Bicep | 2.93.0 / 0.49.2 | |
| uv | 0.11.17 | |
| GitHub Copilot CLI | 1.0.113 | skills discovered from `.github/skills/` |

## 2. API surface

| Capability | API / version | Verified behavior |
|---|---|---|
| Foundry account, project, deployments, connection | ARM `Microsoft.CognitiveServices/*@2025-06-01` | new account/project model, no hub, `disableLocalAuth` |
| Search service | ARM `Microsoft.Search/searchServices@2025-05-01` | system MI, `disableLocalAuth=true` |
| Storage | ARM `Microsoft.Storage/storageAccounts@2025-01-01` | `allowSharedKeyAccess=false` |
| Role assignments | `Microsoft.Authorization/roleAssignments@2022-04-01` | ABAC condition v2.0 |
| Index, skillset, indexer, data source | Search REST `2025-09-01` | blob custom metadata → filterable field; `AzureOpenAIEmbeddingSkill` + vectorizer against the Foundry account with Search MI |
| Decision | `POST https://<account>.openai.azure.com/providers/microsoft/v1/systemone` | `choice` question, per-option probabilities; **`<account>.services.ai.azure.com` returns 401 with Entra tokens** |
| Prompt agents | `azure-ai-projects` 2.x `PromptAgentDefinition` + `AzureAISearchTool` | `query_type=vector_simple_hybrid`, `top_k=5`, `filter` honored (`document_type eq 'contract'`); citations via `url_citation` annotations |
| Hosted agent deploy | `create_version_from_code` (source ZIP, `remote_build`, `python_3_13`) | ~60-70 s to active; own instance identity |
| Hosted agent invoke | `POST {project}/agents/{name}/endpoint/protocols/invocations?api-version=v1` (`invocations` 2.0.0) | 424 while activating; 403 until grants propagate |
| Hosted agent delete | `agents.delete(name, force=True)` | without `force`: 409 when sessions exist |
| Copilot skills | `.github/skills/<name>/SKILL.md` with `name`, `description` frontmatter | `copilot skill list` shows all three |

## 3. Decisions (goal §11)

| # | Choice | Decision | Why | Trade-off |
|---|---|---|---|---|
| 1 | Topology | **B: shared cohort** default; A (sandbox per participant) supported by `participant.bicepparam` | one set of fixed costs, facilitator pre-provisions days ahead, participants need no Owner | isolation is namespace + RBAC, not hard tenancy ([security §4](security-and-rbac.md#4-isolation-in-the-shared-topology-b)) |
| 2 | Hosted deploy route | **source ZIP** (`create_version_from_code`) | no Docker, no ACR, no azd; deterministic ZIP hash → no-op redeploy | preview API; runtime fixed to Python 3.13 |
| 3 | Data layout | **one container, two indexer schemas** | same bytes feed both indexes; raw schema has no classification fields → no leakage | smart index reflects metadata only after the smart indexer re-runs |
| 4 | Smart RAG filter | **fixed** `document_type eq 'contract'` | verified, simple, honest | harms non-contract questions (q05, q06, q08 → P@5 0); `smart_desired` eval row shows intent-aware filtering's potential |
| 5 | Scoring profile | **stretch**: `boost-contracts` (tag boost 3) evaluated, not used by agents | filter is mandatory; boost is a softer alternative | boost P@5 0.28 vs filter 0.23 / desired 0.30 |
| 6 | Skills | **three folders**, each a thin wrapper over `contoso` CLI | three discoverable entry points; same code paths without Copilot | small duplication in SKILL.md text |
| 7 | Storage network | **public endpoint + Entra-only** (`storageNetworkMode='public'`) | hosted agents cannot reach perimeter-protected Storage (see §4) | no network isolation; sandbox policies may need an exemption tag |
| 8 | Local miner | `miner run --local` as labeled fallback only | debugging and policy-blocked tenants | not the hosted requirement |

## 4. Findings worth knowing

1. **NSP blocks hosted agents.** With Storage in a network security perimeter, the hosted miner gets
   403 `AuthorizationFailure` despite correct RBAC. Fix: public + Entra-only; MCAP policy honors an
   exemption tag (`SecurityControl=Ignore`) set via `storagePolicyExemptionTags`.
2. **RBAC propagation** for freshly created agent grants takes 1-2 min; the CLI retries 403
   "Needs Storage Blob Data Contributor" for up to 6 min, but never retries network 403s.
3. **What-if noise.** Foundry accounts show Modify on read-only properties (`currentCapacity`,
   endpoints, a stale `networkInjections` entry). Role assignments named from runtime principal IDs
   show as *Unsupported*; `contoso infra what-if` collapses them into one note.
4. **Embedding endpoint.** The Search embedding skill must target the Foundry account's
   `openai.azure.com` endpoint with the Search MI holding *Cognitive Services OpenAI User*.

## 5. Measured results (reference classifier, 25 docs, 8 questions)

| Run | P@5 | R@5 |
|---|---|---|
| raw | 0.23-0.25 | 0.92-0.94 |
| smart, no filter (control) | 0.23 | 0.92 |
| smart, `contract` filter | 0.23 | 0.60 |
| smart, per-question desired filter | 0.30 | 0.98 |
| smart, boost profile | 0.28 | 0.85 |

Classification accuracy 100 % (25/25). Canonical q01: raw P@5 0.40-0.60 → smart **1.00**.
Timings: cheatcode from scratch 276 s; delta rerun with one changed document 10.8 s.
Full report: [`examples/sample-evaluation.md`](../examples/sample-evaluation.md).

## 6. Unresolved / unverified

| Item | Status |
|---|---|
| Microsoft-Decision-1 pricing | not published at time of writing; `capacity` reports tokens, not dollars |
| Hosted agents behind private networking | out of scope; not tested |
| Dynamic per-question filters in the prompt agent | not implemented (agent tool filter is static) |
| Real 40-person load test | not run; capacity math verified + 4 participants coexisting in the same services |
| `reuse-existing.bicep` against a customer-owned estate | builds and what-ifs; not deployed into a separate estate |
| OCR, entity extraction, event-driven ingest, CI/CD for infra | non-goals (goal §11); possible extensions |
