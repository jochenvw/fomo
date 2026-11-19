from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from fomo import preflight
from fomo.errors import PermissionDenied, WorkshopError
from fomo.preflight import BLOCKED, INFO, PASS, REPO_ROOT, WARNING, Report, _skill_ok, effective, scrub

PERMS = [
    {"actions": ["*"], "notActions": ["Microsoft.Authorization/*/Write", "Microsoft.Authorization/*/Delete"]},
    {"actions": ["Microsoft.Storage/*/read"], "notActions": [],
     "dataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/*"],
     "notDataActions": ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete"]},
]


@pytest.mark.parametrize(
    ("action", "data", "expected"),
    [
        ("Microsoft.Search/searchServices/write", False, True),
        ("Microsoft.Authorization/roleAssignments/write", False, False),  # Contributor-like
        ("microsoft.storage/storageAccounts/read", False, True),  # case-insensitive
        ("Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read", True, True),
        ("Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete", True, False),
        ("Microsoft.Search/searchServices/indexes/documents/read", True, False),
    ],
)
def test_effective_permissions(action, data, expected):
    assert effective(PERMS, action, data=data) is expected


def test_not_actions_only_exclude_within_same_entry():
    perms = [{"actions": ["Microsoft.Storage/*/read"], "notActions": []},
             {"actions": ["*"], "notActions": ["Microsoft.Storage/*"]}]
    assert effective(perms, "Microsoft.Storage/storageAccounts/read")
    assert not effective(perms, "Microsoft.Storage/storageAccounts/write")


def test_scrub_masks_ids_upns_and_sensitive_strings():
    sub = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
    obj = {"scope": f"/subscriptions/{sub}/resourceGroups/rg", "items": [
        "signed in as jane.doe@contoso.onmicrosoft.com", {"name": "Contoso Prod Subscription (EU)"}],
        "token": "abc", "count": 3}
    out = scrub(obj, {"Contoso Prod Subscription", "Contoso Prod"})
    assert out["scope"] == "/subscriptions/<id>/resourceGroups/rg"
    assert out["items"][0] == "signed in as <user>"
    assert out["items"][1]["name"] == "<redacted> (EU)"
    assert out["token"] == "***"
    assert out["count"] == 3
    assert sub not in str(out)


def _report(*statuses):
    rep = Report("now", 40)
    for i, (status, required) in enumerate(statuses):
        rep.add(f"c{i}", "cat", "t", status, required=required)
    return rep


def test_report_verdict():
    assert _report((PASS, True), (INFO, False)).verdict == PASS
    assert _report((PASS, True), (WARNING, True)).verdict == WARNING
    assert _report((PASS, True), (BLOCKED, False)).verdict == WARNING  # optional blockers only warn
    assert _report((WARNING, True), (BLOCKED, True)).verdict == BLOCKED
    assert _report((PASS, True), (PASS, True)).counts() == {PASS: 2}


def test_repo_skills_have_valid_front_matter():
    skills = sorted(REPO_ROOT.glob(".github/skills/*/SKILL.md"))
    assert len(skills) >= 3
    assert all(_skill_ok(s) for s in skills), [s.parent.name for s in skills if not _skill_ok(s)]


@pytest.mark.parametrize(
    "text",
    [
        "no front matter\n",
        "---\nname: other-name\ndescription: x\n---\nbody\n",
        "---\nname: my-skill\n---\nbody\n",
    ],
)
def test_bad_skill_front_matter(tmp_path: Path, text):
    path = tmp_path / "my-skill" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    assert not _skill_ok(path)


def test_good_skill_with_crlf(tmp_path: Path):
    path = tmp_path / "my-skill" / "SKILL.md"
    path.parent.mkdir()
    path.write_bytes(b"---\r\nname: my-skill\r\ndescription: does things\r\n---\r\nbody\r\n")
    assert _skill_ok(path)


