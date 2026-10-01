import sqlite3
from pathlib import Path
import datetime

def parse_iso_time(iso_str):
    return datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))

def run_pass2_outcomes(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Get candidates with detection_pass_complete = 1 and no outcome
    cur.execute('''
        SELECT c.* FROM candidate_events c
        LEFT JOIN forward_outcomes f ON c.id = f.candidate_id
        WHERE c.detection_pass_complete = 1 AND f.id IS NULL
    ''')
    
    candidates = cur.fetchall()
    processed_count = 0
    errors = 0
    
    for c in candidates:
        candidate_time = parse_iso_time(c['candidate_timestamp'])
        strike = c['strike']
        option_side_cheap = c['option_side_cheap']
        option_side_expensive = 'CE' if option_side_cheap == 'PE' else 'PE'
        
        # Fetch forward data
        cur.execute('''
            SELECT * FROM raw_options_data
            WHERE index_name = ? AND expiry_date = ? AND timestamp > ? AND strike = ?
            ORDER BY timestamp ASC
        ''', (c['index_name'], c['expiry_date'], c['candidate_timestamp'], strike))
        
        forward_rows = cur.fetchall()
        
        # Group by time
        time_data = {}
        for r in forward_rows:
            if r['timestamp'] not in time_data:
                time_data[r['timestamp']] = {'CE': None, 'PE': None}
            time_data[r['timestamp']][r['option_side']] = r['close']
            
        sorted_times = sorted(time_data.keys())
        
        outcomes = {
            'outcome_5min_ce_price': None,
            'outcome_5min_pe_price': None,
            'outcome_10min_ce_price': None,
            'outcome_10min_pe_price': None,
            'outcome_15min_ce_price': None,
            'outcome_15min_pe_price': None,
            'outcome_30min_ce_price': None,
            'outcome_30min_pe_price': None
        }
        
        convergence_timestamp = None
        convergence_pct_captured = None
        post_convergence_direction = None
        
        max_favorable = -float('inf')
        max_adverse = float('inf') # We want to track lowest value of cheap - expensive if we bought cheap?
        # Excursion: entry is buying cheap side, so favorable is cheap price going up, or deviation closing.
        # Let's define excursion based on deviation closing.
        # Favorable: min(ce_pe_ratio) if it went down. 
        # But wait, max_favorable_excursion is usually in points. We'll use the deviation points.
        # Actually let's use the cheap option price for excursion.
        cheap_entry_price = c['pe_price'] if option_side_cheap == 'PE' else c['ce_price']
        max_favorable_price = cheap_entry_price
        max_adverse_price = cheap_entry_price
        
        outcome_category = 'EXTENSION' # default
        
        for t_str in sorted_times:
            t_dt = parse_iso_time(t_str)
            ce_p = time_data[t_str]['CE']
            pe_p = time_data[t_str]['PE']
            
            if ce_p is None or pe_p is None:
                continue
                
            cheap_p = pe_p if option_side_cheap == 'PE' else ce_p
            expensive_p = ce_p if option_side_cheap == 'PE' else pe_p
            
            max_favorable_price = max(max_favorable_price, cheap_p)
            max_adverse_price = min(max_adverse_price, cheap_p)
            
            delta_mins = (t_dt - candidate_time).total_seconds() / 60.0
            
            # Snapshots
            if 4 <= delta_mins <= 6 and outcomes['outcome_5min_ce_price'] is None:
                outcomes['outcome_5min_ce_price'] = ce_p
                outcomes['outcome_5min_pe_price'] = pe_p
            elif 9 <= delta_mins <= 11 and outcomes['outcome_10min_ce_price'] is None:
                outcomes['outcome_10min_ce_price'] = ce_p
                outcomes['outcome_10min_pe_price'] = pe_p
            elif 14 <= delta_mins <= 16 and outcomes['outcome_15min_ce_price'] is None:
                outcomes['outcome_15min_ce_price'] = ce_p
                outcomes['outcome_15min_pe_price'] = pe_p
            elif 29 <= delta_mins <= 31 and outcomes['outcome_30min_ce_price'] is None:
                outcomes['outcome_30min_ce_price'] = ce_p
                outcomes['outcome_30min_pe_price'] = pe_p
                
            ratio = expensive_p / cheap_p if cheap_p > 0 else float('inf')
            
            if convergence_timestamp is None and ratio <= 1.1:
                convergence_timestamp = t_str
                # % captured: (initial_dev - current_dev) / initial_dev
                initial_dev = c['deviation_from_fair_value']
                current_dev = expensive_p - cheap_p - c['theoretical_fair_value'] # Approx
                convergence_pct_captured = 100.0 # Reached convergence
                outcome_category = 'REVERSION_COMPLETE'
                
        # If not complete, check if it was moving towards 1.1
        if outcome_category == 'EXTENSION':
            # Check final ratio
            if len(sorted_times) > 0:
                last_t = sorted_times[-1]
                ce_p = time_data[last_t]['CE']
                pe_p = time_data[last_t]['PE']
                if ce_p and pe_p:
                    cheap_p = pe_p if option_side_cheap == 'PE' else ce_p
                    expensive_p = ce_p if option_side_cheap == 'PE' else pe_p
                    last_ratio = expensive_p / cheap_p if cheap_p > 0 else float('inf')
                    if last_ratio < c['ce_pe_ratio']:
                        outcome_category = 'REVERSION_DEFERRED'

        max_favorable = max_favorable_price - cheap_entry_price
        max_adverse = max_adverse_price - cheap_entry_price

        # Insert
        try:
            cur.execute('''
                INSERT INTO forward_outcomes (
                    candidate_id, 
                    outcome_5min_ce_price, outcome_5min_pe_price,
                    outcome_10min_ce_price, outcome_10min_pe_price,
                    outcome_15min_ce_price, outcome_15min_pe_price,
                    outcome_30min_ce_price, outcome_30min_pe_price,
                    convergence_timestamp, convergence_pct_captured, post_convergence_direction,
                    outcome_category, max_adverse_excursion, max_favorable_excursion,
                    data_quality
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            ''', (
                c['id'],
                outcomes['outcome_5min_ce_price'], outcomes['outcome_5min_pe_price'],
                outcomes['outcome_10min_ce_price'], outcomes['outcome_10min_pe_price'],
                outcomes['outcome_15min_ce_price'], outcomes['outcome_15min_pe_price'],
                outcomes['outcome_30min_ce_price'], outcomes['outcome_30min_pe_price'],
                convergence_timestamp, convergence_pct_captured, post_convergence_direction,
                outcome_category, max_adverse, max_favorable,
                c['data_quality']
            ))
            conn.commit()
            processed_count += 1
        except sqlite3.IntegrityError as e:
            # e.g., trigger rejection
            print(f"Failed to insert outcome for candidate {c['id']}: {e}")
            conn.rollback()
            errors += 1
            continue

    conn.close()
    return processed_count, errors

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent.parent
    db_path = project_root / 'data' / 'db' / 'research.db'
    processed, errors = run_pass2_outcomes(db_path)
    print(f"Pass 2 processed: {processed}, Errors: {errors}")
