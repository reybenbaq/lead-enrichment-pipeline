"""Multi-signal scoring — stage 4 of the pipeline.

Scores each contact candidate against a set of illustrative signals, selects
the primary contact, and optionally adds a corroboration bonus when multiple
sources agree on the same contact details.

NOTE: The weights below are illustrative placeholders. They demonstrate the
scoring architecture — the real relative weights of any production system are
not represented here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .models import ContactRecord, LeadRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal weights — ILLUSTRATIVE ONLY, not from any real system
# ---------------------------------------------------------------------------

# Each weight is a float contribution to a score that is clamped to [0.0, 1.0]
# at the end. The comments describe what the signal captures, not the exact
# production implementation.

_W_HAS_EMAIL = 0.20          # contact email present
_W_HAS_PHONE = 0.15          # contact phone present
_W_PHONE_MOBILE = 0.10       # phone is a mobile (higher answer rate)
_W_PHONE_NOT_VOIP = 0.05     # phone is not VoIP (lower spam-flag risk)
_W_NOT_DNC = 0.20            # not on the Do Not Contact registry
_W_PROVIDER_CONFIDENCE = 0.15  # provider's raw confidence score (scaled)
_W_CLASSIFICATION = 0.10     # property is a target classification
_W_ADDRESS_PRESENT = 0.05    # normalised address is non-empty

# Bonus when two or more providers return matching contact details
_CORROBORATION_BONUS = 0.10

# Property classifications considered high-value targets for this pipeline
_TARGET_CLASSIFICATIONS = {"residential", "single_family", "condo", "townhome"}


@dataclass(frozen=True)
class ScoredContact:
    """A contact candidate with its computed score."""

    contact: ContactRecord
    score: float


def score_record(record: LeadRecord) -> None:
    """Score all contact candidates, select the primary contact, and update ``record``.

    Corroboration bonus is applied when more than one provider returned a
    contact and their email or phone overlaps — multiple independent sources
    agreeing on a detail increases confidence.

    Populates ``record.score`` and ``record.primary_contact``.
    Does nothing if ``record.contacts`` is empty.
    """
    if not record.contacts:
        logger.debug("record %s: no contacts to score", record.record_id)
        return

    scored = [_score_contact(c, record) for c in record.contacts]
    scored.sort(key=lambda sc: sc.score, reverse=True)

    best = scored[0]
    bonus = _corroboration_bonus(record.contacts, best.contact)

    record.primary_contact = best.contact
    record.score = min(best.score + bonus, 1.0)

    logger.debug(
        "record %s: primary contact score=%.2f (bonus=%.2f)",
        record.record_id,
        record.score,
        bonus,
    )


def _score_contact(contact: ContactRecord, record: LeadRecord) -> ScoredContact:
    """Compute the weighted signal score for a single contact candidate."""
    score = 0.0

    score += _W_HAS_EMAIL if contact.email else 0.0
    score += _W_HAS_PHONE if contact.phone else 0.0

    match contact.phone_type:
        case "mobile":
            score += _W_PHONE_MOBILE
            score += _W_PHONE_NOT_VOIP
        case "landline":
            score += _W_PHONE_NOT_VOIP
        case "voip" | "unknown" | _:
            pass  # no bonus

    score += _W_NOT_DNC if not contact.on_dnc else 0.0
    score += _W_PROVIDER_CONFIDENCE * contact.confidence
    score += _W_CLASSIFICATION if record.classification in _TARGET_CLASSIFICATIONS else 0.0
    score += _W_ADDRESS_PRESENT if record.address else 0.0

    return ScoredContact(contact=contact, score=min(score, 1.0))


def _corroboration_bonus(contacts: list[ContactRecord], primary: ContactRecord) -> float:
    """Return ``_CORROBORATION_BONUS`` if any other contact agrees with the primary.

    Agreement means the same non-empty email OR the same non-empty phone from a
    different source. A single provider repeating itself does not count.
    """
    if len(contacts) < 2:
        return 0.0

    for c in contacts:
        if c.source == primary.source:
            continue
        if primary.email and c.email == primary.email:
            return _CORROBORATION_BONUS
        if primary.phone and c.phone == primary.phone:
            return _CORROBORATION_BONUS

    return 0.0
