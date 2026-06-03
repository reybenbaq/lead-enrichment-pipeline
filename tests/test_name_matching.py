"""Tests for the name-matching logic in the waterfall stage."""
from __future__ import annotations

import pytest

from lead_enrichment.models import ContactRecord
from lead_enrichment.providers import _name_matches


def _make_contact(first: str, last: str, source: str = "provider_a") -> ContactRecord:
    return ContactRecord(
        source=source, first_name=first, last_name=last,
        phone="555-0000", phone_type="mobile", email="",
        on_dnc=False, confidence=0.7,
    )


class TestNameMatches:
    def test_exact_match(self) -> None:
        contact = _make_contact("Sarah", "Mitchell")
        assert _name_matches(contact, "Sarah", "Mitchell") is True

    def test_case_insensitive(self) -> None:
        contact = _make_contact("sarah", "mitchell")
        assert _name_matches(contact, "Sarah", "Mitchell") is True

    def test_initial_match(self) -> None:
        """Provider returns first initial only — should still match."""
        contact = _make_contact("S", "Mitchell")
        assert _name_matches(contact, "Sarah", "Mitchell") is True

    def test_nickname_match_william_bill(self) -> None:
        contact = _make_contact("Bill", "Thompson")
        assert _name_matches(contact, "William", "Thompson") is True

    def test_nickname_match_robert_bob(self) -> None:
        contact = _make_contact("Bob", "Harper")
        assert _name_matches(contact, "Robert", "Harper") is True

    def test_mismatched_last_name_rejected(self) -> None:
        contact = _make_contact("Sarah", "Johnson")
        assert _name_matches(contact, "Sarah", "Mitchell") is False

    def test_empty_expected_name_accepts_all(self) -> None:
        """When we have no expected name, we cannot filter — accept everything."""
        contact = _make_contact("Anyone", "Whatever")
        assert _name_matches(contact, "", "") is True

    def test_completely_different_name_rejected(self) -> None:
        contact = _make_contact("John", "Smith")
        assert _name_matches(contact, "Sarah", "Mitchell") is False
