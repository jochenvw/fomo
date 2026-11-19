# FOMO: Foundry Organizes Metadata. Occasionally.

FOMO is an experimental **metadata-agent workshop**, not a promise that enrichment makes every
answer better. Build it, measure it, and find out when it helps or hurts. The name is deliberately
less confident than the demo: **Contoso Oil** is the fictional shop supplying the documents,
not the name of the toolkit.

```text
  [o_o]  FOMO
  /|_|\  Foundry Organizes Metadata.
   / \   Occasionally.
```

A 60-minute hands-on workshop on **Microsoft Foundry** + **Azure AI Search**. Participants see a
plain RAG agent cite the wrong documents, build a hosted **Metadata Miner** that classifies
documents with **Microsoft-Decision-1.1**, and measure a **Smart RAG** agent that filters on the new
metadata.

```
 Simple RAG ──> raw index ─┐                       ┌─> smart index ──> Smart RAG
                           ├── Blob container ─────┤      (document_type eq 'contract')
 25 synthetic docs ────────┘        ▲              │
                                    └── Metadata Miner (hosted agent, Decision-1.1)
```

Reference result: canonical question q01, **P@5 0.40 → 1.00**. The fixed filter also **hurts**
non-contract questions; the evaluation reports that honestly
([sample](examples/sample-evaluation.md)).

## 1. Five-minute start

```powershell
git clone <this repo>; cd <repo>
uv sync                                  # Python 3.11+, installed by uv
az login
uv run fomo env pull --subscription <sub-id> --resource-group <rg>   # or use the facilitator's .workshop/environment.json
```

Every command **plans first** and changes nothing; add `--apply` (and `--yes` for non-interactive).

| Goal | Command |
|---|---|
| Can we run the workshop? | `uv run fomo preflight --participants 40` |
| My baseline (container, raw index, Simple RAG) | `uv run fomo provision --participant <hackername> --apply` |
| Build + run my miner | `uv run fomo miner deploy --classifier .workshop/runs/<name>/miner/classifier.yaml --apply` then `uv run fomo miner run --hosted --apply` |
| Smart index + Smart RAG | `uv run fomo smart --apply` |
| Compare | `uv run fomo eval` |
| Skip to the end | `uv run fomo cheatcode --participant <hackername> --apply` |
| Where am I? | `uv run fomo status` |
| Reset | `uv run fomo reset-participant --scope metadata --apply` |
| Clean up | `uv run fomo cleanup --apply` (type `DELETE`) |

With **GitHub Copilot CLI**: run `copilot` in the repo, then *"Use the /fomo-preflight skill"*,
`/fomo-provision` or `/fomo-cheatcode`. Skills call the same commands.
Each skill opens with the FOMO mascot and a small emoji heading: 🔎 preflight, 🛠️ provision,
🚀 cheatcode. Status and approval text stay explicit; JSON output stays machine-readable.

Preflight checks sign-in, shows the username, then confirms tenant, subscription and resource
group. Choose an existing group or approve creation of a new empty one, for example
`rg-fomo-<event>` (shared) or `rg-fomo-<hackername>` (isolated). The confirmed target is remembered
in gitignored `.workshop/environment.json`; an empty group is not yet a ready workshop.
Interrupted reads get bounded retries. If a remembered group is genuinely missing, preflight
offers re-create / choose existing / create another / defer, with approval before any creation.

### Names and existing environments

| What | Naming |
|---|---|
| Skills / CLI | `fomo-preflight`, `fomo-provision`, `fomo-cheatcode` / `uv run fomo ...` |
| New shared infrastructure | `fomo` prefix, Foundry project `fomo`, connection `fomo-search`, `workshop=fomo` tags |
| Participant agents | `<hackername>-simple-rag`, `<hackername>-metadata-miner`, `<hackername>-smart-rag` |
| Participant Search / Blob | `<hackername>-raw`, `<hackername>-smart`, `<hackername>-inbox` |
| Demo company and documents | Contoso Oil, unchanged |

Participant names are already customer-neutral and stay stable so status, reruns and cleanup
still find existing assets. Saved resource names/endpoints are not renamed or migrated.
Before redeploying older infrastructure, keep its original `prefix`, `projectName` and
`searchConnectionName` in your parameters and review the plan; new defaults create new names.
`contoso` remains a compatibility CLI alias, `CONTOSO_*` overrides still work (`FOMO_*` wins),
and Python imports remain `contoso_workshop`. Recorded sample reports may retain legacy labels.

## 2. Facilitator

```powershell
uv run fomo capacity --participants 40                                  # SKU + cost (offline)
uv run fomo infra what-if --parameters infra/parameters/facilitator-new.bicepparam
uv run fomo infra deploy  --parameters infra/parameters/facilitator-new.bicepparam --apply
uv run fomo facilitator dashboard                                       # during the session
```

Customer-owned Foundry/Search/Storage: `infra/reuse-existing.bicep`. Full timeline:
[facilitator runbook](docs/facilitator-runbook.md).

## 3. Costs, roles, limits

| Topic | Summary | Details |
|---|---|---|
| Cost | Search SKU dominates (billed while it exists); models cost cents per participant | [cost-capacity-cleanup](docs/cost-capacity-cleanup.md) |
| Capacity | 7 Search objects per participant; Basic ≤ 7, S1 ≤ 25, S2 ≤ 100 participants | `fomo capacity` |
| Roles | facilitator: Owner (or Contributor + RBAC Admin) on the RG; participants: 7 scoped role assignments from Bicep, incl. 2 ABAC-limited RBAC Admin | [security-and-rbac](docs/security-and-rbac.md) |
| Auth | Entra ID only; no keys anywhere | |
| Region | tested in Sweden Central (needs Decision-1.1 + hosted agents) | [prerequisites](docs/prerequisites.md) |

## 4. Known limitations

1. Fixed `contract` filter drops non-contract questions to P@5 0 (q05, q06, q08); intent-aware filtering is an extension.
2. Hosted agents cannot reach Storage inside a network security perimeter; Storage uses public endpoint + Entra-only ([why](docs/troubleshooting.md#hosted-networking)).
3. Shared topology isolates by namespace + RBAC, not hard tenancy.
4. First hosted `miner run` may wait 1-2 min for role propagation (automatic retry).
5. Microsoft-Decision pricing is not included in estimates; `capacity` reports tokens.
6. Not tested: 40-person live load, private networking.

## 5. Docs

| Doc | For |
|---|---|
| [architecture](docs/architecture.md) | how it fits together, fairness of the comparison |
| [participant guide](docs/participant-guide.md) | the 60 minutes, step by step |
| [facilitator runbook](docs/facilitator-runbook.md) | before, during, after; 40-person load |
| [demo script](demo.md) | copy-paste RAW/SMART comparison, measured win, recorded fallback |
| [evaluation protocol](docs/evaluation-protocol.md) | pre-registered five-variant comparison, metrics, decision rules |
| [prerequisites](docs/prerequisites.md) | tools, Azure, roles |
| [security and RBAC](docs/security-and-rbac.md) | role matrix, ABAC, isolation |
| [troubleshooting](docs/troubleshooting.md) | symptom → cause → fix |
| [cost, capacity, cleanup](docs/cost-capacity-cleanup.md) | sizing and teardown |
| [verified APIs and decisions](docs/verified-apis-and-decisions.md) | versions, API shapes, design choices, open items |

Development: [CONTRIBUTING.md](CONTRIBUTING.md). License: [MIT](LICENSE). The corpus is synthetic;
Contoso Oil is fictional.
