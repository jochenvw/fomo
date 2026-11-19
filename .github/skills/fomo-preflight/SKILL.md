---
name: fomo-preflight
description: Read-only readiness check for the FOMO metadata-agent workshop - local tools, Azure context, providers, models, policy, RBAC, data-plane access, service identities and capacity for N participants. Use when asked to check, verify or troubleshoot the workshop environment, before a workshop, or when something is BLOCKED.
license: MIT
---

# FOMO - Preflight

Thin wrapper over the deterministic CLI. Never re-implement checks in prose and never change
Azure state from this skill without the user's explicit yes.

## Operating model: you run everything

The participant does not type commands. **You run every `uv run fomo ...`, `az ...` and `git`
command yourself** with your shell tool, from the repository root, and translate the output into
plain language. The participant only answers questions, approves plans and completes the browser
sign-in. Never end a step with "run this command"; run it, or ask for the decision you need.

- **Setup:** if `uv` reports a missing environment or `fomo` is not found, run `uv sync` yourself
  (local only, no Azure change). If `uv` itself is missing, explain how to install it and wait.
- **Sign-in:** run `az login` yourself (add `--tenant <tenant-id>` for a confirmed directory).
  Tell the participant a browser window will open and wait for it. If no browser can open,
  use `az login --use-device-code` and relay the code and URL exactly.
- **Context switch:** after the participant explicitly picks a subscription, run
  `az account set --subscription <sub-id>` yourself and verify it. Their choice is the
  approval for this CLI-default change only.
- **Plans:** run `--plan`, present what it will do (targets, writes, cost) in plain language,
  include the command for transparency, ask yes/no, then run the identical command with
  `--apply --yes` yourself. Never ask the participant to apply it.
- **Fix lines:** report `fix` fields that name a `uv run`/`az` command as an action you can
  take (with approval where it writes), not as homework. Fixes owned by IT/facilitator/admin
  become a ready-to-send message you draft for the participant.

## Presentation

Open each skill invocation with this compact ASCII mascot and the heading **🔎 FOMO Preflight**:

```text
  [o_o]  FOMO
  /|_|\  Foundry Organizes Metadata.
   / \   Occasionally.
```

Use restrained status emoji alongside the actual status: ✅ PASS, ⚠️ WARNING, ⛔ BLOCKED,
ℹ️ INFO / not assessed. Never signal success before checks finish, soften a blocker with a
joke, or replace the fix/owner with decoration. Show the mascot once per invocation, not for
every command or approval. Keep art and emoji in conversational headings only, never inside
commands, JSON, report data or resource names.

## 1. Confirm and remember the Azure target

Before Azure checks, follow **check sign-in -> show username -> confirm directory (tenant) ->
confirm subscription -> confirm resource group**, in that order. Never infer the workshop
target from a successful sign-in or an enabled subscription.
Use read-only discovery; switch Azure CLI context only after the participant's explicit choice
and never provision anything automatically.
The only new-resource action allowed here is an empty resource group through the separately
approved plan below; creating workshop services remains a separate infrastructure workflow.
For `--local-only`, skip this step and explicitly say Azure readiness is not assessed.

1. **Sign-in:** first run `az account show --output json`. Check that the session can obtain
   an Azure management token with
   `az account get-access-token --resource https://management.azure.com/ --query expiresOn --output tsv`;
   a cached account alone does not establish a usable sign-in. Never display or save the token.
   If signed out or the session has expired, run `az login` yourself, wait, then retry.
   If sign-in cannot be verified, report the error and stop Azure discovery; do not ask them
   to confirm a tenant or subscription yet.
2. **Username:** after sign-in succeeds, explicitly show `Signed in as: <user.name>` from
   the account response before asking about the tenant. Also show `user.type`; if it is a
   service principal/managed identity, label it as such rather than presenting it as a human
   username. If the identity is missing, report that rather than guessing. If the caller
   says it is the wrong account, run `az login` again so they can pick the intended account, and restart.
3. **Directory:** show the directory name when available and `tenantId`, and ask the caller
   to confirm this is the workshop directory. If wrong, ask for the workshop tenant, run
   `az login --tenant <tenant-id>` yourself, then repeat the sign-in check and username display before
   confirming the directory again.
4. **Subscription:** only after directory confirmation, list enabled subscriptions in that
   tenant with `az account list --output json` (filter locally by the confirmed `tenantId`).
   Show names and IDs, identify the active subscription and ask which is the workshop target.
   If different, run `az account set --subscription <sub-id>` yourself after their choice.
   Re-read `az account show` and verify both IDs before continuing.
