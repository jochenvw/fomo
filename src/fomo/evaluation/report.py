"""Evaluation report rendering: concise Markdown and terminal tables (no fabricated values)."""

from __future__ import annotations

from typing import Any

RUN_LABELS = {
    "raw": "Raw index (Simple RAG scope)",
    "smart_nofilter": "Smart index, no filter (fairness control)",
    "smart_contract": "Smart index, fixed contract filter (fixed-scope baseline)",
    "smart_desired": "Smart index, per-question desired filter",
    "smart_boost": "Smart index, boost profile (no filter)",
}


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


def _docs(top: list[dict[str, Any]]) -> str:
    return ", ".join(f"{d['doc_id']}{'*' if d['relevant'] else ''}" + (f" ({d['label']})" if d.get("label") else "")
                     for d in top) or "(none)"


def markdown(report: dict[str, Any]) -> str:
    k = report["k"]
    lines = [f"# Evaluation - {report['participant']}", "",
             f"Generated {report['generated_at']} | corpus {report['corpus_version']} | k={k} | "
             f"{report['settings']['query_type']}", "",
             "`*` = relevant per ground truth. Labels are what the smart index holds.", "",
             "## 1. Retrieval summary (direct Azure AI Search)", "",
             f"| Run | Filter / profile | mean P@{k} | mean R@{k} | false exclusions |", "|---|---|---|---|---|"]
    for run, s in report["summary"].items():
        example = next(r for r in report["retrieval"] if r["run"] == run)
        how = example["filter"] or (f"profile {example['scoring_profile']}" if example["scoring_profile"] else
                                    "none")
        if run == "smart_desired":
            how = "per question (see below)"
        lines.append(f"| {RUN_LABELS.get(run, run)} | {how} | {_pct(s['mean_precision'])} | "
                     f"{_pct(s['mean_recall'])} | {s['false_exclusions']} |")
    lines += ["", f"## 2. Per question (P@{k} raw -> smart contract filter)", "",
              "| Q | Question | Raw | Smart+contract | Smart+desired | Expectation |", "|---|---|---|---|---|---|"]
    by_q: dict[str, dict[str, Any]] = {}
    for r in report["retrieval"]:
        by_q.setdefault(r["query_id"], {})[r["run"]] = r
    for q in report["queries"]:
        runs = by_q.get(q["id"], {})
        lines.append(f"| {q['id']} | {q['question']} | {_pct(runs.get('raw', {}).get('precision'))} | "
                     f"{_pct(runs.get('smart_contract', {}).get('precision'))} | "
                     f"{_pct(runs.get('smart_desired', {}).get('precision'))} | {q.get('expect') or ''} |")
    lines += ["", "## 3. Top-k documents", ""]
    for q in report["queries"]:
        runs = by_q.get(q["id"], {})
        lines.append(f"**{q['id']}** {q['question']} - relevant: {', '.join(q['relevant'])}")
        lines.append("")
        for run in ("raw", "smart_contract", "smart_desired"):
            if run in runs:
                r = runs[run]
                extra = f" - false exclusions: {', '.join(r['false_exclusions'])}" if r["false_exclusions"] else ""
                lines.append(f"- {run} (`{r['filter'] or 'no filter'}`): {_docs(r['top_docs'])}{extra}")
        lines.append("")
    c = report["classification"]
    lines += ["## 4. Classification (Microsoft-Decision-1 via Metadata Miner)", "",
              f"Accuracy {c['accuracy']:.0%} of {c['documents']} docs (answered-only "
              f"{c['accuracy_answered']:.0%}); needs_review {c['abstained']}; unclassified {c['unclassified']}.", ""]
    if c["errors"]:
        lines += ["| Doc | Truth | Predicted | Trap |", "|---|---|---|---|"]
        lines += [f"| {e['doc_id']} | {e['truth']} | {e['predicted']} | {e.get('trap') or ''} |" for e in c["errors"]]
        lines.append("")
    labels = sorted({p for row in c["confusion"].values() for p in row})
    lines += ["Confusion (rows = truth, columns = predicted):", "",
              "| truth \\ predicted | " + " | ".join(labels) + " |", "|---" * (len(labels) + 1) + "|"]
    for truth, row in c["confusion"].items():
        lines.append(f"| {truth} | " + " | ".join(str(row.get(p, "")) for p in labels) + " |")
    lines.append("")
    if report["agents"]:
        lines += ["## 5. Prompt agents side by side", ""]
        for q in report["queries"]:
            rows = [a for a in report["agents"] if a["query_id"] == q["id"]]
            if not rows:
                continue
            lines.append(f"### {q['id']} {q['question']}")
            lines.append("")
            for a in sorted(rows, key=lambda x: x["role"]):
                if a.get("error"):
                    lines.append(f"- **{a['agent']}**: ERROR {a['error']}")
                    continue
                routed = ("; filter " + ", ".join(f or "none" for f in a["filters"])) if a.get("filters") else ""
                lines.append(f"- **{a['agent']}** - cited {', '.join(a['cited_doc_ids']) or 'nothing'}; "
                             f"not relevant: {', '.join(a['cited_not_relevant']) or 'none'}{routed}; "
                             f"{a['latency_ms']} ms, {a['input_tokens']} in / {a['output_tokens']} out tokens"
                             + (f", ~${a['est_usd']}" if a.get("est_usd") is not None else ""))
                answer = " ".join(str(a["answer"]).split())
                lines.append(f"  > {answer[:700]}{'...' if len(answer) > 700 else ''}")
            lines.append("")
    lines += ["## 6. Reading this honestly", "",
              "- `smart_nofilter` should match `raw`: same chunking, embeddings and query. A gap means the "
              "comparison is not fair (e.g. an indexer still running).",
              "- The contract filter helps contract questions and **harms** others (false exclusions). "
              "That is the trade-off of a fixed scope, not a bug.",
              "- The Smart RAG agent picks its own document_type filter per question (section 5). A wrong "
              "pick behaves like the fixed filter on the wrong question: relevant documents are excluded.",
              "- Misclassified documents become false exclusions under a filter: check section 4.", ""]
    return "\n".join(lines)


