"""Idempotent Azure role assignments via ARM REST (deterministic uuid5 names).

Used for post-deploy grants that Bicep cannot express because the principal only exists
after a data-plane operation (the hosted agent's Entra agent identity).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from .azclient import arm
from .errors import PermissionDenied, WorkshopError

API = "2022-04-01"
NAMESPACE = uuid.UUID("6f1c7a52-3b0e-4f43-9b7c-0c6f8f3e2a11")

ROLES = {
    "Storage Blob Data Contributor": "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    "Storage Blob Data Reader": "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
    "Foundry User": "53ca6127-db72-4b80-b1b0-d745d6d5456d",
    "Cognitive Services User": "a97b65f3-24c7-4388-baec-2e87135dc908",
    "Search Index Data Reader": "1407120a-92aa-4202-b7e9-c0e197c71c8f",
}


@dataclass(frozen=True)
class Grant:
    role: str
    scope: str
    principal_id: str
    principal_type: str = "ServicePrincipal"

    @property
    def role_definition_id(self) -> str:
        sub = self.scope.split("/")[2]
        return f"/subscriptions/{sub}/providers/Microsoft.Authorization/roleDefinitions/{ROLES[self.role]}"

    @property
    def assignment_name(self) -> str:
        return str(uuid.uuid5(NAMESPACE, f"{self.scope.lower()}|{self.principal_id}|{ROLES[self.role]}"))

    @property
    def path(self) -> str:
        return f"{self.scope}/providers/Microsoft.Authorization/roleAssignments/{self.assignment_name}"


def container_scope(storage_account_id: str, container: str) -> str:
    return f"{storage_account_id}/blobServices/default/containers/{container}"


def ensure(grant: Grant, *, replication_wait: float = 15, attempts: int = 8) -> str:
    """Create the assignment if absent. Returns 'created' or 'exists'.

    New agent identities take a while to replicate in Entra ID; PrincipalNotFound is retried."""
    body: dict[str, Any] = {"properties": {
        "roleDefinitionId": grant.role_definition_id,
        "principalId": grant.principal_id,
        "principalType": grant.principal_type,
    }}
    for attempt in range(attempts):
        try:
            arm("PUT", grant.path, API, body, retries=6)
            return "created"
        except PermissionDenied as exc:
            raise PermissionDenied(
                f"cannot assign '{grant.role}' at {grant.scope}: {exc}"[:900],
                fix="ask an Owner / Role Based Access Control Administrator to run this step, or grant you "
                "RBAC Administrator constrained to these roles (see docs/security-and-rbac.md)",
                owner="facilitator",
            ) from exc
        except WorkshopError as exc:
            text = str(exc)
            if "RoleAssignmentExists" in text:
                return "exists"
            if "PrincipalNotFound" in text and attempt < attempts - 1:
                time.sleep(replication_wait)
                continue
            if "PrincipalNotFound" in text:
                raise WorkshopError(
                    f"principal {grant.principal_id} not yet replicated in Entra ID",
                    fix="wait a minute and rerun; new agent identities take time to replicate",
                ) from exc
            raise
    raise AssertionError("unreachable")


def delete(grant: Grant) -> bool:
    try:
        arm("DELETE", grant.path, API, ok=(200, 204))
        return True
    except WorkshopError:
        return False