5. **Resource group:** only after subscription confirmation, run
   `az group list --subscription <sub-id> --output json`. Show names and locations and offer
   **use an existing group** or **create a new empty group**, even if groups already exist.
   Do not select the first or only result silently. For an existing group, ask which one and
   validate it with `az group show --subscription <sub-id> --name <rg> --output json`.
   For a new group, ask for its name and Azure location; never silently choose the region.
   Suggest `rg-fomo-<event>` for a shared workshop or `rg-fomo-<hackername>` for an isolated
   participant. These are suggestions only; remember the actual selected name.
   If discovery is denied, ask for the exact target or intended new group; access/creation
   permissions remain unverified. Do not treat a permission error as proof a group is absent.

Use the question/confirmation tool for each decision and wait for an explicit answer.
Confirmation of a target is **not** approval to deploy, grant roles, register providers or spend.

**Save the existing-group choice:** show this local-write plan:

```
uv run fomo env select --tenant <tenant-id> --subscription <sub-id> --resource-group <rg> --plan
```

Ask for approval, then rerun the identical command with `--apply --yes` instead of `--plan`.
It verifies the group and remembers the target; it does not modify the existing group.

**Create a new empty group:** show this plan verbatim:

```
uv run fomo env select --tenant <tenant-id> --subscription <sub-id> --resource-group <new-rg> --create-resource-group --location <region> --plan
```

Show directory, subscription, group name and location. Explain that this creates **only the
empty resource group** and saves the target locally; the group itself has no charge and this
does not deploy Foundry, Storage, Search, models or roles. Creation requires resource-group
write permission at subscription scope and may be denied by policy. Ask explicitly whether
to create that group in that subscription; only after yes rerun the identical command with
`--apply --yes` instead of `--plan`. The CLI checks context, verifies creation and then saves.
If the name already exists, return to the existing-group choice; never silently adopt it.
On denial/failure, quote the error and ask the facilitator/admin to create it or supply an
accessible group. Do not grant roles, claim it was created or save a failed selection.

**Local workshop memory:** reuse the gitignored `.workshop/environment.json`, not Copilot
long-term memory. Both `env select` branches save `tenant_id`, `subscription_id` and
`resource_group`, plus the same three-value object under `extra.confirmed_azure_context`,
without needing a Bicep deployment. Existing saved configuration is preserved for an unchanged
target. A changed target clears old resource endpoints; explain that they must be refreshed.
Do not store tokens, credentials or user identity. Keep `.workshop/participant.json` as the
existing hackername memory; do not change it during target selection.

On later calls, always check sign-in and show the current username first, even when a target
is remembered. Then read this saved confirmation and `uv run fomo env show`, compare the
resolved target (including environment-variable/flag overrides) with it, and verify the active
CLI tenant/subscription and resource group's subscription. If all match, show the remembered
target briefly and reuse it without repeating questions. If anything differs, is unconfirmed,
or cannot be verified, stop Azure operations and repeat the relevant confirmations in order;
never silently overwrite or follow a changed CLI default. A missing remembered resource group
is a recovery decision, not a reason to abandon the participant; use the recovery section below.

**Group saved, services not configured.** This is the common enterprise case: internal IT
deploys Foundry, Storage and Search, and participants only *use* them (some may also be allowed
to create). Do not ask the facilitator for the group name, which is already known. Run preflight;
when `azure.environment` is BLOCKED it inspects the group read-only and reports
`azure.discovery` (what exists) plus `rbac.provision-services` (whether creation is allowed).
Follow the `azure.environment` fix, in this order:

1. **FOMO Bicep deployment found:** `uv run fomo env pull --subscription <sub-id> --resource-group <rg>`
   (reads deployment outputs, local write only).
2. **Services found (typically IT-provisioned):** show `uv run fomo env discover --plan` verbatim.
   It adopts the unique Foundry account, project, Storage and Search in the group, their real
   endpoints and, when default names are absent, a unique matching embedding/chat/decision
   deployment and existing project -> Search connection. Ask for approval of this local write,
   then rerun with `--apply --yes`. It never creates, changes or grants anything in Azure.
3. **Several candidates:** show the names, ask which ones the workshop uses (never pick),
   then plan `uv run fomo env discover --foundry-account <a> --project <p> --storage-account <s> --search-service <q> --plan`
   with only the ambiguous options.
4. **Services absent:** turn the fix into a short, ready-to-send request to internal IT listing
   each missing service and the participant's role on it, and offer to draft it. If
   `rbac.provision-services` says creation is allowed, still recommend IT first; offer
   self-deployment only as a separately approved paid plan
   (`uv run fomo infra deploy --parameters infra/parameters/participant.bicepparam --plan`).
5. **Group cannot be listed (403/connection):** the contents are unknown, not empty. Ask for at
   least Reader on the group, or for the service names from IT.

After a successful pull/discover, **always rerun full preflight**. Only that run assesses
resources, the Search connection, models, RBAC, data-plane access and capacity. Adopted
services may still lack the required models, the connection or the participant's roles; report
those as requests to IT/the admin with the exact role and scope from the report. For a newly
created empty group there is nothing to adopt; explain that only resource-group setup is
complete. Do not repeatedly retry `env pull` or `env discover` with unchanged inputs.
Alternatively, the facilitator can provide the environment file; validate its target and
record the caller's confirmation through `env select` before using it.

