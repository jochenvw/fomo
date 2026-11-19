---
name: fomo-provision
description: Provision one participant's baseline for the FOMO metadata-agent workshop - Blob container with the Contoso Oil demo corpus, raw Azure AI Search index and indexer, Simple RAG prompt agent, and an editable Metadata Miner starter - then guide the participant through miner deploy/run and Smart RAG. Use when a participant starts the workshop, asks for the next step, or wants status, reset or cleanup of their own resources.
license: MIT
---

# FOMO - Provision

Thin wrapper over the deterministic CLI. Every write is plan -> user yes -> apply.

## Presentation

Open each invocation with the compact ASCII mascot from the "Presentation" section in
`../fomo-preflight/SKILL.md`, followed by **🛠️ FOMO Provision**. Use its status emoji conventions.
Show the greeting once, keep plans/approvals clear, and never decorate commands or JSON.
Follow "Operating model: you run everything" in `../fomo-preflight/SKILL.md`: you run every
command; the participant only answers, approves and edits their classifier.

## Approval rule (applies to every step below)

1. Run the command **without** `--apply`: it prints a PLAN (subscription, resource group,
   each create/update/run, cost hints) and changes nothing.
2. Show the plan to the user and ask for an explicit yes. Approval covers this one plan only.
3. Only then re-run the identical command with `--apply --yes`.

Never add `--apply` on your own initiative, never switch subscription, never run cleanup unless
the user asked for cleanup.

## Steps

Before any step, follow "Confirm and remember the Azure target" in
`../fomo-preflight/SKILL.md`: check sign-in, show the current username, then confirm
directory, subscription and resource group in order. Always check sign-in and show the
username even when reusing a remembered target.
The resource-group step offers an existing group or a separately approved new empty group;
both choices are saved with `env select`. An empty group still needs infrastructure provisioning.
Reuse `.workshop/environment.json` and its `extra.confirmed_azure_context` only after checking
the resolved configuration and live Azure CLI context still match. Context drift requires
reconfirmation, not an automatic subscription switch. Target confirmation is not write approval.
For interruptions or a missing remembered group, follow preflight's "Constructive recovery":
retry safe reads, offer approved target-recovery choices, and inspect uncertain writes before replaying.

1. Readiness (read-only): `uv run fomo status --participant <hackername>`. If the
   environment is missing/incomplete, follow the preflight skill's existing/new-group branch
   (do not pull deployment outputs from a new empty group), then rerun full preflight. A saved group
   with IT-provisioned services is adopted via preflight's "Group saved, services not configured"
   (`env discover`). Missing configuration
   means Azure readiness is unassessed; it does not prove resources are missing.
2. Baseline: `uv run fomo provision --participant <hackername>`
   (omit `--participant` to get a generated name; pass it explicitly so plan and apply match).
   Result: queryable raw index, Simple RAG agent with a cited smoke-test answer, starter in
   `.workshop/runs/<hackername>/miner/`.
3. Participant exercise - the classifier is theirs to improve:
   - edit `.workshop/runs/<hackername>/miner/classifier.yaml` (finish the TODOs, bump `version`);
   - try it read-only: `uv run python .workshop/runs/<hackername>/miner/try_classifier.py doc-003 doc-018`.
4. `uv run fomo miner deploy --classifier .workshop/runs/<hackername>/miner/classifier.yaml`
   (hosted Foundry agent + its two least-privilege grants).
5. `uv run fomo miner run --hosted` (delta: unchanged documents are skipped).
6. `uv run fomo smart` (enable smart indexer, create Smart RAG with `document_type eq 'contract'`).
7. `uv run fomo eval` (read-only apart from agent calls): before/after table with actual
   top-k hits, filters and citations. Report honestly if Smart RAG does not improve.
   For scored comparisons in the Foundry portal, use
   `uv run fomo eval-foundry --participant <hackername> --suite smoke` first, then
   `--suite full`. Each is a separate paid plan requiring approval before `--apply --yes`.
   The SDK uses the same corpus-grounded questions and evaluator thresholds for both pinned
   agent versions. Keep contract/non-contract/missing-answer slices separate, disclose skipped
   grades and reference-context limitations, and never promise an improvement.
   `--resume <manifest.json>` retrieves existing results without submitting new paid runs.

Other commands: `uv run fomo agents ask simple|smart "<question>"`,
`uv run fomo reset-participant --scope metadata|indexes|all`,
`uv run fomo cleanup` (destructive, separate confirmation, participant assets only).

If a step fails, quote the `fix` line from the error and run `fomo status`; all steps are
idempotent and safe to rerun.

## Example prompts

- "Use /fomo-provision to set me up as cosmic-wombat."
- "Use /fomo-provision - what is my next step?"
