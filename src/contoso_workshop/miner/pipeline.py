"""Metadata Miner pipeline (one participant container).

Deterministic code around one model call per document:
  1. list participant blobs (skip `_*`/`.*` outputs), delta by sha256 + classifier version
  2. extract text locally (no OCR, never raw bytes to the model, never filename-only)
  3. Microsoft-Decision-1 `choice` question via the provider decision API
  4. abstention policy -> label or needs_review
  5. merge Blob custom metadata with ETag If-Match; checkpoint every success
Deletions: names in the checkpoint that no longer exist are reported and dropped; the Search
data source uses native soft-delete detection so their chunks leave both indexes on the next run.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import ResourceNotFoundError

from ..data.blob import BlobInfo, ParticipantContainer
from ..data.corpus import sha256_bytes
from ..data.extract import extract
from ..decision.client import DecisionClient, build_state
from ..decision.config import ClassifierConfig
from ..decision.policy import apply_policy
from ..errors import DecisionError, PermissionDenied, WorkshopError

CHECKPOINT_BLOB = "_miner/checkpoint.json"
RUNS_PREFIX = "_miner/runs/"
MIN_TEXT_CHARS = 40


@dataclass
class ItemResult:
    blob: str
    status: str  # classified | unchanged | skipped | error | deleted
    doc_id: str | None = None
    label: str | None = None
    model_choice: str | None = None
    probability: float | None = None
    margin: float | None = None
    reason: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    sha256: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class MinerReport:
    run_id: str
    participant: str
    mode: str
    classifier_version: str
    deployment: str
    dry_run: bool
    started_at: str
    finished_at: str = ""
    items: list[ItemResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out

    def labels(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            if item.label:
                out[item.label] = out.get(item.label, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return not any(i.status == "error" for i in self.items)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["counts"] = self.counts()
        data["labels"] = self.labels()
        return data


class RateLimiter:
    """Minimum interval between calls across threads (bounded request rate)."""

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


def is_current(meta: dict[str, str], sha: str | None, cfg: ClassifierConfig) -> bool:
    lower = {k.lower(): v for k, v in meta.items()}
    return bool(sha) and lower.get("classified_sha256") == sha and lower.get("classification_version") == cfg.version


def classification_metadata(outcome: Any, result: Any, sha: str, cfg: ClassifierConfig) -> dict[str, str]:
    prob = outcome.probability if outcome.probability is not None else result.confidence
    return {
        "document_type": outcome.label,
        "classification_confidence": f"{prob:.4f}" if prob is not None else "",
        "classification_version": cfg.version,
        "classification_model": str(result.model or "microsoft-decision-1")[:64],
        "classification_reason": outcome.reason,
        "classified_sha256": sha,
        "classified_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class Miner:
    def __init__(
        self,
        container: ParticipantContainer,
        decision: DecisionClient,
        cfg: ClassifierConfig,
        *,
        participant: str,
        concurrency: int = 4,
        requests_per_second: float = 5.0,
        log: Callable[..., None] | None = None,
    ) -> None:
        self.container = container
        self.decision = decision
        self.cfg = cfg
        self.participant = participant
        self.concurrency = max(1, min(concurrency, 16))
        self.limiter = RateLimiter(requests_per_second)
        self.log = log or (lambda msg, **kw: None)
        self._ck_lock = threading.Lock()

    # ---- checkpoint -------------------------------------------------------------------
    def load_checkpoint(self) -> dict[str, Any]:
        try:
            data, _ = self.container.download(CHECKPOINT_BLOB)
            return dict(json.loads(data))
        except (WorkshopError, ResourceNotFoundError, ValueError):
            return {"items": {}}

    def save_checkpoint(self, ck: dict[str, Any]) -> None:
        body = json.dumps(ck, indent=1, sort_keys=True).encode()
        self.container.client.upload_blob(CHECKPOINT_BLOB, body, overwrite=True)

    # ---- one document -----------------------------------------------------------------
    def classify_blob(self, info: BlobInfo, *, mode: str, dry_run: bool) -> ItemResult:
        meta_sha = {k.lower(): v for k, v in info.metadata.items()}.get("source_sha256")
        item = ItemResult(blob=info.name, status="error",
                          doc_id={k.lower(): v for k, v in info.metadata.items()}.get("doc_id"))
        if mode == "delta" and is_current(info.metadata, meta_sha, self.cfg):
            item.status, item.sha256 = "unchanged", meta_sha
            return item
        data, etag = self.container.download(info.name)
        sha = sha256_bytes(data)
        item.sha256 = sha
        if mode == "delta" and is_current(info.metadata, sha, self.cfg):
            item.status = "unchanged"
            return item
        try:
            ex = extract(info.name, data)
        except WorkshopError as exc:
            item.status, item.error = "skipped", str(exc)
            return item
        item.warnings = list(ex.warnings)
        if len(ex.text.strip()) < MIN_TEXT_CHARS:
            item.status, item.reason = "skipped", "empty_text"
            item.error = "no extractable text (scanned image? OCR is out of scope)"
            return item
        title = {k.lower(): v for k, v in info.metadata.items()}.get("title") or ex.title
        state = build_state(ex.text, self.cfg, title=title, fmt=ex.fmt)
        self.limiter.wait()
        result = self.decision.classify(state, self.cfg)
        outcome = apply_policy(result, self.cfg)
        item.model_choice, item.label, item.reason = outcome.model_choice, outcome.label, outcome.reason
        item.probability, item.margin = outcome.probability, outcome.margin
        item.probabilities = {k: round(v, 6) for k, v in result.probabilities.items()}
        item.latency_ms = result.latency_ms
        item.input_tokens = result.usage.get("input_tokens")
        if not dry_run:
            self.container.update_metadata(info.name, classification_metadata(outcome, result, sha, self.cfg),
                                           etag=etag)
        item.status = "classified"
        return item

    # ---- whole container ----------------------------------------------------------------
    def run(self, *, mode: str = "delta", only: list[str] | None = None, limit: int | None = None,
            dry_run: bool = False, save_report: bool = True) -> MinerReport:
        if mode not in ("delta", "all"):
            raise WorkshopError(f"unknown mode {mode}", fix="use --mode delta or --mode all")
        report = MinerReport(
            run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), participant=self.participant, mode=mode,
            classifier_version=self.cfg.version, deployment=self.decision.deployment, dry_run=dry_run,
            started_at=datetime.now(UTC).isoformat(),
        )
        blobs = list(self.container.list())
        if only:
            wanted = set(only)
            blobs = [b for b in blobs if b.name in wanted or b.metadata.get("doc_id") in wanted]
            missing = wanted - {b.name for b in blobs} - {b.metadata.get("doc_id") for b in blobs}
            if missing:
                raise WorkshopError(f"not found in container: {', '.join(sorted(missing))}")
        if limit:
            blobs = blobs[:limit]
        ck = self.load_checkpoint() if not dry_run else {"items": {}}
        present = {b.name for b in self.container.list(include_metadata=False)}
        for name in sorted(set(ck.get("items", {})) - present):
            report.items.append(ItemResult(blob=name, status="deleted"))
            ck["items"].pop(name, None)
            self.log(f"{name} deleted since last run; Search drops it on next indexer run", blob=name)

        def work(info: BlobInfo) -> ItemResult:
            try:
                return self.classify_blob(info, mode=mode, dry_run=dry_run)
            except PermissionDenied:
                raise
            except (DecisionError, WorkshopError) as exc:
                return ItemResult(blob=info.name, status="error", error=str(exc)[:500])

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(work, b): b for b in blobs}
            for fut in as_completed(futures):
                item = fut.result()  # PermissionDenied propagates: fail fast, nothing to retry
                report.items.append(item)
                self.log(
                    f"{item.blob}: {item.status}"
                    + (f" -> {item.label} (p={item.probability:.2f}, {item.reason})" if item.label
                       and item.probability is not None else "")
                    + (f" [{item.error}]" if item.error else ""),
                    blob=item.blob, status=item.status, label=item.label, elapsed_ms=item.latency_ms,
                )
                if item.status == "classified" and not dry_run:
                    with self._ck_lock:
                        ck.setdefault("items", {})[item.blob] = {
                            "sha256": item.sha256, "label": item.label, "version": self.cfg.version}
                        self.save_checkpoint(ck)
                elif item.status == "unchanged" and not dry_run:
                    ck.setdefault("items", {}).setdefault(
                        item.blob, {"sha256": item.sha256, "version": self.cfg.version})
        report.items.sort(key=lambda i: i.blob)
        report.finished_at = datetime.now(UTC).isoformat()
        if not dry_run:
            self.save_checkpoint(ck)
            if save_report:
                self.container.client.upload_blob(
                    f"{RUNS_PREFIX}{report.run_id}.json", json.dumps(report.to_dict(), indent=1).encode(),
                    overwrite=True)
        return report
