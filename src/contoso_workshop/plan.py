"""Plan -> explicit confirm -> apply.

Every write path builds a `Plan` first. `--plan` (default) prints it and stops; `--apply`
prints it, verifies the Azure context and asks for a typed `yes` (or `--yes`). Approval
covers exactly the listed actions, for one participant, in one subscription.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from .azclient import az
from .config import Environment
from .errors import ApprovalDeclined, ConfigError, WorkshopError

VERBS = ("create", "update", "upload", "run", "deploy", "grant", "invoke", "delete", "reset", "write")


@dataclass
class Action:
    verb: str
    target: str
    detail: str = ""
    owner: str = "participant"  # participant-owned asset vs facilitator/shared
    cost: str = ""
    state: str = "pending"  # pending | exists | skip  (what the plan found)

    def line(self) -> str:
        cost = f"  [{self.cost}]" if self.cost else ""
        state = f" ({self.state})" if self.state != "pending" else ""
        return f"  {self.verb:<7} {self.target}{state}{' - ' + self.detail if self.detail else ''}{cost}"


@dataclass
class Plan:
    operation: str
    participant: str | None
    subscription_id: str
    resource_group: str
    actions: list[Action] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    destructive: bool = False

    def add(self, verb: str, target: str, detail: str = "", **kw: Any) -> Action:
        if verb not in VERBS:
            raise ValueError(verb)
        act = Action(verb, target, detail, **kw)
        self.actions.append(act)
        return act

    @property
    def writes(self) -> list[Action]:
        return [a for a in self.actions if a.state != "skip"]

    def render(self) -> str:
        head = (f"PLAN {self.operation}" + (f" for '{self.participant}'" if self.participant else "")
                + f"  (subscription {self.subscription_id}, resource group {self.resource_group})")
        lines = [head]
        lines += [a.line() for a in self.actions] or ["  (nothing to do)"]
        if self.notes:
            lines.append("notes:")
            lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def verify_context(env: Environment) -> dict[str, Any]:
    """The signed-in az context must match the configured tenant/subscription before every apply."""
    acct = az("account", "show")
    if not acct:
        raise ConfigError("not signed in to Azure CLI", fix="run `az login` (and `az account set -s <id>`)")
    if env.tenant_id and acct.get("tenantId") != env.tenant_id:
        raise ConfigError(
            f"Azure CLI is in tenant {acct.get('tenantId')}, but the workshop environment targets {env.tenant_id}",
            fix=f"run `az login --tenant {env.tenant_id}` then `az account set -s {env.subscription_id}`",
        )
    if env.subscription_id and acct.get("id") != env.subscription_id:
        raise ConfigError(
            f"Azure CLI is on subscription {acct.get('id')} ({acct.get('name')}), but the workshop "
            f"environment targets {env.subscription_id}",
            fix=f"run `az account set -s {env.subscription_id}` or re-run `fomo env pull`",
        )
    if acct.get("state") not in (None, "Enabled"):
        raise ConfigError(f"subscription state is {acct.get('state')}", owner="admin")
    return {"subscription": acct.get("id"), "subscription_name": acct.get("name"),
            "tenant": acct.get("tenantId"), "user": (acct.get("user") or {}).get("name")}


def confirm(plan: Plan, *, yes: bool, prompt: str | None = None) -> None:
    """Raise ApprovalDeclined unless the user approves. Non-interactive runs need --yes."""
    if not plan.writes:
        return
    if yes:
        return
    if not sys.stdin.isatty():
        raise ApprovalDeclined("approval required but stdin is not interactive",
                               fix="re-run with --apply --yes after reviewing the plan")
    word = "DELETE" if plan.destructive else "yes"
    question = prompt or f"Apply {len(plan.writes)} action(s) above? Type '{word}' to continue: "
    sys.stderr.write(question)
    sys.stderr.flush()
    answer = sys.stdin.readline().strip()
    if answer != word:
        raise ApprovalDeclined("plan not approved; nothing was changed")


def mode_from_flags(check: bool, plan: bool, apply: bool, dry_run: bool, *, default: str = "plan") -> str:
    chosen = [m for m, on in (("check", check), ("plan", plan or dry_run), ("apply", apply)) if on]
    if len(chosen) > 1:
        raise WorkshopError("choose one of --check, --plan/--dry-run, --apply")
    return chosen[0] if chosen else default
