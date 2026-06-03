"""Tests for the ghost-slots cooldown re-admit sweep.

These tests verify the core reliability fix: records parked under an expired
cooldown timestamp must be cleared and re-admitted to the pickup pool so they
can be processed on the next cycle. Without this fix, expired cooldown records
become permanently invisible to the pickup filter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lead_enrichment.driver import _readmit_expired_cooldowns
from lead_enrichment.models import LeadRecord


def _make_record(record_id: str, stage: str, cooldown_until: str = "") -> LeadRecord:
    return LeadRecord(
        record_id=record_id,
        stage=stage,
        raw_input={},
        cooldown_until=cooldown_until,
    )


class TestReadmitExpiredCooldowns:
    def test_expired_cooldown_is_cleared_and_stage_reset(
        self, expired_cooldown_ts: str
    ) -> None:
        record = _make_record("L001", stage="cooldown", cooldown_until=expired_cooldown_ts)
        store = {"L001": record}

        readmitted = _readmit_expired_cooldowns(store)

        assert readmitted == 1
        assert record.cooldown_until == ""
        assert record.stage == "unprocessed"

    def test_active_cooldown_is_not_cleared(self, future_cooldown_ts: str) -> None:
        record = _make_record("L002", stage="cooldown", cooldown_until=future_cooldown_ts)
        store = {"L002": record}

        readmitted = _readmit_expired_cooldowns(store)

        assert readmitted == 0
        assert record.cooldown_until == future_cooldown_ts  # unchanged
        assert record.stage == "cooldown"  # unchanged

    def test_no_cooldown_record_untouched(self) -> None:
        record = _make_record("L003", stage="unprocessed", cooldown_until="")
        store = {"L003": record}

        readmitted = _readmit_expired_cooldowns(store)

        assert readmitted == 0
        assert record.stage == "unprocessed"

    def test_mixed_store_only_readmits_expired(
        self, expired_cooldown_ts: str, future_cooldown_ts: str
    ) -> None:
        """Only expired records are cleared; active cooldowns and normal records stay."""
        expired_record = _make_record("L004", stage="cooldown", cooldown_until=expired_cooldown_ts)
        active_record = _make_record("L005", stage="cooldown", cooldown_until=future_cooldown_ts)
        normal_record = _make_record("L006", stage="unprocessed")
        store = {
            "L004": expired_record,
            "L005": active_record,
            "L006": normal_record,
        }

        readmitted = _readmit_expired_cooldowns(store)

        assert readmitted == 1
        assert expired_record.stage == "unprocessed"
        assert expired_record.cooldown_until == ""
        assert active_record.stage == "cooldown"  # unchanged
        assert normal_record.stage == "unprocessed"  # unchanged

    def test_malformed_cooldown_timestamp_is_cleared(self) -> None:
        """A malformed timestamp is treated as expired to prevent permanent stalling."""
        record = _make_record("L007", stage="cooldown", cooldown_until="not-a-timestamp")
        store = {"L007": record}

        readmitted = _readmit_expired_cooldowns(store)

        assert readmitted == 1
        assert record.cooldown_until == ""
        assert record.stage == "unprocessed"

    def test_empty_store_returns_zero(self) -> None:
        assert _readmit_expired_cooldowns({}) == 0

    def test_multiple_expired_cooldowns_all_readmitted(
        self, expired_cooldown_ts: str
    ) -> None:
        store = {
            f"L{i:03d}": _make_record(f"L{i:03d}", stage="cooldown", cooldown_until=expired_cooldown_ts)
            for i in range(5)
        }
        readmitted = _readmit_expired_cooldowns(store)
        assert readmitted == 5
        for record in store.values():
            assert record.stage == "unprocessed"
            assert record.cooldown_until == ""
