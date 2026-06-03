"""Data sink — stage 5 of the pipeline.

Writes scored lead records to a local JSON file that stands in for a CRM.
Each record is stored as an entry in a top-level dict keyed by ``record_id``.

The sink is designed for the demo scenario (local file, small batch). A
production adapter would replace ``JSONSink`` with a client that writes to
the real CRM via its REST API, using the same ``write`` interface.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .exceptions import SinkError
from .models import ContactRecord, LeadRecord

logger = logging.getLogger(__name__)


# Stages used by the auto-advance gate
STAGE_REVIEW = "pending_review"
STAGE_ADVANCED = "outreach_ready"


class JSONSink:
    """Append-friendly JSON file sink.

    Reads the existing file on first write, merges the updated record, and
    writes the whole file back. Acceptable at demo scale; a production CRM
    adapter would use PATCH endpoints instead.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def write(self, record: LeadRecord) -> None:
        """Upsert ``record`` into the sink by its ``record_id``.

        Raises ``SinkError`` if the file cannot be read or written.
        """
        try:
            data = self._load()
            data[record.record_id] = _serialise(record)
            self._save(data)
        except OSError as exc:
            raise SinkError(f"sink write failed for {record.record_id}: {exc}") from exc

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        with self._path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


def _serialise(record: LeadRecord) -> dict:
    """Convert a ``LeadRecord`` to a plain dict suitable for JSON serialisation."""
    primary = _serialise_contact(record.primary_contact) if record.primary_contact else None
    return {
        "record_id": record.record_id,
        "stage": record.final_stage or record.stage,
        "owner": f"{record.owner_first} {record.owner_last}".strip(),
        "address": record.address,
        "classification": record.classification,
        "score": round(record.score, 4),
        "primary_contact": primary,
        "enrichment_note": record.enrichment_note,
        "pipeline_note": record.pipeline_note,
    }


def _serialise_contact(contact: ContactRecord) -> dict:
    return {
        "source": contact.source,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "phone": contact.phone,
        "phone_type": contact.phone_type,
        "email": contact.email,
        "on_dnc": contact.on_dnc,
        "confidence": contact.confidence,
    }
