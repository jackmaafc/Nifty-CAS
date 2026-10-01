# ARCHITECTURE — Nifty/Sensex CE-PE Mispricing Research Terminal
# Status: FINALIZED — Schema, Stack, Components all specified.
# Branch: research/phase-0-architecture

---

## Stack
> Decisions from grilling session:
> - Language: Python 3.13
> - Database: SQLite (Unified source of truth for both Live and Backfill)
> - Data Processing: Polars (optimized for fast rolling window / time-series operations)
> - UI / Dashboarding: Plotly / Dash

---

## Database Schema

> Finalized. This is the source of truth for all table definitions.
> The schema enforces forward-only simulation by splitting candidate detection
> (Pass 1) from outcome measurement (Pass 2) into separate tables.

### Design Principles
1. **data_quality** (`TEXT NOT NULL CHECK(...)`) on every data-bearing table.
   Allowed values: `'LIVE'`, `'HISTORICAL_NO_BIDASK'`, `'CALCULATED'`,
   `'MODEL_ESTIMATE'`, `'SYNTHETIC_TEST_DATA'`.
2. **cas_regime** (`TEXT NOT NULL CHECK(...)`) on every data-bearing table.
   Allowed values: `'PRE_CAS'`, `'POST_CAS'`.
   Cutoff: rows with timestamps before 2026-08-03 09:15:00 IST → PRE_CAS.
3. **parameter_set_id** foreign key on candidate_events to support multi-pass
   scanner evaluation. Each pass uses a different threshold combination.
4. **candidate_events ↔ candidate_outcomes** are separate tables joined by
   candidate_id. candidate_events must be fully populated before
   candidate_outcomes is written. This is the architectural enforcement of
   forward-only simulation — no interleaving.

---

### Table: `market_data`
Raw spot and futures OHLC. One row per (index, timestamp, instrument_type).

```sql
CREATE TABLE market_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name      TEXT NOT NULL,          -- NIFTY / SENSEX / BANKNIFTY / FINNIFTY / MIDSELECT
    instrument_type TEXT NOT NULL,          -- SPOT / FUTURES
    timestamp       TEXT NOT NULL,          -- ISO 8601, IST
    open            REAL NOT NULL,
    high            REAL NOT NULL,
    low             REAL NOT NULL,
    close           REAL NOT NULL,
    volume          INTEGER,
    oi              INTEGER,               -- NULL for spot
    data_quality    TEXT NOT NULL CHECK(data_quality IN
                      ('LIVE','HISTORICAL_NO_BIDASK','CALCULATED',
                       'MODEL_ESTIMATE','SYNTHETIC_TEST_DATA')),
    cas_regime      TEXT NOT NULL CHECK(cas_regime IN ('PRE_CAS','POST_CAS')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(index_name, instrument_type, timestamp)
);
```

### Table: `option_snapshots`
Per-tick option chain data. One row per (index, expiry, strike, option_type, timestamp).

```sql
CREATE TABLE option_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name      TEXT NOT NULL,
    expiry_date     TEXT NOT NULL,          -- YYYY-MM-DD
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL CHECK(option_type IN ('CE','PE')),
    timestamp       TEXT NOT NULL,          -- ISO 8601, IST
    ltp             REAL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER,
    oi              INTEGER,
    iv              REAL,                   -- implied vol, NULL if unavailable
    bid_price       REAL,                   -- NULL for historical
    ask_price       REAL,                   -- NULL for historical
    bid_qty         INTEGER,
    ask_qty         INTEGER,
    delta           REAL,
    gamma           REAL,
    theta           REAL,
    vega            REAL,
    data_quality    TEXT NOT NULL CHECK(data_quality IN
                      ('LIVE','HISTORICAL_NO_BIDASK','CALCULATED',
                       'MODEL_ESTIMATE','SYNTHETIC_TEST_DATA')),
    cas_regime      TEXT NOT NULL CHECK(cas_regime IN ('PRE_CAS','POST_CAS')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(index_name, expiry_date, strike, option_type, timestamp)
);
```

### Table: `parameter_sets`
Defines each parameter combination for multi-pass scanner sweeps.

