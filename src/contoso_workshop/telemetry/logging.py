"""Structured logging.

Console: terse human lines, e.g. `COSMIC-WOMBAT | [7/24] classified doc-007 => presentation (p=0.86) | 420ms`.
File: JSON lines under .workshop/runs/<hackername>/logs/ with UTC timestamps, run id, stage, elapsed.
Secrets (tokens, keys, SAS signatures) are redacted from every record.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REDACT_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_\.=]+"), r"\1***"),
    (re.compile(r"(?i)(sig=)[^&\s\"']+"), r"\1***"),
    (
        re.compile(r"(?i)((?:api[-_]?key|accountkey|password|client_secret)\"?\s*[:=]\s*\"?)[^\s\";&]+"),
        r"\1***",
    ),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"), "***jwt***"),
]
_SECRET_KEY = re.compile(r"(?i)(api_?key|token|secret|password|sas|credential|authorization)$")


def redact(text: str) -> str:
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if _SECRET_KEY.search(str(k)) and isinstance(v, str):
                out[k] = "***"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list | tuple):
        return [redact_obj(v) for v in obj]
    return obj


_CONTEXT_KEYS = (
    "run_id", "participant", "operation", "stage", "status", "elapsed_ms", "resource", "request_id", "data",
)  # fmt: skip


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": redact(record.getMessage()),
        }
        for key in _CONTEXT_KEYS:
            if hasattr(record, key):
                payload[key] = redact_obj(getattr(record, key))
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        participant = getattr(record, "participant", None)
        prefix = f"{participant.upper()} | " if participant else ""
        msg = redact(record.getMessage())
        elapsed = getattr(record, "elapsed_ms", None)
        suffix = f" | {elapsed}ms" if elapsed is not None else ""
        level = "" if record.levelno == logging.INFO else f"{record.levelname}: "
        return f"{prefix}{level}{msg}{suffix}"


class RunLogger:
    """Adds run/participant/stage context to every record; console + optional JSONL file."""

    def __init__(
        self,
        operation: str,
        participant: str | None = None,
        *,
        verbose: bool = False,
        quiet: bool = False,
        log_dir: Path | None = None,
    ) -> None:
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.operation = operation
        self.participant = participant
        self.quiet = quiet
        self._logger = logging.getLogger(f"contoso.{operation}.{self.run_id}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._console: logging.StreamHandler[Any] | None = None
        if not quiet:
            self._console = logging.StreamHandler(sys.stderr)
            self._console.setLevel(logging.DEBUG if verbose else logging.INFO)
            self._console.setFormatter(ConsoleFormatter())
            self._logger.addHandler(self._console)
        self.log_path: Path | None = None
        if log_dir is not None:
            self.attach_file(log_dir)
        self._stage: str | None = None

    def attach_file(self, log_dir: Path) -> None:
        if self.log_path is not None:
            return
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / f"{self.operation}-{self.run_id}.jsonl"
        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JsonLineFormatter())
        self._logger.addHandler(fh)

    def _extra(self, **kw: Any) -> dict[str, Any]:
        extra: dict[str, Any] = {"run_id": self.run_id, "operation": self.operation}
        if self.participant:
            extra["participant"] = self.participant
        if self._stage:
            extra["stage"] = self._stage
        extra.update({k: v for k, v in kw.items() if v is not None})
        return extra

    def info(self, msg: str, **kw: Any) -> None:
        self._logger.info(msg, extra=self._extra(**kw))

    def debug(self, msg: str, **kw: Any) -> None:
        self._logger.debug(msg, extra=self._extra(**kw))

    def warning(self, msg: str, **kw: Any) -> None:
        self._logger.warning(msg, extra=self._extra(**kw))

    def error(self, msg: str, **kw: Any) -> None:
        self._logger.error(msg, extra=self._extra(**kw))

    def raw(self, text: str) -> None:
        """Unprefixed console output for banners and tables."""
        if self._console is not None:
            self._console.stream.write(text + "\n")
            self._console.flush()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        previous, self._stage = self._stage, name
        start = time.perf_counter()
        self.debug(f"stage {name} start", status="start")
        try:
            yield
        except Exception:
            elapsed = int((time.perf_counter() - start) * 1000)
            self.debug(f"stage {name} failed", status="failed", elapsed_ms=elapsed)
            raise
        else:
            elapsed = int((time.perf_counter() - start) * 1000)
            self.debug(f"stage {name} done", status="ok", elapsed_ms=elapsed)
        finally:
            self._stage = previous

    def detach_file(self) -> None:
        """Release the JSONL log file (Windows cannot delete a folder holding an open file)."""
        for handler in list(self._logger.handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                self._logger.removeHandler(handler)

    def close(self) -> None:
        for handler in list(self._logger.handlers):
            handler.close()
            self._logger.removeHandler(handler)


def write_trace(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_obj(payload), indent=2, default=str), encoding="utf-8")
    return path
