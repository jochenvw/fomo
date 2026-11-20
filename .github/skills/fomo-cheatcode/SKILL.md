---
name: fomo-cheatcode
description: Complete the whole FOMO metadata-agent workshop flow for one participant with the reference classifier and Contoso Oil demo corpus - provision, deploy the hosted Metadata Miner, classify, smart index, Smart RAG and side-by-side evaluation - using the same code paths and the same approval gate. Use when a facilitator wants the finished demo, or a participant is blocked or wants to skip ahead.
license: MIT
---

# FOMO - Cheatcode

Not a safety bypass: same permission checks, same plan, same confirmation, same namespace.

## Presentation

Open each invocation with the compact ASCII mascot from the "Presentation" section in
`../fomo-preflight/SKILL.md`, followed by **🚀 FOMO Cheatcode**. Use its status emoji conventions.
Show the greeting once, keep plans/approvals clear, and never decorate commands or JSON.
Follow "Operating model: you run everything" in `../fomo-preflight/SKILL.md`: you run every
command; the participant only answers and approves.
The rocket is not evidence that a deployment succeeded.

## Steps

Before planning, follow "Confirm and remember the Azure target" in
`../fomo-preflight/SKILL.md`: check sign-in, show the current username, then confirm
directory, subscription and resource group in order. Always check sign-in and show the
username even when reusing a remembered target.
The resource-group step offers an existing group or a separately approved new empty group;
both choices are saved with `env select`. An empty group still needs infrastructure provisioning.
Reuse the locally saved confirmation only after checking the resolved environment and live
Azure CLI context match. Reconfirm any drift; never silently change subscriptions.
Follow the preflight skill's existing/new-group branch for missing environment configuration
and rerun full preflight; do not pull deployment outputs from a new empty group.
Target confirmation does not approve any of the paid/write actions below.
For interruptions or a missing remembered group, follow preflight's "Constructive recovery":
retry safe reads, offer approved target-recovery choices, and inspect uncertain writes before replaying.

1. Plan (read-only): `uv run fomo cheatcode --participant <hackername>`.
2. Show the full plan to the user, including the cost lines and the two role assignments for
   the hosted agent identity. Ask for an explicit yes.
3. Only then: `uv run fomo cheatcode --participant <hackername> --apply --yes`.
   Typical duration on pre-provisioned shared infrastructure: 3-5 minutes (hosted agent
   activation ~1 min, RBAC propagation up to a few minutes is retried automatically).
4. Present the final table: raw vs smart precision@5 per question, the filter Smart RAG's
   knowledge base generated per question, the cited document IDs of both agents, and the links to
   the three agents, both indexes, the knowledge base, the container and the logs. Smart RAG also
   gets query planning and semantic reranking that Simple RAG lacks; say so when reporting the delta.

Options: `--classifier <file>` to use the participant's own classifier instead of the reference;
`--force-deploy` to redeploy the hosted agent even if the code hash is unchanged.

## Failure handling

If the hosted deployment or invocation fails, quote the blocker and the `fix`. Do **not** run
`fomo miner run --local` silently: it is a labeled learning fallback that does not meet the
hosted-agent requirement - offer it, explain that, and let the user decide.

Resources stay in place. Cleanup is a separate request: `uv run fomo cleanup`.

## Example prompts

- "Use /fomo-cheatcode to finish the demo for nimble-otter."
- "I'm stuck - use /fomo-cheatcode for my participant."
