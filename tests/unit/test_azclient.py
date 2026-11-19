from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from contoso_workshop import azclient
from contoso_workshop.errors import WorkshopError


@pytest.fixture
def cli_process(monkeypatch):
    process = Mock()
    sleep = Mock()
    monkeypatch.setattr(azclient, "az_path", lambda: "az")
    monkeypatch.setattr(azclient.subprocess, "run", process)
    monkeypatch.setattr(azclient.time, "sleep", sleep)
    return process, sleep


def _result(*, error="", output='{"id": "sub"}'):
    return SimpleNamespace(returncode=int(bool(error)), stderr=error, stdout=output)


@pytest.mark.parametrize("failure", [
    _result(error="Connection interrupted"),
    subprocess.TimeoutExpired("az", 120),
    _result(output='{"id":'),
])
def test_read_retries_transient_interruption(cli_process, failure):
    process, sleep = cli_process
    process.side_effect = [failure, _result()]
    assert azclient.az("account", "show") == {"id": "sub"}
    assert process.call_count == 2
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("check", [True, False])
def test_read_retry_budget_is_bounded(cli_process, check):
    process, sleep = cli_process
    process.return_value = _result(error="Connection reset by peer")
    if check:
        with pytest.raises(WorkshopError, match="after 3 attempt") as caught:
            azclient.az("group", "show", "--name", "rg", check=check)
        assert "connectivity" in caught.value.fix
    else:
        assert azclient.az("group", "show", "--name", "rg", check=check) is None
    assert process.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize("error", [
    "AuthorizationFailed: connection interrupted",
    "ResourceGroupNotFound",
    "AADSTS700082: token expired",
    "RequestDisallowedByPolicy",
])
def test_permanent_read_errors_are_not_retried(cli_process, error):
    process, sleep = cli_process
    process.return_value = _result(error=error)
    with pytest.raises(WorkshopError, match="after 1 attempt"):
        azclient.az("group", "show", "--name", "rg")
    assert process.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("command", [
    ("group", "create"),
    ("provider", "register"),
    ("deployment", "group", "create"),
])
def test_uncertain_writes_are_not_replayed(cli_process, command):
    process, sleep = cli_process
    process.return_value = _result(error="Connection interrupted")
    with pytest.raises(WorkshopError) as caught:
        azclient.az(*command)
    assert "write may have completed" in caught.value.fix
    assert process.call_count == 1
    sleep.assert_not_called()
