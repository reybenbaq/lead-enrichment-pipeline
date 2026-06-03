# lead-enrichment-pipeline

> A from-scratch reconstruction of a production lead-enrichment pipeline pattern. Raw lead records go in; scored, contact-enriched records come out. The vendor surface, data, weights, and prompts are all synthetic — this repo reconstructs the engineering pattern, not any real system's data or logic.

## What this is

A continuously-looping pipeline that takes messy external lead records, normalises them, runs a multi-vendor skip-trace waterfall to find owner contacts, enriches each record with an LLM-generated personalisation note, scores the result against multiple signals, and routes high-confidence records past a human-review gate directly into an outreach queue.

The architecture and reliability patterns here came from building and debugging a real production system. The data, weights, prompts, and vendor integrations are generic and synthetic throughout. Nothing in this repo reproduces any employer's proprietary configuration.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Driver (one cycle)                      │
│                                                             │
│  ┌──────────────┐                                           │
│  │ Ghost-slots  │  Re-admit sweep: clear expired cooldowns  │
│  │  re-admit    │  BEFORE pickup so stuck records re-enter  │
│  └──────┬───────┘  the pool (see "What broke" below)        │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │   Pickup     │  Poll store for unprocessed records       │
│  │   (batch N)  │  with no active cooldown                  │
│  └──────┬───────┘                                           │
│         │  for each record                                   │
│  ┌──────▼───────┐                                           │
│  │  Normalise   │  Extract name / address / classification  │
│  │              │  Fast-path if contact already present      │
│  └──────┬───────┘                                           │
│         │  (if no pre-existing contact)                     │
│  ┌──────▼───────┐                                           │
│  │  Skip-trace  │  Provider A → Provider B → Provider C     │
│  │  waterfall   │  Name matching validates each return       │
│  │              │  Deduplicates across providers             │
│  └──────┬───────┘                                           │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │  AI enrich   │  One LLM call (gpt-4o-mini) generates     │
│  │              │  a short personalisation note per record   │
│  │              │  Mock fallback when no API key is set      │
│  └──────┬───────┘                                           │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │  Score       │  6–8 illustrative signals (phone type,    │
│  │              │  email presence, DNC flag, provider conf., │
│  │              │  property class, address present)          │
│  │              │  Corroboration bonus: multi-source agree   │
│  └──────┬───────┘                                           │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │  Auto-advance│  score >= threshold AND email AND phone:   │
│  │  gate        │  → outreach_ready (skip review)            │
│  │              │  else: → pending_review                    │
│  └──────┬───────┘                                           │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │  Write sink  │  Patch record with score, contact, note,  │
│  │  (JSON/CRM)  │  stage. Local JSON file in this demo.     │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
```

## What broke / how I fixed it — the ghost-slots cooldown bug

This is the most interesting reliability problem the production version of this pipeline ran into.

**The failure mode.** When a record's waterfall lookup returns nothing (all providers miss), the driver parks the record under a cooldown: it sets `cooldown_until` to a future timestamp and marks the stage as `"cooldown"`. The intent is to retry after the cooldown expires. The pickup filter grabs records in the `"unprocessed"` stage with no active cooldown set. So far this is correct.

The bug: when the cooldown expired, _nothing reset the stage back to `"unprocessed"`_. The pickup filter checks `stage == "unprocessed" AND no cooldown set`. The cooldown sweep only cleared the timestamp — the stage was still `"cooldown"`. So the record was invisible to the pickup filter forever. It occupied a slot in the queue, the queue length stayed non-zero, but nothing ever processed it.

We called these ghost slots. The queue stayed non-empty while throughput collapsed toward zero, because the parked records could never re-enter the pickup pool.

**The fix.** A `_readmit_expired_cooldowns()` sweep runs at the very top of every driver cycle — before the pickup step. It scans all records for an expired `cooldown_until` timestamp. For any match it clears the timestamp _and_ resets the stage to `"unprocessed"`. The pickup step then finds them normally on the same cycle.

The sweep is cheap (one in-memory pass), unconditional, and idempotent. It is the single canonical place where cooldown expiry is applied, which keeps the pickup filter simple.

```python
# driver.py — the fix in one function
def _readmit_expired_cooldowns(store: dict[str, LeadRecord]) -> int:
    now = datetime.now(tz=timezone.utc)
    readmitted = 0
    for record in store.values():
        if not record.cooldown_until:
            continue
        cooldown_end = datetime.fromisoformat(record.cooldown_until)
        if now >= cooldown_end:
            record.cooldown_until = ""
            record.stage = "unprocessed"  # ← the missing step
            readmitted += 1
    return readmitted
