# Participant guide (60 minutes)

You share Foundry, AI Search and Storage, but your workshop objects have your own **hackername**
prefix (e.g. `cosmic-wombat-a42f90`). Use your own checkout. Never use another person's prefix.

## 0. Setup (5 min, before the session if possible)

```powershell
git clone https://github.com/jochenvw/fomo; cd fomo
uv sync
```

With Copilot CLI: open `copilot` in the repo and paste the facilitator's
[kickoff prompt](facilitator-control-sheet.md#private-kickoff-prompt-replace-the-two-placeholders).
You don't need anyone else's environment file, and you don't submit a readiness report.
The skill first checks Azure CLI sign-in and shows the signed-in username, then asks you to
confirm your Azure directory (tenant), subscription and resource group in order before
checking Azure readiness. It remembers the confirmed target locally in the
gitignored `.workshop/environment.json` for the workshop and verifies it on later calls.
It checks sign-in and shows the current username on every call. If the CLI context or configured
target changes, it asks again; confirmation does not approve
provisioning. Missing environment configuration means Azure readiness has not yet been assessed,
not that the Azure resources are necessarily absent.

For the shared session, use the facilitator's **existing group**. For a separate new environment
only, you can instead **create a new empty group**.
Suggested new names: `rg-fomo-<event>` for a shared group or `rg-fomo-<hackername>` for your own.
Both choices are saved before workshop services need to exist. Without Copilot, sign in with
`az login`, select and verify the workshop subscription, then preview with:

```powershell
uv run fomo env select --tenant <tenant-id> --subscription <sub-id> --resource-group <rg> --plan
# For a new empty group, add: --create-resource-group --location <region>
```

After reviewing, rerun with `--apply` instead of `--plan` and confirm. Existing groups are not
modified; new groups require a separate creation approval and have no charge themselves.
This does not deploy workshop services or grant roles. A new empty group remains blocked for
the workshop until the facilitator/admin provisions the infrastructure; skip `env pull`
until a workshop deployment exists.

If your IT team already deployed Foundry, Storage and Search in your group, preflight lists them
and `uv run fomo env discover --plan` adopts them (local write only; nothing changes in Azure).
If something is missing, preflight names exactly what to request from IT and which role you need.

Once the shared services are configured, participant kickoff uses
`uv run fomo preflight --init-participant`. It checks there is room for one more participant,
checks new names against existing indexes and containers, remembers yours in
`.workshop/participant.json` for later commands, and prints the names your container, indexes
and agents will get. Nothing is created yet; `/fomo-help` (or `uv run fomo next`) takes it from
there. The random suffix does not depend on start time. The check is not an
Azure reservation; if a first provision plan shows unexpected existing assets, stop before applying.
The cohort-size check (`--participants <N>`) is for the facilitator.

## 1. Baseline: Simple RAG (10 min)

With Copilot CLI, `/fomo-help` takes you through this and every later step: it checks where you
are, explains what the step builds and where to see it, and runs it after your approval (or
gives you the command). Without Copilot, `uv run fomo next` prints the same checkpoint.

```powershell
uv run fomo provision          # read the plan for your saved name
uv run fomo provision --apply
uv run fomo agents ask simple "What payment terms apply under our supplier contracts?"
```

Step by step instead: `--step upload`, then `--step index`, then `--step agent` (each plans first;
add `--apply`).

Creates: your inbox container with 25 documents, raw index, Simple RAG agent, and starter folder
`.workshop/runs/<your-name>/miner/`.

**Look at the citations.** You will likely see `doc-003` (a *training deck about* contract
management) and `doc-002` (a procurement *policy*). They talk like contracts but are not.

## 2. Build the Metadata Miner (20 min)

Open `.workshop/runs/<your-name>/miner/classifier.yaml`:

| Key | What you decide |
|---|---|
| `instructions` | how Decision-1 should judge a document |
| `criteria` | one key per label, a description each; finish the `TODO`s |
| `thresholds.min_probability`, `min_margin` | when to abstain (`needs_review`) |
| `version` | bump on every change; drives delta re-classification |

Try it locally (read-only, ~1 s/doc):

```powershell
$name = (Get-Content .workshop\participant.json -Raw | ConvertFrom-Json).hackername
uv run python ".workshop\runs\$name\miner\try_classifier.py" doc-003 doc-007 doc-018
```

Deploy and run it **as a hosted Foundry agent**:

```powershell
uv run fomo miner deploy --classifier ".workshop\runs\$name\miner\classifier.yaml" --apply   # ~60-70 s
uv run fomo miner run --hosted --apply                                                           # ~60-90 s
```

The first run may wait 1-2 minutes for fresh role assignments; the CLI retries automatically.
Re-running only classifies changed blobs or a new `version`.

## 3. Smart RAG (10 min)

```powershell
uv run fomo smart --classifier ".workshop\runs\$name\miner\classifier.yaml" --apply
uv run fomo agents ask smart "What payment terms apply under our supplier contracts?"
```

Creates index `<your-name>-smart` (with `document_type`), a Foundry IQ knowledge source
`<your-name>-smart-ks` + knowledge base `<your-name>-smart-kb`, and agent `<your-name>-smart-rag`.
The knowledge base decides per question which document type to search: "payment terms" →
contracts, "how much were we invoiced" → invoices. Its options and their definitions come from your
classifier (`--classifier <file>`; default: the reference), limited to labels in the index.
Better label definitions improve both classification and routing.

## 4. Compare (10 min)

Always finish here. One or two `agents ask` answers are anecdotes; this evaluation scores both
indexes and both agents on the full question set. Rerun it after changing the classifier or
smart index; `fomo next` reports a stale evaluation.

```powershell
uv run fomo eval
```

Reference run (see [`examples/sample-evaluation.md`](../examples/sample-evaluation.md)):

| Question | Raw P@5 | Smart + contract filter P@5 |
|---|---|---|
| q01 payment terms in supplier contracts | 0.40 | **1.00** |
| non-contract questions (q05, q06, q08) | > 0 | **0.00** |

Discussion: a fixed contract filter wins on contract questions and **hurts** everything else. The
`smart_desired` row (right filter per question) is what routing aims for; the agent section of the
report shows which filter Smart RAG's knowledge base actually generated for each question.

### Foundry cloud evaluation

For scored answer quality in the Foundry portal (not just direct Search retrieval), use:

```powershell
uv run fomo eval-foundry --suite smoke --plan
uv run fomo eval-foundry --suite smoke --apply
uv run fomo eval-foundry --suite full --apply
```

The default is a four-question smoke
suite; the full versioned set in `corpus/rag_eval.json` contains 14 contract questions,
four non-contract controls and two missing-answer cases. Every reference includes actual
corpus evidence, checked against both live indexes before submitting. References go only
to the judges, never into agent prompts.

The Foundry SDK registers the dataset and starts two version-pinned agent-target runs in
one evaluation group. Both use the same relevance, reference-groundedness, similarity and
completeness evaluators (threshold 4/5), dataset and judge deployment. If dataset registration
is unavailable, inline inputs are used and the reason is recorded; no storage/connection
settings are changed. This incurs agent and judge usage charges.

IDs, individual answers, judge reasons, citation metrics, separate category scores and
portal links are saved under `.foundry/results/`; `.foundry/agent-metadata.yaml` tracks the
latest run. Groundedness uses curated reference documents, not the actual retrieved tool
context. Latency is reported only when Foundry supplies it. Do not interpret one small run
as statistical proof or hide non-contract misses (a wrong routing choice excludes relevant documents).

To resume polling/downloading without submitting new paid runs:

```powershell
uv run fomo eval-foundry --resume .foundry\results\workshop\<eval-id>\manifest.json
```

### Rigorous comparison (facilitator / after the workshop)

`eval-ladder` answers *which part* of Smart RAG helps or hurts. It compares five variants that
differ only by index/filter (Simple, Smart without filter, fixed contract filter, Decision
1.1-routed filter, oracle filter). It uses about 60 verified questions with a dev/test split,
repeated answers, objective fact/distractor/citation metrics, a different judge model, and
paired bootstrap confidence intervals. Rules are fixed in
[evaluation-protocol.md](evaluation-protocol.md).

```powershell
uv run fomo eval-ladder --split dev  --repeats 1           # plan
uv run fomo eval-ladder --split dev  --repeats 1 --apply   # pipeline check
uv run fomo eval-ladder --split test --repeats 3 --apply   # headline
uv run fomo eval-ladder --resume .foundry\results\ladder\<run>\manifest.json --report-only
```

The first apply creates six evaluation-only prompt agents (`<name>-smart-rag-<type>` and
`-any`); `fomo cleanup` removes them. Every apply needs its own approval.

## 5. Stuck or late?

| Situation | Command |
|---|---|
| Where am I? | `uv run fomo status`, or `uv run fomo next` for the next step and why |
| Jump to the end | `uv run fomo cheatcode --apply` (~5 min, reference classifier) |
| Redo classification | `uv run fomo reset-participant --scope metadata --apply` |
| Something failed | [troubleshooting.md](troubleshooting.md); logs in `.workshop/runs/<name>/` |
| Done (after `fomo eval` and the debrief) | `uv run fomo cleanup --apply` (asks you to type `DELETE`) |
