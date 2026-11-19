"""Preflight: can we actually run the workshop? Read-only by default.

Every check returns PASS / WARNING / BLOCKED (or INFO / SKIPPED) with evidence, the exact
scope/principal, a fix and who must act. Role lists are only *indicators*; functional
read-only probes (list blobs, list indexes, list agents, indexer history) are the proof.
Model smoke calls are opt-in (`--smoke`) because they cost money.
"""

from __future__ import annotations

import fnmatch
import os
import platform
import re
import shutil
import subprocess  # nosec B404 - fixed argument lists only
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import capacity
from ..azclient import ARM, SCOPE_COGNITIVE, arm, az, credential, request
from ..config import Environment
from ..errors import WorkshopError

PASS, WARNING, BLOCKED, INFO, SKIPPED = "PASS", "WARNING", "BLOCKED", "INFO", "SKIPPED"
REPO_ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = ("Microsoft.CognitiveServices", "Microsoft.Search", "Microsoft.Storage")
ROLE_NAMES = {
    "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1": "Storage Blob Data Reader",
    "ba92f5b4-2d11-453d-a403-e96b0029c9fe": "Storage Blob Data Contributor",
    "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd": "Cognitive Services OpenAI User",
    "53ca6127-db72-4b80-b1b0-d745d6d5456d": "Foundry User",
    "1407120a-92aa-4202-b7e9-c0e197c71c8f": "Search Index Data Reader",
    "8ebe5a00-799e-43f5-93ac-243d3dce84a7": "Search Index Data Contributor",
    "7ca78c08-252a-4471-8644-bb5ff32d4ba0": "Search Service Contributor",
    "f58310d9-a9f6-439a-9e8d-f62e7b41a168": "Role Based Access Control Administrator",
    "8e3af657-a8ff-443c-a75c-2fe8c4bcb635": "Owner",
    "b24988ac-6180-42a0-ab88-20f7382dd24c": "Contributor",
}


@dataclass
class Check:
    id: str
    category: str
    title: str
    status: str
    evidence: str = ""
    fix: str = ""
    owner: str = "participant"
    required: bool = True
    cost: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    generated_at: str
    participants: int
    checks: list[Check] = field(default_factory=list)
    capacity: dict[str, Any] = field(default_factory=dict)
    sensitive: set[str] = field(default_factory=set, repr=False)  # masked in the shareable copy

    def add(self, *args: Any, **kw: Any) -> Check:
        c = Check(*args, **kw)
        self.checks.append(c)
        return c

    @property
    def verdict(self) -> str:
        if any(c.status == BLOCKED and c.required for c in self.checks):
            return BLOCKED
        if any(c.status in (WARNING, BLOCKED) for c in self.checks):
            return WARNING
        return PASS

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"generated_at": self.generated_at, "verdict": self.verdict, "counts": self.counts(),
                "participants_planned": self.participants, "capacity": self.capacity,
                "checks": [asdict(c) for c in self.checks]}


# ---- helpers ---------------------------------------------------------------------------------
def _run(*args: str, timeout: float = 30) -> tuple[int, str]:
    exe = shutil.which(args[0])
    if exe is None:
        return 127, ""
    try:
        proc = subprocess.run([exe, *args[1:]], capture_output=True, text=True, timeout=timeout,  # noqa: S603 # nosec B603
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or proc.stderr).strip()


def _version(text: str) -> str:
    m = re.search(r"\d+\.\d+(\.\d+)?", text)
    return m.group(0) if m else text[:40]


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def _probe(report: Report, cid: str, category: str, title: str, fn: Callable[[], tuple[str, str]], *,
           fix: str = "", owner: str = "participant", required: bool = True) -> Check:
    """Run one probe; exceptions become BLOCKED (permission) or WARNING with the real error text."""
    try:
        status, evidence = fn()
    except WorkshopError as exc:
        status = BLOCKED if required else WARNING
        evidence = str(exc)[:500]
        fix = fix or (exc.fix or "")
        owner = exc.owner or owner
    except Exception as exc:
        status = BLOCKED if required else WARNING
        evidence = f"{type(exc).__name__}: {str(exc)[:450]}"
    return report.add(cid, category, title, status, evidence, fix if status != PASS else "", owner, required)


def _matches(action: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(action.lower(), p.lower()) for p in patterns)


def effective(perms: list[dict[str, Any]], action: str, *, data: bool = False) -> bool:
    """Azure permissions API semantics: allowed if any entry grants it and that entry doesn't exclude it."""
    key, notkey = ("dataActions", "notDataActions") if data else ("actions", "notActions")
    return any(_matches(action, p.get(key, [])) and not _matches(action, p.get(notkey, [])) for p in perms)


def permissions(scope: str) -> list[dict[str, Any]]:
    resp = arm("GET", f"{scope}/providers/Microsoft.Authorization/permissions", "2022-04-01")
    return list(resp.json().get("value", []))


def role_assignments(scope: str, principal_id: str) -> list[str]:
    resp = arm("GET", f"{scope}/providers/Microsoft.Authorization/roleAssignments", "2022-04-01",
               params={"$filter": f"principalId eq '{principal_id}'"})
    out = []
    for ra in resp.json().get("value", []):
        rid = ra["properties"]["roleDefinitionId"].rsplit("/", 1)[-1]
        out.append(ROLE_NAMES.get(rid, rid))
    return out


