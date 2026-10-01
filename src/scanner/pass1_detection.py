import sqlite3
import datetime
from pathlib import Path
from src.engine.fair_value import calculate_fair_value_metrics
from src.scanner.consolidation import check_consolidation_confluence

def parse_iso_time(iso_str):
    return datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))

def run_pass1_detection(db_path, index_name, expiry_date):
    """
    Runs the forward-only scanner pass 1 for a single expiry session.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # 1. Get all unique timestamps in the session
    cur.execute('''
        SELECT DISTINCT timestamp FROM raw_options_data
        WHERE index_name = ? AND expiry_date = ?
        ORDER BY timestamp ASC
    ''', (index_name, expiry_date))
    
    all_timestamps = [row['timestamp'] for row in cur.fetchall()]
    if not all_timestamps:
        return 0
        
    session_start_time = parse_iso_time(all_timestamps[0])
    session_end_time = parse_iso_time(all_timestamps[-1])
    
    # Deduplication state
    active_setups = {} # (strike, option_side) -> last_seen_time
    
    inserted_candidates = []
    
    for current_timestamp_str in all_timestamps:
        current_time = parse_iso_time(current_timestamp_str)
        
        # 1. Fetch all raw_options_data rows for this session where timestamp <= T
        # Strictly forward-only
        cur.execute('''
            SELECT * FROM raw_options_data
            WHERE index_name = ? AND expiry_date = ? AND timestamp <= ?
            ORDER BY timestamp ASC
        ''', (index_name, expiry_date, current_timestamp_str))
        
        history_rows = cur.fetchall()
        
        # We need spot_prices and timestamps up to current_time for condition A
        # Since spot is same across all strikes for a timestamp, we can distinct them
        time_to_spot = {}
        for r in history_rows:
            time_to_spot[r['timestamp']] = r['spot_price']
        
        sorted_times = sorted(time_to_spot.keys())
        spot_prices = [time_to_spot[t] for t in sorted_times]
        dt_timestamps = [parse_iso_time(t) for t in sorted_times]
        
        # Get current rows
        current_rows = [r for r in history_rows if r['timestamp'] == current_timestamp_str]
        spot_price = current_rows[0]['spot_price'] if current_rows else 0
        
        time_since_open_min = (current_time - session_start_time).total_seconds() / 60
        time_to_close_min = (session_end_time - current_time).total_seconds() / 60
        
        # We process each strike
        # Need strikes present in current_rows
        strikes = list(set([r['strike'] for r in current_rows]))
        
        for strike in strikes:
            ce_row = next((r for r in current_rows if r['strike'] == strike and r['option_side'] == 'CE'), None)
            pe_row = next((r for r in current_rows if r['strike'] == strike and r['option_side'] == 'PE'), None)
            
            if not ce_row or not pe_row:
                continue
                
            data_quality = ce_row['data_quality']
            cas_regime = ce_row['cas_regime']
            futures_price = ce_row['futures_price']
            
            fv_metrics = calculate_fair_value_metrics(
                ce_price=ce_row['close'], pe_price=pe_row['close'],
                spot_price=spot_price, futures_price=futures_price,
                strike=strike, data_quality=data_quality
            )
            
            if not fv_metrics['tradeable_flag']:
                continue
                
            # It has deviation. Check consolidation.
            # Get OI for adjacent strikes
            strike_dist = 50 # Assuming 50 for Nifty, we should compute it ideally
            adj_strikes = [strike - 2*strike_dist, strike - strike_dist, strike + strike_dist, strike + 2*strike_dist]
            
            # Sum OI for adjacent strikes (CE + PE) at current time
            oi_adjacent = []
            for s in adj_strikes:
                adj_rows = [r for r in current_rows if r['strike'] == s]
                total_oi = sum(r['oi'] for r in adj_rows)
                if total_oi > 0:
                    oi_adjacent.append(total_oi)
                    
            oi_strike = ce_row['oi'] + pe_row['oi']
            
            consolidation = check_consolidation_confluence(
                spot_prices, dt_timestamps, current_time,
                oi_strike, oi_adjacent
            )
            
            if consolidation['consolidation_confluence_met']:
                option_side_cheap = 'CE' if ce_row['close'] < pe_row['close'] else 'PE'
                setup_key = (strike, option_side_cheap)
                
                # Deduplication logic
                if setup_key in active_setups:
                    last_seen = active_setups[setup_key]
                    if (current_time - last_seen).total_seconds() / 60 <= 15:
                        # Setup persists within 15 min gap, update last seen but do not log new candidate
                        active_setups[setup_key] = current_time
                        continue
                        
                # New or gap > 15 mins
                active_setups[setup_key] = current_time
                
                # Prepare insertion
                inserted_candidates.append({
                    'index_name': index_name,
                    'expiry_date': expiry_date,
                    'candidate_timestamp': current_timestamp_str,
                    'strike': strike,
                    'option_side_cheap': option_side_cheap,
                    'cas_regime': cas_regime,
                    'data_quality': data_quality,
                    
                    'spot_price': spot_price,
                    'futures_price': futures_price,
                    'ce_price': ce_row['close'],
                    'pe_price': pe_row['close'],
                    'price_source': 'CLOSE',
                    'ce_pe_ratio': fv_metrics['ce_pe_ratio'],
                    'deviation_pct': fv_metrics['deviation_pct'],
                    'theoretical_fair_value': ce_row['close'] - pe_row['close'] - fv_metrics['raw_deviation'], # C-P - raw_dev
                    'deviation_from_fair_value': fv_metrics['raw_deviation'],
                    'bid_ask_available': 1 if ce_row['bid'] is not None else 0,
                    'estimated_transaction_cost': 0, # not computed for synthetic
                    'txn_cost_data_quality': None,
                    
                    'consolidation_price_range_met': 1 if consolidation['condition_a_met'] else 0,
                    'price_range_points': consolidation['actual_range'],
                    'price_range_duration_min': consolidation['actual_duration'],
                    'consolidation_oi_met': 1 if consolidation['condition_b_met'] else 0,
                    'oi_at_strike': oi_strike,
                    'oi_adjacent_average': sum(oi_adjacent)/len(oi_adjacent) if oi_adjacent else 0,
                    'oi_ratio': consolidation['oi_ratio'],
                    'consolidation_confluence_met': 1,
                    
                    'oi_change_last_5min_abs': 0,
                    'oi_change_last_5min_pct': 0.0,
                    'volume_last_5min': ce_row['volume'] + pe_row['volume'],
                    'volume_vs_session_average': 1.0,
                    'oi_change_direction': 'BOTH',
                    'volume_spike_flag': 1,
                    
                    'time_since_open_min': time_since_open_min,
                    'time_to_close_min': time_to_close_min,
                    'session_bucket': 'MID_SESSION', # simplifying for synthetic
                    
                    'detection_pass_complete': 0
                })
                
    # Write candidates
    for c in inserted_candidates:
        cols = ', '.join(c.keys())
        placeholders = ':' + ', :'.join(c.keys())
        cur.execute(f'INSERT INTO candidate_events ({cols}) VALUES ({placeholders})', c)
        
    conn.commit()
    
    # 5. After processing final timestamp: Set detection_pass_complete = true for ALL candidates in this session
    cur.execute('''
        UPDATE candidate_events
        SET detection_pass_complete = 1
        WHERE index_name = ? AND expiry_date = ?
    ''', (index_name, expiry_date))
    
    conn.commit()
    conn.close()
    
    return len(inserted_candidates)

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent.parent
    db_path = project_root / 'data' / 'db' / 'research.db'
    num = run_pass1_detection(db_path, 'NIFTY', '2026-10-15')
    print(f"Pass 1 found {num} candidates.")
