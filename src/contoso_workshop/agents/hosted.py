"""Hosted Metadata Miner: package -> deploy (source ZIP) -> poll active -> RBAC -> invoke.

Deployment path: Foundry hosted agent from source code (`create_version_from_code`,
`remote_build`, runtime python_3_13, invocations protocol 2.0.0). No container registry.
Versions are content-addressed by the service: an identical ZIP + definition returns the
existing version, so rerunning deploy is idempotent.
"""

from __future__ import annotations

import hashlib
import io
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import rbac
from ..azclient import SCOPE_FOUNDRY, request
from ..config import Environment
from ..errors import AgentError, WorkshopError
from ..naming import ParticipantNames

PKG_ROOT = Path(__file__).resolve().parents[1]  # src/contoso_workshop
APP_DIR = Path(__file__).resolve().parent / "miner_app"
RUNTIME = "python_3_13"
PROTOCOL = ("invocations", "2.0.0")
CPU, MEMORY = "0.5", "1Gi"

# Only what the miner needs; nothing that touches Search, agents or the facilitator plane.
VENDORED = (
    "__init__.py",
    "errors.py",
    "azclient.py",
    "data/__init__.py",
    "data/blob.py",
    "data/corpus.py",
    "data/extract.py",
    "decision/__init__.py",
    "decision/client.py",
    "decision/config.py",
    "decision/policy.py",
    "decision/reference_classifier.yaml",
    "miner/__init__.py",
    "miner/pipeline.py",
)
_EPOCH = (2026, 1, 1, 0, 0, 0)


def build_zip(classifier_yaml: Path | None = None) -> tuple[bytes, str]:
    """Deterministic flat ZIP (fixed timestamps, sorted entries) -> (bytes, sha256).

    `classifier_yaml` (a participant's tuned config) replaces the packaged reference config;
    it is validated first so a broken file never reaches the hosted runtime.
    """
    entries: list[tuple[str, bytes]] = [
        ("main.py", (APP_DIR / "main.py").read_bytes()),
        ("requirements.txt", (APP_DIR / "requirements.txt").read_bytes()),
    ]
    entries += [(f"contoso_workshop/{rel}", (PKG_ROOT / rel).read_bytes()) for rel in VENDORED]
    if classifier_yaml is not None:
        from ..decision.config import load_classifier_config

        load_classifier_config(classifier_yaml)
        target = "contoso_workshop/decision/reference_classifier.yaml"
        entries = [(n, classifier_yaml.read_bytes() if n == target else d) for n, d in entries]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data.replace(b"\r\n", b"\n"))
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def environment_variables(env: Environment, names: ParticipantNames) -> dict[str, str]:
    return {
        "FOMO_PARTICIPANT": names.hackername,
        "FOMO_CONTAINER": names.container,
        "FOMO_BLOB_ENDPOINT": env.resolved_blob_endpoint(),
        "FOMO_DECISION_ENDPOINT": env.resolved_decision_endpoint(),
        "FOMO_DECISION_DEPLOYMENT": env.decision_deployment,
        "PYTHONNOUSERSITE": "1",
    }


def required_grants(env: Environment, names: ParticipantNames, principal_id: str) -> list[rbac.Grant]:
    """Least privilege for the agent identity: one container + decision model data plane."""
    return [
        rbac.Grant("Storage Blob Data Contributor",
                   rbac.container_scope(env.resolved_storage_account_id(), names.container), principal_id),
        rbac.Grant("Foundry User", env.foundry_account_id, principal_id),
    ]


@dataclass
class HostedDeployment:
    name: str
    version: str
    status: str
    principal_id: str | None
    sha256: str
    elapsed_s: float = 0.0
    grants: dict[str, str] = field(default_factory=dict)


def _get(obj: Any, key: str) -> Any:
    try:
        return obj[key]
    except (KeyError, TypeError):
        return getattr(obj, key, None)


