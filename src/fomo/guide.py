"""Workshop guide: map live participant state to the next checkpoint.

Read-only and deterministic. The `fomo-help` skill calls `fomo next` and narrates the result;
it never re-derives progress from conversation history. A probe that failed is *unknown*,
not missing: the guide then asks for a status check instead of suggesting a rebuild.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Checkpoint:
    id: str
    title: str
    why: str
    see: str
    commands: tuple[str, ...] = ()
    writes: bool = True


CHECKPOINTS: tuple[Checkpoint, ...] = (
    Checkpoint(
        "preflight", "Check readiness",
        "Confirms your Azure target, sign-in, roles and shared services before anything is created, "
        "and gives you a hackername that prefixes every object you own.",
        "A PASS/WARNING verdict, your hackername, and named owners for any blocker.",
        ("uv run fomo preflight --participants <cohort-size> --init-participant",), writes=False),
    Checkpoint(
        "upload", "Upload the documents",
        "25 synthetic Contoso Oil documents (contracts, invoices, reports, decks, policies) go into your "
        "own Blob container. Both agents read this same source, which keeps the comparison fair.",
        "Azure portal -> storage account -> Containers -> {container}: 25 blobs, no document_type metadata yet.",
        ("uv run fomo provision --step upload",)),
    Checkpoint(
        "index", "Build the raw index",
        "An Azure AI Search indexer reads the container, extracts text, chunks and embeds it into "
        "{raw_index}. This is plain RAG plumbing: no classification fields, no filters.",
        "Portal -> AI Search -> Indexers -> {raw_indexer}: success, 25 docs. Indexes -> {raw_index} -> "
        "Search explorer: query 'payment terms' and note which doc_ids come back.",
        ("uv run fomo provision --step index",)),
    Checkpoint(
        "simple-rag", "Create Simple RAG and ask it",
        "A prompt agent that searches the raw index without filters. It is the baseline: watch which "
        "documents it cites for a contract question.",
        "Foundry portal -> Agents -> {simple_rag}. Look at the citations: doc-003 (a training deck ABOUT "
        "contracts) or doc-002 (a policy) sound like contracts but are not.",
        ("uv run fomo provision --step agent",
         'uv run fomo agents ask simple "What payment terms apply under our supplier contracts?"')),
    Checkpoint(
        "classifier", "Write your classifier",
        "The Metadata Miner labels each document with Microsoft-Decision. You decide what each label means; "
        "the near misses (decks and policies that talk like contracts) are the interesting part.",
        "Edit {run_dir}/miner/classifier.yaml: finish the TODO criteria, bump `version`. Then try it read-only "
        "on a near miss and check the probabilities.",
        ("uv run python {run_dir}/miner/try_classifier.py doc-003 doc-007 doc-018",), writes=False),
    Checkpoint(
        "miner-deploy", "Deploy the Metadata Miner",
        "Your classifier is packaged with the shared miner code and deployed as a hosted Foundry agent. "
        "Its managed identity gets write access to your container only.",
        "Foundry portal -> Agents -> {metadata_miner}: hosted, status active, a new version per code change.",
        ("uv run fomo miner deploy --classifier {run_dir}/miner/classifier.yaml",)),
    Checkpoint(
        "miner-run", "Run the miner and check the metadata",
        "The hosted agent classifies every blob and writes document_type (plus confidence) as blob "
        "metadata. Unchanged documents are skipped on reruns.",
        "Portal -> container {container} -> a blob -> Metadata: document_type, confidence. "
        "`fomo status` shows how many documents are classified.",
        ("uv run fomo miner run --hosted",)),
    Checkpoint(
        "smart", "Build the smart index and Smart RAG",
        "A second indexer copies the blob metadata into a filterable document_type field. Smart RAG picks a "
        "document_type filter per question from your classifier's labels.",
        "Portal -> {smart_index} -> facets on document_type. Ask Smart RAG the same question and compare "
        "citations and the filter it chose.",
        ("uv run fomo smart --classifier {run_dir}/miner/classifier.yaml",
         'uv run fomo agents ask smart "What payment terms apply under our supplier contracts?"')),
    Checkpoint(
        "compare", "Compare Simple and Smart RAG",
        "Same questions, both agents, actual top-k hits and citations. Filtering helps contract questions "
        "and can hurt others when the route is wrong; the evaluation shows both.",
        "The before/after table: precision@5 per question, chosen filters, misses on non-contract controls.",
        ("uv run fomo eval",), writes=False),
)
ORDER = tuple(c.id for c in CHECKPOINTS)
BY_ID = {c.id: c for c in CHECKPOINTS}


@dataclass
class Next:
    checkpoint: str
    title: str
    why: str
    see: str
    commands: list[str]
    writes: bool
    done: list[str] = field(default_factory=list)
    reason: str = ""
    unknown: list[str] = field(default_factory=list)

    @property
    def position(self) -> int:
        return ORDER.index(self.checkpoint) + 1 if self.checkpoint in ORDER else 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"position": self.position, "total": len(ORDER)}


def classifier_todos(path: Path) -> list[str] | None:
    """Labels whose criteria still contain TODO; None when the starter file is missing or unreadable."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    criteria = data.get("criteria") or {}
    return [k for k, v in criteria.items() if "TODO" in str(v)]


