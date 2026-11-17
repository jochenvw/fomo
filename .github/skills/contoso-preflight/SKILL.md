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

## 3. Optional remediation (approval required)

Only the safe, participant-level fixes are automated (environment pull, provider registration):

1. `uv run contoso preflight --fix --plan` and show the plan verbatim.
2. Ask the user: "Apply these changes to subscription <name>?" Wait for an explicit yes.
3. Only then: `uv run contoso preflight --fix --apply --yes`. The command re-checks afterwards.

Role assignments, policy exemptions, PIM activation and quota are **requests to an admin**,
never actions of this skill.

## Example prompts

- "Use /contoso-preflight to check my workshop environment."
- "Use /contoso-preflight for 40 participants and tell me what blocks us."
