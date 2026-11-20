# Facilitator runbook

## 1. Timeline

| When | Action | Command | Expect |
|---|---|---|---|
| T-7 days | Size the cohort | `uv run fomo capacity --participants 40` | SKU + cost; 40 → `standard2` |
| T-7 days | Check subscription | `uv run fomo preflight --participants 40` | verdict READY/WARNING |
| T-5 days | Edit params | copy `infra/parameters/facilitator-new.bicepparam` to `.local/`, set `participants`, `searchSku` | |
| T-5 days | What-if | `uv run fomo infra what-if --parameters .local/<file>.bicepparam` | creates only; read the role assignment note |
| T-5 days | Deploy | `uv run fomo infra deploy --parameters .local/<file>.bicepparam --apply` | 10-15 min |
| T-5 days | Confirm target | `uv run fomo env pull` for FOMO Bicep; `env discover --plan` for IT-provisioned services | configured locally |
| T-5 days | Rehearse | plan then apply `uv run fomo cheatcode --participant <fresh-rehearsal-name>`; save the comparison before cleaning up only that prefix | hosted deploy/invoke and both queries succeed on target |
| T-2 days | Private kickoff | send the [participant-agent prompt](facilitator-control-sheet.md) with confirmed IDs and cohort size | participants run full preflight locally; no file handoff |
| T-0 | Dashboard | `uv run fomo facilitator dashboard` | stage per discovered raw index, not an attendance list |
| T+0 | Cleanup | see §5 | |

Customer-owned resources: use `infra/reuse-existing.bicep` instead; it adds only the connection,
missing deployments and RBAC.

## 2. Load: planning for a cohort of 40

Capacity estimates and a one-person rehearsal do not validate concurrent starts. In rehearsal,
record the actual group size, start offsets, indexing time and throttling on the customer target.
Use those offsets on the day. If only one path was rehearsed, start one table first and release
the next when indexing progresses; do not promise 40 simultaneous starts.

| Bottleneck | Symptom | Mitigation |
|---|---|---|
| Search indexing (1 replica, shared) | indexers queue; `provision` slow (> 2 min) | hold new starts until the queue drains; review SKU/replicas before the event |
| Search object limits | `provision` fails "quota" | `fomo capacity` before; never upgrade silently |
| Chat TPM | 429 in `agents ask`/`eval` | ≥ 1k TPM per participant; CLI retries with backoff |
| Decision TPM | miner run slows | 25 docs × ~1.5k tokens per run; re-runs are delta-only |
| Embedding TPM | indexer errors in `status` | indexers retry automatically on the next schedule; `smart --reset` |
| Hosted agent activation | `miner deploy` ~60-70 s; first invoke 424 | CLI waits; no action |
| RBAC propagation | first `miner run` 403 for 1-2 min | CLI retries every 30 s (max 6 min) |

## 3. During the session

```powershell
uv run fomo facilitator dashboard              # stage per participant; read-only
uv run fomo facilitator dashboard --no-agents  # faster under load
uv run fomo status --participant <name> -v     # one participant in detail
```

Stages: `not-provisioned` → `provisioning-incomplete` → `baseline-ready` → `classified-not-indexed`
→ `smart-index-ready` → `complete` (`complete-local-miner` if the hosted miner was skipped).
Use the [live control sheet](facilitator-control-sheet.md) for checkpoints and rescue decisions.

## 4. Quota or capacity failure mid-session

1. Read the failing action in the plan output (`failed: …`, reason + fix).
2. 429 / TPM: wait 60 s and re-run the same command; all steps are idempotent.
3. Search object limit: move late participants to topology A or a second Search service; do not
   delete other participants' objects.
4. Model unavailable: preflight `--smoke` on the next run; note it in the session retro.

## 5. Cleanup

| Scope | Command |
|---|---|
| One participant | `uv run fomo cleanup --participant <name> --apply` (type `DELETE`) |
| Reset for a rerun | `uv run fomo reset-participant --participant <name> --scope all --apply` |
| Disposable facilitator-owned environment only | `az group delete -n <rg>` (never delete the customer's shared group or accounts) |

Cleanup deletes only objects with the participant's prefix: agents (hosted with `force`),
indexers, indexes, skillsets, data source, container, and the hosted agent's role assignments.
