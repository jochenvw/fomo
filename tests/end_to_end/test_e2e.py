"""Full live end-to-end runs. Creates and deletes real resources and incurs cost.

Opt-in: FOMO_E2E=1 and a populated .workshop/environment.json; signed in with `az login`.

    $env:FOMO_E2E = "1"; uv run pytest -m e2e tests/end_to_end
"""

from __future__ import annotations

import json
import os

import pytest

from fomo import cli
from fomo.config import ENV_FILE
from fomo.data.corpus import REPO_ROOT
from fomo.naming import generate_hackername

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("FOMO_E2E") != "1",
                       reason="end-to-end tests need FOMO_E2E=1"),
]


@pytest.fixture(autouse=True)
def repo_cwd(monkeypatch):
    if not (REPO_ROOT / ENV_FILE).exists():
        pytest.skip(".workshop/environment.json missing; run `fomo env pull` first")
    monkeypatch.chdir(REPO_ROOT)


def _json(capsys, argv: list[str]) -> tuple[int, dict]:
    capsys.readouterr()
    rc = cli.main([*argv, "--output", "json"])
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out else {})


def _cleanup(name: str) -> None:
    rc = cli.main(["cleanup", "--participant", name, "--local", "--apply", "--yes"])
    assert rc == cli.EXIT_OK, f"cleanup of {name} failed (rc={rc}); delete its resources manually"


def test_cheatcode_end_to_end(capsys):
    name = generate_hackername()
    try:
        assert cli.main(["cheatcode", "--participant", name, "--apply", "--yes"]) == cli.EXIT_OK
        rc, state = _json(capsys, ["status", "--participant", name])
        assert rc == cli.EXIT_OK
        assert state["stage"] == "complete"

        rc, rerun = _json(capsys, ["miner", "run", "--hosted", "--participant", name, "--apply", "--yes"])
        assert rc == cli.EXIT_OK
        counts = rerun["result"]["counts"]
        assert counts.get("unchanged") and set(counts) <= {"unchanged", "skipped"}, counts
        assert rerun["result"]["runtime"] == "foundry-hosted"
    finally:
        _cleanup(name)


def test_provision_only(capsys):
    name = generate_hackername()
    try:
        rc, _ = _json(capsys, ["provision", "--participant", name, "--apply", "--yes"])
        assert rc == cli.EXIT_OK
        rc, state = _json(capsys, ["status", "--participant", name])
        assert rc == cli.EXIT_OK
        assert state["stage"] == "baseline-ready"
        assert state["smart_indexer"]["disabled"] is True
        assert (REPO_ROOT / ".workshop" / "runs" / name / "miner" / "classifier.yaml").exists()
    finally:
        _cleanup(name)
    assert not (REPO_ROOT / ".workshop" / "runs" / name).exists()
