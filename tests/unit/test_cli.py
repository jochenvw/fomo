from __future__ import annotations

import json
from pathlib import Path

import pytest

from contoso_workshop import cli


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["preflight", "--participants", "40", "--smoke"], {"command": "preflight", "participants": 40, "smoke": True}),
        (["provision", "--participant", "x", "--apply", "--yes"],
         {"command": "provision", "participant": "x", "apply": True, "yes": True}),
        (["miner", "deploy", "--classifier", "f"], {"miner_command": "deploy", "classifier": Path("f")}),
        (["miner", "run", "--hosted", "--mode", "delta"],
         {"miner_command": "run", "hosted": True, "local": False, "mode": "delta"}),
        (["miner", "run", "--local", "--only", "doc-003", "doc-018", "--dry-run"],
         {"local": True, "only": ["doc-003", "doc-018"], "dry_run": True}),
        (["smart"], {"command": "smart", "apply": False}),
        (["eval"], {"command": "eval", "agents": "canonical"}),
        (["cheatcode", "--force-deploy"], {"command": "cheatcode", "force_deploy": True}),
        (["status", "--no-agents"], {"command": "status", "no_agents": True}),
        (["reset-participant", "--scope", "metadata"], {"command": "reset-participant", "scope": "metadata"}),
        (["cleanup", "--local"], {"command": "cleanup", "local": True}),
        (["capacity", "--participants", "40"], {"command": "capacity", "participants": 40, "sku": "basic"}),
        (["env", "pull", "--resource-group", "rg"], {"env_command": "pull", "resource_group": "rg"}),
        (["agents", "ask", "smart", "q"], {"agents_command": "ask", "which": "smart", "question": "q"}),
        (["infra", "what-if", "--parameters", "p", "--resource-group", "rg"],
         {"infra_command": "what-if", "parameters": Path("p"), "resource_group": "rg"}),
        (["facilitator", "dashboard"], {"facilitator_command": "dashboard"}),
        (["status", "--output", "json"], {"output": "json"}),
    ],
)
def test_parser_accepts_documented_commands(argv, expected):
    args = cli.build_parser().parse_args(argv)
    for key, value in expected.items():
        assert getattr(args, key) == value, key


@pytest.mark.parametrize(
    "argv",
    [
        ["reset-participant"],
        ["miner", "run", "--hosted", "--local"],
        ["miner", "run", "--mode", "some"],
        ["agents", "ask", "fancy"],
        ["infra", "what-if"],
        ["status", "--output", "xml"],
    ],
)
def test_parser_rejects_invalid(argv, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(argv)


def test_every_command_has_a_handler():
    sub = next(a for a in cli.build_parser()._actions if a.dest == "command")
    assert set(sub.choices) == set(cli.COMMANDS)


def test_capacity_command_exit_codes(capsys):
    assert cli.main(["capacity", "--participants", "40"]) == cli.EXIT_BLOCKED
    assert "DOES NOT FIT" in capsys.readouterr().out
    assert cli.main(["capacity", "--participants", "5"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["capacity", "--participants", "40", "--sku", "standard2", "--output", "json"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["fits"] is True
    assert data["participants_max"] == 100
