from __future__ import annotations

import io
import zipfile

import pytest
import yaml

from contoso_workshop import rbac
from contoso_workshop.agents.hosted import build_zip, environment_variables, required_grants
from contoso_workshop.decision.config import REFERENCE_CONFIG
from contoso_workshop.errors import ConfigError


def _names(data):
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_build_zip_is_deterministic_and_complete():
    data1, sha1 = build_zip()
    data2, sha2 = build_zip()
    assert (data1, sha1) == (data2, sha2)
    entries = _names(data1)
    assert entries == sorted(entries)
    assert {"main.py", "requirements.txt", "contoso_workshop/decision/reference_classifier.yaml",
            "contoso_workshop/miner/pipeline.py", "contoso_workshop/decision/client.py"} <= set(entries)
    assert not any("search" in e or "preflight" in e or "cli.py" in e for e in entries)
    with zipfile.ZipFile(io.BytesIO(data1)) as zf:
        assert b"\r\n" not in zf.read("main.py")
        assert all(i.date_time == (2026, 1, 1, 0, 0, 0) for i in zf.infolist())


def test_build_zip_with_participant_classifier(tmp_path):
    data = yaml.safe_load(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    data["version"] = "cosmic-wombat-v2"
    path = tmp_path / "classifier.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    zipped, sha = build_zip(path)
    assert sha != build_zip()[1]
    packaged = zipfile.ZipFile(io.BytesIO(zipped)).read("contoso_workshop/decision/reference_classifier.yaml")
    assert yaml.safe_load(packaged)["version"] == "cosmic-wombat-v2"


def test_build_zip_rejects_unfinished_classifier(tmp_path):
    path = tmp_path / "classifier.yaml"
    path.write_text(REFERENCE_CONFIG.read_text(encoding="utf-8").replace("A binding agreement", "TODO"),
                    encoding="utf-8")
    with pytest.raises(ConfigError):
        build_zip(path)


def test_required_grants_are_least_privilege(env, names):
    grants = required_grants(env, names, "principal-1")
    assert [(g.role, g.scope) for g in grants] == [
        ("Storage Blob Data Contributor",
         f"{env.resolved_storage_account_id()}/blobServices/default/containers/cosmic-wombat-inbox"),
        ("Foundry User", env.foundry_account_id),
    ]
    assert grants[1].role_definition_id.endswith("/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d")
    assert grants[1].role_definition_id.startswith(f"/subscriptions/{env.subscription_id}/")
    assert all(g.principal_id == "principal-1" for g in grants)


def test_grant_assignment_name_is_deterministic(env):
    a = rbac.Grant("Foundry User", env.foundry_account_id, "p1")
    assert a.assignment_name == rbac.Grant("Foundry User", env.foundry_account_id.upper(), "p1").assignment_name
    assert a.assignment_name != rbac.Grant("Foundry User", env.foundry_account_id, "p2").assignment_name
    assert a.assignment_name != rbac.Grant("Cognitive Services User", env.foundry_account_id, "p1").assignment_name
    assert a.path.endswith(f"/providers/Microsoft.Authorization/roleAssignments/{a.assignment_name}")


def test_environment_variables_pin_one_container(env, names):
    ev = environment_variables(env, names)
    assert ev["CONTOSO_CONTAINER"] == "cosmic-wombat-inbox"
    assert ev["CONTOSO_DECISION_ENDPOINT"] == "https://contoso-foundry.openai.azure.com"
