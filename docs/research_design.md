# RESEARCH DESIGN — CE-PE Mispricing Research Terminal
# This document defines the research logic the system must implement.
# Antigravity must read this before building Phase 4 (scanner) and Phase 5 (analysis).

---

## 1. The Research Goal

This is an observation-first project. No fixed trading rules exist yet.
The system's job is to discover rules from data, not confirm a pre-existing belief.

The system must be equally capable of concluding:
- "The edge exists and is statistically robust"
- "The edge exists but only under narrow conditions"
- "The edge is too small to trade after costs"
- "The edge does not exist in the data"

All four conclusions are acceptable. The report format must make any of them
easy to state plainly.

---

## 2. The Core Hypothesis (preserved exactly)

On expiry days, when spot consolidates near a strike in the latter half of
the session, ATM CE and PE should be priced near-equally — the market is
pricing "we expire here."

When a short-term move pushes spot away from that strike, one side overreacts
and spikes disproportionately. The other side becomes artificially cheap.

The hypothesis: this overreaction is temporary. Price returns to the
consolidation equilibrium. Buying the cheap side captures that reversion.

The user's current discretionary entry relies on confluence of:
- SMC price structure (BOS/CHoCH, order blocks, premium/discount zones)
- OI buildup at the strike relative to adjacent strikes
- Volume at the moment of the spike
- Judgment about whether the spike is manipulation vs informed flow

The system's job is to find the objective, measurable components of this
confluence and test them.

---

## 3. Forward-Only Simulation — Architectural Enforcement

### The Constraint
At each candidate moment T, only data available up to T may be used to
identify whether a setup exists. The system follows forward from T to
measure outcome. The end-of-day result must never inform the entry signal.

### Why This Matters
A scanner that knows the end of day will always find "correct" entries
in hindsight. This produces a useless, over-optimistic result that cannot
be replicated in live trading. This constraint is the single most important
design requirement in the entire system.

### How to Enforce It Architecturally (not just in comments)
1. The scanner runs in a time-ordered loop over each expiry session.
2. At each timestamp T, the scanner has access to a rolling window of data
   ending at T. It must not access any row with timestamp > T during
   candidate detection.
3. Candidate detection and outcome measurement are separate database passes:
   Pass 1 (detection): scan forward in time, flag candidates using data ≤ T only.
   Pass 2 (outcomes): for each flagged candidate, look forward from T to measure
   what happened. This pass is allowed to see future data, but only for outcome
   measurement, never for signal generation.
4. The candidate_events table must be fully populated before outcome measurement
   begins. No interleaving.
5. Any analyst reviewing the code must be able to verify this separation by
   inspecting the two passes independently.

## 3.5 Fair Value Engine (Phase 3)
> Decision: We will use the front-month futures price to reverse-engineer the implied cost of carry (interest rate - dividend yield). This is computationally cheap and reflects actual market pricing accurately for Nifty/Sensex intraday.

---

## 4. Consolidation Detection Logic

### Definition (both conditions required — confluence)

**Condition A — Price Range:**
Spot has been oscillating within X points of a strike for at least Y minutes
immediately before the candidate moment T.
- Test multiple (X, Y) combinations: e.g. X = 20/30/50 points, Y = 10/15/20/30 min
- Report which combination correlates best with successful reversion outcomes
- Also test Condition A alone vs. Condition B alone vs. both together

**Condition B — OI Buildup:**
OI at the candidate strike is materially higher than OI at adjacent strikes
(±1 and ±2 strikes) at moment T.
- "Materially higher": test multiple thresholds (e.g. 1.5x, 2x, 3x adjacent average)
- Report which threshold correlates best with successful reversion outcomes

**Combined Filter:**
Both A and B must be true for a candidate to be flagged.
Report separately: hit rate with A only, B only, A+B. This shows which
condition is doing more of the predictive work.

---

## 5. Candidate Event Schema

Every candidate moment must log ALL of the following fields:

### Identity
- candidate_id (unique)
- index (NIFTY / SENSEX / BANKNIFTY / FINNIFTY / MIDSELECT)
- expiry_date
- candidate_timestamp (moment T)
- strike
- option_side_cheap (CE or PE — the side being considered for entry)
- cas_regime (PRE_CAS / POST_CAS)
- data_quality (LIVE / HISTORICAL_NO_BIDASK / SYNTHETIC_TEST_DATA)

### Price Context at T
- spot_price
- futures_price
- ce_price (LTP or close — label source)
- pe_price (LTP or close — label source)
- ce_pe_ratio (expensive / cheap)
- deviation_pct ((expensive - cheap) / cheap * 100)
- theoretical_fair_value (from Phase 3 engine)
- deviation_from_fair_value
- bid_ask_available (boolean — false for all historical data)
- estimated_transaction_cost (if bid_ask not available, label as MODEL_ESTIMATE)

### Consolidation Context at T
- consolidation_price_range_met (boolean)
- price_range_points (actual range over lookback window)
- price_range_duration_min (how long in range)
- consolidation_oi_met (boolean)
- oi_at_strike
- oi_adjacent_average
- oi_ratio (strike / adjacent average)
- consolidation_confluence_met (both A and B true)

### OI and Volume at T (spike moment)
- oi_change_last_5min (absolute and %)
- volume_last_5min
- volume_vs_session_average (ratio)
- oi_change_direction (CE increasing / PE increasing / both / neither)
- volume_spike_flag (boolean — volume > 2x session average, test threshold)

