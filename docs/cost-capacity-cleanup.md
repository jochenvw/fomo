# Cost, capacity and cleanup

## 1. Per-participant footprint

| Service | Objects | Counts against |
|---|---|---|
| AI Search | 2 indexes, 2 indexers, 2 skillsets, 1 data source | service object limits |
| Blob | 1 container, 25 docs (~1 MB) | nothing material |
| Foundry | 2 prompt agents, 1 hosted agent (+ versions) | project agent limits |
| RBAC | 2 role assignments for the hosted agent | subscription limit (4,000) |

## 2. Search SKU sizing

Binding constraint is **indexes / indexers / skillsets (2 each)**.

| SKU | Object limit | Max participants | Use for |
|---|---|---|---|
| Basic | 15 | 7 | pilot, rehearsal |
| S1 | 50 | 25 | small cohort |
| S2 | 200 | 100 | 40-person workshop (default recommendation) |

```powershell
uv run contoso capacity --participants 40 --sku basic   # exit 2 = does not fit
uv run contoso capacity --participants 40 --sku standard2 --replicas 2 --hours 8
```

## 3. Cost model (list prices, swedencentral, 8 h, 40 participants)

| Item | Driver | Estimate |
|---|---|---|
| AI Search | SKU × replicas × hours | dominant; Basic ~$0.82/8 h, S2 materially more |
| Chat (`gpt-5-mini`) | Simple + Smart RAG + eval answers | ~$1.6 |
| Embeddings | 25 docs × 2 indexes × N + queries | < $0.05 |
| Decision-1 | ~3M input tokens (25 docs × ~1.5k × 2-3 runs × 40) | pricing not public at time of writing; check Foundry pricing |
| Hosted agents | compute while active | scale-to-zero; check Foundry pricing |
| Storage | 40 MB | negligible |

`contoso capacity` prints the current estimate; the CLI never invents prices it does not know.
**Search is billed while it exists** — delete it after the workshop.

## 4. Cleanup

| Goal | Command | Removes |
|---|---|---|
| Undo classification | `contoso reset-participant --scope metadata --apply` | classification metadata + miner checkpoint; smart indexer disabled (agents and container stay) |
| Rebuild indexes | `contoso reset-participant --scope indexes --apply` | resets change tracking and re-runs both enabled indexers (re-embeds) |
| Leave the workshop | `contoso cleanup --apply [--local]` | all participant objects + agent grants (+ local run folder) |
| End the workshop | `az group delete -n <rg>` + `az cognitiveservices account purge …` | everything |

All destructive commands show an itemized plan and require typing `DELETE`.