# ---- local -----------------------------------------------------------------------------------
def local_checks(report: Report) -> None:
    cat = "local"
    shell = "PowerShell" if os.environ.get("PSMODULEPATH") and os.name == "nt" else os.environ.get("SHELL", "?")
    report.add("local.os", cat, "OS and shell", INFO, f"{platform.platform()}; shell {shell}", required=False)
    rc, out = _run("git", "--version")
    report.add("local.git", cat, "Git", PASS if rc == 0 else BLOCKED, out or "git not found",
               "" if rc == 0 else "install Git: https://git-scm.com/downloads")
    py = sys.version.split()[0]
    report.add("local.python", cat, "Python (via uv)", PASS if sys.version_info >= (3, 12) else BLOCKED,
               f"Python {py} at {sys.executable}", "" if sys.version_info >= (3, 12) else "`uv python install 3.13`")
    rc, out = _run("uv", "--version")
    report.add("local.uv", cat, "uv", PASS if rc == 0 else BLOCKED, out or "uv not found",
               "" if rc == 0 else "install uv: https://docs.astral.sh/uv/getting-started/installation/")
    rc, out = _run("az", "version", "-o", "json", timeout=60)
    if rc != 0:
        report.add("local.az", cat, "Azure CLI", BLOCKED, "az not found", "install: https://aka.ms/azcli")
    else:
        import json

        info = json.loads(out)
        ver = info.get("azure-cli", "?")
        status = PASS if _vtuple(ver) >= (2, 60) else WARNING
        report.add("local.az", cat, "Azure CLI", status, f"azure-cli {ver}; extensions: "
                   + (", ".join(f"{k} {v}" for k, v in (info.get("extensions") or {}).items()) or "none"),
                   "" if status == PASS else "`az upgrade` (2.60+ recommended)", required=True)
    rc, out = _run("az", "bicep", "version", timeout=60)
    report.add("local.bicep", cat, "Bicep (facilitator infra only)", PASS if rc == 0 else WARNING,
               out.splitlines()[0] if out else "not installed",
               "" if rc == 0 else "`az bicep install` (only needed to deploy infra)", owner="facilitator",
               required=False)
    rc, out = _run("azd", "version")
    report.add("local.azd", cat, "azd (not needed)", INFO, (out.splitlines()[0] if rc == 0 else "not installed")
               + "; hosted agents deploy from a source ZIP via the Foundry SDK, so azd is not required",
               required=False)
    rc, out = _run("docker", "--version")
    report.add("local.docker", cat, "Docker (not needed)", INFO, (out if rc == 0 else "not installed")
               + "; no local image build: Foundry builds the hosted agent remotely", required=False)
    rc, out = _run("copilot", "--version")
    report.add("local.copilot", cat, "GitHub Copilot CLI (optional front end)", PASS if rc == 0 else WARNING,
               (f"copilot {_version(out)}; sign-in state is checked by copilot itself (/login)" if rc == 0
                else "copilot not found; every operation also runs as `uv run fomo ...`"),
               "" if rc == 0 else "install: `npm install -g @github/copilot` (Node 22+) or use Copilot in VS Code "
               "agent mode; see docs/prerequisites.md", required=False)
    skills = sorted(REPO_ROOT.glob(".github/skills/*/SKILL.md"))
    bad = [s.parent.name for s in skills if not _skill_ok(s)]
    status = PASS if len(skills) >= 3 and not bad else WARNING
    report.add("local.skills", cat, "Copilot skills in this repo", status,
               f"{len(skills)} skill(s): {', '.join(s.parent.name for s in skills)}"
               + (f"; invalid front matter: {', '.join(bad)}" if bad else ""),
               "" if status == PASS else "run from the repo root; check SKILL.md front matter", required=False)


def _skill_ok(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.S)
    if not m:
        return False
    import yaml

    meta = yaml.safe_load(m.group(1)) or {}
    return bool(meta.get("name")) and bool(meta.get("description")) and meta["name"] == path.parent.name