class HostedMiner:
    def __init__(self, env: Environment, names: ParticipantNames) -> None:
        from azure.ai.projects import AIProjectClient

        from ..azclient import credential

        self.env, self.names = env, names
        self.client = AIProjectClient(endpoint=env.resolved_project_endpoint(), credential=credential())

    @property
    def name(self) -> str:
        return self.names.metadata_miner_agent

    def definition(self) -> Any:
        from azure.ai.projects.models import CodeConfiguration, HostedAgentDefinition, ProtocolVersionRecord

        return HostedAgentDefinition(
            cpu=CPU,
            memory=MEMORY,
            code_configuration=CodeConfiguration(
                runtime=RUNTIME, entry_point=["python", "main.py"], dependency_resolution="remote_build"),
            protocol_versions=[ProtocolVersionRecord(protocol=PROTOCOL[0], version=PROTOCOL[1])],
            environment_variables=environment_variables(self.env, self.names),
        )

    def current(self) -> Any | None:
        try:
            return self.client.agents.get(self.name)
        except Exception:
            return None

    def latest_version(self) -> Any | None:
        agent = self.current()
        if agent is None:
            return None
        latest = _get(_get(agent, "versions"), "latest")
        return latest

    def deploy(self, *, classifier_yaml: Path | None = None, force: bool = False, timeout: float = 900,
               poll: float = 10, log: Any = None) -> HostedDeployment:
        log = log or (lambda msg: None)
        data, sha = build_zip(classifier_yaml)
        start = time.monotonic()
        latest = None if force else self.latest_version()
        if latest is not None and (_get(latest, "metadata") or {}).get("code_sha256") == sha[:32] \
                and str(_get(latest, "status")) == "active":
            ident = _get(latest, "instance_identity")
            log(f"{self.name} v{_get(latest, 'version')} already runs this code (sha256 {sha[:12]}); reusing")
            return HostedDeployment(self.name, str(_get(latest, "version")), "active",
                                    _get(ident, "principal_id") if ident is not None else None, sha, 0.0)
        try:
            created = self.client.agents.create_version_from_code(
                agent_name=self.name, definition=self.definition(),
                code=(f"{self.name}.zip", data, "application/zip"),  # type: ignore[arg-type] # multipart tuple, verified live
                code_zip_sha256=sha,
                description=(f"FOMO Metadata Miner for {self.names.hackername} "
                             f"(container {self.names.container})"),
                metadata={"workshop": "fomo", "participant": self.names.hackername,
                          "container": self.names.container, "code_sha256": sha[:32]},
            )
        except Exception as exc:
            raise AgentError(f"hosted agent deploy failed: {exc}"[:900],
                             fix="check `fomo preflight` (Foundry User on the project; hosted agents "
                             "available in this region)", owner="facilitator") from exc
        version = str(created.version)
        log(f"deployed {self.name} v{version} (zip sha256 {sha[:12]}); waiting for active")
        status, details = str(_get(created, "status") or ""), created
        while status not in ("active", "failed"):
            if time.monotonic() - start > timeout:
                raise AgentError(f"{self.name} v{version} not active after {timeout:.0f}s (status {status})",
                                 fix="rerun the same command; deployment continues server-side")
            time.sleep(poll)
            details = self.client.agents.get_version(agent_name=self.name, agent_version=version)
            new = str(_get(details, "status") or "")
            if new != status:
                log(f"{self.name} v{version}: {new}")
            status = new
        if status == "failed":
            raise AgentError(f"{self.name} v{version} provisioning failed: {_get(details, 'error')}"[:900],
                             fix="see docs/troubleshooting.md#hosted-agent; `fomo miner run --local` is a "
                             "labeled fallback that does NOT meet the hosted requirement")
        ident = _get(details, "instance_identity")
        principal = _get(ident, "principal_id") if ident is not None else None
        if not principal:
            agent = self.current()
            ident = _get(agent, "instance_identity") if agent is not None else None
            principal = _get(ident, "principal_id") if ident is not None else None
        return HostedDeployment(self.name, version, status, principal, sha, round(time.monotonic() - start, 1))

    def grant(self, dep: HostedDeployment) -> dict[str, str]:
        if not dep.principal_id:
            raise AgentError(f"{self.name} has no agent identity yet; cannot grant storage access",
                             fix="rerun deploy in a minute")
        for g in required_grants(self.env, self.names, dep.principal_id):
            dep.grants[g.role] = rbac.ensure(g)
        return dep.grants

    def invoke(self, payload: dict[str, Any], *, timeout: float = 600) -> dict[str, Any]:
        url = f"{self.env.resolved_project_endpoint()}/agents/{self.name}/endpoint/protocols/invocations"
        try:
            resp = request("POST", f"{url}?api-version=v1", scope=SCOPE_FOUNDRY, json_body=payload,
                           timeout=timeout, retries=1, ok=(200,))
        except WorkshopError as exc:
            raise AgentError(f"hosted miner invocation failed: {exc}"[:1200],
                             fix="424 session_not_ready: wait and retry; 403 AuthorizationFailure/network "
                             "security perimeter: the agent cannot reach storage (see docs/troubleshooting.md, "
                             "hosted networking); 403 PermissionDenied: role assignments still propagating "
                             "(wait 1-5 min)") from exc
        out: dict[str, Any] = resp.json()
        out.setdefault("session_id", resp.headers.get("x-agent-session-id"))
        return out

    def delete(self) -> bool:
        try:
            self.client.agents.delete(self.name, force=True)
            return True
        except Exception:
            return False
