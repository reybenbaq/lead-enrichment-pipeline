"""Demo entry point - runs synthetic lead records through the full pipeline.

Usage (no API key required):
    SINK_PATH=./demo_sink.json python main.py

With OpenAI key (live AI enrichment):
    SINK_PATH=./demo_sink.json OPENAI_API_KEY=sk-... python main.py

The demo inserts 9 synthetic lead records into an in-memory store, runs one
pipeline cycle, and prints a scored summary to stdout. The records are chosen to
exercise every path: leads that advance, leads parked by the gate, the full
A -> B -> C provider waterfall, a fast-path record, and a messy row that is
skipped. The ghost-slots scenario is also demonstrated: two records are
pre-seeded with an *expired* cooldown timestamp so the re-admit sweep is
exercised on the first cycle.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as ``python main.py`` from the repo root before installing
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lead_enrichment.config import load_config
from lead_enrichment.driver import run_cycle
from lead_enrichment.models import LeadRecord
from lead_enrichment.sink import JSONSink


def _build_demo_store() -> dict[str, LeadRecord]:
    """Return 9 synthetic lead records for the demo run.

    Records 7 and 8 are pre-seeded with an *expired* cooldown timestamp to
    demonstrate the ghost-slots re-admit sweep.
    """
    now = datetime.now(tz=timezone.utc)
    expired = (now - timedelta(hours=2)).isoformat()  # 2 hours ago - definitely expired

    raw_leads = [
        {"id": "L001", "owner_first_name": "Sarah", "owner_last_name": "Mitchell",
         "property_address": "42 Maple Street, Springfield", "property_type": "residential"},
        {"id": "L002", "owner_first_name": "James", "owner_last_name": "Thornton",
         "property_address": "88 Oak Avenue, Riverside", "property_type": "single_family"},
        {"id": "L003", "owner_first_name": "Patricia", "owner_last_name": "Nguyen",
         "property_address": "200 Pine Road, Lakewood", "property_type": "condo"},
        # Hard lead - provider A and B both miss; the waterfall falls all the
        # way through to provider C (low-confidence VoIP, no email -> parked).
        {"id": "L004", "owner_first_name": "Henry", "owner_last_name": "Vasquez",
         "property_address": "250 Dogwood Drive, Clearwater", "property_type": "single_family"},
        {"id": "L005", "owner_first_name": "Margaret", "owner_last_name": "Davies",
         "property_address": "77 Cedar Drive, Westview", "property_type": "residential"},
        # Fast-path record - contact info already present
        {"id": "L006", "owner_first_name": "Thomas", "owner_last_name": "Erikson",
         "property_address": "19 Elm Court, Northgate", "property_type": "single_family",
         "contact_email": "thomas.erikson@example.com", "contact_phone": "555-9999"},
        # Ghost-slots scenario: these two were previously processed and parked
        # under a cooldown that has now expired. The re-admit sweep should rescue
        # them so they re-enter the pickup pool on this cycle.
        {"id": "L007", "owner_first_name": "Jennifer", "owner_last_name": "Walsh",
         "property_address": "301 Willow Way, Eastdale", "property_type": "residential"},
        {"id": "L008", "owner_first_name": "Michael", "owner_last_name": "Santos",
         "property_address": "14 Aspen Circle, Southpark", "property_type": "condo"},
        # Messy permit row - owner name never parsed off the source record. No
        # provider can match an empty name, so this lead is skipped (no contacts).
        {"id": "L009", "owner_first_name": "", "owner_last_name": "",
         "property_address": "parcel listed to a holding entity", "property_type": "residential"},
    ]

    store: dict[str, LeadRecord] = {}
    for raw in raw_leads:
        record_id = raw["id"]
        stage = "unprocessed"
        cooldown_until = ""

        # Seed L007 and L008 with an expired cooldown to trigger the ghost-slots fix
        if record_id in ("L007", "L008"):
            stage = "cooldown"
            cooldown_until = expired

        store[record_id] = LeadRecord(
            record_id=record_id,
            stage=stage,
            raw_input=raw,
            cooldown_until=cooldown_until,
        )

    return store


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # Provide defaults so the demo runs without any env setup
    os.environ.setdefault("SINK_PATH", "./demo_sink.json")

    config = load_config()
    sink = JSONSink(config.sink_path)
    store = _build_demo_store()

    mode = "live AI" if config.openai_api_key else "mock (no OPENAI_API_KEY set)"
    print(f"\n{'='*60}")
    print(f"  lead-enrichment-pipeline demo")
    print(f"  enrichment mode : {mode}")
    print(f"  batch size      : {config.batch_size}")
    print(f"  advance threshold: {config.score_advance_threshold}")
    print(f"{'='*60}\n")

    summary = run_cycle(store, config, sink)

    print(f"\n{'='*60}")
    print("  Run summary")
    print(f"{'='*60}")
    print(f"  records processed  : {summary.processed}")
    print(f"  auto-advanced      : {summary.advanced}")
    print(f"  parked for review  : {summary.review}")
    print(f"  errored            : {summary.errored}")
    print(f"  skipped (no result): {summary.skipped}")
    print(f"  readmitted (ghost-slots fix): {summary.readmitted}")
    print(f"\n  Sink written to: {config.sink_path}")

    # Pretty-print the scored records
    sink_path = Path(config.sink_path)
    if sink_path.exists():
        data = json.loads(sink_path.read_text(encoding="utf-8"))
        print(f"\n{'='*60}")
        print("  Scored records")
        print(f"{'='*60}")
        for rec in sorted(data.values(), key=lambda r: r["score"], reverse=True):
            pc = rec.get("primary_contact") or {}
            print(
                f"  {rec['record_id']} | score={rec['score']:.2f} | "
                f"stage={rec['stage']} | "
                f"phone={pc.get('phone', 'n/a')} | "
                f"email={'yes' if pc.get('email') else 'no'}"
            )


if __name__ == "__main__":
    main()