# ---- azure -----------------------------------------------------------------------------------
def azure_checks(report: Report, env: Environment, *, participant: str | None, smoke: bool) -> None:
    try:
        acct = az("account", "show")
    except WorkshopError as exc:
        report.add("azure.login", "azure-context", "Azure CLI sign-in", BLOCKED, str(exc)[:500],
                   exc.fix or "verify Azure connectivity; if the session expired, run `az login`, then retry",
                   owner=exc.owner)
        return
    if not acct:
        report.add("azure.login", "azure-context", "Azure CLI sign-in", BLOCKED, "not signed in",
                   "`az login` then `az account set -s <subscription>`")
        return
    user = acct.get("user") or {}
    report.sensitive.update(s for s in (acct.get("name"), user.get("name"), acct.get("tenantDisplayName")) if s)
    report.add("azure.login", "azure-context", "Azure CLI sign-in", PASS,
               f"{user.get('type')} {user.get('name')} in tenant {acct.get('tenantId')}")
    if env.tenant_id:
        tenant_match = acct.get("tenantId") == env.tenant_id
        report.add("azure.tenant", "azure-context", "CLI directory == workshop directory",
                   PASS if tenant_match else BLOCKED,
                   f"az: {acct.get('tenantId')}; workshop: {env.tenant_id}",
                   "" if tenant_match else
                   f"`az login --tenant {env.tenant_id}` then `az account set -s {env.subscription_id}`")
        if not tenant_match:
            return
    sub_ok = acct.get("state") == "Enabled"
    report.add("azure.subscription", "azure-context", "Subscription", PASS if sub_ok else BLOCKED,
               f"{acct.get('name')} ({acct.get('id')}) state={acct.get('state')}",
               "" if sub_ok else "use an enabled subscription", owner="admin")
    if not sub_ok:
        return
    if env.subscription_id:
        match = acct.get("id") == env.subscription_id
        report.add("azure.target", "azure-context", "CLI subscription == workshop subscription",
                   PASS if match else BLOCKED,
                   f"az: {acct.get('id')}; workshop: {env.subscription_id}, RG {env.resource_group}",
                   "" if match else f"`az account set -s {env.subscription_id}`")
        if not match:
            return
    if env.subscription_id and env.resource_group:
        group_exists = None

        def group_probe() -> tuple[str, str]:
            nonlocal group_exists
            rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"
            response = arm("GET", rg, "2021-04-01", ok=(200, 404))
            group_exists = response.status_code == 200
            return ((PASS, f"{env.resource_group} exists in the configured subscription") if group_exists else
                    (BLOCKED, f"saved resource group {env.resource_group} was not found (HTTP 404)"))

        check = _probe(report, "azure.resource-group", "azure-context", "Remembered resource group", group_probe,
                       fix="group existence is unknown; verify connectivity and access to the confirmed subscription "
                       "and group, then rerun; do not recreate on a permission or connection error")
        check.data["missing"] = None if group_exists is None else not group_exists
        if group_exists is False:
            check.fix = (
                "confirm tenant/subscription first; choose to recreate this empty group, select an existing group, "
                "create a different group, or defer to the facilitator. Review "
                f"`uv run fomo env select --tenant {env.tenant_id or acct.get('tenantId') or '<tenant-id>'} "
                f"--subscription {env.subscription_id} --resource-group {env.resource_group} "
                "--create-resource-group --location <region> --plan`; obtain approval before --apply. "
                "Recreating the group does not restore its former services or data"
            )
            check.data["recovery_options"] = ["recreate", "select-existing", "create-new", "defer"]
        if check.status != PASS:
            return
    missing = env.missing()
    if missing:
        _unconfigured(report, env, missing)
        return
    sub = f"/subscriptions/{env.subscription_id}"
    rg = f"{sub}/resourceGroups/{env.resource_group}"
    _providers(report, sub)
    shared = _resources(report, env, rg)
    _models(report, env)
    _policy(report, env, rg, shared)
    if participant:
        _participant_resources(report, env, participant)
    else:
        report.add("resources.participant", "resources", "Participant resources", INFO,
                   "no participant selected; container/index existence and container-scoped RBAC not checked",
                   required=False)
    _control_plane(report, env, rg, shared, participant=participant)
    _pim(report, rg)
    _data_plane(report, env, participant)
    _identities(report, env, shared)
    _capacity(report, env)
    if smoke:
        _smoke(report, env)
    else:
        report.add("smoke.models", "models", "Live model calls (chat, embedding, decision)", SKIPPED,
                   "opt-in: `fomo preflight --smoke` makes 3 tiny paid calls (< $0.001)", required=False)


def _unconfigured(report: Report, env: Environment, missing: list[str]) -> None:
    """Group known, services not configured: look inside (read-only) and say exactly what to do next."""
    from ..discover import advise, inventory, resolve

    title = "Workshop environment"
    evidence = f"missing: {', '.join(missing)}"
    if not (env.subscription_id and env.resource_group):
        report.add("azure.environment", "azure-context", title, BLOCKED, evidence,
                   "confirm tenant, subscription and resource group: `uv run fomo env select ... --plan`")
        return
    try:
        inv = inventory(env.subscription_id, env.resource_group)
    except WorkshopError as exc:
        report.add("azure.environment", "azure-context", title, BLOCKED,
                   f"{evidence}; could not list {env.resource_group}: {str(exc)[:300]}",
                   "result unknown, not proof the group is empty: verify connectivity and at least Reader on the "
                   "group, then rerun; or ask internal IT for the Foundry account/project, Storage and Search names",
                   owner="facilitator")
        return
    rg = f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"
    actions = ("Microsoft.CognitiveServices/accounts/write", "Microsoft.Storage/storageAccounts/write",
               "Microsoft.Search/searchServices/write", "Microsoft.Resources/deployments/write")
    try:
        perms = permissions(rg)
        granted = [a for a in actions if effective(perms, a)]
        can_create: bool | None = len(granted) == len(actions)
    except WorkshopError:
        granted, can_create = [], None
    choices = {"foundry": env.foundry_account_name or None, "project": env.project_name or None,
               "storage": env.storage_account_name or None, "search": env.search_service_name or None}
    _, problems = resolve(inv, choices)
    fix, owner = advise(env, inv, problems, can_create)
    report.add("azure.discovery", "azure-context", "Services in the resource group (read-only)", INFO,
               inv.summary() + ("; " + "; ".join(f"{r}: {p}" for r, p in problems.items()) if problems else ""),
               required=False, data={"problems": problems, "deployment": inv.deployment,
                                     "foundry": [a["name"] for a in inv.foundry], "projects": inv.projects,
                                     "storage": [s["name"] for s in inv.storage],
                                     "search": [s["name"] for s in inv.search]})
    report.add("rbac.provision-services", "control-plane", "Can create services in the group (RBAC indicator)",
               INFO if can_create is not None else WARNING,
               "unknown (permissions query failed)" if can_create is None else
               f"allowed: {', '.join(a.split('/')[-2] for a in granted) or 'none'}; only relevant if IT has not "
               "provisioned the services; policy and quota still apply", required=False,
               owner="participant" if can_create else "facilitator")
    report.add("azure.environment", "azure-context", title, BLOCKED, evidence, fix, owner=owner)


def _providers(report: Report, sub: str) -> None:
    for ns in PROVIDERS:
        def fn(ns: str = ns) -> tuple[str, str]:
            state = arm("GET", f"{sub}/providers/{ns}", "2021-04-01").json().get("registrationState")
            return (PASS if state == "Registered" else BLOCKED), f"{ns}: {state}"
        _probe(report, f"providers.{ns}", "providers", f"Resource provider {ns}", fn,
               fix=f"`az provider register -n {ns}` (needs subscription-level */register/action)", owner="admin")


