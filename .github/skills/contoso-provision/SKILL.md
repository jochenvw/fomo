---
name: contoso-provision
description: Provision one participant's baseline for the Contoso Oil Foundry workshop - Blob container with the synthetic corpus, raw Azure AI Search index and indexer, Simple RAG prompt agent, and an editable Metadata Miner starter - then guide the participant through miner deploy/run and Smart RAG. Use when a participant starts the workshop, asks for the next step, or wants status, reset or cleanup of their own resources.
license: MIT
---

# Contoso Oil workshop - Provision

Thin wrapper over the deterministic CLI. Every write is plan -> user yes -> apply.

## Approval rule (applies to every step below)

1. Run the command **without** `--apply`: it prints a PLAN (subscription, resource group,
   each create/update/run, cost hints) and changes nothing.
2. Show the plan to the user and ask for an explicit yes. Approval covers this one plan only.
3. Only then re-run the identical command with `--apply --yes`.

Never add `--apply` on your own initiative, never switch subscription, never run cleanup unless
the user asked for cleanup.

## Steps

1. Readiness (read-only): `uv run contoso status --participant <hackername>`. If the
   environment is missing: `uv run contoso env pull --resource-group <rg>`.
2. Baseline: `uv run contoso provision --participant <hackername>`
   (omit `--participant` to get a generated name; pass it explicitly so plan and apply match).
   Result: queryable raw index, Simple RAG agent with a cited smoke-test answer, starter in
   `.workshop/runs/<hackername>/miner/`.
3. Participant exercise - the classifier is theirs to improve:
   - edit `.workshop/runs/<hackername>/miner/classifier.yaml` (finish the TODOs, bump `version`);
   - try it read-only: `uv run python .workshop/runs/<hackername>/miner/try_classifier.py doc-003 doc-018`.
4. `uv run contoso miner deploy --classifier .workshop/runs/<hackername>/miner/classifier.yaml`
   (hosted Foundry agent + its two least-privilege grants).
5. `uv run contoso miner run --hosted` (delta: unchanged documents are skipped).
6. `uv run contoso smart` (enable smart indexer, create Smart RAG with `document_type eq 'contract'`).
7. `uv run contoso eval` (read-only apart from agent calls): before/after table with actual
   top-k hits, filters and citations. Report honestly if Smart RAG does not improve.

Other commands: `uv run contoso agents ask simple|smart "<question>"`,
`uv run contoso reset-participant --scope metadata|indexes|all`,
`uv run contoso cleanup` (destructive, separate confirmation, participant assets only).

If a step fails, quote the `fix` line from the error and run `contoso status`; all steps are
idempotent and safe to rerun.

## Example prompts

- "Use /contoso-provision to set me up as cosmic-wombat."
- "Use /contoso-provision - what is my next step?"
