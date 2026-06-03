"""Custom exception hierarchy for lead-enrichment-pipeline."""
from __future__ import annotations


class EnrichmentError(Exception):
    """Base class for all pipeline errors."""


class ConfigError(EnrichmentError):
    """Raised when required configuration is missing or invalid."""


class ProviderError(EnrichmentError):
    """Generic failure from a skip-trace provider."""


class CreditExhaustedError(ProviderError):
    """Raised when the provider returns a credit-exhausted signal (HTTP 402 or equivalent).

    This is a terminal condition for the current run — no retry should be attempted.
    The driver logs the error and halts the batch so credits are not wasted on
    retries that will all fail.
    """


class SinkError(EnrichmentError):
    """Raised when writing a result to the data sink fails."""
