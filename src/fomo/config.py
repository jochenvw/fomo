"""Workshop configuration.

Precedence (low -> high): YAML config file -> .workshop/environment.json (written from the
Bicep deployment outputs) -> FOMO_* environment variables -> CLI flags.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError

WORKSPACE = Path(".workshop")
ENV_FILE = WORKSPACE / "environment.json"
PARTICIPANT_FILE = WORKSPACE / "participant.json"

# Map Bicep output names -> Environment fields.
BICEP_OUTPUTS = {
    "AZURE_LOCATION": "location",
    "FOUNDRY_ACCOUNT_NAME": "foundry_account_name",
    "FOUNDRY_ACCOUNT_ENDPOINT": "foundry_account_endpoint",
    "FOUNDRY_OPENAI_ENDPOINT": "foundry_openai_endpoint",
    "FOUNDRY_PROJECT_NAME": "project_name",
    "FOUNDRY_PROJECT_ENDPOINT": "project_endpoint",
    "CHAT_DEPLOYMENT": "chat_deployment",
    "EMBEDDING_DEPLOYMENT": "embedding_deployment",
    "DECISION_DEPLOYMENT": "decision_deployment",
    "STORAGE_ACCOUNT_NAME": "storage_account_name",
    "STORAGE_ACCOUNT_ID": "storage_account_id",
    "STORAGE_BLOB_ENDPOINT": "blob_endpoint",
    "SEARCH_SERVICE_NAME": "search_service_name",
    "SEARCH_ENDPOINT": "search_endpoint",
    "SEARCH_SKU": "search_sku",
    "SEARCH_CONNECTION_NAME": "search_connection_name",
    "SEARCH_OPENAPI_CONNECTION_NAME": "search_openapi_connection_name",
}


@dataclass
class Environment:
    """Shared (facilitator- or customer-owned) Azure resources the workshop runs on."""

    subscription_id: str = ""
    tenant_id: str = ""
    resource_group: str = ""
    location: str = "swedencentral"
    topology: str = "shared"  # shared (Topology B) | isolated (Topology A)

    foundry_account_name: str = ""
    foundry_account_endpoint: str = ""
    foundry_openai_endpoint: str = ""
    project_name: str = ""
    project_endpoint: str = ""

    chat_deployment: str = "gpt-5-mini"
    embedding_deployment: str = "text-embedding-3-small"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    decision_deployment: str = "decision-1-1"

    storage_account_name: str = ""
    storage_account_id: str = ""
    blob_endpoint: str = ""

    search_service_name: str = ""
    search_endpoint: str = ""
    search_sku: str = ""
    search_connection_name: str = "fomo-search"
    search_openapi_connection_name: str = "fomo-search-openapi"

    deployment_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- derived ---------------------------------------------------------------
    @property
    def foundry_account_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{self.foundry_account_name}"
        )

    @property
    def search_service_id(self) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.Search/searchServices/{self.search_service_name}"
        )

    def resolved_storage_account_id(self) -> str:
        if self.storage_account_id:
            return self.storage_account_id
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.Storage/storageAccounts/{self.storage_account_name}"
        )

    def resolved_blob_endpoint(self) -> str:
        return self.blob_endpoint or f"https://{self.storage_account_name}.blob.core.windows.net/"

    def resolved_search_endpoint(self) -> str:
        return self.search_endpoint or f"https://{self.search_service_name}.search.windows.net"

    def resolved_account_endpoint(self) -> str:
        return self.foundry_account_endpoint or f"https://{self.foundry_account_name}.services.ai.azure.com"

    def resolved_openai_endpoint(self) -> str:
        return self.foundry_openai_endpoint or f"https://{self.foundry_account_name}.openai.azure.com"

    def resolved_decision_endpoint(self) -> str:
        """Decision API host. Verified: *.openai.azure.com accepts Entra tokens; *.services.ai.azure.com 401s."""
        return self.resolved_openai_endpoint()

    def resolved_project_endpoint(self) -> str:
        return self.project_endpoint or (
            f"{self.resolved_account_endpoint()}/api/projects/{self.project_name}"
        )

    REQUIRED = (
        "subscription_id",
        "resource_group",
        "foundry_account_name",
        "project_name",
        "storage_account_name",
        "search_service_name",
    )

    def missing(self) -> list[str]:
        return [name for name in self.REQUIRED if not getattr(self, name)]

    def require(self) -> Environment:
        missing = self.missing()
        if missing:
            raise ConfigError(
                f"workshop environment incomplete; missing: {', '.join(missing)}",
                fix="run `uv run fomo env pull --resource-group <rg>` or set FOMO_* variables "
                "(see .env.example)",
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ---- loading -------------------------------------------------------------
    @classmethod
    def field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)} - {"extra"}

    def apply(self, values: dict[str, Any]) -> None:
        names = self.field_names()
        for key, value in values.items():
            if value in (None, "", {}):
                continue
            if key == "extra" and isinstance(value, dict):
                self.extra.update(value)
            elif key in names:
                current = getattr(self, key)
                setattr(self, key, int(value) if isinstance(current, int) else value)
            else:
                self.extra[key] = value

    @classmethod
    def load(
        cls,
        *,
        config_file: Path | None = None,
        env_file: Path = ENV_FILE,
        environ: dict[str, str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Environment:
        env = cls()
        if config_file is not None:
            if not config_file.exists():
                raise ConfigError(f"config file not found: {config_file}")
            data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            env.apply(dict(data.get("environment") or data))
        if env_file.exists():
            env.apply(json.loads(env_file.read_text(encoding="utf-8")))
        environ = dict(os.environ) if environ is None else environ
        env.apply({name: environ.get(f"FOMO_{name.upper()}") for name in cls.field_names()})
        if overrides:
            env.apply(overrides)
        return env

    def save(self, path: Path = ENV_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_bicep_outputs(cls, outputs: dict[str, Any], **base: Any) -> Environment:
        env = cls()
        env.apply(base)
        mapped = {}
        lowered = {k.lower(): v for k, v in outputs.items()}  # az CLI mangles output-name casing
        for out_name, field_name in BICEP_OUTPUTS.items():
            if out_name.lower() in lowered:
                value = lowered[out_name.lower()]
                mapped[field_name] = value.get("value") if isinstance(value, dict) else value
        env.apply(mapped)
        return env


def load_participant(path: Path = PARTICIPANT_FILE) -> str | None:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data.get("hackername")
        return str(name) if name else None
    return None


def save_participant(hackername: str, path: Path = PARTICIPANT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hackername": hackername}, indent=2), encoding="utf-8")