@pytest.mark.parametrize("matching", [False, True])
def test_azure_tenant_checked_before_incomplete_environment(monkeypatch, env, matching):
    env.foundry_account_name = ""
    account = {"id": env.subscription_id, "tenantId": env.tenant_id if matching else "other-tenant",
               "state": "Enabled"}
    az = Mock(return_value=account)
    arm = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(preflight, "az", az)
    monkeypatch.setattr(preflight, "arm", arm)
    report = Report("now", 1)
    preflight.azure_checks(report, env, participant=None, smoke=False)
    checks = {c.id: c for c in report.checks}
    assert checks["azure.tenant"].status == (PASS if matching else BLOCKED)
    assert ("azure.environment" in checks) is matching
    assert ("azure.target" in checks) is matching
    if not matching:
        assert f"az login --tenant {env.tenant_id}" in checks["azure.tenant"].fix
    assert az.call_count == 1
    assert arm.call_count == int(matching)


@pytest.mark.parametrize("incomplete", [False, True])
def test_missing_saved_group_offers_recovery_before_service_checks(monkeypatch, env, incomplete):
    if incomplete:
        env.foundry_account_name = ""
    monkeypatch.setattr(preflight, "az", lambda *args: {
        "id": env.subscription_id, "tenantId": env.tenant_id, "state": "Enabled"})
    arm = Mock(return_value=Mock(status_code=404))
    monkeypatch.setattr(preflight, "arm", arm)
    providers = Mock()
    monkeypatch.setattr(preflight, "_providers", providers)
    report = Report("now", 1)
    preflight.azure_checks(report, env, participant="cosmic-wombat", smoke=False)
    group = report.checks[-1]
    assert group.id == "azure.resource-group"
    assert group.status == BLOCKED and group.data["missing"] is True
    assert group.data["recovery_options"] == ["recreate", "select-existing", "create-new", "defer"]
    assert env.resource_group in group.evidence
    assert "--create-resource-group --location <region> --plan" in group.fix
    assert "does not restore" in group.fix
    arm.assert_called_once_with(
        "GET", f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}",
        "2021-04-01", ok=(200, 404))
    providers.assert_not_called()
    assert report.verdict == BLOCKED


@pytest.mark.parametrize("error", [
    PermissionDenied("HTTP 403: access denied", owner="facilitator"),
    WorkshopError("Connection interrupted"),
])
def test_unverifiable_group_is_not_reported_missing(monkeypatch, env, error):
    monkeypatch.setattr(preflight, "az", lambda *args: {
        "id": env.subscription_id, "tenantId": env.tenant_id, "state": "Enabled"})
    monkeypatch.setattr(preflight, "arm", Mock(side_effect=error))
    report = Report("now", 1)
    preflight.azure_checks(report, env, participant=None, smoke=False)
    check = report.checks[-1]
    assert check.id == "azure.resource-group" and check.status == BLOCKED
    assert check.data["missing"] is None
    assert "recovery_options" not in check.data
    assert "existence is unknown" in check.fix
    assert "--create-resource-group" not in check.fix
    assert str(error) in check.evidence


def test_subscription_drift_stops_group_discovery_even_with_incomplete_config(monkeypatch, env):
    env.foundry_account_name = ""
    monkeypatch.setattr(preflight, "az", lambda *args: {
        "id": "wrong-sub", "tenantId": env.tenant_id, "state": "Enabled"})
    arm = Mock()
    monkeypatch.setattr(preflight, "arm", arm)
    report = Report("now", 1)
    preflight.azure_checks(report, env, participant=None, smoke=False)
    assert report.checks[-1].id == "azure.target"
    assert report.checks[-1].status == BLOCKED
    arm.assert_not_called()


