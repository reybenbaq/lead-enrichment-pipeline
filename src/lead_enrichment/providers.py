"""Skip-trace provider waterfall - stage 2 of the pipeline.

Three mock providers stand in for real skip-trace vendors. They return
synthetic ``ContactRecord`` objects; no real vendor names appear here.

The waterfall tries Provider A first, falls back to Provider B, then Provider C.
A successful hit from any provider populates ``record.contacts`` and the
waterfall stops for that record.

Name matching validates that the name returned by the provider actually
matches the owner name we searched for, using a lightweight phonetic/initial
heuristic to handle nicknames and middle-name variations.

Retry logic uses exponential backoff on transient errors. A 402-equivalent
``CreditExhaustedError`` is terminal - the driver catches it and halts the
batch.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Protocol

from .exceptions import CreditExhaustedError, ProviderError
from .models import ContactRecord, LeadRecord

logger = logging.getLogger(__name__)

# Retry configuration
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5  # seconds; wait = _BACKOFF_BASE * 2**attempt


class SkipTraceProvider(Protocol):
    """Contract for a skip-trace provider adapter."""

    @property
    def name(self) -> str: ...

    def lookup(self, first: str, last: str, address: str) -> list[ContactRecord]:
        """Return candidate contacts for the given name + address.

        Raises:
            ProviderError: transient failure; caller should retry with backoff.
            CreditExhaustedError: terminal; caller should halt the batch.
        """
        ...


# ---------------------------------------------------------------------------
# Mock providers - deterministic synthetic data
#
# Real skip-trace vendors have imperfect, uncorrelated hit rates: one finds a
# mobile, another only a landline, a third nothing at all. These mocks
# reproduce that variety deterministically from a hash of the lead identity, so
# the demo exercises the full A -> B -> C waterfall and leaves the auto-advance
# gate a realistic mix of records to advance and park. No real vendor names or
# data appear here.
# ---------------------------------------------------------------------------

def _bucket(first: str, last: str, address: str) -> int:
    """Deterministic 0-9 bucket from the lead identity (stable across runs)."""
    raw = f"{first.lower()}|{last.lower()}|{address.lower()}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16) % 10  # noqa: S324 - non-crypto use


class _MockProviderA:
    """Provider A mock - best coverage. Returns a mobile + email for most leads.

    Misses entirely for the top buckets (forcing fallback to provider B), and
    returns a phone-only result for a couple of buckets so the auto-advance
    gate has records that score well yet park for missing an email.
    """

    name = "provider_a"

    def lookup(self, first: str, last: str, address: str) -> list[ContactRecord]:
        if not first or not last:
            return []
        b = _bucket(first, last, address)
        if b >= 8:
            return []  # no record found - the waterfall falls through to provider B
        has_email = b not in (6, 7)
        email = f"{first.lower()}.{last.lower()}@example-mail.com" if has_email else ""
        return [
            ContactRecord(
                source=self.name,
                first_name=first,
                last_name=last,
                phone="555-0100",
                phone_type="mobile",
                email=email,
                on_dnc=False,
                confidence=0.82,
            )
        ]


class _MockProviderB:
    """Provider B mock - fallback. Landline only, no email, lower confidence."""

    name = "provider_b"

    def lookup(self, first: str, last: str, address: str) -> list[ContactRecord]:
        if not first or not last:
            return []
        if _bucket(first, last, address) == 9:
            return []  # also misses - last-resort provider C is tried next
        return [
            ContactRecord(
                source=self.name,
                first_name=first,
                last_name=last,
                phone="555-0200",
                phone_type="landline",
                email="",
                on_dnc=False,
                confidence=0.61,
            )
        ]


class _MockProviderC:
    """Provider C mock - last resort. Low-confidence VoIP number, no email."""

    name = "provider_c"

    def lookup(self, first: str, last: str, address: str) -> list[ContactRecord]:
        if not first or not last:
            return []
        return [
            ContactRecord(
                source=self.name,
                first_name=first,
                last_name=last,
                phone="555-0300",
                phone_type="voip",
                email="",
                on_dnc=False,
                confidence=0.44,
            )
        ]


# The ordered waterfall sequence
_PROVIDERS: list[SkipTraceProvider] = [
    _MockProviderA(),
    _MockProviderB(),
    _MockProviderC(),
]


# ---------------------------------------------------------------------------
# Waterfall orchestration
# ---------------------------------------------------------------------------

def run_waterfall(record: LeadRecord) -> None:
    """Try each provider in order; stop at the first successful hit.

    Populates ``record.contacts`` with deduplicated candidates. Each provider
    result is validated against the expected owner name before acceptance.
    CreditExhaustedError propagates immediately - do not catch it here.
    """
    seen_phones: set[str] = set()

    for provider in _PROVIDERS:
        candidates = _lookup_with_retry(
            provider,
            record.owner_first,
            record.owner_last,
            record.address,
        )
        matched = [
            c for c in candidates
            if _name_matches(c, record.owner_first, record.owner_last)
            and c.phone not in seen_phones
        ]
        if matched:
            record.contacts.extend(matched)
            seen_phones.update(c.phone for c in matched)
            logger.debug(
                "record %s: provider %s returned %d matched contacts",
                record.record_id,
                provider.name,
                len(matched),
            )
            break  # waterfall stops on first hit

    if not record.contacts:
        logger.debug("record %s: all providers returned no contacts", record.record_id)


def _lookup_with_retry(
    provider: SkipTraceProvider,
    first: str,
    last: str,
    address: str,
) -> list[ContactRecord]:
    """Call ``provider.lookup`` with exponential backoff on transient errors.

    Raises ``CreditExhaustedError`` immediately without retrying.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            return provider.lookup(first, last, address)
        except CreditExhaustedError:
            raise  # terminal - propagate immediately
        except ProviderError as exc:
            wait = _BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "provider %s transient error (attempt %d/%d): %s - retrying in %.1fs",
                provider.name,
                attempt + 1,
                _MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)

    logger.error(
        "provider %s failed after %d attempts - skipping", provider.name, _MAX_RETRIES
    )
    return []


# ---------------------------------------------------------------------------
# Name-matching helpers
# ---------------------------------------------------------------------------

_NICKNAMES: dict[str, set[str]] = {
    "william": {"bill", "will", "billy"},
    "robert": {"bob", "rob", "bobby"},
    "richard": {"rick", "dick", "rich"},
    "james": {"jim", "jimmy"},
    "thomas": {"tom", "tommy"},
    "michael": {"mike", "mickey"},
    "patricia": {"pat", "patty", "trish"},
    "margaret": {"maggie", "peggy", "meg"},
    "elizabeth": {"liz", "beth", "lisa", "eliza"},
    "jennifer": {"jen", "jenny"},
}


def _name_matches(contact: ContactRecord, expected_first: str, expected_last: str) -> bool:
    """Return True when the contact name plausibly matches the expected owner name.

    Checks are applied in order:
    1. Exact match (case-insensitive).
    2. Initial match - contact first is the initial of the expected first.
    3. Nickname match - contact first is a known nickname of the expected first.

    Last name must always match exactly (case-insensitive).
    """
    if not expected_first or not expected_last:
        return True  # no expected name means no filter

    last_ok = contact.last_name.lower() == expected_last.lower()
    if not last_ok:
        return False

    cf = contact.first_name.lower()
    ef = expected_first.lower()

    if cf == ef:
        return True
    if len(cf) == 1 and ef.startswith(cf):  # initial match
        return True
    # nickname lookup
    nicknames = _NICKNAMES.get(ef, set()) | _NICKNAMES.get(cf, set())
    return cf in nicknames or ef in nicknames