def _resources(report: Report, env: Environment, rg: str) -> dict[str, Any]:
    shared: dict[str, Any] = {}

    def get(path: str, api: str) -> dict[str, Any]:
        response = arm("GET", path, api, ok=(200, 404))
        if response.status_code == 404:
            shared.setdefault("missing", []).append(path)
            raise WorkshopError(f"resource not found: {path}", owner="facilitator")
        return dict(response.json())

    def foundry() -> tuple[str, str]:
        a = get(env.foundry_account_id, "2025-06-01")
        shared["foundry"] = a
        props = a.get("properties", {})
        ok = props.get("provisioningState") == "Succeeded" and props.get("allowProjectManagement")
        inj = props.get("networkInjections")
        return (PASS if ok else BLOCKED), (f"{a.get('kind')} {a['name']} in {a.get('location')}, "
                                           f"projects enabled={props.get('allowProjectManagement')}, "
                                           f"publicNetworkAccess={props.get('publicNetworkAccess')}"
                                           + (f", networkInjections={len(inj)} entr(y/ies)" if inj else ""))
    _probe(report, "resources.foundry", "resources", "Foundry account (new resource model, no hub)", foundry,
           fix="facilitator: `fomo infra deploy --plan`", owner="facilitator")

    def project() -> tuple[str, str]:
        p = get(f"{env.foundry_account_id}/projects/{env.project_name}", "2025-06-01")
        shared["project"] = p
        return PASS, f"project {env.project_name}; identity {(p.get('identity') or {}).get('principalId')}"
    _probe(report, "resources.project", "resources", "Foundry project", project, owner="facilitator")

    def connection() -> tuple[str, str]:
        c = get(f"{env.foundry_account_id}/projects/{env.project_name}/connections/{env.search_connection_name}",
                "2025-06-01")
        props = c.get("properties", {})
        ok = props.get("category") == "CognitiveSearch" and props.get("authType") == "AAD"
        return (PASS if ok else WARNING), (f"{env.search_connection_name}: {props.get('category')} -> "
                                           f"{props.get('target')} auth={props.get('authType')}")
    _probe(report, "resources.connection", "resources", "Project -> Search connection (Entra ID)", connection,
           fix="facilitator: redeploy infra (search-connection module)", owner="facilitator")

    def search() -> tuple[str, str]:
        s = get(env.search_service_id, "2025-05-01")
        shared["search"] = s
        props = s.get("properties", {})
        auth = "RBAC only" if props.get("disableLocalAuth") else "keys enabled"
        return PASS, (f"{s['name']} sku={s['sku']['name']} replicas={props.get('replicaCount')} "
                      f"partitions={props.get('partitionCount')} {auth} "
                      f"publicNetworkAccess={props.get('publicNetworkAccess')} "
                      f"identity={(s.get('identity') or {}).get('type')}")
    _probe(report, "resources.search", "resources", "Azure AI Search service", search, owner="facilitator")

    def storage() -> tuple[str, str]:
        s = get(env.resolved_storage_account_id(), "2023-05-01")
        shared["storage"] = s
        props = s.get("properties", {})
        return PASS, (f"{s['name']} publicNetworkAccess={props.get('publicNetworkAccess')} "
                      f"sharedKey={props.get('allowSharedKeyAccess')} anonymous={props.get('allowBlobPublicAccess')} "
                      f"defaultAction={(props.get('networkAcls') or {}).get('defaultAction')}")
    _probe(report, "resources.storage", "resources", "Storage account", storage, owner="facilitator")
    return shared


def _models(report: Report, env: Environment) -> None:
    try:
        deps = arm("GET", f"{env.foundry_account_id}/deployments", "2025-06-01").json().get("value", [])
    except WorkshopError as exc:
        report.add("models.list", "models", "Model deployments", BLOCKED, str(exc)[:400], owner="facilitator")
        return
    by_name = {d["name"]: d for d in deps}
    for role, name, expect in (("chat", env.chat_deployment, None), ("embedding", env.embedding_deployment, None),
                               ("decision", env.decision_deployment, "decision")):
        d = by_name.get(name)
        if d is None:
            report.add(f"models.{role}", "models", f"{role} deployment '{name}'", BLOCKED,
                       f"not found; deployments: {', '.join(by_name) or 'none'}",
                       "facilitator: `fomo infra deploy --plan` (models are deployed ahead of the workshop)",
                       owner="facilitator", data={"missing": True})
            continue
        model = d["properties"]["model"]
        sku = d.get("sku", {})
        ok = d["properties"].get("provisioningState") == "Succeeded"
        if expect and expect not in model.get("name", "").lower():
            report.add(f"models.{role}", "models", f"{role} deployment '{name}'", BLOCKED,
                       f"model is {model.get('name')}, not Microsoft-Decision-1",
                       "never substitute a generative model for the decision model", owner="facilitator")
            continue
        report.add(f"models.{role}", "models", f"{role} deployment '{name}'", PASS if ok else BLOCKED,
                   f"{model.get('format')}/{model.get('name')} {model.get('version')} sku={sku.get('name')} "
                   f"capacity={sku.get('capacity')}", owner="facilitator",
                   data={"capacity": sku.get("capacity"), "model": model.get("name")})


