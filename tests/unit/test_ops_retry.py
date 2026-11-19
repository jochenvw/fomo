from __future__ import annotations

from typing import ClassVar

import pytest

from fomo.errors import AgentError
from fomo.provision import ops
from fomo.telemetry.logging import RunLogger


class FakeHosted:
    outcomes: ClassVar[list] = []
    calls = 0

    def __init__(self, env, names) -> None:
        pass

    def current(self):
        return object()

    def invoke(self, payload):
        type(self).calls += 1
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def hosted(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(ops, "HostedMiner", FakeHosted)
    monkeypatch.setattr(ops.time, "sleep", sleeps.append)
    FakeHosted.calls = 0
    return sleeps


def _invoke(env, names):
    return ops.invoke_hosted(env, names, RunLogger("test", quiet=True), {"mode": "delta"})


def test_retries_while_rbac_propagates(env, names, hosted):
    FakeHosted.outcomes = [
        AgentError("hosted miner invocation failed: HTTP 403 ... Needs Storage Blob Data Contributor."),
        AgentError("hosted miner invocation failed: HTTP 424 session_not_ready"),
        {"ok": True, "counts": {"classified": 25}},
    ]
    assert _invoke(env, names) == {"ok": True, "counts": {"classified": 25}}
    assert FakeHosted.calls == 3
    assert hosted == [30, 30]


def test_network_denial_is_not_retried(env, names, hosted):
    FakeHosted.outcomes = [
        AgentError("HTTP 403 (AuthorizationFailure). Storage network rules rejected this caller. "
                   "Needs Storage Blob Data Contributor."),
        {"ok": True},
    ]
    with pytest.raises(AgentError, match="network rules rejected"):
        _invoke(env, names)
    assert FakeHosted.calls == 1
    assert hosted == []


def test_other_errors_not_retried(env, names, hosted):
    FakeHosted.outcomes = [AgentError("HTTP 500 boom"), {"ok": True}]
    with pytest.raises(AgentError, match="boom"):
        _invoke(env, names)
    assert FakeHosted.calls == 1


def test_gives_up_after_rbac_wait(env, names, hosted):
    FakeHosted.outcomes = [AgentError("PermissionDenied")] * 3
    with pytest.raises(AgentError):
        ops.invoke_hosted(env, names, RunLogger("test", quiet=True), {}, rbac_wait=-1)
    assert FakeHosted.calls == 1


def test_missing_agent(env, names, monkeypatch):
    class Missing(FakeHosted):
        def current(self):
            return None

    monkeypatch.setattr(ops, "HostedMiner", Missing)
    with pytest.raises(AgentError, match="does not exist"):
        _invoke(env, names)
