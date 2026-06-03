"""Tests for the multi-signal scoring stage."""
from __future__ import annotations

import pytest

from lead_enrichment.models import ContactRecord, LeadRecord
from lead_enrichment.scoring import score_record, _corroboration_bonus


class TestScoreRecord:
    def test_scores_above_zero_with_good_contact(
        self, basic_lead: LeadRecord, contact_mobile: ContactRecord
    ) -> None:
        basic_lead.classification = "residential"
        basic_lead.address = "42 Maple Street"
        basic_lead.contacts = [contact_mobile]

        score_record(basic_lead)

        assert basic_lead.primary_contact is contact_mobile
        assert basic_lead.score > 0.0
        assert basic_lead.score <= 1.0

    def test_mobile_phone_scores_higher_than_voip(self, basic_lead: LeadRecord) -> None:
        mobile = ContactRecord(
            source="provider_a", first_name="Sarah", last_name="Mitchell",
            phone="555-0100", phone_type="mobile", email="s@example.com",
            on_dnc=False, confidence=0.8,
        )
        voip = ContactRecord(
            source="provider_a", first_name="Sarah", last_name="Mitchell",
            phone="555-0200", phone_type="voip", email="s@example.com",
            on_dnc=False, confidence=0.8,
        )
        basic_lead.classification = "residential"
        basic_lead.address = "42 Maple Street"

        basic_lead.contacts = [mobile]
        score_record(basic_lead)
        mobile_score = basic_lead.score

        # Reset for voip test
        basic_lead.contacts = [voip]
        basic_lead.primary_contact = None
        basic_lead.score = 0.0
        score_record(basic_lead)
        voip_score = basic_lead.score

        assert mobile_score > voip_score

    def test_dnc_flag_reduces_score(self, basic_lead: LeadRecord) -> None:
        clean = ContactRecord(
            source="provider_a", first_name="Sarah", last_name="Mitchell",
            phone="555-0100", phone_type="mobile", email="s@example.com",
            on_dnc=False, confidence=0.8,
        )
        flagged = ContactRecord(
            source="provider_a", first_name="Sarah", last_name="Mitchell",
            phone="555-0100", phone_type="mobile", email="s@example.com",
            on_dnc=True, confidence=0.8,
        )
        basic_lead.classification = "residential"
        basic_lead.address = "42 Maple Street"

        basic_lead.contacts = [clean]
        score_record(basic_lead)
        clean_score = basic_lead.score

        basic_lead.contacts = [flagged]
        basic_lead.primary_contact = None
        basic_lead.score = 0.0
        score_record(basic_lead)
        flagged_score = basic_lead.score

        assert clean_score > flagged_score

    def test_no_contacts_leaves_score_at_zero(self, basic_lead: LeadRecord) -> None:
        basic_lead.contacts = []
        score_record(basic_lead)
        assert basic_lead.score == 0.0
        assert basic_lead.primary_contact is None

    def test_score_clamped_to_one(self, basic_lead: LeadRecord) -> None:
        """Even with maximum signal on every dimension, score stays <= 1.0."""
        contact = ContactRecord(
            source="provider_a", first_name="Sarah", last_name="Mitchell",
            phone="555-0100", phone_type="mobile", email="s@example.com",
            on_dnc=False, confidence=1.0,
        )
        basic_lead.classification = "residential"
        basic_lead.address = "42 Maple Street"
        basic_lead.contacts = [contact]
        score_record(basic_lead)
        assert basic_lead.score <= 1.0


class TestCorroborationBonus:
    def test_bonus_applied_when_email_matches_across_sources(
        self, contact_mobile: ContactRecord
    ) -> None:
        other = ContactRecord(
            source="provider_b", first_name="Sarah", last_name="Mitchell",
            phone="555-9999", phone_type="landline",
            email=contact_mobile.email,  # same email
            on_dnc=False, confidence=0.6,
        )
        bonus = _corroboration_bonus([contact_mobile, other], contact_mobile)
        assert bonus > 0.0

    def test_no_bonus_when_single_source(self, contact_mobile: ContactRecord) -> None:
        bonus = _corroboration_bonus([contact_mobile], contact_mobile)
        assert bonus == 0.0

    def test_no_bonus_when_same_source_repeated(self, contact_mobile: ContactRecord) -> None:
        duplicate = ContactRecord(
            source=contact_mobile.source,  # same source - does not count
            first_name="Sarah", last_name="Mitchell",
            phone=contact_mobile.phone, phone_type="mobile",
            email=contact_mobile.email, on_dnc=False, confidence=0.9,
        )
        bonus = _corroboration_bonus([contact_mobile, duplicate], contact_mobile)
        assert bonus == 0.0
