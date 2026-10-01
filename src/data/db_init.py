"""
Database initialization for the CE-PE Mispricing Research Terminal.

Creates data/db/research.db with the full schema:
  1. backfill_progress    — resumable checkpointed backfill job tracker
  2. raw_options_data     — historical + live option chain data
  3. candidate_events     — Pass 1 scanner output (detection only, no outcomes)
  4. forward_outcomes     — Pass 2 outcomes (linked to candidate_events)
  5. bias_log             — user discretionary input on expiry days
  6. proxy_engine_log     — rules-based SMC/OI/PCR proxy readings

Forward-only simulation enforcement:
  - candidate_events and forward_outcomes are separate tables.
  - candidate_events must be fully populated (detection_pass_complete = 1)
    before forward_outcomes rows may be inserted for that candidate.
  - A CHECK constraint on forward_outcomes enforces this via a trigger.

Data integrity:
  - Every data-bearing table has data_quality and cas_regime columns
    with CHECK constraints enforcing allowed values.
  - data_quality: LIVE, HISTORICAL_NO_BIDASK, SYNTHETIC_TEST_DATA
  - cas_regime: PRE_CAS, POST_CAS (cutoff: 2026-08-03)
"""

import sqlite3
import os
from pathlib import Path

# Resolve paths relative to the project root (two levels up from src/data/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_DIR = PROJECT_ROOT / "data" / "db"
DB_PATH = DB_DIR / "research.db"

