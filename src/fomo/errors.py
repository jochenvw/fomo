"""Structured exception types. Each carries who must act and a recommended fix."""

from __future__ import annotations


class WorkshopError(Exception):
    """Base error. `fix` is a human-actionable next step; `owner` is participant|facilitator|admin."""

    def __init__(self, message: str, *, fix: str | None = None, owner: str = "participant") -> None:
        super().__init__(message)
        self.fix = fix
        self.owner = owner


class ConfigError(WorkshopError):
    """Missing or invalid configuration."""


class NamingError(WorkshopError):
    """Invalid or colliding resource name."""


class PermissionDenied(WorkshopError):
    """403 / missing role. Usually needs a facilitator or admin."""


class Throttled(WorkshopError):
    """429 after retries were exhausted."""


class DecisionError(WorkshopError):
    """Microsoft-Decision-1 call failed or returned an unusable answer."""


class SearchError(WorkshopError):
    """Azure AI Search data-plane failure (index, indexer, query)."""


class AgentError(WorkshopError):
    """Foundry agent creation / deployment / invocation failure."""


class ApprovalDeclined(WorkshopError):
    """User declined a plan. Not a failure; the report remains useful."""


class Blocked(WorkshopError):
    """A hard blocker (unsupported region/model/feature). Never faked."""
