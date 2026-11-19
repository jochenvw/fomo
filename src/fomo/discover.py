"""Find workshop services that already exist in the confirmed resource group (read-only).

Typical enterprise case: internal IT deploys Foundry, Storage and Search; participants only use them.
Discovery turns "group known, services unknown" into either a saved configuration or a precise request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .azclient import arm
from .config import Environment

FOUNDRY_TYPE = "microsoft.cognitiveservices/accounts"
STORAGE_TYPE = "microsoft.storage/storageaccounts"
SEARCH_TYPE = "microsoft.search/searchservices"
EMBEDDING_DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072,
                        "text-embedding-ada-002": 1536}
ROLES = ("foundry", "project", "storage", "search")
OPTIONS = {"foundry": "--foundry-account", "project": "--project", "storage": "--storage-account",
           "search": "--search-service"}
# What internal IT must provide when a service is absent (and the role participants need on it).
IT_REQUEST = {
    "foundry": "Foundry account (kind AIServices, project management enabled) with gpt-5-mini, "
               "text-embedding-3-small and Microsoft-Decision-1.1 deployments; you need Foundry User",
    "project": "Foundry project in that account; you need Foundry User",
    "storage": "Storage account (Blob, Entra ID auth, public endpoint for the hosted agent); "
               "you need Storage Blob Data Contributor",
    "search": "Azure AI Search (Basic or higher, RBAC auth) connected to the project; "
              "you need Search Service Contributor + Search Index Data Contributor",
}


@dataclass
class Inventory:
    resource_count: int = 0
    deployment: str | None = None  # successful FOMO Bicep deployment with outputs
    foundry: list[dict[str, Any]] = field(default_factory=list)
    projects: dict[str, list[str]] = field(default_factory=dict)  # account name -> project names
    storage: list[dict[str, Any]] = field(default_factory=list)
    search: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        def names(items: list[dict[str, Any]]) -> str:
            return ", ".join(i["name"] for i in items) or "none"
        projects = ", ".join(f"{a}/{p}" for a, ps in self.projects.items() for p in ps) or "none"
        return (f"{self.resource_count} resource(s); Foundry: {names(self.foundry)}; projects: {projects}; "
                f"Storage: {names(self.storage)}; Search: {names(self.search)}; "
                f"FOMO deployment: {self.deployment or 'none'}")


def _list(path: str, api: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    url: str | None = path
    while url:
        page = arm("GET", url, api).json()
        items.extend(page.get("value", []))
        url = page.get("nextLink")
        if url:
            url = url.split("api-version=")[0].rstrip("?&")
    return items


def inventory(subscription_id: str, resource_group: str) -> Inventory:
    rg = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
    inv = Inventory()
    resources = _list(f"{rg}/resources", "2021-04-01")
    inv.resource_count = len(resources)
    for r in resources:
        kind = r.get("type", "").lower()
        if kind == FOUNDRY_TYPE and (r.get("kind") or "").lower() == "aiservices":
            account = arm("GET", r["id"], "2025-06-01").json()
            props = account.get("properties") or {}
            sub = props.get("customSubDomainName") or account["name"]
            inv.foundry.append({"name": account["name"], "id": account["id"],
                                "endpoint": f"https://{sub}.services.ai.azure.com",
                                "openai_endpoint": f"https://{sub}.openai.azure.com",
                                "projects_enabled": bool(props.get("allowProjectManagement"))})
            inv.projects[account["name"]] = [p["name"].split("/")[-1]
                                             for p in _list(f"{r['id']}/projects", "2025-06-01")]
        elif kind == STORAGE_TYPE:
            inv.storage.append({"name": r["name"], "id": r["id"]})
        elif kind == SEARCH_TYPE:
            inv.search.append({"name": r["name"], "id": r["id"], "sku": (r.get("sku") or {}).get("name", "")})
    for d in _list(f"{rg}/providers/Microsoft.Resources/deployments", "2021-04-01"):
        props = d.get("properties") or {}
        if props.get("provisioningState") == "Succeeded" and "foundry_account_name" in {
                k.lower() for k in (props.get("outputs") or {})}:
            inv.deployment = d["name"]
            break
    return inv


def _pick(candidates: list[str], wanted: str | None) -> tuple[str | None, str]:
    if wanted:
        return (wanted, "") if wanted in candidates else (None, f"'{wanted}' not found")
    if len(candidates) == 1:
        return candidates[0], ""
    return None, "none in the resource group" if not candidates else f"ambiguous: {', '.join(candidates)}"


def resolve(inv: Inventory, choices: dict[str, str | None]) -> tuple[dict[str, Any], dict[str, str]]:
    """Map discovered services to Environment fields. Returns (fields, problems by role)."""
    fields: dict[str, Any] = {}
    problems: dict[str, str] = {}
    account, why = _pick([a["name"] for a in inv.foundry], choices.get("foundry"))
    if account:
        a = next(x for x in inv.foundry if x["name"] == account)
        fields.update(foundry_account_name=account, foundry_account_endpoint=a["endpoint"],
                      foundry_openai_endpoint=a["openai_endpoint"])
        project, pwhy = _pick(inv.projects.get(account, []), choices.get("project"))
        if project:
            fields.update(project_name=project,
                          project_endpoint=f"{a['endpoint']}/api/projects/{project}")
        else:
            problems["project"] = pwhy
    else:
        problems["foundry"] = why
        problems["project"] = "needs a Foundry account first"
    for role, items in (("storage", inv.storage), ("search", inv.search)):
        name, why = _pick([i["name"] for i in items], choices.get(role))
        if not name:
            problems[role] = why
            continue
        item = next(i for i in items if i["name"] == name)
        if role == "storage":
            fields.update(storage_account_name=name, storage_account_id=item["id"],
                          blob_endpoint=f"https://{name}.blob.core.windows.net/")
        else:
            fields.update(search_service_name=name, search_endpoint=f"https://{name}.search.windows.net",
                          search_sku=item["sku"])
    return fields, problems


def adopt_deployments(env: Environment, fields: dict[str, Any]) -> list[str]:
    """If the default model deployment / Search connection names are absent, adopt a unique match."""
    notes: list[str] = []
    if "foundry_account_name" not in fields:
        return notes
    account_id = (f"/subscriptions/{env.subscription_id}/resourceGroups/{env.resource_group}"
                  f"/providers/Microsoft.CognitiveServices/accounts/{fields['foundry_account_name']}")
    deps = _list(f"{account_id}/deployments", "2025-06-01")
    models = {d["name"]: ((d.get("properties") or {}).get("model") or {}).get("name", "").lower() for d in deps}
    for attr, match in (("chat_deployment", lambda m: m.startswith("gpt")),
                        ("embedding_deployment", lambda m: m in EMBEDDING_DIMENSIONS),
                        ("decision_deployment", lambda m: "decision" in m)):
        current = getattr(env, attr)
        if current in models:
            continue
        found = [n for n, m in models.items() if match(m)]
        if len(found) == 1:
            fields[attr] = found[0]
            notes.append(f"{attr}: '{current}' absent, adopting '{found[0]}' ({models[found[0]]})")
            if attr == "embedding_deployment":
                fields["embedding_model"] = models[found[0]]
                fields["embedding_dimensions"] = EMBEDDING_DIMENSIONS[models[found[0]]]
        else:
            notes.append(f"{attr}: '{current}' absent; candidates: {', '.join(found) or 'none'} "
                         "(preflight will report it)")
    if "project_name" in fields:
        connections = _list(f"{account_id}/projects/{fields['project_name']}/connections", "2025-06-01")
        search = [c["name"] for c in connections
                  if ((c.get("properties") or {}).get("category") or "") == "CognitiveSearch"]
        if env.search_connection_name not in search and len(search) == 1:
            fields["search_connection_name"] = search[0]
            notes.append(f"search_connection_name: adopting existing '{search[0]}'")
        elif not search:
            notes.append("no project -> Search connection yet (preflight will report who must create it)")
    return notes


def advise(env: Environment, inv: Inventory, problems: dict[str, str],
           can_create: bool | None) -> tuple[str, str]:
    """Next step for 'resource group known, services not configured': (fix, owner)."""
    sub, rg = env.subscription_id, env.resource_group
    if inv.deployment:
        return (f"`uv run fomo env pull --subscription {sub} --resource-group {rg}` "
                f"(reads deployment {inv.deployment}; local write only)", "participant")
    if not problems:
        return ("services found in the group: review `uv run fomo env discover --plan`, then rerun it with "
                "`--apply --yes` after approval (local write only), then rerun preflight", "participant")
    ambiguous = {r: p for r, p in problems.items() if p.startswith("ambiguous")}
    absent = [r for r, p in problems.items() if r not in ambiguous]
    if ambiguous and not absent:
        flags = " ".join(f"{OPTIONS[r]} <name>" for r in ambiguous)
        return (f"several candidates ({'; '.join(f'{r} {p}' for r, p in ambiguous.items())}): ask which ones "
                f"the workshop uses, then `uv run fomo env discover {flags} --plan`", "participant")
    request = "; ".join(IT_REQUEST[r] for r in absent)
    if can_create:
        return (f"missing in {rg}: {', '.join(absent)}. You appear able to create services here (RBAC indicator "
                "only; policy/quota may still block). Prefer asking internal IT: " + request
                + ". Self-deploy only with explicit approval of a reviewed plan: "
                "`uv run fomo infra deploy --parameters infra/parameters/participant.bicepparam --plan`",
                "participant")
    return (f"ask internal IT to provision in {rg} (or tell you where it exists): {request}. "
            "Then rerun preflight; discovery adopts them automatically", "facilitator")