def _policy(report: Report, env: Environment, rg: str, shared: dict[str, Any]) -> None:
    storage = shared.get("storage") or {}
    props = storage.get("properties", {})
    pna = props.get("publicNetworkAccess")
    perimeter = pna == "SecuredByPerimeter"
    tags = storage.get("tags") or {}
    if pna == "Disabled" or perimeter:
        report.add("network.hosted-egress", "networking", "Hosted agent -> Blob reachability", BLOCKED,
                   f"storage publicNetworkAccess={pna}. Verified: Foundry hosted agents reach Storage over public "
                   "egress; network-perimeter inbound rules and post-creation VNet injection do not admit them",
                   "facilitator: deploy with storageNetworkMode='public' (Entra-only, no keys) or create the Foundry "
                   "account with hosted-agent VNet injection from day one; see docs/troubleshooting.md",
                   owner="facilitator")
    elif pna == "Enabled":
        exempt = tags.get("SecurityControl") == "Ignore"
        report.add("network.hosted-egress", "networking", "Hosted agent -> Blob reachability",
                   PASS if exempt else WARNING,
                   "publicNetworkAccess=Enabled with Entra-only auth"
                   + ("; tag SecurityControl=Ignore exempts it from the tenant's PNA Modify policy" if exempt else
                      "; no policy exemption tag: a tenant 'modify publicNetworkAccess' policy may disable it later"),
                   "" if exempt else "facilitator: check `az policy state list` for the storage account",
                   owner="facilitator")

    def noncompliant() -> tuple[str, str]:
        url = (f"{ARM}{rg}/providers/Microsoft.PolicyInsights/policyStates/latest/queryResults"
               f"?api-version=2019-10-01&$top=200&$filter=complianceState eq 'NonCompliant'")
        rows = request("POST", url, scope="https://management.azure.com/.default", timeout=90).json().get("value", [])
        refs = sorted({r.get("policyDefinitionReferenceId") or r.get("policyDefinitionName") for r in rows})
        net = [r for r in refs if r and re.search(r"(?i)public|network|private|firewall|localauth", r)]
        status = WARNING if net else PASS
        return status, (f"{len(rows)} non-compliant policy states in the RG"
                        + (f"; network/auth related: {', '.join(net[:8])}" if net else ""))
    _probe(report, "policy.states", "policy", "Azure Policy (non-compliant states in RG)", noncompliant,
           owner="facilitator", required=False)

    def deny() -> tuple[str, str]:
        rows = arm("GET", f"{rg}/providers/Microsoft.Authorization/denyAssignments", "2022-04-01").json()
        n = len(rows.get("value", []))
        return (WARNING if n else PASS), f"{n} deny assignment(s) apply to the resource group"
    _probe(report, "policy.deny", "policy", "Deny assignments", deny, owner="admin", required=False)


def _participant_resources(report: Report, env: Environment, participant: str) -> None:
    from ..data.blob import ParticipantContainer
    from ..naming import ParticipantNames
    from ..search.client import SearchClient

    names = ParticipantNames(participant)
    container = ParticipantContainer(env.resolved_blob_endpoint(), names.container, credential())
    search = SearchClient(env)
    probes: list[tuple[str, str, Callable[[], bool]]] = [
        ("container", f"Blob container {names.container}", container.exists),
    ]
    for kind, name in (
        ("indexes", names.raw_index), ("indexes", names.smart_index),
        ("indexers", names.raw_indexer), ("indexers", names.smart_indexer),
        ("skillsets", names.raw_skillset), ("skillsets", names.smart_skillset),
        ("datasources", names.datasource),
    ):
        def exists_search(kind: str = kind, name: str = name) -> bool:
            return search.get(kind, name) is not None
        probes.append((name, f"Search {kind}/{name}", exists_search))
    for key, title, exists in probes:
        def fn(exists: Callable[[], bool] = exists) -> tuple[str, str]:
            return (PASS, "exists") if exists() else (BLOCKED, "not found")
        check = _probe(report, f"resources.{key}", "resources", title, fn,
                       fix="resolve access with the facilitator/admin, then rerun preflight", owner="facilitator")
        check.data["missing"] = check.evidence == "not found"


def _creation_checks(report: Report, env: Environment, rg: str, shared: dict[str, Any],
                     read: Callable[[str], list[dict[str, Any]] | None], participant: str | None) -> None:
    from ..naming import ParticipantNames

    missing_paths = shared.get("missing", [])
    foundry_scope = rg if env.foundry_account_id in missing_paths else env.foundry_account_id
    project_id = f"{env.foundry_account_id}/projects/{env.project_name}"
    project_scope = foundry_scope if project_id in missing_paths else project_id
    storage_scope = rg if env.resolved_storage_account_id() in missing_paths else env.resolved_storage_account_id()
    search_scope = rg if env.search_service_id in missing_paths else env.search_service_id
    specs = [
        ("storage", "resources.storage", rg, "Microsoft.Storage/storageAccounts/write",
         env.resolved_storage_account_id()),
        ("search", "resources.search", rg, "Microsoft.Search/searchServices/write", env.search_service_id),
        ("foundry", "resources.foundry", rg, "Microsoft.CognitiveServices/accounts/write", env.foundry_account_id),
        ("project", "resources.project", foundry_scope,
         "Microsoft.CognitiveServices/accounts/projects/write", project_id),
        ("connection", "resources.connection", project_scope,
         "Microsoft.CognitiveServices/accounts/projects/connections/write",
         f"{project_id}/connections/{env.search_connection_name}"),
    ]
    for role in ("chat", "embedding", "decision"):
        specs.append((f"model-{role}", f"models.{role}", foundry_scope,
                      "Microsoft.CognitiveServices/accounts/deployments/write", ""))
    if participant:
        names = ParticipantNames(participant)
        specs.append(("container", "resources.container", storage_scope,
                      "Microsoft.Storage/storageAccounts/blobServices/containers/write", ""))
        for kind, name in (
            ("indexes", names.raw_index), ("indexes", names.smart_index),
            ("indexers", names.raw_indexer), ("indexers", names.smart_indexer),
            ("skillsets", names.raw_skillset), ("skillsets", names.smart_skillset),
            ("datasources", names.datasource),
        ):
            specs.append((name, f"resources.{name}", search_scope,
                          f"Microsoft.Search/searchServices/{kind}/write", ""))
    by_id = {c.id: c for c in report.checks}
    for key, resource_id, scope, action, path in specs:
        resource = by_id.get(resource_id)
        missing = path in missing_paths if path else bool(resource and resource.data.get("missing"))
        perms = read(scope)
        allowed = effective(perms, action) if perms is not None else None
        owner = "participant" if allowed else "facilitator"
        plan = (f"uv run fomo provision --participant {participant} --no-smoke --plan"
                if participant and (key == "container" or key.startswith(f"{participant}-"))
                else "uv run fomo infra deploy --parameters <file>.bicepparam --plan")
        if allowed:
            fix = (f"missing, self-provisionable subject to approval: review `{plan}`; obtain explicit approval "
                   "before applying. Full-template permissions, service identities, policy and quota must also pass")
        elif allowed is False:
            fix = (f"ask the facilitator/admin to provision this resource for you before the workshop; "
                   f"{action} is not granted at {scope}")
        else:
            fix = ("creation permissions unknown: ask the facilitator/admin to verify access or provision "
                   "this resource for you; do not assume self-provisioning is possible")
        if missing and resource:
            resource.fix, resource.owner = fix, owner
        status = (PASS if allowed else BLOCKED) if missing else (
            WARNING if allowed is None else PASS if allowed else INFO)
        report.add(f"rbac.provision-{key}", "control-plane",
                   f"Can create {resource.title if resource else key} (RBAC indicator)", status,
                   f"{action} at {scope}: "
                   + ("unknown" if allowed is None else "allowed" if allowed else "not granted")
                   + ("; resource is missing" if missing else "; creation only needed if missing")
                   + "; RBAC indicator only; conditions, policy and quota still apply",
                   fix if missing or allowed is None else "", owner=owner, required=missing)


