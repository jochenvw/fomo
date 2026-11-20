# Shared workshop: facilitator control sheet (60 minutes)

**For the facilitator and their agent.** Keep this open; send only the kickoff prompt below to
participant agents. The goal is a fun, workable session: everyone should get to compare citations
and explain when metadata helps or hurts, even if a deployment stalls.

**Before the room opens:** prefer Sweden Central for new infrastructure. Use another working region
if full preflight and a hosted deploy/run rehearsal succeed on the actual shared target. If services
are missing, agree who can provision them and by when; permission alone isn't readiness. Save a
rehearsal comparison for fallback. Follow the [runbook](facilitator-runbook.md) for capacity and starts.

### Private kickoff prompt (replace the three placeholders)

> Use `/fomo-preflight`. I am joining the shared workshop in subscription `<subscription-id>`,
> existing resource group `<resource-group>`, with `<cohort-size>` participants. Verify my sign-in
> and confirm the target with me. Find the shared Foundry project, AI Search service and Storage
> account; ask if there is more than one match. Remember the verified resources locally and
> initialize or reuse my random hackername. Run full preflight and tell me my name, whether I can
> start, and who can fix any blocker. Do not create Azure resources, make paid calls or send files.
> When I can start, tell me to continue with `/fomo-help`.

Use the customer's private workshop channel. No credentials in the prompt. No uploads, public posts
or commits of `.workshop/`. Each participant uses their own checkout; only Azure services are shared.

### In the room

| Minutes | Participant checkpoint | Facilitator agent: observe and recommend |
|---|---|---|
| 00–05 | Verified target and saved name; introduce the payment-terms question. | Ask for a show of hands. The dashboard cannot see local preflight or people without a raw index. Pair anyone blocked on sign-in. |
| 05–15 | Baseline answers the question. Identify one citation that sounds like a contract but isn't. | `uv run fomo facilitator dashboard`: look for `baseline-ready`. If indexing queues across tables, hold new starts; use the rehearsal answer while waiting. |
| 15–35 | Edit one label criterion, test it on a near miss, deploy and run the hosted miner. | Coach the label decision. `classified-not-indexed` means metadata exists. If activation or role propagation is slow, defer that deploy and follow a neighbour; don't launch competing retries. |
| 35–45 | Smart index has document types; ask Smart RAG the same question. | Look for `smart-index-ready` then `complete` (Smart RAG built; the evaluation is local, so the dashboard cannot see it). For one participant who wants to skip classifier work, offer `/fomo-cheatcode`: it uses the reference classifier. Explain what it skips and obtain approval first. |
| 45–55 | Run `uv run fomo eval` (required; `fomo next` shows `complete`). Read the per-question table, not individual chat answers. | If several tables hit the same service limit, stop new work and use the saved comparison. In the table, identify which filter was chosen and which evidence it excluded, including non-contract controls. |
| 55–60 | Debrief: name one helpful label and one case where filtering hurts. | Protect these five minutes. Distinguish observed results from the rehearsal; leave cleanup for after discussion. |
