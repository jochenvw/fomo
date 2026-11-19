# Prerequisites

## 1. Local tools

| Tool | Version tested | Needed by | Install |
|---|---|---|---|
| Git | 2.56 | everyone | <https://git-scm.com> |
| uv | 0.11.x | everyone (installs Python 3.11+ itself) | `winget install astral-sh.uv` / `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Azure CLI | 2.93 | everyone (sign-in, `AzureCliCredential`) | <https://aka.ms/installazurecli> |
| Bicep | 0.49 | facilitator only | `az bicep install` |
| GitHub Copilot CLI | 1.0.11x | optional front end (skills) | <https://docs.github.com/copilot/how-tos/copilot-cli> |

**Not needed:** Docker, azd, a container registry, Node.js, API keys. The hosted agent is deployed
from a source ZIP.

Windows: run `$env:PYTHONIOENCODING='utf-8'` if your console shows encoding errors.

## 2. Azure

| Item | Requirement | Checked by preflight |
|---|---|---|
| Resource providers | `Microsoft.CognitiveServices`, `Microsoft.Search`, `Microsoft.Storage` registered | `providers.*` |
| Region | one region with `gpt-5-mini`, `text-embedding-3-small`, `Microsoft-Decision-1.1` and Foundry hosted agents (tested: **Sweden Central**) | `models.*` |
| Quota | chat TPM ≥ ~1k × participants; see `fomo capacity` | `capacity.chat-tpm` |
| Search SKU | Basic ≤ 7 participants, S1 ≤ 25, S2 ≤ 100 | `capacity.search` |
| Policy | no deny on shared-key-free Storage, public endpoint (see [troubleshooting](troubleshooting.md#hosted-networking)) | `policy.*`, `network.hosted-egress` |

## 3. Roles

| Who | Needs | Why |
|---|---|---|
| Facilitator | Owner, or Contributor + Role Based Access Control Administrator, on the resource group | Bicep creates resources and role assignments |
| Participant | the 7 Bicep-granted role assignments (rows 6-11) in [security-and-rbac.md](security-and-rbac.md#2-role-matrix) | nothing else; no subscription roles |

PIM: activate eligible roles **before** the session; preflight reports eligible-but-inactive as
`rbac.pim` WARNING.

## 4. Verify in one command

```powershell
uv sync
az login
uv run fomo preflight --participants 40            # read-only; ~40 s
uv run fomo preflight --participants 40 --smoke    # + 3 tiny paid model calls
```

Verdict `READY` / `WARNING` → go. `BLOCKED` → each finding prints the fix; `--fix` plans the
fixable ones. Share `.workshop/preflight/readiness-*.json` (no IDs, UPNs or subscription names);
see [`examples/sample-readiness.json`](../examples/sample-readiness.json).