def _control_plane(report: Report, env: Environment, rg: str, shared: dict[str, Any], *,
                   participant: str | None = None) -> None:
    from ..naming import ParticipantNames

    cat = "control-plane"
    cache: dict[str, list[dict[str, Any]] | None] = {}

    def read(scope: str) -> list[dict[str, Any]] | None:
        if scope not in cache:
            try:
                cache[scope] = permissions(scope)
            except WorkshopError as exc:
                cache[scope] = None
                report.add(f"rbac.permissions-{len(cache)}", cat, "Effective permissions query", WARNING,
                           f"{scope}: {str(exc)[:300]}",
                           "ask the facilitator/admin to verify permissions; resolve missing/inaccessible resources",
                           owner="facilitator", required=False)
        return cache[scope]

    _creation_checks(report, env, rg, shared, read, participant)
    rg_perms = read(rg)
    facilitator_actions = {
        "Microsoft.Resources/deployments/write": "deploy Bicep",
        "Microsoft.CognitiveServices/accounts/write": "create/update Foundry",
        "Microsoft.CognitiveServices/accounts/deployments/write": "create model deployments",
        "Microsoft.Search/searchServices/write": "create/update Search",
        "Microsoft.Storage/storageAccounts/write": "create/update Storage",
    }
    have = [d for a, d in facilitator_actions.items() if rg_perms is not None and effective(rg_perms, a)]
    report.add("rbac.facilitator", cat, "Can provision shared infrastructure (facilitator)",
               WARNING if rg_perms is None else PASS if len(have) == len(facilitator_actions) else INFO,
               f"allowed: {', '.join(have) or ('unknown' if rg_perms is None else 'none')} "
               "(participants do not need these when shared infrastructure exists)",
               "ask the facilitator/admin to verify infrastructure deployment permissions" if rg_perms is None else "",
               required=False,
               owner="facilitator")
    storage_scope = env.resolved_storage_account_id()
    if participant:
        container_scope = f"{storage_scope}/blobServices/default/containers/{ParticipantNames(participant).container}"
        container = next((c for c in report.checks if c.id == "resources.container"), None)
        if not (container and container.data.get("missing")):
            storage_scope = container_scope
    if env.resolved_storage_account_id() in shared.get("missing", []):
        storage_scope = rg
    st_perms = read(storage_scope)
    foundry_scope = rg if env.foundry_account_id in shared.get("missing", []) else env.foundry_account_id
    fa_perms = read(foundry_scope)
    grant_st = effective(st_perms, "Microsoft.Authorization/roleAssignments/write") if st_perms is not None else None
    grant_fa = effective(fa_perms, "Microsoft.Authorization/roleAssignments/write") if fa_perms is not None else None
    conditional = any(p.get("condition") for p in (st_perms or []) + (fa_perms or []))
    report.add("rbac.grant-agent", cat, "Can grant the hosted agent identity its roles",
               PASS if grant_st and grant_fa else BLOCKED,
               f"roleAssignments/write at {storage_scope}={grant_st}, {foundry_scope}={grant_fa} "
               "(None=unknown)"
               + ("; ABAC-conditioned (limited to Storage Blob Data Contributor / Foundry User for service "
                  "principals)" if conditional else "")
               + "; Contributor alone cannot assign roles",
               "" if grant_st and grant_fa else "facilitator: add your object id to `participants` in the Bicep "
               "parameters (constrained RBAC Administrator) or have an Owner run `fomo miner deploy`",
               owner="facilitator")
    checks = (
        ("rbac.blob-read", "blob read", st_perms,
         "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read", True),
        ("rbac.blob", "blob write/metadata (Storage Blob Data Contributor)", st_perms,
         "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write", True),
        ("rbac.agents", "create/invoke Foundry agents", fa_perms,
         "Microsoft.CognitiveServices/accounts/AIServices/agents/write", True),
        ("rbac.decision", "call the decision API", fa_perms,
         "Microsoft.CognitiveServices/accounts/AIServices/providers/action", True),
        ("rbac.search-read", "Search document read",
         read(rg if env.search_service_id in shared.get("missing", []) else env.search_service_id),
         "Microsoft.Search/searchServices/indexes/documents/read", True),
        ("rbac.search-documents", "Search document write (Search Index Data Contributor)",
         read(rg if env.search_service_id in shared.get("missing", []) else env.search_service_id),
         "Microsoft.Search/searchServices/indexes/documents/write", True),
    )
    for cid, what, perms, action, data in checks:
        ok = perms is not None and effective(perms, action, data=data)
        report.add(cid, cat, f"Data-plane permission: {what} (indicator)", PASS if ok else BLOCKED,
                   f"{action}: {'unknown' if perms is None else 'allowed' if ok else 'not granted'}"
                   + (f" at {storage_scope}" if cid.startswith("rbac.blob") else ""),
                   "" if ok else "ask the facilitator/admin to grant the required data role at the narrowest "
                   "supported scope (Storage Blob Data Contributor on your container; Search Index Data "
                   "Contributor on Search; Foundry User on Foundry), or provision/run it for you; "
                   "resource creation permissions do not grant data access",
                   owner="facilitator")


