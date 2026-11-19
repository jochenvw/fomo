from __future__ import annotations

import pytest

from fomo.errors import NamingError
from fomo.naming import (
    ParticipantNames,
    generate_hackername,
    normalize_hackername,
    pick_free_hackername,
    validate_agent_name,
    validate_container_name,
    validate_hackername,
    validate_search_object_name,
)


def test_generated_names_are_valid_and_fully_derivable():
    for _ in range(50):
        name = generate_hackername()
        validate_hackername(name)
        ParticipantNames(name).validate()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Cosmic Wombat", "cosmic-wombat"), ("  SNEAKY__otter ", "sneaky-otter"), ("a--b  c", "a-b-c")],
)
def test_normalize(raw, expected):
    assert normalize_hackername(raw) == expected


@pytest.mark.parametrize("bad", ["ab", "x" * 25, "cosmic.wombat", "9lives", "wombat!", "über-otter", ""])
def test_invalid_hackernames(bad):
    with pytest.raises(NamingError):
        normalize_hackername(bad)


def test_max_length_name_still_derives_valid_names():
    name = "a" * 24
    validate_hackername(name)
    ParticipantNames(name).validate()


def test_participant_names(names):
    assert names.container == "cosmic-wombat-inbox"
    assert names.raw_index == "cosmic-wombat-raw"
    assert names.raw_indexer == "cosmic-wombat-raw-ixr"
    assert names.smart_index == "cosmic-wombat-smart"
    assert names.smart_indexer == "cosmic-wombat-smart-ixr"
    assert names.simple_rag_agent == "cosmic-wombat-simple-rag"
    assert names.metadata_miner_agent == "cosmic-wombat-metadata-miner"
    assert names.smart_rag_agent == "cosmic-wombat-smart-rag"
    assert names.run_dir == ".workshop/runs/cosmic-wombat"
    assert len(set(names.all_names().values())) == len(names.all_names())
    assert names.smart_route_agent("contract") == names.smart_rag_agent
    assert names.smart_route_agent("technical_report") == "cosmic-wombat-smart-rag-technical-report"
    with pytest.raises(NamingError):
        names.smart_route_agent("memo")


@pytest.mark.parametrize("bad", ["ab", "Upper-case", "a--b", "-lead", "trail-", "x" * 64])
def test_container_validator(bad):
    with pytest.raises(NamingError):
        validate_container_name(bad)


@pytest.mark.parametrize("bad", ["a", "Upper", "a--b", "-lead", "trail-", "x" * 129])
def test_search_validator(bad):
    with pytest.raises(NamingError):
        validate_search_object_name(bad)


@pytest.mark.parametrize("bad", ["-lead", "trail-", "under_score", "x" * 64])
def test_agent_validator(bad):
    with pytest.raises(NamingError):
        validate_agent_name(bad)


def test_agent_validator_allows_upper_case():
    validate_agent_name("Cosmic-Wombat-1")


def test_pick_free_prefers_untaken_preferred():
    taken = {"cosmic-wombat"}
    assert pick_free_hackername(taken.__contains__, preferred=["Cosmic Wombat", "Sneaky Otter"]) == "sneaky-otter"


def test_pick_free_skips_generated_collisions():
    seen: list[str] = []

    def is_taken(name: str) -> bool:
        seen.append(name)
        return len(seen) < 3

    name = pick_free_hackername(is_taken)
    assert name == seen[-1]
    assert len(seen) == 3


def test_pick_free_gives_up():
    with pytest.raises(NamingError):
        pick_free_hackername(lambda _: True, attempts=5)
