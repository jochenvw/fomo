from __future__ import annotations

import json

import pytest

from contoso_workshop.config import Environment, load_participant, save_participant
from contoso_workshop.errors import ConfigError


def test_load_precedence(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "environment:\n  resource_group: from-yaml\n  location: westeurope\n  project_name: yaml-proj\n"
        "  search_sku: free\n",
        encoding="utf-8",
    )
    env_file = tmp_path / "environment.json"
    env_file.write_text(json.dumps({"resource_group": "from-file", "project_name": "file-proj",
                                    "embedding_dimensions": "3072"}), encoding="utf-8")
    environ = {"CONTOSO_PROJECT_NAME": "env-proj", "CONTOSO_SEARCH_SKU": "basic", "UNRELATED": "x"}
    env = Environment.load(config_file=cfg, env_file=env_file, environ=environ,
                           overrides={"search_sku": "standard", "subscription_id": None})
    assert env.location == "westeurope"  # yaml only
    assert env.resource_group == "from-file"  # file beats yaml
    assert env.project_name == "env-proj"  # env var beats file
    assert env.search_sku == "standard"  # override beats env var
    assert env.subscription_id == ""  # None overrides are ignored
    assert env.embedding_dimensions == 3072  # int fields are coerced


def test_load_without_files_uses_defaults(tmp_path):
    env = Environment.load(env_file=tmp_path / "missing.json", environ={})
    assert env.location == "swedencentral"
    assert env.missing() == list(Environment.REQUIRED)


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        Environment.load(config_file=tmp_path / "nope.yaml", env_file=tmp_path / "x.json", environ={})


def test_require(env):
    assert env.require() is env
    env.search_service_name = ""
    with pytest.raises(ConfigError, match="search_service_name"):
        env.require()


def test_extra_round_trip_does_not_nest(tmp_path, env):
    env.apply({"facilitator_note": "hello", "extra": {"cohort": "b"}})
    assert env.extra == {"facilitator_note": "hello", "cohort": "b"}
    path = env.save(tmp_path / "environment.json")
    loaded = Environment.load(env_file=path, environ={})
    assert loaded.extra == {"facilitator_note": "hello", "cohort": "b"}
    loaded.save(path)
    again = Environment.load(env_file=path, environ={})
    assert again.extra == env.extra
    assert "extra" not in again.extra
    assert again.to_dict() == env.to_dict()


def test_from_bicep_outputs():
    outputs = {
        "foundrY_ACCOUNT_NAME": {"type": "String", "value": "acct"},
        "SEARCH_SERVICE_NAME": {"value": "srch"},
        "storage_account_name": "store",
        "UNKNOWN_OUTPUT": {"value": "ignored"},
    }
    env = Environment.from_bicep_outputs(outputs, subscription_id="sub", resource_group="rg")
    assert (env.foundry_account_name, env.search_service_name, env.storage_account_name) == ("acct", "srch", "store")
    assert env.subscription_id == "sub"
    assert "UNKNOWN_OUTPUT" not in env.extra
    assert env.resolved_search_endpoint() == "https://srch.search.windows.net"
    assert env.resolved_decision_endpoint() == "https://acct.openai.azure.com"


def test_participant_file_round_trip(tmp_path):
    path = tmp_path / "participant.json"
    assert load_participant(path) is None
    save_participant("cosmic-wombat", path)
    assert load_participant(path) == "cosmic-wombat"
