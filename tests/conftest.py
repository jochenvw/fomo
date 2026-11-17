from __future__ import annotations

import pytest

from contoso_workshop.config import Environment
from contoso_workshop.naming import ParticipantNames

SUB = "00000000-1111-2222-3333-444444444444"


@pytest.fixture
def env() -> Environment:
    return Environment(
        subscription_id=SUB,
        tenant_id="55555555-6666-7777-8888-999999999999",
        resource_group="rg-contoso",
        foundry_account_name="contoso-foundry",
        project_name="contoso-proj",
        storage_account_name="contosostore",
        search_service_name="contoso-search",
        search_sku="basic",
    )


@pytest.fixture
def names() -> ParticipantNames:
    return ParticipantNames("cosmic-wombat")