def terminal_table(report: dict[str, Any]) -> str:
    k = report["k"]
    by_q: dict[str, dict[str, Any]] = {}
    for r in report["retrieval"]:
        by_q.setdefault(r["query_id"], {})[r["run"]] = r
    out = [f"{'Q':4} {'raw P@' + str(k):>9} {'smart+contract':>15} {'smart+desired':>14}  top-3 raw | smart+contract"]
    for q in report["queries"]:
        runs = by_q.get(q["id"], {})
        raw, sc, sd = runs.get("raw"), runs.get("smart_contract"), runs.get("smart_desired")
        raw_top = (raw or {}).get("top_docs", [])[:3]
        top_raw = ",".join(d["doc_id"][-3:] + ("*" if d["relevant"] else "") for d in raw_top)
        top_sc = ",".join(d["doc_id"][-3:] + ("*" if d["relevant"] else "") for d in (sc or {}).get("top_docs", [])[:3])
        out.append(f"{q['id']:4} {_pct(raw and raw['precision']):>9} {_pct(sc and sc['precision']):>15} "
                   f"{_pct(sd and sd['precision']):>14}  {top_raw} | {top_sc}")
    c = report["classification"]
    out.append(f"classification accuracy {c['accuracy']:.0%} ({len(c['errors'])} wrong, {c['abstained']} needs_review)")
    for a in report["agents"]:
        if a.get("error"):
            out.append(f"{a['query_id']} {a['role']:6} ERROR {a['error'][:120]}")
        else:
            routed = (" filter " + ",".join((f or "none").replace("document_type eq ", "") for f in a["filters"])
                      if a.get("filters") else "")
            out.append(f"{a['query_id']} {a['role']:6} cites {','.join(a['cited_doc_ids']) or '-'}"
                       f"  (not relevant: {','.join(a['cited_not_relevant']) or 'none'}){routed} {a['latency_ms']}ms")
    return "\n".join(out)
