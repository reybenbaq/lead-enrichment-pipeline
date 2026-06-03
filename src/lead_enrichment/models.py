"""Data shapes for the lead-enrichment pipeline.

All models use frozen dataclasses for immutability once constructed, except
``LeadRecord`` which is mutable to support incremental field population
during the pipeline run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunSummary:
    """Per-cycle run counts returned by the driver."""

    processed: int = 0
    advanced: int = 0
    review: int = 0
    errored: int = 0
    skipped: int = 0
    readmitted: int = 0  # records rescued from expired cooldowns


@dataclass
class ContactRecord:
    """A single contact candidate returned by a skip-trace provider.

    Multiple candidates may be returned per lead; the scoring step selects
    the primary contact and optionally awards a corroboration bonus when
    multiple sources agree.
    """

    source: str  # provider name ("provider_a", "provider_b", etc.)
    first_name: str
    last_name: str
    phone: str
    phone_type: str  # "mobile", "landline", "voip", "unknown"
    email: str
    on_dnc: bool  # Do Not Contact registry flag
    confidence: float  # raw provider confidence, 0.0–1.0


@dataclass
class LeadRecord:
    """A mutable record representing one lead as it moves through the pipeline.

    Fields are populated incrementally — the record starts with only the raw
    input fields and accumulates enrichment data stage by stage.
    """

    record_id: str
    stage: str
    raw_input: dict[str, Any]

    # Normalised fields (populated by the normalisation stage)
    owner_first: str = ""
    owner_last: str = ""
    address: str = ""
    classification: str = ""
    contact_email: str = ""      # fast-path: pre-existing contact info
    contact_phone: str = ""

    # Skip-trace results (populated by the waterfall stage)
    contacts: list[ContactRecord] = field(default_factory=list)

    # Enrichment (populated by the AI enrichment stage)
    enrichment_note: str = ""

    # Scoring (populated by the scoring stage)
    score: float = 0.0
    primary_contact: ContactRecord | None = None

    # Sink metadata (populated by the write stage)
    final_stage: str = ""
    pipeline_note: str = ""

    # Cooldown support — an ISO-format timestamp string, or empty string
    # Records parked under an unexpired cooldown are invisible to the pickup
    # filter (see _readmit_expired_cooldowns in driver.py for the ghost-slots
    # fix and a full explanation of the failure mode).
    cooldown_until: str = ""
