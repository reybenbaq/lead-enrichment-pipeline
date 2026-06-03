"""Pipeline driver - orchestrates one full processing cycle.

The driver is the only module that touches the data store directly. It
implements the two mechanisms that make the pipeline reliable at scale:

1. Batch pickup with stage filter - fetches up to N unprocessed records
   per run and dispatches each through the full pipeline.

2. Ghost-slots cooldown re-admit - runs at the start of every cycle to
   prevent records from being permanently stuck. See ``_readmit_expired_cooldowns``
   for a full explanation of the failure mode and the fix.

The data store in this sample is a dict (populated by the demo script).
A production driver would call the CRM's list/patch endpoints instead.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .config import Config
from .enrichment import enrich
from .exceptions import CreditExhaustedError
from .models import ContactRecord, LeadRecord, RunSummary
from .normalizer import normalise
from .providers import run_waterfall
from .scoring import score_record
from .sink import JSONSink, STAGE_ADVANCED, STAGE_REVIEW

logger = logging.getLogger(__name__)

# Records in this stage are eligible for pickup
_PICKUP_STAGE = "unprocessed"


def run_cycle(
    store: dict[str, LeadRecord],
    config: Config,
    sink: JSONSink,
) -> RunSummary:
    """Execute one pipeline cycle and return a summary of the run.

    Steps:
    1. Re-admit any records whose cooldown has expired (ghost-slots fix).
    2. Pick up to ``config.batch_size`` unprocessed records.
    3. Run each record through: normalise → waterfall → enrich → score → write.
    4. Apply the auto-advance gate.
    """
    summary = RunSummary()

    # Step 1: rescue any records stuck in expired cooldowns before we pick
    summary.readmitted = _readmit_expired_cooldowns(store)

    # Step 2: pickup batch
    batch = _pickup_batch(store, config.batch_size)
    if not batch:
        logger.info("no records eligible for pickup this cycle")
        return summary

    logger.info("picked up %d records for processing", len(batch))

    # Step 3-4: process each record
    for record in batch:
        try:
            _process_record(record, config, sink, summary)
        except CreditExhaustedError:
            logger.error(
                "credit exhausted - halting batch after %d records processed",
                summary.processed,
            )
            summary.errored += 1
            break
        except Exception:  # noqa: BLE001
            logger.exception("unexpected error processing record %s", record.record_id)
            summary.errored += 1

    logger.info(
        "cycle complete - processed=%d advanced=%d review=%d errored=%d "
        "skipped=%d readmitted=%d",
        summary.processed,
        summary.advanced,
        summary.review,
        summary.errored,
        summary.skipped,
        summary.readmitted,
    )
    return summary


# ---------------------------------------------------------------------------
# Ghost-slots cooldown re-admit
# ---------------------------------------------------------------------------

def _readmit_expired_cooldowns(store: dict[str, LeadRecord]) -> int:
    """Clear expired cooldown timestamps so records re-enter the pickup pool.

    THE PROBLEM (the ghost-slots failure mode):
    When a record fails transiently (e.g., all providers return no result),
    the driver parks it under a cooldown timestamp and sets its stage to
    "cooldown". The pickup filter only fetches records in the PICKUP stage
    with no cooldown set. So far so good.

    The bug: when the cooldown expires, the stage is still "cooldown".
    The pickup filter ignores it because the stage is wrong.
    The cooldown sweep that was supposed to reset the stage to PICKUP_STAGE
    only checked for records with the cooldown field *set* - but the filter
    for pickup checked for records with no cooldown field. The two conditions
    never aligned, so the record was invisible to both sweeps.

    These records filled slots in the store permanently - queue length grew,
    real throughput fell, and the logs showed a non-empty queue that never
    drained. We called these "ghost slots."

    THE FIX:
    At the top of every cycle, sweep all records for an expired
    ``cooldown_until`` timestamp. For any match, clear the timestamp AND
    reset the stage to ``_PICKUP_STAGE``. The next pickup step then finds
    them normally.

    This sweep is cheap (one pass over the in-memory store) and runs
    unconditionally - it is the canonical place where cooldown expiry is
    applied, so the pickup filter stays simple.
    """
    now = datetime.now(tz=timezone.utc)
    readmitted = 0

    for record in store.values():
        if not record.cooldown_until:
            continue
        try:
            cooldown_end = datetime.fromisoformat(record.cooldown_until)
        except ValueError:
            logger.warning(
                "record %s has malformed cooldown_until value %r - clearing",
                record.record_id,
                record.cooldown_until,
            )
            record.cooldown_until = ""
            record.stage = _PICKUP_STAGE
            readmitted += 1
            continue

        if now >= cooldown_end:
            logger.debug(
                "record %s cooldown expired - re-admitting to pickup pool",
                record.record_id,
            )
            record.cooldown_until = ""
            record.stage = _PICKUP_STAGE
            readmitted += 1

    if readmitted:
        logger.info("re-admitted %d records from expired cooldowns", readmitted)

    return readmitted


# ---------------------------------------------------------------------------
# Pickup
# ---------------------------------------------------------------------------

def _pickup_batch(store: dict[str, LeadRecord], batch_size: int) -> list[LeadRecord]:
    """Return up to ``batch_size`` records in the pickup stage with no cooldown set."""
    eligible = [
        r for r in store.values()
        if r.stage == _PICKUP_STAGE and not r.cooldown_until
    ]
    return eligible[:batch_size]


# ---------------------------------------------------------------------------
# Per-record pipeline
# ---------------------------------------------------------------------------

def _process_record(
    record: LeadRecord,
    config: Config,
    sink: JSONSink,
    summary: RunSummary,
) -> None:
    """Run one record through the full pipeline."""

    # Stage 1: normalise inputs
    fast_path = normalise(record)

    if not fast_path:
        # Stage 2: multi-vendor waterfall skip-trace
        run_waterfall(record)
    else:
        # Fast-path: synthesise a ContactRecord from the pre-existing contact
        # fields so the scoring stage has something to evaluate.
        record.contacts = [
            ContactRecord(
                source="pre_existing",
                first_name=record.owner_first,
                last_name=record.owner_last,
                phone=record.contact_phone,
                phone_type="unknown",
                email=record.contact_email,
                on_dnc=False,
                confidence=1.0,  # we trust pre-existing data over provider results
            )
        ]

    if not record.contacts:
        logger.debug("record %s: no contacts found - skipping", record.record_id)
        summary.skipped += 1
        return

    # Stage 3: AI enrichment
    record.enrichment_note = enrich(
        owner_first=record.owner_first,
        owner_last=record.owner_last,
        address=record.address,
        classification=record.classification,
        api_key=config.openai_api_key,
    )

    # Stage 4: score
    score_record(record)

    # Stage 5: auto-advance gate
    _apply_advance_gate(record, config.score_advance_threshold)

    # Stage 6: write to sink
    sink.write(record)

    summary.processed += 1
    if record.final_stage == STAGE_ADVANCED:
        summary.advanced += 1
    else:
        summary.review += 1

    logger.info(
        "record %s processed - score=%.2f stage=%s",
        record.record_id,
        record.score,
        record.final_stage,
    )


def _apply_advance_gate(record: LeadRecord, threshold: float) -> None:
    """Promote the record past review when it meets the advance criteria.

    Criteria: score >= threshold AND primary contact has both email AND phone.
    Records that pass go to STAGE_ADVANCED (outreach-ready queue).
    Records that fail go to STAGE_REVIEW for manual validation.
    """
    pc = record.primary_contact
    has_email = bool(pc and pc.email)
    has_phone = bool(pc and pc.phone)

    if record.score >= threshold and has_email and has_phone:
        record.final_stage = STAGE_ADVANCED
        record.pipeline_note = (
            f"auto-advanced: score={record.score:.2f} >= threshold={threshold}"
        )
    else:
        record.final_stage = STAGE_REVIEW
        reasons = []
        if record.score < threshold:
            reasons.append(f"score={record.score:.2f} < {threshold}")
        if not has_email:
            reasons.append("no email")
        if not has_phone:
            reasons.append("no phone")
        record.pipeline_note = f"parked for review: {', '.join(reasons)}"