def test_login_probe_preserves_connectivity_error_instead_of_claiming_signed_out(monkeypatch, env):
    monkeypatch.setattr(preflight, "az", Mock(side_effect=WorkshopError(
        "connection interrupted after 3 attempt(s)", fix="check VPN/proxy, then retry")))
    report = Report("now", 1)
    preflight.azure_checks(report, env, participant=None, smoke=False)
    assert len(report.checks) == 1
    assert report.checks[0].status == BLOCKED
    assert "connection interrupted" in report.checks[0].evidence
    assert "not signed in" not in report.checks[0].evidence
    assert "VPN/proxy" in report.checks[0].fix


@pytest.mark.parametrize("allowed", [True, False, None])
@pytest.mark.parametrize("resource", ["storage", "project", "model-chat", "container", "cosmic-wombat-raw"])
def test_missing_resource_creation_readiness(monkeypatch, env, names, resource, allowed):
    report = Report("now", 1)
    paths = {
        "storage": env.resolved_storage_account_id(),
        "project": f"{env.foundry_account_id}/projects/{env.project_name}",
    }
    cid = "models.chat" if resource == "model-chat" else f"resources.{resource}"
    report.add(cid, "resources", resource, BLOCKED, "not found", data={"missing": True})
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"
    calls = []

    def permissions(scope):
        calls.append(scope)
        if allowed is None:
            raise WorkshopError("permissions API unavailable")
        return [{"actions": ["*"] if allowed else []}]

    monkeypatch.setattr(preflight, "permissions", permissions)
    preflight._control_plane(report, env, rg, {"missing": [paths[resource]] if resource in paths else []},
                             participant=names.hackername)
    checks = {c.id: c for c in report.checks}
    creation = checks[f"rbac.provision-{resource}"]
    assert creation.status == (PASS if allowed else BLOCKED)
    assert checks[cid].status == BLOCKED  # creation RBAC never makes a missing resource ready
    assert checks[cid].owner == ("participant" if allowed else "facilitator")
    if allowed:
        assert "subject to approval" in checks[cid].fix
        assert "--plan" in checks[cid].fix
        assert ("fomo provision" in checks[cid].fix) == (
            resource in ("container", "cosmic-wombat-raw"))
    elif allowed is False:
        assert "provision this resource for you" in checks[cid].fix
    else:
        assert "permissions unknown" in checks[cid].fix
    assert len(calls) == len(set(calls))


def test_missing_storage_does_not_skip_other_permission_checks(monkeypatch, env):
    report = Report("now", 1)
    report.add("resources.storage", "resources", "Storage", BLOCKED)
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"

    def permissions(scope):
        assert scope != env.resolved_storage_account_id()
        if scope == rg:
            return [{"actions": ["Microsoft.Storage/storageAccounts/write"]}]
        return [{"actions": [], "dataActions": ["*"]}]

    monkeypatch.setattr(preflight, "permissions", permissions)
    preflight._control_plane(report, env, rg, {"missing": [env.resolved_storage_account_id()]})
    checks = {c.id: c for c in report.checks}
    assert checks["rbac.provision-storage"].status == PASS
    assert checks["rbac.agents"].status == PASS
    assert checks["rbac.blob"].status == BLOCKED  # management write does not grant blob write


def test_existing_resources_do_not_require_creation_rights_and_use_container_scope(monkeypatch, env, names):
    report = Report("now", 1)
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"
    container_scope = f"{env.resolved_storage_account_id()}/blobServices/default/containers/{names.container}"
    calls = []

    def permissions(scope):
        calls.append(scope)
        return [{"actions": [], "dataActions": ["*"] if scope == container_scope else []}]

    monkeypatch.setattr(preflight, "permissions", permissions)
    preflight._control_plane(report, env, rg, {}, participant=names.hackername)
    checks = {c.id: c for c in report.checks}
    assert checks["rbac.provision-storage"].status == INFO
    assert not checks["rbac.provision-storage"].required
    assert checks["rbac.blob"].status == PASS
    assert checks["rbac.blob-read"].status == PASS
    assert container_scope in calls
    assert checks["rbac.grant-agent"].status == BLOCKED


