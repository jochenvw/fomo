"""Classifier configuration: categories, instructions and thresholds.

This is the part participants edit during the workshop (see the generated starter's
classifier.yaml). Labels are validated so a typo can never reach Blob metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError

LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
NEEDS_REVIEW = "needs_review"
REFERENCE_CONFIG = Path(__file__).with_name("reference_classifier.yaml")


@dataclass
class ClassifierConfig:
    version: str
    question: str
    instructions: str
    criteria: dict[str, str]
    abstain_label: str = "cannot_tell"
    min_probability: float = 0.6
    min_margin: float = 0.15
    max_chars: int = 6000
    include_title: bool = True
    include_format_hint: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def labels(self) -> list[str]:
        return [k for k in self.criteria if k != self.abstain_label]

    def validate(self) -> ClassifierConfig:
        if not LABEL_RE.match(self.question):
            raise ConfigError(f"question name '{self.question}' must match {LABEL_RE.pattern}")
        if len(self.criteria) < 2:
            raise ConfigError("need at least two categories")
        for label, description in self.criteria.items():
            if not LABEL_RE.match(label):
                raise ConfigError(f"category '{label}' must be lower_snake_case (<=32 chars)")
            if not description or len(description.strip()) < 10:
                raise ConfigError(f"category '{label}' needs a meaningful description")
        if self.abstain_label not in self.criteria:
            raise ConfigError(
                f"abstain label '{self.abstain_label}' must be one of the criteria",
                fix="keep an explicit 'cannot tell' option so the model can abstain",
            )
        if NEEDS_REVIEW in self.criteria:
            raise ConfigError(f"'{NEEDS_REVIEW}' is reserved for the abstention policy output")
        if not 0 <= self.min_probability <= 1 or not 0 <= self.min_margin <= 1:
            raise ConfigError("thresholds must be between 0 and 1")
        if not 500 <= self.max_chars <= 100_000:
            raise ConfigError("max_chars must be between 500 and 100000")
        return self

    def to_question(self) -> dict[str, Any]:
        return {
            self.question: {
                "type": "choice",
                "instructions": self.instructions.strip(),
                "criteria": {k: v.strip() for k, v in self.criteria.items()},
            }
        }


def load_classifier_config(path: Path | None = None) -> ClassifierConfig:
    path = path or REFERENCE_CONFIG
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"classifier config not found: {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"classifier config {path} must be a mapping")
    thresholds = data.get("thresholds", {})
    known = {"version", "question", "instructions", "criteria", "abstain_label", "thresholds", "state"}
    state = data.get("state", {})
    cfg = ClassifierConfig(
        version=str(data.get("version", "unversioned")),
        question=str(data.get("question", "document_type")),
        instructions=str(data.get("instructions", "")),
        criteria={str(k): str(v) for k, v in (data.get("criteria") or {}).items()},
        abstain_label=str(data.get("abstain_label", "cannot_tell")),
        min_probability=float(thresholds.get("min_probability", 0.6)),
        min_margin=float(thresholds.get("min_margin", 0.15)),
        max_chars=int(state.get("max_chars", 6000)),
        include_title=bool(state.get("include_title", True)),
        include_format_hint=bool(state.get("include_format_hint", False)),
        extra={k: v for k, v in data.items() if k not in known},
    )
    if "TODO" in cfg.instructions or any("TODO" in v for v in cfg.criteria.values()):
        raise ConfigError(
            f"{path} still contains TODO placeholders",
            fix="finish the category descriptions/instructions, or run cheatcode for the reference config",
        )
    return cfg.validate()