def _pim(report: Report, rg: str) -> None:
    def fn() -> tuple[str, str]:
        url = (f"{rg}/providers/Microsoft.Authorization/roleEligibilityScheduleInstances"
               "?$filter=asTarget()")
        rows = arm("GET", url, "2020-10-01").json().get("value", [])
        names = sorted({(r.get("properties", {}).get("expandedProperties", {}).get("roleDefinition") or {})
                        .get("displayName", "?") for r in rows})
        if rows:
            return WARNING, (f"PIM-eligible (not active) roles: {', '.join(names)}. Activate them before the "
                             "workshop; activation and propagation can take minutes")
        return PASS, "no PIM-eligible roles pending activation at this scope"
    _probe(report, "rbac.pim", "control-plane", "PIM / just-in-time access", fn, required=False)


def _data_plane(report: Report, env: Environment, participant: str | None) -> None:
    from azure.ai.projects import AIProjectClient

    from ..data.blob import ParticipantContainer
    from ..naming import ParticipantNames
    from ..search.client import SearchClient

    cat = "data-plane"

    def blob() -> tuple[str, str]:
        if participant:
            names = ParticipantNames(participant)
            cont = ParticipantContainer(env.resolved_blob_endpoint(), names.container, credential())
            if cont.exists():
                n = sum(1 for _ in cont.list())
                return PASS, f"listed {n} blob(s) + metadata in {names.container}"
        cont = ParticipantContainer(env.resolved_blob_endpoint(), "preflight-nonexistent", credential())
        cont.exists()
        return PASS, "Blob endpoint reachable with your Entra token (container-level probe)"
    _probe(report, "data.blob", cat, "Blob data plane (functional, read-only)", blob,
           fix="facilitator: Storage Blob Data Contributor for you; check storage firewall", owner="facilitator")

    search = SearchClient(env)

    def srch() -> tuple[str, str]:
        names = search.list_names("indexes")
        return PASS, f"listed {len(names)} index(es) on {env.search_service_name}"
    _probe(report, "data.search", cat, "Search data plane (functional, read-only)", srch,
           fix="facilitator: Search Service Contributor + Search Index Data Contributor for you", owner="facilitator")

    def agents() -> tuple[str, str]:
        client = AIProjectClient(endpoint=env.resolved_project_endpoint(), credential=credential())
        items = list(client.agents.list(limit=100))
        return PASS, f"listed {len(items)} agent(s) in project {env.project_name}"
    _probe(report, "data.agents", cat, "Foundry Agent Service (functional, read-only)", agents,
           fix="facilitator: Foundry User on the account and Foundry Project Manager on the project",
           owner="facilitator")


def _identities(report: Report, env: Environment, shared: dict[str, Any]) -> None:
    from ..search.client import SearchClient

    cat = "service-identities"
    search_pid = ((shared.get("search") or {}).get("identity") or {}).get("principalId")
    project_pid = ((shared.get("project") or {}).get("identity") or {}).get("principalId")
    expect = (
        ("ident.search-storage", "Search identity -> Blob (indexer reads blobs + metadata)", search_pid,
         env.resolved_storage_account_id(), {"Storage Blob Data Reader", "Storage Blob Data Contributor"}),
        ("ident.search-foundry", "Search identity -> Foundry embeddings (skill + vectorizer)", search_pid,
         env.foundry_account_id, {"Cognitive Services OpenAI User", "Foundry User"}),
        ("ident.project-search", "Foundry project identity -> Search (agent Search tool)", project_pid,
         env.search_service_id, {"Search Index Data Reader", "Search Index Data Contributor"}),
    )
    for cid, title, pid, scope, roles in expect:
        if not pid:
            report.add(cid, cat, title, BLOCKED, "service has no system-assigned identity",
                       "facilitator: redeploy infra", owner="facilitator")
            continue

        def fn(pid: str = pid, scope: str = scope, roles: set[str] = roles) -> tuple[str, str]:
            have = set(role_assignments(scope, pid))
            ok = bool(have & roles)
            held = ", ".join(sorted(have)) or "no roles"
            return (PASS if ok else BLOCKED), f"principal {pid} at {scope.rsplit('/', 1)[-1]}: {held}"
        _probe(report, cid, cat, title, fn, fix="facilitator: redeploy infra (rbac modules)", owner="facilitator")

    def functional() -> tuple[str, str]:
        search = SearchClient(env)
        ixrs = [n for n in search.list_names("indexers") if n.endswith("-raw-ixr")]
        for name in ixrs:
            last = (search.indexer_status(name).get("lastResult") or {})
            if last.get("status") == "success" and int(last.get("itemsProcessed") or 0) >= 0:
                return PASS, (f"indexer {name} last run succeeded: proves Search -> Blob and Search -> embedding "
                              "end to end")
        return INFO, "no successful participant indexer yet; `fomo provision` proves both arrows live"
    _probe(report, "ident.functional", cat, "Search -> Blob -> embeddings (functional evidence)", functional,
           required=False)