@pytest.mark.parametrize("denied", [False, True])
def test_resources_distinguish_missing_from_inaccessible(monkeypatch, env, denied):
    def arm(method, path, api, **kwargs):
        assert method == "GET"
        if denied:
            raise PermissionDenied("HTTP 403", owner="facilitator")
        return Mock(status_code=404)

    monkeypatch.setattr(preflight, "arm", arm)
    report = Report("now", 1)
    shared = preflight._resources(report, env, "rg")
    assert (env.resolved_storage_account_id() in shared.get("missing", [])) is not denied
    assert all(c.status == BLOCKED for c in report.checks)


def test_participant_resources_inspect_all_objects_without_writing(monkeypatch, env, names):
    monkeypatch.setattr(preflight, "credential", lambda: None)
    container = Mock()
    container.exists.return_value = False
    monkeypatch.setattr("fomo.data.blob.ParticipantContainer", lambda *args: container)
    search = Mock()
    search.get.side_effect = lambda kind, name: {} if name == names.raw_index else None
    monkeypatch.setattr("fomo.search.client.SearchClient", lambda *args: search)
    report = Report("now", 1)
    preflight._participant_resources(report, env, names.hackername)
    checks = {c.id: c for c in report.checks}
    assert len(checks) == 8
    assert checks["resources.container"].data["missing"]
    assert checks[f"resources.{names.raw_index}"].status == PASS
    assert checks[f"resources.{names.smart_index}"].data["missing"]
    assert [call[0] for call in container.method_calls] == ["exists"]
    assert all(call[0] == "get" for call in search.method_calls)


def test_unavailable_storage_permissions_do_not_hide_foundry_or_search(monkeypatch, env):
    report = Report("now", 1)
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"

    def permissions(scope):
        if scope == env.resolved_storage_account_id():
            raise PermissionDenied("cannot inspect Storage permissions")
        return [{"actions": ["*"], "dataActions": ["*"]}]

    monkeypatch.setattr(preflight, "permissions", permissions)
    preflight._control_plane(report, env, rg, {})
    checks = {c.id: c for c in report.checks}
    assert checks["rbac.blob"].status == BLOCKED
    assert "unknown" in checks["rbac.blob"].evidence
    assert checks["rbac.agents"].status == PASS
    assert checks["rbac.search-documents"].status == PASS


def test_missing_models_record_presence_and_account_scoped_creation_permission(monkeypatch, env):
    monkeypatch.setattr(preflight, "arm", lambda *args: Mock(json=lambda: {"value": []}))
    report = Report("now", 1)
    preflight._models(report, env)
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"

    def permissions(scope):
        return [{"actions": ["Microsoft.CognitiveServices/accounts/deployments/write"]
                 if scope == env.foundry_account_id else []}]

    monkeypatch.setattr(preflight, "permissions", permissions)
    preflight._control_plane(report, env, rg, {})
    checks = {c.id: c for c in report.checks}
    for role in ("chat", "embedding", "decision"):
        assert checks[f"models.{role}"].data["missing"]
        assert "subject to approval" in checks[f"models.{role}"].fix
        assert checks[f"rbac.provision-model-{role}"].status == PASS
    assert checks["rbac.provision-foundry"].status == INFO


@pytest.mark.parametrize("writer", [False, True])
def test_search_document_access_uses_registered_read_and_write_actions(monkeypatch, env, writer):
    actions = ["Microsoft.Search/searchServices/indexes/documents/read"]
    if writer:
        actions.append("Microsoft.Search/searchServices/indexes/documents/write")
    monkeypatch.setattr(preflight, "permissions", lambda scope: [
        {"actions": [], "dataActions": actions if scope == env.search_service_id else []},
    ])
    report = Report("now", 1)
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"
    preflight._control_plane(report, env, rg, {})
    checks = {c.id: c for c in report.checks}
    assert checks["rbac.search-read"].status == PASS
    assert checks["rbac.search-documents"].status == (PASS if writer else BLOCKED)
