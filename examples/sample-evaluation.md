# Evaluation - testfox

Generated 2026-11-17T12:32:23+00:00 | corpus 2026.1 | k=5 | hybrid BM25 + vector (RRF); Foundry agents use vector_simple_hybrid

`*` = relevant per ground truth. Labels are what the smart index holds.

## 1. Retrieval summary (direct Azure AI Search)

| Run | Filter / profile | mean P@5 | mean R@5 | false exclusions |
|---|---|---|---|---|
| Raw index (Simple RAG scope) | none | 0.25 | 0.94 | 0 |
| Smart index, no filter (fairness control) | none | 0.23 | 0.92 | 0 |
| Smart index, fixed contract filter (Smart RAG scope) | document_type eq 'contract' | 0.23 | 0.60 | 3 |
| Smart index, per-question desired filter | per question (see below) | 0.30 | 0.98 | 0 |
| Smart index, boost profile (no filter) | profile boost-contracts | 0.28 | 0.85 | 0 |

## 2. Per question (P@5 raw -> smart contract filter)

| Q | Question | Raw | Smart+contract | Smart+desired | Expectation |
|---|---|---|---|---|---|
| q01 | What payment terms apply under our supplier contracts? | 0.60 | 1.00 | 1.00 | filter helps: the contract-management deck and PP-07 policy compete in raw results |
| q02 | What late payment interest applies to the supply vessel charter? | 0.20 | 0.20 | 0.20 | little difference: specific wording already retrieves the charter |
| q03 | Which supplier agreements offer a discount for early payment? | 0.20 | 0.20 | 0.20 | converse trap: doc-009 avoids the word contract; deck mentions discounts |
| q04 | How much retention is withheld from rig maintenance payments? | 0.20 | 0.20 | 0.20 | invoice doc-011 also mentions 5% retention; filter keeps the agreement |
| q05 | What did the in-line inspection of export pipeline segment 14 find? | 0.20 | 0.00 | 0.20 | contract filter HARMS: the fixed contracts scope excludes the report |
| q06 | How much were we invoiced for the emergency coffee airlift in March? | 0.20 | 0.00 | 0.20 | contract filter HARMS; an invoice filter would help |
| q07 | What notice period applies to terminating the rig maintenance agreement for convenience? | 0.20 | 0.20 | 0.20 | little difference |
| q08 | What is our default payment term policy for new suppliers? | 0.20 | 0.00 | 0.20 | contract filter HARMS: the answer lives in a policy |

## 3. Top-k documents

**q01** What payment terms apply under our supplier contracts? - relevant: doc-001, doc-004, doc-009, doc-013, doc-018, doc-021

- raw (`no filter`): doc-002 (policy), doc-003 (presentation), doc-009* (contract), doc-013* (contract), doc-018* (contract)
- smart_contract (`document_type eq 'contract'`): doc-018* (contract), doc-009* (contract), doc-013* (contract), doc-021* (contract), doc-001* (contract)
- smart_desired (`document_type eq 'contract'`): doc-018* (contract), doc-009* (contract), doc-013* (contract), doc-021* (contract), doc-001* (contract)

**q02** What late payment interest applies to the supply vessel charter? - relevant: doc-001

- raw (`no filter`): doc-001* (contract), doc-008 (correspondence), doc-003 (presentation), doc-018 (contract), doc-024 (correspondence)
- smart_contract (`document_type eq 'contract'`): doc-001* (contract), doc-018 (contract), doc-021 (contract), doc-009 (contract), doc-004 (contract)
- smart_desired (`document_type eq 'contract'`): doc-001* (contract), doc-018 (contract), doc-021 (contract), doc-009 (contract), doc-004 (contract)

**q03** Which supplier agreements offer a discount for early payment? - relevant: doc-009

- raw (`no filter`): doc-002 (policy), doc-003 (presentation), doc-009* (contract), doc-013 (contract), doc-018 (contract)
- smart_contract (`document_type eq 'contract'`): doc-018 (contract), doc-009* (contract), doc-013 (contract), doc-004 (contract), doc-001 (contract)
- smart_desired (`document_type eq 'contract'`): doc-018 (contract), doc-009* (contract), doc-013 (contract), doc-004 (contract), doc-001 (contract)

**q04** How much retention is withheld from rig maintenance payments? - relevant: doc-004

- raw (`no filter`): doc-004* (contract), doc-023 (technical_report), doc-007 (presentation), doc-016 (policy), doc-015 (presentation)
- smart_contract (`document_type eq 'contract'`): doc-004* (contract), doc-001 (contract), doc-018 (contract), doc-021 (contract), doc-013 (contract)
- smart_desired (`document_type eq 'contract'`): doc-004* (contract), doc-001 (contract), doc-018 (contract), doc-021 (contract), doc-013 (contract)

**q05** What did the in-line inspection of export pipeline segment 14 find? - relevant: doc-006

