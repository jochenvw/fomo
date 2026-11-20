from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fomo import cli
from fomo.config import ENV_FILE, Environment, load_participant, save_participant
from fomo.errors import PermissionDenied


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["preflight", "--participants", "40", "--smoke"], {"command": "preflight", "participants": 40, "smoke": True}),
        (["provision", "--participant", "x", "--apply", "--yes"],
         {"command": "provision", "participant": "x", "apply": True, "yes": True, "step": "all"}),
        (["provision", "--step", "upload"], {"command": "provision", "step": "upload", "apply": False}),
        (["next", "--output", "json"], {"command": "next", "output": "json"}),
        (["miner", "deploy", "--classifier", "f"], {"miner_command": "deploy", "classifier": Path("f")}),
        (["miner", "run", "--hosted", "--mode", "delta"],
         {"miner_command": "run", "hosted": True, "local": False, "mode": "delta"}),
        (["miner", "run", "--local", "--only", "doc-003", "doc-018", "--dry-run"],
         {"local": True, "only": ["doc-003", "doc-018"], "dry_run": True}),
        (["smart"], {"command": "smart", "apply": False}),
        (["eval"], {"command": "eval", "agents": "canonical"}),
        (["eval-foundry"], {"command": "eval-foundry", "suite": "smoke", "apply": False}),
        (["eval-foundry", "--suite", "full", "--apply", "--participant", "cosmic-wombat"],
         {"command": "eval-foundry", "suite": "full", "apply": True}),
        (["cheatcode", "--force-deploy"], {"command": "cheatcode", "force_deploy": True}),
        (["status", "--no-agents"], {"command": "status", "no_agents": True}),
        (["reset-participant", "--scope", "metadata"], {"command": "reset-participant", "scope": "metadata"}),
        (["cleanup", "--local"], {"command": "cleanup", "local": True}),
        (["capacity", "--participants", "40"], {"command": "capacity", "participants": 40, "sku": "basic"}),
        (["env", "pull", "--resource-group", "rg"], {"env_command": "pull", "resource_group": "rg"}),
        (["env", "select", "--tenant", "tenant", "--subscription", "sub", "--resource-group", "rg",
          "--create-resource-group", "--location", "swedencentral"],
         {"env_command": "select", "create_resource_group": True, "location": "swedencentral"}),
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


@pytest.mark.parametrize("output", ["text", "json"])
def test_preflight_mascot_is_human_output_only(monkeypatch, tmp_path, capsys, output):
    from fomo.preflight import Report

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fomo.preflight.run_preflight", lambda *args, **kwargs: Report("now", 1))
    assert cli.main(["preflight", "--local-only", "--participants", "1", "--output", output]) == cli.EXIT_OK
    captured = capsys.readouterr()
    if output == "json":
        assert json.loads(captured.out)["verdict"] == "PASS"
        assert "[o_o]" not in captured.err
        assert "🔎" not in captured.err
    else:
        assert "[o_o]" in captured.err
        assert "🔎 FOMO Preflight" in captured.err
    reports = list((tmp_path / ".workshop" / "preflight").glob("readiness-*.json"))
    assert reports
    for path in reports:
        assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == "PASS"
        assert "[o_o]" not in path.read_text(encoding="utf-8")


def test_preflight_kickoff_saves_and_reuses_available_name(monkeypatch, tmp_path):
    from fomo.preflight import Report
    from fomo.provision import participant as namespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli.Ctx, "require_env", lambda self: None)
    candidates = iter(["cosmic-wombat-000001", "cosmic-wombat-000002", "cosmic-wombat-000003"])
    monkeypatch.setattr("fomo.naming.generate_hackername", lambda rng: next(candidates))
    monkeypatch.setattr(namespace, "SearchClient", lambda env: SimpleNamespace(
        list_names=lambda kind: ["cosmic-wombat-000001-raw"]))
    monkeypatch.setattr(namespace, "container_client", lambda env, names: SimpleNamespace(
        exists=lambda: names.hackername.endswith("000002")))
    checked = []

    def preflight(env, **kwargs):
        checked.append(kwargs["participant"])
        return Report("now", 1)

    monkeypatch.setattr("fomo.preflight.run_preflight", preflight)
    for _ in range(2):
        assert cli.main(["preflight", "--init-participant"]) == cli.EXIT_OK
    assert checked == ["cosmic-wombat-000003"] * 2
    assert load_participant() == checked[0]


@pytest.mark.parametrize("extra", [["--local-only"], ["--participant", "someone-else"], ["--fix"]])
def test_preflight_rejects_conflicting_kickoff_modes(monkeypatch, tmp_path, extra):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["preflight", "--init-participant", *extra]) == cli.EXIT_ERROR
    assert load_participant() is None