### Time Context
- time_since_open_min
- time_to_close_min
- session_bucket (OPEN_30 / FIRST_HOUR / MID_SESSION / POST_LUNCH / FINAL_HOUR / FINAL_30)

### Forward Outcomes (Pass 2 only — never used in Pass 1)
- outcome_5min_ce_price
- outcome_5min_pe_price
- outcome_10min_ce_price
- outcome_10min_pe_price
- outcome_15min_ce_price
- outcome_15min_pe_price
- outcome_30min_ce_price
- outcome_30min_pe_price
- convergence_timestamp (when CE and PE reached price parity, if they did)
- convergence_pct_captured (% of deviation that closed at convergence)
- post_convergence_direction (UP / DOWN / FLAT / DID_NOT_CONVERGE)
- outcome_category:
    REVERSION_COMPLETE — reversion completed same session
    REVERSION_DEFERRED — reversion correct but incomplete by close
    EXTENSION — move extended, reversion did not occur
- max_adverse_excursion (worst point against entry before close)
- max_favorable_excursion (best point for entry before close)

---

## 6. Statistical Analysis Plan (Phase 5)

### Minimum Sample Size Before Reporting a Finding
- Do not report any pattern as a finding with n < 30 instances
- Report 30–99 instances as PRELIMINARY FINDING — label explicitly
- Report 100+ instances as FINDING — still include confidence interval
- Always show n= next to every statistic, every time

### Questions Answered in Order

Q1: Deviation magnitude
- Distribution of CE/PE deviation ratios across all expiry sessions
- Hit rate (reversion complete) by deviation bucket (1.5x, 2x, 2.5x, 3x+)
- Does higher deviation correlate with higher hit rate, or higher risk of extension?

Q2: Consolidation filter value
- Hit rate with consolidation filter on vs off
- Hit rate with price condition only vs OI condition only vs both
- False positive rate (setups that looked like consolidation but extended)

Q3: Time of day
- Setup frequency by session bucket (defined above)
- Hit rate by session bucket
- Do not assume 1–2:30 PM — verify whether this is the best window

Q4: OI and volume signature at spike
- Compare OI change direction and volume spike between:
  REVERSION_COMPLETE outcomes vs EXTENSION outcomes
- Is there a measurable signature that separates the two at moment T?
- This is the manipulation detection question — report honestly if no signal found

Q5: Index comparison
- Run Q1–Q4 separately per index
- Report whether results are similar enough to justify pooling
- If pooled: report that this is a tested assumption, not a default

Q6: Exit timing
- Compare expectancy: convergence exit vs 5/10/15/30 min time exit
- Account for theta decay during the convergence window
- Report which exit produces better risk-adjusted expectancy
- Include REVERSION_DEFERRED outcomes in this analysis — they change the picture

### What Not To Do
- Never report a single "optimal" threshold number — report a range
- Never search for the best parameters and report only those — report the full range
- Never re-tune parameters on new data without noting this explicitly
- Never present a finding from n < 30 as reliable — say so directly

---

## 7. The Manipulation Problem

### The Question
A sudden PE spike near a consolidating strike could be:
(a) Temporary — retail panic, delta hedging, short-term flow — price returns
(b) Informed — institutional positioning ahead of a real move — price extends

From historical OHLC/OI/volume data alone, this cannot be determined with
certainty at the moment of the spike. This is an honest limitation.

### What the System Can Do
Log the OI and volume signature at the moment of the spike for every candidate.
Over enough instances, compare signatures between REVERSION_COMPLETE and
EXTENSION outcomes.

Potential signals to test:
- OI increasing on the expensive side (PE spiking + PE OI rising = possible real flow)
- OI decreasing on the expensive side (PE spiking + PE OI falling = possible unwind)
- Volume spike on the cheap side (CE volume surge = possible informed counter-buying)
- Futures price moving with or against the spot move at the spike moment

Report honestly: if no measurable signal separates the two cases, say so.
This is a valid research finding, not a failure.

---

## 8. Honest Limitations

The following questions cannot be answered from this system alone:

1. **Bid-ask on historical data**: All historical calculations use LTP/close as
   proxy. Real tradeable edge may be smaller after actual spread costs.
   Label all historical calculations HISTORICAL_NO_BIDASK and do not present
   them at the same confidence level as live-collected data.

2. **Pre/post CAS comparison**: Post-CAS sample is currently 1–2 expiry sessions.
   This is an anecdote, not a finding. Report n= every time. Do not attempt
   a meaningful pre/post conclusion until post-CAS n ≥ 30.

3. **The user's discretionary input**: The system can proxy SMC structure and
   OI/PCR, but cannot replicate the user's full discretionary read. Phase 5.5
   exists to measure the gap between proxy and discretionary over time.
   > SMC Proxy Decision: We will use a classic N-bar fractal definition to identify structural pivots (e.g., a swing high is defined as a peak where the N bars before and after are lower). A Break of Structure (BOS) will be a candle close beyond the most recent pivot. The N parameter will be configurable.

4. **Execution quality**: The system assumes fills at LTP. Real fills depend on
   liquidity, order type, and market impact. This affects expectancy calculations.
   Label all expectancy figures as MODEL (assumes LTP fill) until live execution
   data exists.

5. **Small total sample**: ~150 weekly expiries over 3 years, filtered to actual
   qualifying setups, likely yields a few dozen historical instances per index.
   This is a small sample. Patterns found here are hypotheses to monitor going
   forward, not proven edges. Say this explicitly in every Phase 5 report.
