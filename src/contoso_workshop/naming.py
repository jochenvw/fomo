"""Docker-style hackernames and validated per-participant resource names.

A hackername is a namespace label, never a security boundary.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .errors import NamingError

ADJECTIVES = (
    "agile", "bold", "brave", "bright", "calm", "clever", "cosmic", "crafty", "curious", "daring",
    "dazzling", "eager", "electric", "epic", "fearless", "fuzzy", "gallant", "gentle", "glowing",
    "groovy", "happy", "humble", "jolly", "jumpy", "keen", "lucky", "mellow", "mighty", "nimble",
    "noble", "plucky", "polite", "quick", "quirky", "rapid", "rusty", "sneaky", "snappy", "sparkly",
    "speedy", "spry", "stellar", "sturdy", "sunny", "swift", "tidy", "turbo", "witty", "zany", "zesty",
)  # fmt: skip

ANIMALS = (
    "alpaca", "badger", "beaver", "bison", "capybara", "cheetah", "coyote", "dingo", "dolphin",
    "falcon", "ferret", "gecko", "gibbon", "heron", "ibex", "jackal", "koala", "lemur", "llama",
    "lynx", "manatee", "marmot", "meerkat", "mongoose", "narwhal", "ocelot", "orca", "otter",
    "panda", "pangolin", "pelican", "penguin", "puffin", "quokka", "raccoon", "seal", "sloth",
    "squid", "tapir", "toucan", "walrus", "weasel", "wombat", "yak", "zebra",
)  # fmt: skip

# Lower-case alnum words joined by single hyphens; 3..24 chars so every derived name fits.
_HACKERNAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_HACKERNAME = 24
# Evaluation-only Smart RAG variants: one agent per document_type filter plus an unfiltered
# control. `contract` is the workshop's Smart RAG agent itself.
EVAL_ROUTES = ("any", "invoice", "policy", "technical_report", "correspondence", "presentation")


def generate_hackername(rng: secrets.SystemRandom | None = None) -> str:
    r = rng or secrets.SystemRandom()
    return f"{r.choice(ADJECTIVES)}-{r.choice(ANIMALS)}"


def normalize_hackername(raw: str) -> str:
    """Lower-case, map spaces/underscores to hyphens, collapse repeats, then validate."""
    name = re.sub(r"[\s_]+", "-", raw.strip().lower())
    name = re.sub(r"-{2,}", "-", name).strip("-")
    validate_hackername(name)
    return name


def validate_hackername(name: str) -> None:
    if not 3 <= len(name) <= MAX_HACKERNAME:
        raise NamingError(
            f"hackername '{name}' must be 3-{MAX_HACKERNAME} characters",
            fix="pick a shorter name, e.g. 'sneaky-otter'",
        )
    if not _HACKERNAME_RE.match(name):
        raise NamingError(
            f"hackername '{name}' must be lower-case letters/digits separated by single hyphens",
            fix="use something like 'cosmic-wombat'",
        )


@dataclass(frozen=True)
class ParticipantNames:
    """All logical resource names for one participant (goal.md §2 manifest)."""

    hackername: str

    @property
    def container(self) -> str:
        return f"{self.hackername}-inbox"

    @property
    def raw_index(self) -> str:
        return f"{self.hackername}-raw"

    @property
    def smart_index(self) -> str:
        return f"{self.hackername}-smart"

    @property
    def raw_indexer(self) -> str:
        return f"{self.hackername}-raw-ixr"

    @property
    def smart_indexer(self) -> str:
        return f"{self.hackername}-smart-ixr"

    @property
    def datasource(self) -> str:
        return f"{self.hackername}-inbox-ds"

    @property
    def raw_skillset(self) -> str:
        return f"{self.hackername}-raw-sks"

    @property
    def smart_skillset(self) -> str:
        return f"{self.hackername}-smart-sks"

    @property
    def simple_rag_agent(self) -> str:
        return f"{self.hackername}-simple-rag"

    @property
    def metadata_miner_agent(self) -> str:
        return f"{self.hackername}-metadata-miner"

    @property
    def smart_rag_agent(self) -> str:
        return f"{self.hackername}-smart-rag"

    def smart_route_agent(self, route: str) -> str:
        if route == "contract":
            return self.smart_rag_agent
        if route not in EVAL_ROUTES:
            raise NamingError(f"unknown Smart RAG evaluation route '{route}'")
        return f"{self.hackername}-smart-rag-{route.replace('_', '-')}"

    @property
    def eval_route_agents(self) -> list[str]:
        return [self.smart_route_agent(route) for route in EVAL_ROUTES]

    @property
    def run_dir(self) -> str:
        return f".workshop/runs/{self.hackername}"

    def all_names(self) -> dict[str, str]:
        return {
            "container": self.container,
            "datasource": self.datasource,
            "raw_index": self.raw_index,
            "raw_skillset": self.raw_skillset,
            "raw_indexer": self.raw_indexer,
            "smart_index": self.smart_index,
            "smart_skillset": self.smart_skillset,
            "smart_indexer": self.smart_indexer,
            "simple_rag_agent": self.simple_rag_agent,
            "metadata_miner_agent": self.metadata_miner_agent,
            "smart_rag_agent": self.smart_rag_agent,
        }

    def validate(self) -> None:
        validate_hackername(self.hackername)
        validate_container_name(self.container)
        for key, value in self.all_names().items():
            if key == "container":
                continue
            if key.endswith("_agent"):
                validate_agent_name(value)
            else:
                validate_search_object_name(value)


_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){2,62}$")
_SEARCH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,126}[a-z0-9]$")
_AGENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def validate_container_name(name: str) -> None:
    """Blob container: 3-63, lower-case alnum + single hyphens, starts/ends alnum."""
    if not _CONTAINER_RE.match(name) or "--" in name:
        raise NamingError(f"invalid Blob container name '{name}'")


def validate_search_object_name(name: str) -> None:
    """Search index/indexer/skillset/data source: lower-case alnum + hyphen, <=128."""
    if not _SEARCH_RE.match(name) or "--" in name:
        raise NamingError(f"invalid Azure AI Search object name '{name}'")


def validate_agent_name(name: str) -> None:
    """Foundry agent: alnum start/end, hyphens allowed, <=63."""
    if not _AGENT_RE.match(name):
        raise NamingError(f"invalid Foundry agent name '{name}'")


def pick_free_hackername(
    is_taken: Callable[[str], bool],
    *,
    attempts: int = 20,
    rng: secrets.SystemRandom | None = None,
    preferred: Iterable[str] = (),
) -> str:
    """Return the first preferred-or-generated name for which `is_taken` is False."""
    for candidate in preferred:
        name = normalize_hackername(candidate)
        if not is_taken(name):
            return name
    for _ in range(attempts):
        name = generate_hackername(rng)
        if not is_taken(name):
            return name
    raise NamingError(
        "could not find a free hackername after several attempts",
        fix="pass --participant <name> explicitly",
    )
