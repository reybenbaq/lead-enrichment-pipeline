"""Shared fixtures for the lead-enrichment-pipeline test suite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lead_enrichment.models import ContactRecord, LeadRecord


@pytest.fixture
def basic_lead() -> LeadRecord:
    """A minimal unprocessed lead record."""
    return LeadRecord(
        record_id="T001",
        stage="unprocessed",
        raw_input={
            "owner_first_name": "Sarah",
            "owner_last_name": "Mitchell",
            "property_address": "42 Maple Street, Springfield",
            "property_type": "residential",
        },
    )


@pytest.fixture
def contact_mobile() -> ContactRecord:
    """A high-quality mobile contact candidate."""
    return ContactRecord(
        source="provider_a",
        first_name="Sarah",
        last_name="Mitchell",
        phone="555-0100",
        phone_type="mobile",
        email="sarah.mitchell@example.com",
        on_dnc=False,
        confidence=0.85,
    )


@pytest.fixture
def contact_landline() -> ContactRecord:
    """A lower-quality landline contact with no email."""
    return ContactRecord(
        source="provider_b",
        first_name="Sarah",
        last_name="Mitchell",
        phone="555-0200",
        phone_type="landline",
        email="",
        on_dnc=False,
        confidence=0.55,
    )


@pytest.fixture
def expired_cooldown_ts() -> str:
    """An ISO timestamp two hours in the past (definitely expired)."""
    past = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    return past.isoformat()


@pytest.fixture
def future_cooldown_ts() -> str:
    """An ISO timestamp two hours in the future (not yet expired)."""
    future = datetime.now(tz=timezone.utc) + timedelta(hours=2)
    return future.isoformat()
