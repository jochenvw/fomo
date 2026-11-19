# Troubleshooting

Every error prints `reason` + `fix`; JSONL logs are in `.workshop/runs/<name>/`. Find the
symptom, then apply the fix.

## 1. Sign-in and environment

| Symptom | Cause | Fix |
|---|---|---|
| `DefaultAzureCredential failed` / `AzureCliCredential` error | not signed in | `az login`, then `az account set -s <sub>` |
| `azure.target` BLOCKED | CLI subscription ≠ workshop subscription | `az account set -s <workshop sub>` |
| `connection interrupted` / timeout during an Azure CLI read | temporary connectivity/service failure | supported reads retry twice; after exhaustion check VPN/proxy/connectivity, then rerun the failed check |
| `azure.resource-group` BLOCKED, `missing: true` | saved group not found in the confirmed subscription | preflight offers re-create empty / choose existing / create another / defer; review `fomo env select ... --plan` and approve before applying |
| `azure.resource-group` BLOCKED, `missing: null` | access or connectivity could not be verified | fix the reported access/network issue and retry; do not assume the group was deleted or recreate it |
| `environment incomplete` | group saved, services not configured | `uv run fomo preflight` names the step: `env pull` (FOMO deployment), `env discover` (IT-provisioned services) or a request to IT |
| `UnicodeEncodeError` on Windows | console code page | `$env:PYTHONIOENCODING='utf-8'` |
| `classifier.yaml still contains TODO` | starter not finished | complete the `criteria` descriptions |

Interrupted **writes** are not automatically replayed: inspect actual state before deciding
what remains. Re-creating a missing group does not restore its former services or data.
The saved target is changed only after successful verification; full readiness must be rechecked.

## 2. Provision / Search

| Symptom | Cause | Fix |
|---|---|---|
| 403 on Search REST | role not yet effective (new participant) | wait 2-5 min; `preflight` → `rbac.*` |
| indexer `transientFailure` / embedding 429 | shared embedding TPM | `uv run fomo status`; re-run `provision --apply` or `smart --reset --apply` |
| `quota` / "exceeds maximum" | Search object limit | `uv run fomo capacity`; see facilitator runbook §4 |
| Smart RAG returns nothing | smart indexer still disabled or never re-ran after classification | `uv run fomo smart --apply` |

## 3. Hosted Metadata Miner

| Symptom | Cause | Fix |
|---|---|---|
| `miner run` 403 `PermissionDenied`, "Needs Storage Blob Data Contributor" | role assignment propagating (1-2 min) | none; CLI retries every 30 s up to 6 min |
| 424 `session not ready` | agent activating (~60-70 s after deploy) | none; CLI retries |
| 403 `AuthorizationFailure` / "network security perimeter" | see [hosted networking](#hosted-networking) | storage network mode |
| `miner deploy` denied creating role assignment | ABAC condition or missing RBAC Administrator role | redeploy Bicep with your object ID in `participants` |
| `cleanup` 409 "active sessions" | hosted agent has live sessions | fixed: CLI deletes with `force=true`; re-run cleanup |
| documents `needs_review` | below `min_probability` or `min_margin` | intended; inspect probabilities with `try_classifier.py` |
| Decision 401 | wrong host | the CLI uses `https://<account>.openai.azure.com/providers/microsoft/v1/systemone`; `services.ai.azure.com` rejects Entra tokens |

<a id="hosted-networking"></a>
## 4. Hosted networking

**Symptom:** hosted `miner run` fails with 403 `AuthorizationFailure` while `--local` works.

**Cause:** the hosted agent runs in Microsoft-managed compute. Storage protected by a **network
security perimeter** or `publicNetworkAccess=Disabled` rejects it, even with correct RBAC.
Some sandbox subscriptions (e.g. MCAP) attach such a perimeter by policy.

**Fix (in order):**

1. Keep `storageNetworkMode='public'` (default). Storage stays Entra-only (no shared keys).
2. If policy forces a perimeter, add the exemption tag your policy honors via
   `storagePolicyExemptionTags` (see `infra/parameters/*.bicepparam`), redeploy, and confirm with
   `uv run fomo preflight` → `network.hosted-egress` PASS.
3. If neither is allowed: run the miner with `--local` (clearly labeled fallback; not the hosted
   requirement) and record the constraint in the session notes.

## 5. Evaluation

| Symptom | Cause | Fix |
|---|---|---|
| smart ≈ raw | classification not indexed | `status` → smart doc count; `smart --apply` |
| `smart_nofilter` ≠ raw | indexes out of sync | `reset-participant --scope indexes --apply` |
| q05/q06/q08 drop to 0 with filter | **expected**: fixed `contract` filter excludes non-contract answers | discuss; compare `smart_desired` |
