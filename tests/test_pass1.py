import unittest
import sqlite3
import tempfile
import os
from pathlib import Path
from src.scanner.pass1_detection import run_pass1_detection
import sys

class TestPass1(unittest.TestCase):
    def setUp(self):
        self.fd, self.db_path = tempfile.mkstemp()
        
        # Init schema
        project_root = Path(__file__).resolve().parent.parent
        sys.path.append(str(project_root))
        from src.data.db_init import SCHEMA_SQL
        
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_SQL)
        
        # Insert minimal synthetic data to trigger a candidate
        cur = conn.cursor()
        
        # We need at least 15 mins of data for consolidation (duration_min=15)
        # 2026-10-15 10:00 to 10:20 every 3 mins -> 7 timestamps
        timestamps = [f'2026-10-15T10:{i:02d}:00.000Z' for i in range(0, 21, 3)]
        
        # strike 100
        # spot stays at 100.
        # OI adjacent = 1000, OI strike = 3000
        
        for i, t in enumerate(timestamps):
            for side in ['CE', 'PE']:
                price = 10.0
                if i == 6 and side == 'CE':
                    price = 20.0 # deviation at the last step
                
                cur.execute('''
                    INSERT INTO raw_options_data (
                        index_name, expiry_date, strike, option_side, timestamp,
                        close, volume, oi, spot_price, futures_price, data_quality, cas_regime
                    ) VALUES (
                        'NIFTY', '2026-10-15', 100.0, ?, ?,
                        ?, 100, 1500, 100.0, 100.0, 'SYNTHETIC_TEST_DATA', 'POST_CAS'
                    )
                ''', (side, t, price))
                
                # Adjacent strikes for OI condition
                cur.execute('''
                    INSERT INTO raw_options_data (
                        index_name, expiry_date, strike, option_side, timestamp,
                        close, volume, oi, spot_price, futures_price, data_quality, cas_regime
                    ) VALUES (
                        'NIFTY', '2026-10-15', 50.0, ?, ?,
                        ?, 100, 250, 100.0, 100.0, 'SYNTHETIC_TEST_DATA', 'POST_CAS'
                    )
                ''', (side, t, 1.0))
                
        conn.commit()
        conn.close()

    def tearDown(self):
        os.close(self.fd)
        os.remove(self.db_path)

    def test_pass1_synthetic(self):
        num = run_pass1_detection(self.db_path, 'NIFTY', '2026-10-15')
        self.assertEqual(num, 1) # Should detect at the last timestamp
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM candidate_events")
        rows = cur.fetchall()
        
        self.assertEqual(len(rows), 1)
        row = rows[0]
        
        # Test 1: Detected at correct timestamp
        self.assertEqual(row['candidate_timestamp'], '2026-10-15T10:18:00.000Z')
        
        # Test 3: detection_pass_complete is true after run
        self.assertEqual(row['detection_pass_complete'], 1)
        
        conn.close()
        
    def test_duplicate_suppression(self):
        # Insert more data at 10:21 with same deviation
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        t = '2026-10-15T10:21:00.000Z'
        for side in ['CE', 'PE']:
            price = 20.0 if side == 'CE' else 10.0
            cur.execute('''
                INSERT INTO raw_options_data (
                    index_name, expiry_date, strike, option_side, timestamp,
                    close, volume, oi, spot_price, futures_price, data_quality, cas_regime
                ) VALUES (
                    'NIFTY', '2026-10-15', 100.0, ?, ?,
                    ?, 100, 1500, 100.0, 100.0, 'SYNTHETIC_TEST_DATA', 'POST_CAS'
                )
            ''', (side, t, price))
            cur.execute('''
                INSERT INTO raw_options_data (
                    index_name, expiry_date, strike, option_side, timestamp,
                    close, volume, oi, spot_price, futures_price, data_quality, cas_regime
                ) VALUES (
                    'NIFTY', '2026-10-15', 50.0, ?, ?,
                    ?, 100, 250, 100.0, 100.0, 'SYNTHETIC_TEST_DATA', 'POST_CAS'
                )
            ''', (side, t, 1.0))
        conn.commit()
        conn.close()
        
        # Should still be 1 candidate due to deduplication (within 15 mins)
        num = run_pass1_detection(self.db_path, 'NIFTY', '2026-10-15')
        self.assertEqual(num, 1)

if __name__ == '__main__':
    unittest.main()
