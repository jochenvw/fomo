---
name: contoso-preflight
description: Read-only readiness check for the Contoso Oil Foundry workshop - local tools, Azure context, providers, models, policy, RBAC, data-plane access, service identities and capacity for N participants. Use when asked to check, verify or troubleshoot the workshop environment, before a workshop, or when something is BLOCKED.
license: MIT
---

# Contoso Oil workshop - Preflight

Thin wrapper over the deterministic CLI. Never re-implement checks in prose and never change
Azure state from this skill without the user's explicit yes.

## 1. Confirm and remember the Azure target

Before Azure checks, confirm **directory (tenant) -> subscription -> resource group**, in
that order. Never infer the workshop target from a successful sign-in or an enabled subscription.
Use read-only discovery; do not switch Azure CLI context or provision anything automatically.
For `--local-only`, skip this step and explicitly say Azure readiness is not assessed.

1. **Directory:** run `az account show --output json`. Show the directory name when available
   and `tenantId`, and ask the caller to confirm this is the workshop directory. If signed out
   or wrong, have them run `az login --tenant <tenant-id>`, then inspect and confirm again.
2. **Subscription:** only after directory confirmation, list enabled subscriptions in that
   tenant with `az account list --output json` (filter locally by the confirmed `tenantId`).
   Show names and IDs, identify the active subscription and ask which is the workshop target.
   If different, have the caller run `az account set --subscription <sub-id>`.
   Re-read `az account show` and verify both IDs before continuing.
3. **Resource group:** only after subscription confirmation, run
   `az group list --subscription <sub-id> --output json`. Show names and locations and ask
   the caller to confirm the workshop group. Do not select the first or only result silently.
   Validate it with `az group show --subscription <sub-id> --name <rg> --output json`.
   If discovery is denied, ask for the facilitator's exact target; access remains unverified.

Use the question/confirmation tool for each decision and wait for an explicit answer.
Confirmation of a target is **not** approval to deploy, grant roles, register providers or spend.

**Local workshop memory:** reuse the gitignored `.workshop/environment.json`, not Copilot
long-term memory. After the three confirmations, save `tenant_id`, `subscription_id` and
`resource_group`, plus the same three-value object under `extra.confirmed_azure_context`.
Preserve unrelated configuration when the target is unchanged. For a different target,
explain that resource endpoints must be refreshed and save a fresh environment containing
only the confirmed target and confirmation object; never retain the previous target's resources.
Do not store tokens, credentials or user identity. Keep `.workshop/participant.json` as the
existing hackername memory; do not change it during target selection.

On later calls, read this saved confirmation and `uv run contoso env show`, compare the
resolved target (including environment-variable/flag overrides) with it, and verify the active
CLI tenant/subscription and resource group's subscription. If all match, show the remembered
target briefly and reuse it without repeating questions. If anything differs, is unconfirmed,
or cannot be verified, stop Azure operations and repeat the relevant confirmations in order;
never silently overwrite or follow a changed CLI default.

If workshop resource configuration is missing, use the confirmed target:

```
uv run contoso env pull --subscription <sub-id> --resource-group <rg>
```

This reads deployment outputs and writes local configuration; it does not provision Azure
resources. After a successful pull, restore `extra.confirmed_azure_context` in the resulting
file and verify its three target fields still match. If no successful workshop deployment
exists, keep the confirmed target locally and direct the facilitator/admin to provision it
through the existing approval workflow. Alternatively, the facilitator can provide the
environment file; validate its target and record the caller's confirmation before using it.

## 2. Run the read-only check

From the repository root:

```
uv run contoso preflight --participants <N> --participant <hackername>
```

- `<N>`: expected number of participants (ask if unknown; default 1 for a personal check).
- Use the supplied or remembered hackername; omit `--participant` only if none is known.
- Add `--smoke` only if the user agrees to 3 tiny paid model calls (< $0.001).
- Add `--local-only` if Azure login is not yet possible.
- If the environment file is missing/incomplete, complete step 1 before Azure checks.

Exit code: 0 = PASS/WARNING, 2 = at least one BLOCKED, 1 = tool error.

## 3. Explain the result

Summarise per category: count of PASS / WARNING / BLOCKED. For every BLOCKED and WARNING item
quote the `fix` line and say **who must act** (participant, facilitator or admin). Do not soften
BLOCKED into success. Point to the scrubbed report
`.workshop/preflight/readiness-<timestamp>.shareable.json` (IDs and users masked) as the file to
send to the facilitator; the unscrubbed report stays local.

If `azure.environment` is BLOCKED, explain **missing configuration; Azure readiness not yet
assessed**, not "Azure resources are missing". The checker returns early: resource existence,
RBAC, model availability, policy, networking and capacity are unknown, and `capacity: {}`
means unassessed. Resolve the configuration using step 1 and rerun full preflight before
claiming readiness. Likewise, sign-in and subscription PASS alone do not establish access.
The optional Bicep warning belongs to the facilitator and does not prevent participants
from using already-provisioned infrastructure.

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

## 4. Optional remediation (approval required)

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
