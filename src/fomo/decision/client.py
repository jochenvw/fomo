"""REST client for the Microsoft-Decision-1/1.1 provider decision API.

POST https://<account>.openai.azure.com/providers/microsoft/v1/systemone
Auth: Entra token for https://cognitiveservices.azure.com/.default

Verified live: the *.openai.azure.com host accepts Entra tokens for Foundry User principals;
the *.services.ai.azure.com host returned 401 "Principal does not have access to API/Operation".
Response: {"model", "answers": {<q>: {"type": "choice", "choice", "confidence",
"probabilities": {label: p}}}, "usage": {"input_tokens", "output_tokens"}}
Body: {"model": <deployment name>, "state": <text>, "questions": {<name>: {"type": "choice", ...}}}

This is NOT the chat-completions API; the model returns numbers, never prose.
Docs: https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from ..azclient import SCOPE_COGNITIVE, bearer, http_error, request
from ..errors import DecisionError, WorkshopError
from .config import ClassifierConfig

DECISION_PATH = "/providers/microsoft/v1/systemone"


@dataclass
class DecisionResult:
    question: str
    choice: str
    probabilities: dict[str, float]
    model: str | None
    usage: dict[str, Any]
    latency_ms: int
    request_id: str | None
    confidence: float | None = None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    def probability_of(self, label: str) -> float | None:
        return self.probabilities.get(label)

    def margin(self) -> float | None:
        if len(self.probabilities) < 2:
            return None
        top = sorted(self.probabilities.values(), reverse=True)
        return round(top[0] - top[1], 6)


def _as_probabilities(value: Any) -> dict[str, float]:
    """Accept {label: p}, {label: {probability: p}} or [{label|option|choice, probability|score}]."""
    probs: dict[str, float] = {}
    if isinstance(value, dict):
        for label, v in value.items():
            if isinstance(v, int | float):
                probs[str(label)] = float(v)
            elif isinstance(v, dict):
                p = v.get("probability", v.get("score", v.get("p")))
                if isinstance(p, int | float):
                    probs[str(label)] = float(p)
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            label = item.get("label", item.get("option", item.get("choice", item.get("name"))))
            p = item.get("probability", item.get("score", item.get("p")))
            if label is not None and isinstance(p, int | float):
                probs[str(label)] = float(p)
    return probs


_PROB_KEYS = ("probabilities", "probability", "scores", "distribution", "options", "choices")


def normalize_response(
    body: dict[str, Any], question: str, *, latency_ms: int = 0, request_id: str | None = None
) -> DecisionResult:
    answers = body.get("answers")
    if not isinstance(answers, dict) or question not in answers:
        raise DecisionError(f"decision response has no answer for '{question}': {str(body)[:300]}")
    answer = answers[question]
    if not isinstance(answer, dict):
        raise DecisionError(f"unexpected answer shape for '{question}': {answer!r}"[:300])
    if answer.get("type") not in (None, "choice"):
        raise DecisionError(f"expected a choice answer, got type={answer.get('type')}")
    probs: dict[str, float] = {}
    for key in _PROB_KEYS:
        if key in answer:
            probs = _as_probabilities(answer[key])
            if probs:
                break
    choice = answer.get("choice", answer.get("answer", answer.get("value")))
    if choice is None and probs:
        choice = max(probs, key=lambda k: probs[k])
    if not isinstance(choice, str) or not choice:
        raise DecisionError(f"decision answer for '{question}' has no choice: {answer!r}"[:300])
    for p in probs.values():
        if not 0.0 <= p <= 1.0001:
            raise DecisionError(f"probability out of range in decision answer: {probs}")
    return DecisionResult(
        question=question,
        choice=choice,
        probabilities=probs,
        model=body.get("model"),
        usage=body.get("usage") or {},
        latency_ms=latency_ms,
        request_id=request_id,
        confidence=float(answer["confidence"]) if isinstance(answer.get("confidence"), int | float) else None,
        raw=body,
    )


def build_state(text: str, cfg: ClassifierConfig, *, title: str | None = None, fmt: str | None = None) -> str:
    parts = []
    if cfg.include_title and title:
        parts.append(f"Title: {title.strip()[:200]}")
    if cfg.include_format_hint and fmt:
        parts.append(f"File format: {fmt}")
    body = text.strip()
    if len(body) > cfg.max_chars:
        body = body[: cfg.max_chars] + "\n[...truncated]"
    parts.append("Document text:\n" + body)
    return "\n".join(parts)


class DecisionClient:
    def __init__(
        self,
        endpoint: str,
        deployment: str,
        *,
        token_provider: Any = None,
        timeout: float = 60,
        retries: int = 5,
    ) -> None:
        self.url = endpoint.rstrip("/") + DECISION_PATH
        self.deployment = deployment
        self._token_provider = token_provider or (lambda: bearer(SCOPE_COGNITIVE))
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()

    def payload(self, state: str, cfg: ClassifierConfig) -> dict[str, Any]:
        return {"model": self.deployment, "state": state, "questions": cfg.to_question()}

    def classify(self, state: str, cfg: ClassifierConfig) -> DecisionResult:
        if not state.strip():
            raise DecisionError("refusing to classify empty text", fix="check text extraction")
        body = self.payload(state, cfg)
        start = time.perf_counter()
        try:
            resp = request(
                "POST",
                self.url,
                token=self._token_provider(),
                json_body=body,
                timeout=self.timeout,
                retries=self.retries,
                ok=(200,),
                session=self._session,
            )
        except WorkshopError as exc:
            if "HTTP 404" in str(exc) or "HTTP 422" in str(exc):
                raise DecisionError(
                    str(exc), fix=f"check that deployment '{self.deployment}' is Microsoft-Decision-1.1 "
                    "(or a compatible Microsoft-Decision deployment)"
                ) from exc
            raise
        latency = int((time.perf_counter() - start) * 1000)
        try:
            data = resp.json()
        except ValueError as exc:
            raise http_error(resp, "decision response not JSON") from exc
        return normalize_response(
            data,
            cfg.question,
            latency_ms=latency,
            request_id=resp.headers.get("apim-request-id") or resp.headers.get("x-request-id"),
        )
