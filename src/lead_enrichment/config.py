"""Configuration loading.

All ``os.environ`` reads live in this module. ``load_config`` collects every
missing required variable before raising, so the operator sees the full list
of problems on first run rather than fixing them one at a time.

``OPENAI_API_KEY`` is optional — if absent the AI enrichment stage runs a
deterministic mock so the demo executes with zero external credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .exceptions import ConfigError


@dataclass(frozen=True)
class Config:
    sink_path: str
    batch_size: int
    score_advance_threshold: float
    cooldown_minutes: int
    openai_api_key: str  # empty string means "use mock"
    log_level: str


def load_config() -> Config:
    """Load and validate config from environment.

    Raises ``ConfigError`` listing every missing required variable.
    ``OPENAI_API_KEY`` is optional and defaults to empty string (mock mode).
    """
    required = ("SINK_PATH",)
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"missing required env vars: {', '.join(missing)}")

    return Config(
        sink_path=os.environ["SINK_PATH"],
        batch_size=int(os.environ.get("BATCH_SIZE", "20")),
        score_advance_threshold=float(os.environ.get("SCORE_ADVANCE_THRESHOLD", "0.65")),
        cooldown_minutes=int(os.environ.get("COOLDOWN_MINUTES", "60")),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
