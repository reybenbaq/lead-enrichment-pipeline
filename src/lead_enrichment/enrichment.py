"""AI enrichment stage - stage 3 of the pipeline.

One LLM call per record generates a short personalisation note that the
outreach team uses to tailor first contact. The call is gated behind
OPENAI_API_KEY; when the key is absent the stage returns a deterministic
mock so the demo runs with zero credentials.

Call shape: one system message + one user message, temperature 0.8,
max_tokens 200, model gpt-4o-mini. Plain-text extraction from
choices[0].message.content. Empty-string fallback on any exception.
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

# System prompt for the LLM enrichment call.
# This is a generic prompt written for the sample - it does not reproduce
# any proprietary prompt text from any production system.
_SYSTEM_PROMPT = (
    "You are a research assistant helping a business development team. "
    "Given a property owner's name, address, and property type, write one short sentence "
    "(under 30 words) that could personalise an outreach message. "
    "Focus on the property location or type. Do not make up facts. "
    "Output only the sentence - no preamble."
)


def enrich(
    owner_first: str,
    owner_last: str,
    address: str,
    classification: str,
    *,
    api_key: str,
) -> str:
    """Return a short personalisation note for the lead.

    Uses the OpenAI API when ``api_key`` is non-empty; falls back to a
    deterministic mock otherwise so the demo runs without credentials.

    Returns an empty string on any exception - the pipeline continues.
    """
    if not api_key:
        return _mock_note(owner_first, owner_last, address, classification)

    return _call_openai(owner_first, owner_last, address, classification, api_key)


def _call_openai(
    owner_first: str,
    owner_last: str,
    address: str,
    classification: str,
    api_key: str,
) -> str:
    """Make a synchronous OpenAI chat completion call.

    Returns the model's reply as plain text, or empty string on failure.
    """
    try:
        import openai  # deferred import - only needed when key is present

        client = openai.OpenAI(api_key=api_key)
        user_message = (
            f"Owner: {owner_first} {owner_last}\n"
            f"Address: {address}\n"
            f"Property type: {classification}"
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.8,
            max_tokens=200,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI enrichment failed: %s - continuing without note", exc)
        return ""


def _mock_note(
    owner_first: str,
    owner_last: str,
    address: str,
    classification: str,
) -> str:
    """Deterministic mock note for demo / no-key mode.

    Uses a hash of the inputs so different records get different notes
    without any randomness or external calls.
    """
    key = f"{owner_first}|{owner_last}|{address}|{classification}"
    digest = int(hashlib.md5(key.encode()).hexdigest(), 16)  # noqa: S324 - non-crypto use
    templates = [
        f"Your {classification} property at {address} caught our attention.",
        f"We noticed your {classification} listing at {address} - great location.",
        f"{owner_first}, we work with {classification} owners in that area.",
        f"The {classification} property on {address} matches what our clients look for.",
        f"We focus on {classification} owners like yourself in that market.",
    ]
    return templates[digest % len(templates)]
