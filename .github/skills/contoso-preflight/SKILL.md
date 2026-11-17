---
name: contoso-preflight
description: Read-only readiness check for the Contoso Oil Foundry workshop - local tools, Azure context, providers, models, policy, RBAC, data-plane access, service identities and capacity for N participants. Use when asked to check, verify or troubleshoot the workshop environment, before a workshop, or when something is BLOCKED.
license: MIT
---

# Contoso Oil workshop - Preflight

Thin wrapper over the deterministic CLI. Never re-implement checks in prose and never change
Azure state from this skill without the user's explicit yes.

## 1. Run the read-only check

From the repository root:

```
uv run contoso preflight --participants <N>
```

- `<N>`: expected number of participants (ask if unknown; default 1 for a personal check).
- Add `--smoke` only if the user agrees to 3 tiny paid model calls (< $0.001).
- Add `--local-only` if Azure login is not yet possible.
- If the environment file is missing: `uv run contoso env pull --resource-group <rg>` (read-only).

Exit code: 0 = PASS/WARNING, 2 = at least one BLOCKED, 1 = tool error.

## 2. Explain the result

Summarise per category: count of PASS / WARNING / BLOCKED. For every BLOCKED and WARNING item
quote the `fix` line and say **who must act** (participant, facilitator or admin). Do not soften
BLOCKED into success. Point to the scrubbed report
`.workshop/preflight/readiness-<timestamp>.shareable.json` (IDs and users masked) as the file to
send to the facilitator; the unscrubbed report stays local.

For every missing resource, include the matching `rbac.provision-*` check: Storage accounts,
Foundry accounts/projects/connections and model deployments, Search services and participant
indexes/indexers/skillsets/data sources, and Blob containers. Creation checks use the
appropriate parent scope and continue even when another resource's permission query fails.
Participant object checks use `--participant <hackername>` (or the remembered participant);
without one, explicitly state these checks were not performed.

- Creation permission granted: explain **missing, self-provisionable subject to approval**.
  Keep the missing resource BLOCKED until provisioned and re-checked.
- Creation permission not granted: tell the participant to have the facilitator/admin
  **provision it for them before the workshop**; do not assume everyone needs Contributor.
- Permissions unavailable: report **unknown**, never self-provisionable.

An RBAC PASS is only an indicator: policy, quota, provider registration and permissions
needed by the full Bicep template (including role assignments) may still block deployment.
Existing shared resources do not require participants to have creation permissions.
Distinguish creating a resource from using its data: verify Blob read/write/metadata at
the participant container scope (Storage Blob Data Contributor), Search document access
(Search Index Data Contributor), Foundry invocation access, and the ability to grant the
hosted agent its container/account roles. A management-plane Contributor role does not
grant data-plane access. Missing roles must be granted by the facilitator/admin, not by
this skill; scoped/conditional RBAC and functional read-only probes remain separate evidence.

## 3. Optional remediation (approval required)

Only the safe, participant-level fixes are automated by `preflight --fix`
(environment pull, provider registration):

1. `uv run contoso preflight --fix --plan` and show the plan verbatim.
2. Ask the user: "Apply these changes to subscription <name>?" Wait for an explicit yes.
3. Only then: `uv run contoso preflight --fix --apply --yes`. The command re-checks afterwards.

Missing resources are not part of `preflight --fix`. If creation RBAC is granted,
use the existing infra workflow with the appropriate Bicep parameters: show
`uv run contoso infra deploy --parameters <file>.bicepparam --plan` verbatim, verify the
template's deployment and role-assignment permissions, explain costs and the target
subscription/resource group, then wait for an explicit yes before applying that plan.
Do not grant roles to work around missing permissions. Rerun preflight after provisioning;
otherwise direct the participant to the facilitator/admin for pre-provisioned resources.
For participant containers and Search objects, use the existing participant workflow instead:
show `uv run contoso provision --participant <hackername> --no-smoke --plan`, wait for explicit
approval, then apply that plan. It also uploads the corpus, indexes/embeds documents and
creates the baseline agent; explain that scope and cost, not just the missing object.
Do not silently enable the smart indexer or skip the workshop's classification stages.

Role assignments, policy exemptions, PIM activation and quota are **requests to an admin**,
never actions of this skill.

## Example prompts

- "Use /contoso-preflight to check my workshop environment."
- "Use /contoso-preflight for 40 participants and tell me what blocks us."
