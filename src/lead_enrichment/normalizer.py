"""Input normalisation — stage 1 of the pipeline.

Extracts structured fields from the raw lead record. When a contact phone or
email is already present on the record the normalisation stage sets a fast-path
flag so the waterfall lookup is skipped for that record.
"""
from __future__ import annotations

import logging

from .models import LeadRecord

logger = logging.getLogger(__name__)


def normalise(record: LeadRecord) -> bool:
    """Populate structured fields on ``record`` from its raw input dict.

    Returns ``True`` when the fast-path applies (contact info already present),
    ``False`` when the waterfall lookup is still needed.
    """
    raw = record.raw_input

    record.owner_first = _clean(raw.get("owner_first_name", ""))
    record.owner_last = _clean(raw.get("owner_last_name", ""))
    record.address = _clean(raw.get("property_address", ""))
    record.classification = _clean(raw.get("property_type", "residential")).lower()
    record.contact_email = _clean(raw.get("contact_email", ""))
    record.contact_phone = _clean(raw.get("contact_phone", ""))

    if record.contact_email and record.contact_phone:
        logger.debug(
            "record %s fast-path: contact info already present", record.record_id
        )
        return True

    return False


def _clean(value: object) -> str:
    """Strip and normalise a raw field value to a plain string."""
    if value is None:
        return ""
    return str(value).strip()
