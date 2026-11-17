# Contoso Oil: metadata beats prompts

A 60-minute hands-on workshop on **Microsoft Foundry** + **Azure AI Search**. Participants see a
plain RAG agent cite the wrong documents, build a hosted **Metadata Miner** that classifies
documents with **Microsoft-Decision-1**, and measure a **Smart RAG** agent that filters on the new
metadata.

```
 Simple RAG ──> raw index ─┐                       ┌─> smart index ──> Smart RAG
                           ├── Blob container ─────┤      (document_type eq 'contract')
 25 synthetic docs ────────┘        ▲              │
                                    └── Metadata Miner (hosted agent, Decision-1)
```

Reference result: canonical question q01, **P@5 0.40 → 1.00**. The fixed filter also **hurts**
non-contract questions; the evaluation reports that honestly
([sample](examples/sample-evaluation.md)).

## 1. Five-minute start

```powershell
git clone <this repo>; cd <repo>
uv sync                                  # Python 3.11+, installed by uv
az login
uv run contoso env pull --subscription <sub-id> --resource-group <rg>   # or use the facilitator's .workshop/environment.json
```

Every command **plans first** and changes nothing; add `--apply` (and `--yes` for non-interactive).

| Goal | Command |
|---|---|
| Can we run the workshop? | `uv run contoso preflight --participants 40` |
| My baseline (container, raw index, Simple RAG) | `uv run contoso provision --participant <hackername> --apply` |
| Build + run my miner | `uv run contoso miner deploy --classifier .workshop/runs/<name>/miner/classifier.yaml --apply` then `uv run contoso miner run --hosted --apply` |
| Smart index + Smart RAG | `uv run contoso smart --apply` |
| Compare | `uv run contoso eval` |
| Skip to the end | `uv run contoso cheatcode --participant <hackername> --apply` |
| Where am I? | `uv run contoso status` |
| Reset | `uv run contoso reset-participant --scope metadata --apply` |
| Clean up | `uv run contoso cleanup --apply` (type `DELETE`) |

With **GitHub Copilot CLI**: run `copilot` in the repo, then *"Use the /contoso-preflight skill"*,
`/contoso-provision` or `/contoso-cheatcode`. Skills call the same commands.

## 2. Facilitator

```powershell
uv run contoso capacity --participants 40                                  # SKU + cost (offline)
uv run contoso infra what-if --parameters infra/parameters/facilitator-new.bicepparam
uv run contoso infra deploy  --parameters infra/parameters/facilitator-new.bicepparam --apply
uv run contoso facilitator dashboard                                       # during the session
```

Customer-owned Foundry/Search/Storage: `infra/reuse-existing.bicep`. Full timeline:
[facilitator runbook](docs/facilitator-runbook.md).

## 3. Costs, roles, limits

| Topic | Summary | Details |
|---|---|---|
| Cost | Search SKU dominates (billed while it exists); models cost cents per participant | [cost-capacity-cleanup](docs/cost-capacity-cleanup.md) |
| Capacity | 7 Search objects per participant; Basic ≤ 7, S1 ≤ 25, S2 ≤ 100 participants | `contoso capacity` |
| Roles | facilitator: Owner (or Contributor + RBAC Admin) on the RG; participants: 7 scoped role assignments from Bicep, incl. 2 ABAC-limited RBAC Admin | [security-and-rbac](docs/security-and-rbac.md) |
| Auth | Entra ID only; no keys anywhere | |
| Region | tested in Sweden Central (needs Decision-1 + hosted agents) | [prerequisites](docs/prerequisites.md) |

## 4. Known limitations

1. Fixed `contract` filter drops non-contract questions to P@5 0 (q05, q06, q08); intent-aware filtering is an extension.
2. Hosted agents cannot reach Storage inside a network security perimeter; Storage uses public endpoint + Entra-only ([why](docs/troubleshooting.md#hosted-networking)).
3. Shared topology isolates by namespace + RBAC, not hard tenancy.
4. First hosted `miner run` may wait 1-2 min for role propagation (automatic retry).
5. Microsoft-Decision-1 pricing not published at time of writing; `capacity` reports tokens.
6. Not tested: 40-person live load, private networking.

## 5. Docs

| Doc | For |
|---|---|
| [architecture](docs/architecture.md) | how it fits together, fairness of the comparison |
| [participant guide](docs/participant-guide.md) | the 60 minutes, step by step |
| [facilitator runbook](docs/facilitator-runbook.md) | before, during, after; 40-person load |
| [prerequisites](docs/prerequisites.md) | tools, Azure, roles |
| [security and RBAC](docs/security-and-rbac.md) | role matrix, ABAC, isolation |
| [troubleshooting](docs/troubleshooting.md) | symptom → cause → fix |
| [cost, capacity, cleanup](docs/cost-capacity-cleanup.md) | sizing and teardown |
| [verified APIs and decisions](docs/verified-apis-and-decisions.md) | versions, API shapes, design choices, open items |

Development: [CONTRIBUTING.md](CONTRIBUTING.md). License: [MIT](LICENSE). The corpus is synthetic;
Contoso Oil is fictional.
