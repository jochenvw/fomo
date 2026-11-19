---
name: contoso-cheatcode
description: Complete the whole Contoso Oil workshop flow for one participant with the reference classifier - provision, deploy the hosted Metadata Miner, classify, smart index, Smart RAG and side-by-side evaluation - using the same code paths and the same approval gate. Use when a facilitator wants the finished demo, or a participant is blocked or wants to skip ahead.
license: MIT
---

# Contoso Oil workshop - Cheatcode

Not a safety bypass: same permission checks, same plan, same confirmation, same namespace.

## Steps

Before planning, follow "Confirm and remember the Azure target" in
`../contoso-preflight/SKILL.md`: confirm directory, then subscription, then resource group.
Reuse the locally saved confirmation only after checking the resolved environment and live
Azure CLI context match. Reconfirm any drift; never silently change subscriptions.
Pull missing environment configuration for that confirmed target and rerun full preflight.
Target confirmation does not approve any of the paid/write actions below.

1. Plan (read-only): `uv run contoso cheatcode --participant <hackername>`.
2. Show the full plan to the user, including the cost lines and the two role assignments for
   the hosted agent identity. Ask for an explicit yes.
3. Only then: `uv run contoso cheatcode --participant <hackername> --apply --yes`.
   Typical duration on pre-provisioned shared infrastructure: 3-5 minutes (hosted agent
   activation ~1 min, RBAC propagation up to a few minutes is retried automatically).
4. Present the final table: raw vs smart precision@5 per question, the cited document IDs of
   both agents, and the links to the three agents, both indexes, the container and the logs.

Options: `--classifier <file>` to use the participant's own classifier instead of the reference;
`--force-deploy` to redeploy the hosted agent even if the code hash is unchanged.

## Failure handling

If the hosted deployment or invocation fails, quote the blocker and the `fix`. Do **not** run
`contoso miner run --local` silently: it is a labeled learning fallback that does not meet the
hosted-agent requirement - offer it, explain that, and let the user decide.

Resources stay in place. Cleanup is a separate request: `uv run contoso cleanup`.

## Example prompts

- "Use /contoso-cheatcode to finish the demo for nimble-otter."
- "I'm stuck - use /contoso-cheatcode for my participant."