def _fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def build(checkpoint: str, values: dict[str, str], *, reason: str = "", unknown: list[str] | None = None) -> Next:
    c = BY_ID[checkpoint]
    done = list(ORDER[:ORDER.index(checkpoint)])
    if checkpoint == "compare":
        done.append("compare-ready")
    return Next(c.id, c.title, _fill(c.why, values), _fill(c.see, values), [_fill(x, values) for x in c.commands],
                c.writes, done=done, reason=reason, unknown=unknown or [])


def _st(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return value if isinstance(value, dict) else {}


def next_checkpoint(state: dict[str, Any], values: dict[str, str], *, expected_docs: int,
                    classifier: Path) -> Next:
    """First checkpoint whose evidence is missing. Order matches the participant guide."""
    unknown = [k for k, v in state.items() if isinstance(v, dict) and "error" in v]
    agents = _st(state, "agents")
    unknown += [f"agents.{k}" for k, v in agents.items() if isinstance(v, dict) and "error" in v]
    if unknown:
        return Next("unknown", "Live state could not be read", "Some checks failed, so missing and unreadable "
                    "look the same. Nothing is assumed missing.", "Run `fomo status` and read the errors; "
                    "rerun preflight if access or sign-in changed.", ["uv run fomo status"], False,
                    reason="probe errors", unknown=unknown)

    def agent(key: str) -> bool:
        return bool(_st(agents, key).get("exists"))

    container = _st(state, "container")
    docs = int(container.get("documents") or 0)
    if not container.get("exists") or docs < expected_docs:
        return build("upload", values, reason=f"{docs}/{expected_docs} documents in the container")
    if not _st(state, "raw_index").get("chunks"):
        return build("index", values, reason="raw index has no chunks")
    if not agent("simple_rag"):
        return build("simple-rag", values, reason="Simple RAG agent not found")
    classified = int(container.get("classified") or 0)
    if not agent("metadata_miner"):
        todos = classifier_todos(classifier)
        if todos is None:
            return build("classifier", values, reason=f"starter missing: {classifier}; "
                         "`uv run fomo provision --step agent` restores it")
        if todos:
            return build("classifier", values, reason="criteria still TODO: " + ", ".join(todos))
        reason = "hosted miner not deployed"
        if classified:
            reason += f" ({classified} documents were classified locally; the hosted agent is the requirement)"
        return build("miner-deploy", values, reason=reason)
    if not classified:
        return build("miner-run", values, reason="no documents carry document_type metadata yet")
    smart_types = {k: v for k, v in (_st(state, "smart_index").get("document_type") or {}).items()
                   if k != "unclassified"}
    if not smart_types:
        return build("smart", values, reason="smart index has no document_type values yet")
    if not agent("smart_rag"):
        return build("smart", values, reason="Smart RAG agent not found")
    return build("compare", values, reason=f"{classified}/{docs} documents classified; both agents exist")


def progress_bar(n: Next) -> str:
    if not n.position:
        return ""
    marks = ["■" if i < n.position - 1 else ("▶" if i == n.position - 1 else "□") for i in range(len(ORDER))]
    if "compare-ready" in n.done:
        marks[-1] = "▶"
    return " ".join(marks) + f"  {n.position}/{len(ORDER)}"


def render(n: Next) -> str:
    lines = [f"{progress_bar(n)}  next: {n.title}".strip()]
    if n.reason:
        lines.append(f"  where:  {n.reason}")
    if n.unknown:
        lines.append("  unknown: " + ", ".join(n.unknown))
    lines.append(f"  why:    {n.why}")
    lines.append(f"  see:    {n.see}")
    for cmd in n.commands:
        lines.append(f"  run:    {cmd}" + ("   (plan first; add --apply after approval)" if n.writes
                                            and "fomo agents" not in cmd else ""))
    return "\n".join(lines)