## Constructive recovery (applies throughout)

Work toward a usable workshop, not just a list of failures: **diagnose -> propose the smallest
safe fix -> obtain approval if it changes state -> verify -> resume**. Keep the actual evidence,
owner and next command visible. A recovered check can pass; an unverified one cannot.

- **Interrupted connection, timeout or temporary service failure:** retry the identical
  read-only command up to twice, with short backoff (about 1 then 2 seconds). The workshop's
  Python wrapper already retries its supported Azure CLI reads; raw `az` shell commands need
  the skill-level retries. Do not multiply the wrapper's budget by repeatedly rerunning a
  `uv run fomo ...` command after its retries are exhausted. If the tool reports an interruption, check whether the
  original command is still running before starting another. Never poll or loop indefinitely.
  If it still fails, explain that the result is unknown (not proof of missing resources or a
  signed-out user), suggest checking connectivity/VPN/proxy/Azure status, and offer a retry
  after that is addressed or a local-only check while waiting.
- **Stored resource group not found:** first verify the live tenant/subscription against the
  remembered target, then require an actual not-found response in that subscription.
  `azure.resource-group` reports this even when service configuration is incomplete. Show the
  saved group name and subscription and ask the caller to choose **re-create this group**,
  **select an existing group**, **create a differently named group**, or **pause / ask the
  facilitator**. Use the existing `env select` plans above, confirm the creation location, show
  costs/scope and wait for approval before applying. Do not silently choose, erase the old
  selection on a failed attempt, or reinterpret a 403/connection error as absence.
  Re-creating makes an empty group, not a restoration of former services, roles or data.
  A successful create clears stale service endpoints and saves the confirmed target; rerun
  preflight and explain the separate infrastructure step still needed.
- **Expired sign-in / wrong account or target:** guide `az login` / explicit context selection
  and restart sign-in -> username -> tenant -> subscription -> group verification. Do not
  repeatedly retry an authentication error or silently switch identity/subscription.
- **Permission, policy, quota or unsupported-region failure:** do not retry unchanged
  indefinitely or grant roles. Name the smallest admin/facilitator action, offer an accessible
  existing environment or a separately confirmed alternative target, and preserve the blocker
  until the relevant probe passes. Any alternate region still needs model/capacity checks.
- **Interrupted write (including group creation):** never automatically replay it, even if a
  read would be retryable. Inspect the target's actual state first; the write may have succeeded.
  Explain what is known, reconcile what remains with the approved plan, and ask again for any
  new/different mutation. Do not delete resources as a recovery shortcut.

After a fix, repeat the failed read and resume relevant checks with the same participant and
planned cohort size. Do not repeat paid smoke calls without approval. Continue independent
checks where safe and label skipped/dependent checks as unassessed. Finish with what recovered,
what remains blocked, who must act and the next concrete step; never just "try again".

## 2. Run the read-only check

From the repository root:

```
uv run fomo preflight --participants <N> --participant <hackername>
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
means unassessed. Resolve the configuration using "Group saved, services not configured" in
step 1 (use the `azure.discovery` evidence) and rerun full preflight before
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
- Creation permission not granted: draft a request for the participant asking the facilitator/admin to
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

1. `uv run fomo preflight --fix --plan` and show the plan verbatim.
2. Ask the user: "Apply these changes to subscription <name>?" Wait for an explicit yes.
3. Only then: `uv run fomo preflight --fix --apply --yes`. The command re-checks afterwards.

Missing resources are not part of `preflight --fix`; the approved empty-group branch in step 1
uses `env select` instead. For workshop services, if creation RBAC is granted,
use the existing infra workflow with the appropriate Bicep parameters: show
`uv run fomo infra deploy --parameters <file>.bicepparam --plan` verbatim, verify the
template's deployment and role-assignment permissions, explain costs and the target
subscription/resource group, then wait for an explicit yes before applying that plan.
Do not grant roles to work around missing permissions. Rerun preflight after provisioning;
otherwise direct the participant to the facilitator/admin for pre-provisioned resources.
For participant containers and Search objects, use the existing participant workflow instead:
show `uv run fomo provision --participant <hackername> --no-smoke --plan`, wait for explicit
approval, then apply that plan. It also uploads the corpus, indexes/embeds documents and
creates the baseline agent; explain that scope and cost, not just the missing object.
Do not silently enable the smart indexer or skip the workshop's classification stages.

Role assignments, policy exemptions, PIM activation and quota are **requests to an admin**,
never actions of this skill.

## Example prompts

- "Use /fomo-preflight to check my workshop environment."
- "Use /fomo-preflight for 40 participants and tell me what blocks us."