SCHEMA_SQL = """
-- ============================================================
-- Table 1: backfill_progress
-- Resumable checkpointed batch job tracker.
-- One row per (index, strike, option_side, date chunk).
-- ============================================================
CREATE TABLE IF NOT EXISTS backfill_progress (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name      TEXT NOT NULL,          -- NIFTY / SENSEX / BANKNIFTY / FINNIFTY / MIDSELECT
    strike          REAL NOT NULL,
    option_side     TEXT NOT NULL CHECK(option_side IN ('CE', 'PE')),
    date_from       TEXT NOT NULL,          -- YYYY-MM-DD
    date_to         TEXT NOT NULL,          -- YYYY-MM-DD
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK(status IN ('PENDING', 'IN_PROGRESS', 'COMPLETE', 'FAILED')),
    last_updated    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    error_log       TEXT,                   -- error message if FAILED
    UNIQUE(index_name, strike, option_side, date_from, date_to)
);

-- ============================================================
-- Table 2: raw_options_data
-- Per-timestamp option chain data (historical + live).
-- One row per (index, expiry, strike, option_side, timestamp).
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_options_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    index_name      TEXT NOT NULL,
    expiry_date     TEXT NOT NULL,          -- YYYY-MM-DD
    strike          REAL NOT NULL,
    option_side     TEXT NOT NULL CHECK(option_side IN ('CE', 'PE')),
    timestamp       TEXT NOT NULL,          -- ISO 8601, IST
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER,
    oi              INTEGER,
    iv              REAL,                   -- implied volatility, NULL if unavailable
    spot_price      REAL,
    futures_price   REAL,
    bid             REAL,                   -- NULL for historical (no bid-ask)
    ask             REAL,                   -- NULL for historical (no bid-ask)
    data_quality    TEXT NOT NULL
                    CHECK(data_quality IN ('LIVE', 'HISTORICAL_NO_BIDASK', 'SYNTHETIC_TEST_DATA')),
    cas_regime      TEXT NOT NULL
                    CHECK(cas_regime IN ('PRE_CAS', 'POST_CAS')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_options_data_lookup
    ON raw_options_data(index_name, expiry_date, strike, option_side, timestamp);

-- ============================================================
-- Table 3: candidate_events
-- Scanner Pass 1 output ONLY — candidate detection.
-- Contains identity, price context, consolidation context,
-- OI/volume context, and time context fields.
-- NO outcome fields — those live in forward_outcomes.
--
-- detection_pass_complete: set to 1 only when the full session
-- scan is done. Never set during scanning. This flag gates
-- whether forward_outcomes may be written for this candidate.
-- ============================================================
CREATE TABLE IF NOT EXISTS candidate_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identity (Section 5.1 of research_design.md)
    index_name                  TEXT NOT NULL,
    expiry_date                 TEXT NOT NULL,          -- YYYY-MM-DD
    candidate_timestamp         TEXT NOT NULL,          -- moment T, ISO 8601 IST
    strike                      REAL NOT NULL,
    option_side_cheap           TEXT NOT NULL CHECK(option_side_cheap IN ('CE', 'PE')),
    cas_regime                  TEXT NOT NULL CHECK(cas_regime IN ('PRE_CAS', 'POST_CAS')),
    data_quality                TEXT NOT NULL
                                CHECK(data_quality IN ('LIVE', 'HISTORICAL_NO_BIDASK', 'SYNTHETIC_TEST_DATA')),

    -- Price Context at T (Section 5.2)
    spot_price                  REAL NOT NULL,
    futures_price               REAL,
    ce_price                    REAL NOT NULL,
    pe_price                    REAL NOT NULL,
    price_source                TEXT NOT NULL CHECK(price_source IN ('LTP', 'CLOSE')),
    ce_pe_ratio                 REAL NOT NULL,          -- expensive / cheap
    deviation_pct               REAL NOT NULL,          -- (expensive - cheap) / cheap * 100
    theoretical_fair_value      REAL,                   -- from Phase 3 fair value engine
    deviation_from_fair_value   REAL,
    bid_ask_available           INTEGER NOT NULL DEFAULT 0,  -- 0 = false, 1 = true
    estimated_transaction_cost  REAL,
    txn_cost_data_quality       TEXT                    -- label: LIVE or MODEL_ESTIMATE
                                CHECK(txn_cost_data_quality IS NULL
                                      OR txn_cost_data_quality IN ('LIVE', 'MODEL_ESTIMATE')),

    -- Consolidation Context at T (Section 5.3)
    consolidation_price_range_met   INTEGER NOT NULL,   -- boolean
    price_range_points              REAL,               -- actual range over lookback window
    price_range_duration_min        REAL,               -- how long spot stayed in range
    consolidation_oi_met            INTEGER NOT NULL,   -- boolean
    oi_at_strike                    INTEGER,
    oi_adjacent_average             REAL,
    oi_ratio                        REAL,               -- strike OI / adjacent average
    consolidation_confluence_met    INTEGER NOT NULL,    -- both A and B true

    -- OI and Volume at T — spike moment (Section 5.4)
    oi_change_last_5min_abs         INTEGER,            -- absolute change
    oi_change_last_5min_pct         REAL,               -- % change
    volume_last_5min                INTEGER,
    volume_vs_session_average       REAL,               -- ratio
    oi_change_direction             TEXT
                                    CHECK(oi_change_direction IS NULL
                                          OR oi_change_direction IN (
                                              'CE_INCREASING', 'PE_INCREASING', 'BOTH', 'NEITHER')),
    volume_spike_flag               INTEGER,            -- boolean (volume > 2x session avg)

    -- Time Context (Section 5.5)
    time_since_open_min             REAL NOT NULL,
    time_to_close_min               REAL NOT NULL,
    session_bucket                  TEXT NOT NULL
                                    CHECK(session_bucket IN (
                                        'OPEN_30', 'FIRST_HOUR', 'MID_SESSION',
                                        'POST_LUNCH', 'FINAL_HOUR', 'FINAL_30')),

    -- Forward-only enforcement flag
    -- Set to 1 ONLY when the full session detection pass is complete.
    -- Must remain 0 during scanning. forward_outcomes rows are gated on this.
    detection_pass_complete         INTEGER NOT NULL DEFAULT 0
                                    CHECK(detection_pass_complete IN (0, 1)),

    created_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE(index_name, expiry_date, candidate_timestamp, strike, option_side_cheap)
);

-- ============================================================
-- Table 4: forward_outcomes
-- Scanner Pass 2 ONLY — forward outcome measurement.
-- Linked to candidate_events by candidate_id (FK).
-- A trigger enforces that this table cannot be written to
-- unless the corresponding candidate_events row has
-- detection_pass_complete = 1.
-- ============================================================
CREATE TABLE IF NOT EXISTS forward_outcomes (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id                INTEGER NOT NULL UNIQUE,

    -- Forward price snapshots from moment T
    outcome_5min_ce_price       REAL,
    outcome_5min_pe_price       REAL,
    outcome_10min_ce_price      REAL,
    outcome_10min_pe_price      REAL,
    outcome_15min_ce_price      REAL,
    outcome_15min_pe_price      REAL,
    outcome_30min_ce_price      REAL,
    outcome_30min_pe_price      REAL,

    -- Convergence
    convergence_timestamp       TEXT,           -- ISO 8601 IST; NULL if never converged
    convergence_pct_captured    REAL,           -- % of deviation that closed
    post_convergence_direction  TEXT
                                CHECK(post_convergence_direction IS NULL
                                      OR post_convergence_direction IN (
                                          'UP', 'DOWN', 'FLAT', 'DID_NOT_CONVERGE')),

    -- Outcome classification
    outcome_category            TEXT NOT NULL
                                CHECK(outcome_category IN (
                                    'REVERSION_COMPLETE', 'REVERSION_DEFERRED', 'EXTENSION')),

    -- Excursion
    max_adverse_excursion       REAL,           -- worst point against entry before close
    max_favorable_excursion     REAL,           -- best point for entry before close

    data_quality                TEXT NOT NULL
                                CHECK(data_quality IN ('LIVE', 'HISTORICAL_NO_BIDASK', 'SYNTHETIC_TEST_DATA')),

    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    FOREIGN KEY (candidate_id) REFERENCES candidate_events(id)
);

-- Trigger: enforce that forward_outcomes can only be inserted when
-- the corresponding candidate_events row has detection_pass_complete = 1.
-- This is the DB-level enforcement of the two-pass separation.
CREATE TRIGGER IF NOT EXISTS enforce_forward_only
    BEFORE INSERT ON forward_outcomes
    FOR EACH ROW
    WHEN (SELECT detection_pass_complete FROM candidate_events WHERE id = NEW.candidate_id) != 1
    BEGIN
        SELECT RAISE(ABORT, 'Cannot insert forward_outcomes: candidate_events.detection_pass_complete must be 1. Forward-only simulation violated.');
    END;

-- ============================================================
-- Table 5: bias_log
-- User's daily discretionary input on expiry days.
-- ============================================================
CREATE TABLE IF NOT EXISTS bias_log (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date                        TEXT NOT NULL,      -- YYYY-MM-DD
    expiry_date                     TEXT NOT NULL,      -- YYYY-MM-DD (the expiry being traded)
    index_name                      TEXT NOT NULL,
    bias_direction                  TEXT NOT NULL
                                    CHECK(bias_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')),
    confidence                      INTEGER NOT NULL
                                    CHECK(confidence BETWEEN 1 AND 5),
    factors_smc                     INTEGER NOT NULL DEFAULT 0,   -- boolean
    factors_oi                      INTEGER NOT NULL DEFAULT 0,   -- boolean
    factors_prop                    INTEGER NOT NULL DEFAULT 0,   -- boolean
    factors_other                   TEXT,                          -- free text
    override_flag                   INTEGER NOT NULL DEFAULT 0,   -- boolean
    override_reason                 TEXT,
    -- End-of-day capture (filled after close)
    move_completed_same_session     INTEGER,            -- boolean, NULL until filled
    -- Proxy comparison (filled by proxy engine, not user)
    proxy_bias_direction            TEXT
                                    CHECK(proxy_bias_direction IS NULL
                                          OR proxy_bias_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')),
    proxy_vs_user_agreement         INTEGER,            -- boolean, NULL until compared
    created_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(log_date, index_name)
);

-- ============================================================
-- Table 6: proxy_engine_log
-- Rules-based proxy readings alongside user bias for comparison.
-- ============================================================
CREATE TABLE IF NOT EXISTS proxy_engine_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp               TEXT NOT NULL,          -- ISO 8601, IST
    index_name              TEXT NOT NULL,
    expiry_date             TEXT NOT NULL,           -- YYYY-MM-DD
    -- SMC proxy (N-bar fractal)
    smc_structure_state     TEXT
                            CHECK(smc_structure_state IS NULL
                                  OR smc_structure_state IN (
                                      'BOS_UP', 'BOS_DOWN', 'CHOCH_UP', 'CHOCH_DOWN',
                                      'RANGING', 'UNKNOWN')),
    -- OI/PCR proxy
    oi_pcr_value            REAL,
    -- Opening range
    opening_range_high      REAL,
    opening_range_low       REAL,
    -- VWAP
    vwap_position           TEXT
                            CHECK(vwap_position IS NULL
                                  OR vwap_position IN ('ABOVE', 'BELOW', 'AT')),
    -- Proxy conclusion
    proxy_bias_output       TEXT
                            CHECK(proxy_bias_output IS NULL
                                  OR proxy_bias_output IN ('BULLISH', 'BEARISH', 'NEUTRAL')),
    -- Data tags
    data_quality            TEXT NOT NULL
                            CHECK(data_quality IN ('LIVE', 'HISTORICAL_NO_BIDASK', 'SYNTHETIC_TEST_DATA')),
    cas_regime              TEXT NOT NULL
                            CHECK(cas_regime IN ('PRE_CAS', 'POST_CAS')),
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_proxy_engine_log_lookup
    ON proxy_engine_log(index_name, expiry_date, timestamp);
"""


def init_db() -> str:
    """Create the database and all tables. Returns the DB path."""
    DB_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL;")      # better concurrent read perf
    conn.execute("PRAGMA foreign_keys=ON;")        # enforce FK constraints
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # Verify all tables exist
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    )
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    return str(DB_PATH), tables


if __name__ == "__main__":
    db_path, tables = init_db()
    print(f"Database created: {db_path}")
    print(f"Tables ({len(tables)}):")
    for t in tables:
        print(f"  - {t}")