@pytest.mark.parametrize("explicit_subscription", [False, True])
@pytest.mark.parametrize("confirmation", ["matching", "different", "none"])
def test_env_pull_remembers_target_subscription_tenant(monkeypatch, tmp_path, explicit_subscription, confirmation):
    monkeypatch.chdir(tmp_path)
    sub = "target-sub" if explicit_subscription else "active-sub"
    calls = []

    def az(*args):
        calls.append(args)
        if args == ("account", "show"):
            return {"id": "active-sub", "tenantId": "active-tenant"}
        if args == ("account", "show", "--subscription", sub):
            return {"id": sub, "tenantId": "target-tenant"}
        assert args == ("deployment", "group", "show", "-g", "rg", "-n", "workshop", "--subscription", sub)
        return {"properties": {"outputs": {"FOUNDRY_ACCOUNT_NAME": {"value": "foundry"}}}}

    monkeypatch.setattr("fomo.azclient.az", az)
    ctx = SimpleNamespace(env=Environment(subscription_id=sub if explicit_subscription else "", resource_group="rg"))
    confirmed = {"tenant_id": "target-tenant", "subscription_id": sub, "resource_group": "rg"}
    if confirmation != "none":
        ctx.env.extra["confirmed_azure_context"] = (
            confirmed if confirmation == "matching" else {**confirmed, "resource_group": "old-rg"})
    result = cli._env_pull(ctx, "workshop")
    saved = Environment.load(env_file=Path(result["path"]), environ={})
    assert (saved.tenant_id, saved.subscription_id, saved.resource_group) == ("target-tenant", sub, "rg")
    assert saved.foundry_account_name == "foundry"
    assert ctx.env.to_dict() == saved.to_dict()
    assert saved.extra.get("confirmed_azure_context") == (confirmed if confirmation == "matching" else None)
    assert len(calls) == 3


@pytest.fixture
def group_selection(monkeypatch, tmp_path, env):
    monkeypatch.chdir(tmp_path)
    state = SimpleNamespace(
        exists=True, fail_create=False, calls=[],
        account={"id": env.subscription_id, "tenantId": env.tenant_id, "state": "Enabled"},
        argv=["env", "select", "--tenant", env.tenant_id, "--subscription", env.subscription_id,
              "--resource-group", env.resource_group],
    )
    scope = ("--subscription", env.subscription_id, "--name", env.resource_group)

    def az(*args, **kwargs):
        state.calls.append(args)
        if args == ("account", "show"):
            return state.account
        if args == ("group", "exists", *scope):
            return state.exists
        if args == ("group", "create", *scope, "--location", "westeurope"):
            if state.fail_create:
                raise PermissionDenied("resource group creation denied", owner="admin")
            state.exists = True
            return {}
        if args == ("group", "show", *scope):
            assert state.exists
            return {"id": f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}",
                    "location": "westeurope"}
        raise AssertionError(f"unexpected Azure operation: {args}")

    monkeypatch.setattr("fomo.azclient.az", az)
    monkeypatch.setattr("fomo.plan.az", az)
    return state


@pytest.mark.parametrize("create", [False, True])
def test_env_select_plans_without_creating_or_saving(group_selection, create, capsys):
    group_selection.exists = not create
    argv = group_selection.argv + (["--create-resource-group", "--location", "westeurope"] if create else [])
    assert cli.main(argv) == cli.EXIT_OK
    assert not ENV_FILE.exists()
    assert not any(call[:2] == ("group", "create") for call in group_selection.calls)
    assert ("empty group in westeurope" in capsys.readouterr().out) is create


@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("same_target", [False, True])
def test_env_select_applies_only_group_and_remembers_target(group_selection, env, create, same_target):
    group_selection.exists = not create
    saved = Environment(**env.to_dict())
    if not same_target:
        saved.resource_group = "old-rg"
    saved.save()
    save_participant("cosmic-wombat")
    argv = group_selection.argv + (["--create-resource-group", "--location", "westeurope"] if create else [])
    assert cli.main([*argv, "--apply", "--yes"]) == cli.EXIT_OK
    loaded = Environment.load(environ={})
    confirmed = {"tenant_id": env.tenant_id, "subscription_id": env.subscription_id,
                 "resource_group": env.resource_group}
    assert loaded.extra["confirmed_azure_context"] == confirmed
    assert all(getattr(loaded, key) == value for key, value in confirmed.items())
    assert loaded.storage_account_name == (env.storage_account_name if same_target and not create else "")
    assert load_participant() == "cosmic-wombat"
    assert sum(call[:2] == ("group", "create") for call in group_selection.calls) == int(create)
    assert group_selection.calls[-1][:2] == ("group", "show")
    if create:
        assert loaded.location == "westeurope"
        assert loaded.missing()  # an empty group is not a ready workshop


@pytest.mark.parametrize("failure", [
    "approval", "tenant", "subscription", "permission", "missing", "collision", "unknown",
])
def test_env_select_failure_does_not_save(group_selection, env, monkeypatch, failure):
    env.save()
    before = ENV_FILE.read_bytes()
    group_selection.exists = failure == "collision"
    argv = [*group_selection.argv, "--create-resource-group", "--location", "westeurope", "--apply", "--yes"]
    if failure == "approval":
        from fomo.errors import ApprovalDeclined

        def decline(*args, **kwargs):
            raise ApprovalDeclined("not approved")

        monkeypatch.setattr(cli, "confirm", decline)
    elif failure in ("tenant", "subscription"):
        group_selection.account["tenantId" if failure == "tenant" else "id"] = "other"
    elif failure == "permission":
        group_selection.fail_create = True
    elif failure == "missing":
        argv = [*group_selection.argv, "--apply", "--yes"]
    elif failure == "unknown":
        group_selection.exists = None
    assert cli.main(argv) in (cli.EXIT_ERROR, cli.EXIT_BLOCKED)
    assert ENV_FILE.read_bytes() == before
    assert sum(call[:2] == ("group", "create") for call in group_selection.calls) == int(failure == "permission")
