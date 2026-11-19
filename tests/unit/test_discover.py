from __future__ import annotations

import pytest

from fomo import discover, preflight
from fomo.discover import Inventory, adopt_deployments, advise, resolve
from fomo.errors import PermissionDenied
from fomo.preflight import BLOCKED, Report


def it_provisioned(**overrides: object) -> Inventory:
    inv = Inventory(
        resource_count=3,
        foundry=[{"name": "it-foundry", "id": "/f", "endpoint": "https://it-sub.services.ai.azure.com",
                  "openai_endpoint": "https://it-sub.openai.azure.com", "projects_enabled": True}],
        projects={"it-foundry": ["workshop"]},
        storage=[{"name": "itstorage", "id": "/s"}],
        search=[{"name": "it-search", "id": "/q", "sku": "basic"}],
    )
    for key, value in overrides.items():
        setattr(inv, key, value)
    return inv


def test_resolve_adopts_unique_services_with_their_real_endpoints():
    fields, problems = resolve(it_provisioned(), {})
    assert problems == {}
    assert fields["foundry_account_name"] == "it-foundry"
    assert fields["project_endpoint"] == "https://it-sub.services.ai.azure.com/api/projects/workshop"
    assert fields["storage_account_id"] == "/s" and fields["search_sku"] == "basic"


def test_resolve_never_guesses_between_candidates():
    inv = it_provisioned(search=[{"name": "a", "id": "/a", "sku": "basic"}, {"name": "b", "id": "/b", "sku": ""}])
    assert resolve(inv, {})[1] == {"search": "ambiguous: a, b"}
    fields, problems = resolve(inv, {"search": "b"})
    assert problems == {} and fields["search_service_name"] == "b"
    assert resolve(inv, {"search": "c"})[1] == {"search": "'c' not found"}


@pytest.mark.parametrize("can_create, owner", [(False, "facilitator"), (None, "facilitator"), (True, "participant")])
def test_empty_group_becomes_a_request_to_internal_it(env, can_create, owner):
    _, problems = resolve(Inventory(), {})
    fix, who = advise(env, Inventory(), problems, can_create)
    assert who == owner
    assert "internal IT" in fix and "Foundry account" in fix and "Search Index Data Contributor" in fix
    assert ("infra deploy" in fix) is bool(can_create)


def test_advice_prefers_pull_then_discover_then_disambiguation(env):
    assert "env pull" in advise(env, it_provisioned(deployment="fomo"), {}, None)[0]
    assert "env discover --plan" in advise(env, it_provisioned(), {}, None)[0]
    fix, _ = advise(env, it_provisioned(), {"storage": "ambiguous: x, y"}, None)
    assert "--storage-account <name>" in fix


def test_absent_default_deployment_names_adopt_a_unique_match(monkeypatch, env):
    def fake_list(path: str, api: str) -> list[dict]:
        if path.endswith("/deployments"):
            return [{"name": "chat", "properties": {"model": {"name": "gpt-4.1-mini"}}},
                    {"name": "emb", "properties": {"model": {"name": "text-embedding-3-large"}}}]
        return [{"name": "it-search-conn", "properties": {"category": "CognitiveSearch"}}]
    monkeypatch.setattr(discover, "_list", fake_list)
    fields = {"foundry_account_name": "it-foundry", "project_name": "workshop"}
    notes = adopt_deployments(env, fields)
    assert fields["chat_deployment"] == "chat"
    assert fields["embedding_deployment"] == "emb" and fields["embedding_dimensions"] == 3072
    assert "decision_deployment" not in fields and any("decision_deployment" in n for n in notes)
    assert fields["search_connection_name"] == "it-search-conn"


def unconfigured_report(monkeypatch, env, inventory) -> dict:
    env.foundry_account_name = env.project_name = env.storage_account_name = env.search_service_name = ""
    monkeypatch.setattr(discover, "inventory", inventory)
    monkeypatch.setattr(preflight, "permissions", lambda scope: [])
    report = Report("now", 1)
    preflight._unconfigured(report, env, env.missing())
    return {c.id: c for c in report.checks}


def test_preflight_points_at_it_provisioned_services(monkeypatch, env):
    checks = unconfigured_report(monkeypatch, env, lambda *a: it_provisioned())
    assert "it-foundry" in checks["azure.discovery"].evidence
    assert checks["azure.environment"].status == BLOCKED
    assert "env discover --plan" in checks["azure.environment"].fix
    assert "facilitator shares" not in checks["azure.environment"].fix


def test_unlistable_group_is_unknown_not_empty(monkeypatch, env):
    def denied(*a: object) -> Inventory:
        raise PermissionDenied("HTTP 403")
    checks = unconfigured_report(monkeypatch, env, denied)
    assert "not proof the group is empty" in checks["azure.environment"].fix
    assert "azure.discovery" not in checks
