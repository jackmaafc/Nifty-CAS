import math
from statistics import NormalDist
import sqlite3
import datetime
from pathlib import Path
import random

def bs_price(S, K, T, r, sigma, option_type='CE'):
    if T <= 1e-6:
        return max(0.0, S - K) if option_type == 'CE' else max(0.0, K - S)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    norm = NormalDist(mu=0, sigma=1)
    
    if option_type == 'CE':
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def generate_spot_path():
    """Generates a spot path every 3 mins from 9:15 to 15:30 (126 steps)."""
    # 9:15 is step 0. 15:30 is step 125.
    steps = 126
    path = []
    for i in range(steps):
        mins = i * 3
        # 9:15 to 11:00 (105 mins) -> 35 steps
        if mins < 105:
            spot = 24500 + (100 * mins / 105) + random.uniform(-5, 5)
        # 11:00 to 13:30 (150 mins, total 255) -> 50 steps
        elif mins < 255:
            spot = 24600 + random.uniform(-10, 10)
        # 13:30 to 14:15 (45 mins, total 300) -> 15 steps
        elif mins < 300:
            spot = 24600 + (50 * (mins - 255) / 45) + random.uniform(-5, 5)
        # 14:15 to 15:30 (75 mins, total 375) -> 25 steps
        else:
            spot = 24650 - (50 * (mins - 300) / 75) + random.uniform(-5, 5)
        path.append(spot)
    return path

def generate_synthetic_data():
    # Setup DB
    project_root = Path(__file__).resolve().parent.parent.parent
    db_path = project_root / 'data' / 'db' / 'research.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Initialize DB schema if it doesn't exist
    import sys
    sys.path.append(str(project_root))
    from src.data.db_init import init_db
    init_db()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Define session
    date_str = '2026-10-15' # A future Thursday
    index_name = 'NIFTY'
    start_time = datetime.datetime(2026, 10, 15, 9, 15)
    r = 0.05
    sigma = 0.15 # 15% IV

    spot_path = generate_spot_path()
    
    rows = []
    
    # Generate data for ATM +- 5 strikes
    # Main ATM during consolidation is 24600
    base_strike = 24600
    strikes = [base_strike + (i * 50) for i in range(-5, 6)]

    # We will use POST_CAS for this synthetic session
    cas_regime = 'POST_CAS'
    data_quality = 'SYNTHETIC_TEST_DATA'

    for i, spot in enumerate(spot_path):
        current_time = start_time + datetime.timedelta(minutes=3 * i)
        timestamp_str = current_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        # Time to expiry in years. At 15:30 it's 0. Let's say 9:15 is 6.25 hours = 0.000713 years
        # But we need T to be non-zero mostly
        mins_to_expiry = max(0, 375 - i * 3)
        T = (mins_to_expiry / (60 * 24 * 365))
        
        # Futures price approx
        futures_price = spot * math.exp(r * T)
        
        for strike in strikes:
            for option_side in ['CE', 'PE']:
                price = bs_price(spot, strike, T, r, sigma, option_side)
                
                # Add some noise to price
                price = max(0.05, price + random.uniform(-0.5, 0.5))
                
                # Build OI and volume
                # Consolidation is 11:00 to 13:30 (steps 35 to 84). ATM strike 24600 gets high OI.
                oi = random.randint(10000, 50000)
                if strike == 24600 and i >= 35:
                    oi += 100000 + (i - 35) * 5000
                
                volume = random.randint(1000, 5000)
                # Spike is 13:30 to 14:15 (steps 85 to 99)
                if 85 <= i < 100 and strike == 24600:
                    volume += random.randint(20000, 50000)

                bid = max(0.05, price - 0.5)
                ask = price + 0.5

                rows.append((
                    index_name, date_str, strike, option_side, timestamp_str,
                    price, price+1, price-1, price, volume, oi, sigma,
                    spot, futures_price, bid, ask, data_quality, cas_regime
                ))
    
    cur.executemany('''
        INSERT OR IGNORE INTO raw_options_data
        (index_name, expiry_date, strike, option_side, timestamp,
         open, high, low, close, volume, oi, iv, spot_price, futures_price,
         bid, ask, data_quality, cas_regime)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', rows)
    
    conn.commit()
    print(f"Inserted {len(rows)} rows into raw_options_data.")
    
    cur.execute("SELECT * FROM raw_options_data ORDER BY RANDOM() LIMIT 3")
    sample = cur.fetchall()
    print("Sample rows:")
    for r in sample:
        print(r)
        
    conn.close()

if __name__ == "__main__":
    generate_synthetic_data()