```

The demo seeds two records with expired cooldowns (`L007`, `L008`) so you can see the sweep execute on the first run.

## File structure

```
lead-enrichment-pipeline/
├── src/
│   └── lead_enrichment/
│       ├── __init__.py         ← public API re-exports
│       ├── models.py           ← LeadRecord, ContactRecord, RunSummary
│       ├── config.py           ← env var loading, Config dataclass
│       ├── exceptions.py       ← exception hierarchy
│       ├── normalizer.py       ← stage 1: input normalisation + fast-path
│       ├── providers.py        ← stage 2: waterfall + name matching + retry
│       ├── enrichment.py       ← stage 3: LLM call + mock fallback
│       ├── scoring.py          ← stage 4: multi-signal scoring
│       ├── sink.py             ← stage 5/6: auto-advance gate + write
│       └── driver.py           ← orchestrates one cycle; ghost-slots fix
├── tests/
│   ├── conftest.py             ← shared fixtures
│   ├── test_scoring.py         ← signal scoring + corroboration bonus
│   ├── test_name_matching.py   ← phonetic/nickname/initial matching
│   └── test_cooldown_readmit.py ← ghost-slots re-admit sweep (7 cases)
├── main.py                     ← runnable demo
├── .env.example
├── requirements.txt
├── pyproject.toml
└── LICENSE
```

## Reliability patterns

**Exponential backoff on transient provider errors.** Each provider call retries up to 3 times with `wait = 1.5 * 2**attempt` seconds. A 402-equivalent `CreditExhaustedError` is terminal — it propagates immediately and halts the batch rather than burning retries that will all fail.

**Per-record isolation.** One bad record does not kill the batch. The driver wraps each record in its own try/except; errors are logged and counted; the cycle continues.

**Fast-path normalisation.** When a record already carries contact email and phone, the waterfall lookup is skipped entirely — no provider credits consumed.

**Corroboration bonus.** When two providers independently return the same email or phone for the same owner, the primary contact gets a bonus to its score. Multiple independent sources agreeing on a detail is a stronger signal than any single provider's confidence alone.

**Structured run summary.** Every cycle returns a `RunSummary` with counts of processed, advanced, review, errored, skipped, and readmitted records. The demo prints this to stdout; a production driver would push it to a monitoring channel.

## How to run the demo

No API key required. The AI enrichment stage uses a deterministic mock when `OPENAI_API_KEY` is absent.

```bash
# Clone and install
git clone <repo-url>
cd lead-enrichment-pipeline
pip install -r requirements.txt

# Run the demo (mock mode — no credentials needed)
python main.py

# Run with live OpenAI enrichment
OPENAI_API_KEY=sk-... python main.py
```

Expected output (mock mode):

```
============================================================
  lead-enrichment-pipeline demo
  enrichment mode : mock (no OPENAI_API_KEY set)
  batch size      : 20
  advance threshold: 0.65
============================================================

... log lines showing pickup, waterfall, scoring, and re-admit ...

============================================================
  Run summary
============================================================
  records processed  : 8
  auto-advanced      : 5
  parked for review  : 3
  errored            : 0
  skipped (no result): 1
  readmitted (ghost-slots fix): 2

  Sink written to: ./demo_sink.json

============================================================
  Scored records
============================================================
  L002 | score=0.97 | stage=outreach_ready | phone=555-0100 | email=yes
  L003 | score=0.97 | stage=outreach_ready | phone=555-0100 | email=yes
  L007 | score=0.97 | stage=outreach_ready | phone=555-0100 | email=yes
  L008 | score=0.97 | stage=outreach_ready | phone=555-0100 | email=yes
  L006 | score=0.85 | stage=outreach_ready | phone=555-9999 | email=yes
  L001 | score=0.77 | stage=pending_review | phone=555-0100 | email=no
  L005 | score=0.64 | stage=pending_review | phone=555-0200 | email=no
  L004 | score=0.57 | stage=pending_review | phone=555-0300 | email=no
```

The mix is intentional: `L002/L003/L007/L008` advance on a clean mobile + email hit, `L006` advances via the fast-path, and three records park at increasing waterfall depth — `L001` (provider A found a phone but no email), `L005` (fell through to provider B), `L004` (fell all the way through to provider C). One messy row with no owner name is skipped entirely.

## Run tests

```bash
pip install pytest
pytest
```

The test suite covers scoring signal weights, corroboration bonus, phonetic name matching (nicknames, initials), and 7 cases of the ghost-slots cooldown re-admit sweep.

## Design decisions worth calling out

**Providers as mock adapters, not real clients.** The three provider classes implement the `SkipTraceProvider` Protocol and return synthetic data. Swapping in a real vendor means implementing the Protocol against that vendor's API — the waterfall orchestration and retry logic stay unchanged.

**Score weights are illustrative, not tuned.** The weight constants in `scoring.py` demonstrate the signal architecture. Every production scoring system I've built has been tuned against real outcome data. The illustrative weights here should be treated as starting points only.

**Sink as a replaceable adapter.** `JSONSink` writes to a local file. A production adapter replaces it with a class that calls the real CRM's PATCH endpoint using the same `write(record)` interface. Nothing in the driver couples to the file format.

**LLM call shape.** The enrichment call uses `gpt-4o-mini` with a short system prompt and one user message. Temperature 0.8, max_tokens 200, synchronous. The prompt text in this sample is generic — it does not reproduce any proprietary prompt from any production system.

## License

MIT. See `LICENSE`.