- raw (`no filter`): doc-006* (technical_report), doc-013 (contract), doc-015 (presentation), doc-023 (technical_report), doc-004 (contract)
- smart_contract (`document_type eq 'contract'`): doc-013 (contract), doc-004 (contract), doc-018 (contract), doc-001 (contract), doc-009 (contract) - false exclusions: doc-006
- smart_desired (`no filter`): doc-006* (technical_report), doc-013 (contract), doc-023 (technical_report), doc-025 (technical_report), doc-004 (contract)

**q06** How much were we invoiced for the emergency coffee airlift in March? - relevant: doc-019

- raw (`no filter`): doc-014 (correspondence), doc-018 (contract), doc-020 (correspondence), doc-019* (invoice), doc-016 (policy)
- smart_contract (`document_type eq 'contract'`): doc-018 (contract), doc-009 (contract), doc-001 (contract), doc-021 (contract), doc-004 (contract) - false exclusions: doc-019
- smart_desired (`document_type eq 'invoice'`): doc-019* (invoice), doc-011 (invoice), doc-005 (invoice)

**q07** What notice period applies to terminating the rig maintenance agreement for convenience? - relevant: doc-004

- raw (`no filter`): doc-004* (contract), doc-008 (correspondence), doc-001 (contract), doc-023 (technical_report), doc-021 (contract)
- smart_contract (`document_type eq 'contract'`): doc-004* (contract), doc-018 (contract), doc-001 (contract), doc-013 (contract), doc-021 (contract)
- smart_desired (`document_type eq 'contract'`): doc-004* (contract), doc-018 (contract), doc-001 (contract), doc-013 (contract), doc-021 (contract)

**q08** What is our default payment term policy for new suppliers? - relevant: doc-002

- raw (`no filter`): doc-002* (policy), doc-003 (presentation), doc-009 (contract), doc-013 (contract), doc-018 (contract)
- smart_contract (`document_type eq 'contract'`): doc-018 (contract), doc-013 (contract), doc-009 (contract), doc-004 (contract), doc-001 (contract) - false exclusions: doc-002
- smart_desired (`document_type eq 'policy'`): doc-002* (policy), doc-016 (policy), doc-010 (policy)

## 4. Classification (Microsoft-Decision-1 via Metadata Miner)

Accuracy 100% of 25 docs (answered-only 100%); needs_review 0; unclassified 0.

Confusion (rows = truth, columns = predicted):

| truth \ predicted | contract | correspondence | invoice | policy | presentation | technical_report |
|---|---|---|---|---|---|---|
| contract | 6 |  |  |  |  |  |
| correspondence |  | 3 |  |  |  |  |
| invoice |  |  | 3 |  |  |  |
| needs_review |  | 1 |  |  |  |  |
| policy |  |  |  | 3 |  |  |
| presentation |  |  |  |  | 4 |  |
| technical_report |  |  |  |  |  | 5 |

## 5. Prompt agents side by side

### q01 What payment terms apply under our supplier contracts?

- **testfox-simple-rag** - cited doc-002, doc-009, doc-018, doc-013, doc-003; not relevant: doc-002, doc-003; 21781 ms, 2426 in / 1104 out tokens, ~$0.00281
  > - The default payment term for new supplier contracts is thirty (30) days from receipt of a valid invoice (Procurement Policy PP-07: Supplier Payment Terms)【7:0†source】. - Payment terms longer than sixty (60) days require CFO approval (Procurement Policy PP-07: Supplier Payment Terms)【7:0†source】. - Small and medium suppliers must be offered terms no longer than thirty (30) days (Procurement Policy PP-07: Supplier Payment Terms)【7:0†source】. - The policy’s model clause guides: “The Company shall pay each correct invoice within thirty (30) days of receipt” (Procurement Policy PP-07: Supplier Payment Terms)【7:0†source】. - Example contract variations: "Master Purchase Terms for Drilling Consuma...
- **testfox-smart-rag** - cited doc-018, doc-009, doc-013, doc-001, doc-004; not relevant: none; 17431 ms, 2804 in / 877 out tokens, ~$0.00246
  > Payment terms in the located supplier contracts are: - Offshore Catering and Coffee Supply Agreement — Contoso shall pay each valid monthly invoice within thirty (30) days of receipt【7:0†source】. - Master Purchase Terms for Drilling Consumables — Buyer shall settle each correct invoice sixty (60) days end of month; 2% discount if paid within ten (10) days【7:1†source】. - Pipeline Inspection Services Framework Agreement — Fees payable by milestones (40%/40%/20%); Client pays undisputed milestone invoices within thirty (30) days of receipt【7:2†source】. - Offshore Supply Vessel Time Charter Agreement CW-OSV-2025-014 — Dayrate payable monthly in arrears; Charterer shall pay each correct invoice w...

## 6. Reading this honestly

- `smart_nofilter` should match `raw`: same chunking, embeddings and query. A gap means the comparison is not fair (e.g. an indexer still running).
- The contract filter helps contract questions and **harms** others (false exclusions). That is the trade-off of a fixed scope, not a bug.
- Misclassified documents become false exclusions under a filter: check section 4.
