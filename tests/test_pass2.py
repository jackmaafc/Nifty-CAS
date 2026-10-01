import unittest
import sqlite3
import tempfile
import os
from pathlib import Path
from src.scanner.pass2_outcomes import run_pass2_outcomes
import sys

class TestPass2(unittest.TestCase):
    def setUp(self):
        self.fd, self.db_path = tempfile.mkstemp()
        
        project_root = Path(__file__).resolve().parent.parent
        sys.path.append(str(project_root))
        from src.data.db_init import SCHEMA_SQL
        
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_SQL)
        
        # Add a candidate event
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO candidate_events (
                id, index_name, expiry_date, candidate_timestamp, strike, option_side_cheap,
                cas_regime, data_quality, spot_price, futures_price, ce_price, pe_price,
                price_source, ce_pe_ratio, deviation_pct, theoretical_fair_value,
                deviation_from_fair_value, bid_ask_available, estimated_transaction_cost,
                consolidation_price_range_met, consolidation_oi_met, consolidation_confluence_met,
                time_since_open_min, time_to_close_min, session_bucket, detection_pass_complete
            ) VALUES (
                1, 'NIFTY', '2026-10-15', '2026-10-15T10:00:00.000Z', 100.0, 'PE',
                'POST_CAS', 'SYNTHETIC_TEST_DATA', 100.0, 100.0, 20.0, 10.0,
                'CLOSE', 2.0, 100.0, 0.0,
                10.0, 0, 0,
                1, 1, 1,
                60.0, 240.0, 'MID_SESSION', 0  -- Set to 0 initially
            )
        ''')
        
        # Insert forward data where ratio drops to 1.0 (REVERSION_COMPLETE)
        cur.execute('''
            INSERT INTO raw_options_data (
                index_name, expiry_date, strike, option_side, timestamp,
                close, volume, oi, data_quality, cas_regime
            ) VALUES (
                'NIFTY', '2026-10-15', 100.0, 'CE', '2026-10-15T10:15:00.000Z',
                15.0, 100, 1500, 'SYNTHETIC_TEST_DATA', 'POST_CAS'
            )
        ''')
        cur.execute('''
            INSERT INTO raw_options_data (
                index_name, expiry_date, strike, option_side, timestamp,
                close, volume, oi, data_quality, cas_regime
            ) VALUES (
                'NIFTY', '2026-10-15', 100.0, 'PE', '2026-10-15T10:15:00.000Z',
                15.0, 100, 1500, 'SYNTHETIC_TEST_DATA', 'POST_CAS'
            )
        ''')
        
        conn.commit()
        conn.close()

    def tearDown(self):
        os.close(self.fd)
        os.remove(self.db_path)

    def test_rejection_trigger(self):
        # detection_pass_complete is 0
        processed, errors = run_pass2_outcomes(self.db_path)
        self.assertEqual(processed, 0)
        
        # Manually try to insert to see if DB trigger blocks it
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError) as context:
            cur.execute('''
                INSERT INTO forward_outcomes (
                    candidate_id, outcome_category, data_quality
                ) VALUES (1, 'EXTENSION', 'SYNTHETIC_TEST_DATA')
            ''')
        self.assertIn('detection_pass_complete must be 1', str(context.exception))
        conn.close()

    def test_reversion_complete(self):
        # Set detection complete
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE candidate_events SET detection_pass_complete = 1 WHERE id = 1")
        conn.commit()
        conn.close()
        
        processed, errors = run_pass2_outcomes(self.db_path)
        self.assertEqual(processed, 1)
        self.assertEqual(errors, 0)
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM forward_outcomes WHERE candidate_id = 1")
        row = cur.fetchone()
        
        self.assertEqual(row['outcome_category'], 'REVERSION_COMPLETE')
        self.assertEqual(row['convergence_timestamp'], '2026-10-15T10:15:00.000Z')
        conn.close()

if __name__ == '__main__':
    unittest.main()