```sql
CREATE TABLE parameter_sets (
    parameter_set_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    description         TEXT,
    -- Consolidation Condition A params
    price_range_points  REAL NOT NULL,      -- X: max oscillation range in points
    price_range_min_dur INTEGER NOT NULL,   -- Y: min duration in minutes
    -- Consolidation Condition B params
    oi_ratio_threshold  REAL NOT NULL,      -- e.g. 1.5, 2.0, 3.0
    -- Deviation params
    min_deviation_pct   REAL,               -- minimum CE/PE deviation % to flag
    -- Volume spike params
    volume_spike_multiplier REAL NOT NULL DEFAULT 2.0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

### Table: `candidate_events` (Scanner Pass 1 — detection only)
Every candidate moment flagged by the scanner. Uses ONLY data available at
timestamp T. This table must be **fully populated** before candidate_outcomes
is written.

```sql
CREATE TABLE candidate_events (
    candidate_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    parameter_set_id            INTEGER NOT NULL REFERENCES parameter_sets(parameter_set_id),

    -- Identity
    index_name                  TEXT NOT NULL,
    expiry_date                 TEXT NOT NULL,
    candidate_timestamp         TEXT NOT NULL,          -- moment T (ISO 8601, IST)
    strike                      REAL NOT NULL,
    option_side_cheap           TEXT NOT NULL CHECK(option_side_cheap IN ('CE','PE')),
    cas_regime                  TEXT NOT NULL CHECK(cas_regime IN ('PRE_CAS','POST_CAS')),
    data_quality                TEXT NOT NULL CHECK(data_quality IN
                                  ('LIVE','HISTORICAL_NO_BIDASK','CALCULATED',
                                   'MODEL_ESTIMATE','SYNTHETIC_TEST_DATA')),

    -- Price Context at T
    spot_price                  REAL NOT NULL,
    futures_price               REAL,
    ce_price                    REAL NOT NULL,
    pe_price                    REAL NOT NULL,
    price_source                TEXT NOT NULL CHECK(price_source IN ('LTP','CLOSE')),
    ce_pe_ratio                 REAL NOT NULL,          -- expensive / cheap
    deviation_pct               REAL NOT NULL,          -- (expensive - cheap) / cheap * 100
    theoretical_fair_value      REAL,                   -- from Phase 3 engine
    deviation_from_fair_value   REAL,
    bid_ask_available           INTEGER NOT NULL DEFAULT 0,  -- boolean: 0 or 1
    estimated_transaction_cost  REAL,
    txn_cost_quality            TEXT CHECK(txn_cost_quality IN
                                  ('LIVE','MODEL_ESTIMATE','SYNTHETIC_TEST_DATA')),

    -- Consolidation Context at T
    consolidation_price_range_met   INTEGER NOT NULL,   -- boolean
    price_range_points              REAL,
    price_range_duration_min        REAL,
    consolidation_oi_met            INTEGER NOT NULL,   -- boolean
    oi_at_strike                    INTEGER,
    oi_adjacent_average             REAL,
    oi_ratio                        REAL,               -- strike / adjacent average
    consolidation_confluence_met    INTEGER NOT NULL,   -- both A and B true

    -- OI and Volume at T (spike moment)
    oi_change_last_5min_abs         INTEGER,
    oi_change_last_5min_pct         REAL,
    volume_last_5min                INTEGER,
    volume_vs_session_average       REAL,
    oi_change_direction             TEXT CHECK(oi_change_direction IN
                                      ('CE_INCREASING','PE_INCREASING','BOTH','NEITHER')),
    volume_spike_flag               INTEGER,            -- boolean

    -- Time Context
    time_since_open_min             REAL NOT NULL,
    time_to_close_min               REAL NOT NULL,
    session_bucket                  TEXT NOT NULL CHECK(session_bucket IN
                                      ('OPEN_30','FIRST_HOUR','MID_SESSION',
                                       'POST_LUNCH','FINAL_HOUR','FINAL_30')),

    created_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(parameter_set_id, index_name, expiry_date, candidate_timestamp, strike)
);
```

### Table: `candidate_outcomes` (Scanner Pass 2 — forward measurement only)
Written AFTER candidate_events is fully populated. Looks forward from T.
Never used to inform candidate detection.

```sql
CREATE TABLE candidate_outcomes (
    outcome_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id                INTEGER NOT NULL UNIQUE REFERENCES candidate_events(candidate_id),

    -- Forward price snapshots
    outcome_5min_ce_price       REAL,
    outcome_5min_pe_price       REAL,
    outcome_10min_ce_price      REAL,
    outcome_10min_pe_price      REAL,
    outcome_15min_ce_price      REAL,
    outcome_15min_pe_price      REAL,
    outcome_30min_ce_price      REAL,
    outcome_30min_pe_price      REAL,

    -- Convergence
    convergence_timestamp       TEXT,                   -- ISO 8601, IST; NULL if never converged
    convergence_pct_captured    REAL,
    post_convergence_direction  TEXT CHECK(post_convergence_direction IN
                                  ('UP','DOWN','FLAT','DID_NOT_CONVERGE')),

    -- Outcome classification
    outcome_category            TEXT NOT NULL CHECK(outcome_category IN
                                  ('REVERSION_COMPLETE','REVERSION_DEFERRED','EXTENSION')),

    -- Excursion
    max_adverse_excursion       REAL,
    max_favorable_excursion     REAL,

    data_quality                TEXT NOT NULL CHECK(data_quality IN
                                  ('LIVE','HISTORICAL_NO_BIDASK','CALCULATED',
                                   'MODEL_ESTIMATE','SYNTHETIC_TEST_DATA')),

    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

### Table: `backfill_progress`
Resumable, checkpointed batch job tracker. One row per unit of work.

```sql
CREATE TABLE backfill_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name      TEXT NOT NULL,
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL CHECK(option_type IN ('CE','PE')),
    date_range_start TEXT NOT NULL,         -- YYYY-MM-DD
    date_range_end  TEXT NOT NULL,          -- YYYY-MM-DD
    status          TEXT NOT NULL CHECK(status IN
                      ('PENDING','IN_PROGRESS','COMPLETED','FAILED','RATE_LIMITED')),
    rows_fetched    INTEGER DEFAULT 0,
    error_message   TEXT,
    last_updated    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(index_name, strike, option_type, date_range_start, date_range_end)
);
```

### Table: `bias_log`
User's daily discretionary input on expiry days.

```sql
CREATE TABLE bias_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date            TEXT NOT NULL,      -- YYYY-MM-DD (expiry day)
    index_name          TEXT NOT NULL,
    bias_direction      TEXT NOT NULL CHECK(bias_direction IN ('BULLISH','BEARISH','NEUTRAL')),
    confidence_level    INTEGER NOT NULL CHECK(confidence_level BETWEEN 1 AND 5),
    primary_factor      TEXT NOT NULL CHECK(primary_factor IN
                          ('SMC','OI','PROP_POSITIONING','OTHER')),
    secondary_factors   TEXT,               -- JSON array of additional factors
    override_flag       INTEGER NOT NULL DEFAULT 0,  -- boolean
    override_reason     TEXT,
    notes               TEXT,
    -- End-of-day capture
    eod_move_completed  TEXT CHECK(eod_move_completed IN
                          ('COMPLETED_SAME_SESSION','DEFERRED','DID_NOT_COMPLETE',NULL)),
    eod_notes           TEXT,
    is_backfill         INTEGER NOT NULL DEFAULT 0,  -- boolean: reconstructed from memory
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(log_date, index_name)
);
```

### Table: `proxy_engine_log`
Rules-based proxy readings alongside user bias for comparison.

```sql
CREATE TABLE proxy_engine_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date            TEXT NOT NULL,
    index_name          TEXT NOT NULL,
    timestamp           TEXT NOT NULL,      -- ISO 8601, IST

    -- SMC proxy (N-bar fractal)
    smc_structure_state TEXT CHECK(smc_structure_state IN
                          ('BOS_UP','BOS_DOWN','CHOCH_UP','CHOCH_DOWN','RANGING','UNKNOWN')),
    smc_n_bars          INTEGER,            -- N parameter used
    last_pivot_high     REAL,
    last_pivot_low      REAL,

    -- OI/PCR proxy
    pcr_ratio           REAL,
    pcr_shift_5min      REAL,
    oi_buildup_bias     TEXT CHECK(oi_buildup_bias IN ('CALL_HEAVY','PUT_HEAVY','BALANCED')),

    -- Other proxies
    opening_range_high  REAL,
    opening_range_low   REAL,
    vwap_position       TEXT CHECK(vwap_position IN ('ABOVE','BELOW','AT')),

    -- Comparison fields (filled after bias_log entry exists)
    agrees_with_user    INTEGER,            -- boolean; NULL until compared
    actual_outcome      TEXT,               -- filled end-of-day

    data_quality        TEXT NOT NULL CHECK(data_quality IN
                          ('LIVE','HISTORICAL_NO_BIDASK','CALCULATED',
                           'MODEL_ESTIMATE','SYNTHETIC_TEST_DATA')),
    cas_regime          TEXT NOT NULL CHECK(cas_regime IN ('PRE_CAS','POST_CAS')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(log_date, index_name, timestamp)
);
```

### Table: `session_events`
Collector lifecycle and gap logging.

```sql
CREATE TABLE session_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date    TEXT NOT NULL,
    index_name      TEXT,
    event_type      TEXT NOT NULL CHECK(event_type IN
                      ('STARTUP','SUBSCRIPTION','GAP_DETECTED','RECONNECT',
                       'ERROR','SHUTDOWN','EXPIRY_ROLLOVER','ATM_RECALC')),
    event_timestamp TEXT NOT NULL,
    details         TEXT,                   -- JSON blob for event-specific data
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
```

---

## System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA LAYER                               │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐   │
│  │ Backfill Job │    │ Live         │    │ Synthetic Data   │   │
│  │ (Phase 1)    │    │ Collector    │    │ Generator        │   │
│  │              │    │ (Phase 2)    │    │ (Phase 0.25)     │   │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘   │
│         │                   │                     │             │
│         └───────────────────┼─────────────────────┘             │
│                             ▼                                   │
│                    ┌────────────────┐                            │
│                    │   SQLite DB    │ ← Unified source of truth │
│                    │  (research.db) │                            │
│                    └────────┬───────┘                            │
│                             │                                   │
└─────────────────────────────┼───────────────────────────────────┘
                              │
┌─────────────────────────────┼───────────────────────────────────┐
│                     ANALYSIS LAYER                              │
│                             │                                   │
│  ┌──────────────────────────┼──────────────────────────┐        │
│  │        Scanner (Phase 4)                            │        │
│  │  ┌────────────────┐   ┌─────────────────────────┐   │        │
│  │  │ Pass 1:        │   │ Pass 2:                 │   │        │
│  │  │ Candidate      │──▶│ Forward Outcome         │   │        │
│  │  │ Detection      │   │ Measurement             │   │        │
│  │  │ (data ≤ T)     │   │ (data > T, post-Pass 1) │   │        │
│  │  └────────────────┘   └─────────────────────────┘   │        │
│  └─────────────────────────────────────────────────────┘        │
│                                                                 │
│  ┌────────────────┐    ┌────────────────┐                       │
│  │ Fair Value     │    │ Statistical    │                       │
│  │ Engine         │    │ Analysis       │                       │
│  │ (Phase 3)      │    │ (Phase 5)      │                       │
│  └────────────────┘    └────────────────┘                       │
│                                                                 │
│  ┌────────────────┐    ┌────────────────┐                       │
│  │ Proxy Engine   │    │ Bias Logger    │                       │
│  │ (Phase 5.5)    │    │ (Phase 5.5)    │                       │
│  └────────────────┘    └────────────────┘                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────┼───────────────────────────────────┐
│                      UI LAYER (Plotly / Dash)                   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ Dashboard (Phase 6)                                      │   │
│  │  • Session replay view                                   │   │
│  │  • Deviation + OI charts                                 │   │
│  │  • Outcome heatmap                                       │   │
│  │  • Bias logging panel (prominent on expiry days)         │   │
│  │  • Proxy vs User comparison panel                        │   │
│  │  • Export: CSV / Excel with all tags                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow Rules
1. All data enters the DB before any analysis reads it.
2. The Scanner tails the DB — same code path for historical and live.
3. Pass 1 writes `candidate_events`. Pass 2 reads `candidate_events` and
   writes `candidate_outcomes`. Never interleaved.
4. Every row carries `data_quality` and `cas_regime`. No exceptions.

---

## Known Hard Constraints (do not re-litigate these)

### DhanHQ API
- Data API subscription required: ₹499+tax/month
  (live quotes, market depth, option chain, historical/expired options)
- Order/trading APIs are free but not useful for this project alone
- Expired options endpoint: one strike, one side (CE or PUT), 30-day max per call
- Option chain endpoint: full chain per call, rate limit 1 per 3 seconds
- Rate limits: Data API 5 req/sec, 100k/day | Quote API 1 req/sec unlimited
- dhanhq Python library: v2.2.0 is the current stable release on PyPI (as of Oct 2026).
  v2.2.0 introduced the DhanContext interface as a breaking change FROM the
  older direct-init pattern. Pin to dhanhq==2.2.0. All code must use:
    from dhanhq import DhanContext, dhanhq
    context = DhanContext(client_id, access_token)
    dhan = dhanhq(context)
  Do NOT use the pre-2.2.0 direct constructor pattern. If a future version
  is released, test it in isolation before upgrading the pin.
- Historical data has no bid/ask — OHLC/IV/OI/volume only
  Tag all historical calculations: HISTORICAL_NO_BIDASK

### CAS Regime
- Closing Auction Session went live: August 3, 2026
- Pre-CAS and post-CAS data must be tagged separately on every record
- No pre/post comparison until post-CAS sample is statistically meaningful
- Report n= (sample size) explicitly every time a comparison is shown

### Index Scope
- Historical backfill: Nifty50, Sensex, Bank Nifty, FinNifty, Nifty MidSelect
- Live collection: Nifty50 and Sensex only (weekly expiry retained post-Nov 2024)
- Never pool across indices by default — stratify first

### What Cannot Be Built Until Dhan Subscription Is Active
- Any real historical data fetch
- Any live data collection
- Any calculation using real OI, Greeks, bid-ask, or volume
- Any finding labeled as real (not synthetic)

---

## Bottlenecks & Failed Approaches Log
> Updated here whenever an approach fails so it is never re-recommended.
> Format: [Date] Approach tried → Why it failed → What replaced it