def _capacity(report: Report, env: Environment) -> None:
    from ..search.client import SearchClient

    def fn() -> tuple[str, str]:
        stats = SearchClient(env).service_stats().get("counters", {})
        current = {"indexes": stats.get("indexesCount", {}).get("usage", 0),
                   "indexers": stats.get("indexersCount", {}).get("usage", 0),
                   "skillsets": stats.get("skillsetCount", {}).get("usage", 0),
                   "datasources": stats.get("dataSourcesCount", {}).get("usage", 0)}
        sku = (env.search_sku or "basic").lower()
        rep = capacity.capacity_report(report.participants, sku, current=current)
        status = PASS if rep.fits else BLOCKED
        evidence = (f"{report.participants} participants need {rep.required['indexes']} indexes / "
                    f"{rep.required['indexers']} indexers; {sku} allows {rep.limits['indexes']} "
                    f"({current['indexes']} in use) -> max {rep.participants_max} participants"
                    + ("" if rep.fits else f"; recommended SKU: {rep.recommended_sku}"))
        report.capacity = rep.to_dict()
        return status, evidence
    _probe(report, "capacity.search", "capacity", f"Search object capacity for {report.participants} participants",
           fn, fix="facilitator: `fomo capacity --participants N` and pick the SKU it recommends",
           owner="facilitator")
    model_caps = {c.id: c.data.get("capacity") for c in report.checks if c.id.startswith("models.")}
    chat_cap = model_caps.get("models.chat")
    if chat_cap:
        need_k = report.participants * 2 * (capacity.AGENT_TOKENS_PER_QUESTION["input"]
                                            + capacity.AGENT_TOKENS_PER_QUESTION["output"]) / 1000
        ok = chat_cap >= need_k
        report.add("capacity.chat-tpm", "capacity", "Chat deployment throughput for a synchronized demo minute",
                   PASS if ok else WARNING,
                   f"capacity {chat_cap}k TPM vs ~{need_k:.0f}k tokens if all {report.participants} participants "
                   "ask both agents in the same minute",
                   "" if ok else "facilitator: raise deployment capacity or stagger the comparison step",
                   owner="facilitator", required=False)


def _smoke(report: Report, env: Environment) -> None:
    from ..decision.client import DecisionClient
    from ..decision.config import load_classifier_config

    openai = env.resolved_openai_endpoint().rstrip("/")

    def chat() -> tuple[str, str]:
        t = time.perf_counter()
        r = request("POST", f"{openai}/openai/v1/chat/completions", scope=SCOPE_COGNITIVE, retries=2,
                    json_body={"model": env.chat_deployment, "max_completion_tokens": 64,
                               "messages": [{"role": "user", "content": "Reply with the word ok."}]}).json()
        return PASS, (f"{env.chat_deployment}: {r.get('usage', {}).get('total_tokens')} tokens, "
                      f"{int((time.perf_counter() - t) * 1000)} ms")
    _probe(report, "smoke.chat", "models", "Live chat call", chat, owner="facilitator")

    def emb() -> tuple[str, str]:
        r = request("POST", f"{openai}/openai/v1/embeddings", scope=SCOPE_COGNITIVE, retries=2,
                    json_body={"model": env.embedding_deployment, "input": "pipeline inspection"}).json()
        dims = len(r["data"][0]["embedding"])
        ok = dims == env.embedding_dimensions
        return (PASS if ok else BLOCKED), (f"{env.embedding_deployment}: {dims} dimensions "
                                           f"(index expects {env.embedding_dimensions})")
    _probe(report, "smoke.embedding", "models", "Live embedding call", emb, owner="facilitator")

    def decision() -> tuple[str, str]:
        cfg = load_classifier_config()
        res = DecisionClient(env.resolved_decision_endpoint(), env.decision_deployment, retries=2).classify(
            "Title: Invoice INV-1001\nDocument text:\nInvoice number INV-1001. Line items: 2 x valve. Total EUR 400.",
            cfg)
        return PASS, (f"{env.decision_deployment}: choice={res.choice} p={(res.confidence or 0):.2f} "
                      f"({res.latency_ms} ms, decision API {res.model})")
    _probe(report, "smoke.decision", "models", "Live Microsoft-Decision-1 `choice` call", decision,
           owner="facilitator")


def run_preflight(env: Environment, *, participants: int = 40, participant: str | None = None,
                  smoke: bool = False, local_only: bool = False) -> Report:
    report = Report(datetime.now(UTC).isoformat(timespec="seconds"), participants)
    local_checks(report)
    if not local_only:
        azure_checks(report, env, participant=participant, smoke=smoke)
    return report


# ---- output ----------------------------------------------------------------------------------
_GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_UPN = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")


def scrub(obj: Any, sensitive: set[str] | frozenset[str] = frozenset()) -> Any:
    """Shareable report: no tenant/subscription/object ids, no user or subscription names."""
    from ..telemetry.logging import redact_obj

    obj = redact_obj(obj)
    if isinstance(obj, str):
        for s in sorted(sensitive, key=len, reverse=True):
            obj = obj.replace(s, "<redacted>")
        return _UPN.sub("<user>", _GUID.sub("<id>", obj))
    if isinstance(obj, dict):
        return {k: scrub(v, sensitive) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, sensitive) for v in obj]
    return obj


def render(report: Report) -> str:
    lines = []
    cat = None
    for c in report.checks:
        if c.category != cat:
            cat = c.category
            lines.append(f"[{cat}]")
        lines.append(f"  {c.status:<8} {c.title}: {c.evidence}")
        if c.fix and c.status in (WARNING, BLOCKED):
            fix = c.fix.removeprefix(f"{c.owner}: ")
            lines.append(f"           fix ({c.owner}): {fix}")
    counts = report.counts()
    lines.append(f"verdict: {report.verdict}  ({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))})")
    return "\n".join(lines)
