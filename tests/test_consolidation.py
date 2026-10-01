import unittest
from src.scanner.consolidation import check_consolidation_a, check_consolidation_b, check_consolidation_confluence
import time

class TestConsolidation(unittest.TestCase):
    def test_condition_a_met(self):
        # Flat spot in 30-point range for 20 min
        current_time = 1000000
        timestamps = [current_time - i * 60 for i in range(21)]
        timestamps.reverse()
        spot_prices = [10000 + (i % 10) for i in range(21)] # Range is 10 points
        
        met, actual_range, actual_duration = check_consolidation_a(
            spot_prices, timestamps, current_time, range_points=30, duration_min=20
        )
        self.assertTrue(met)
        self.assertLessEqual(actual_range, 30)
        self.assertGreaterEqual(actual_duration, 20)

    def test_condition_a_not_met(self):
        # Spot moving 60 points in 20 min
        current_time = 1000000
        timestamps = [current_time - i * 60 for i in range(21)]
        timestamps.reverse()
        spot_prices = [10000 + i * 3 for i in range(21)] # Range is 60 points
        
        met, actual_range, actual_duration = check_consolidation_a(
            spot_prices, timestamps, current_time, range_points=30, duration_min=20
        )
        self.assertFalse(met)
        self.assertGreater(actual_range, 30)

    def test_condition_b(self):
        # OI at strike 2x adjacent average
        oi_strike = 2000
        oi_adjacent = [900, 1100, 1000, 1000] # Avg 1000
        
        # Met at 2x threshold
        met, ratio = check_consolidation_b(oi_strike, oi_adjacent, threshold=2.0)
        self.assertTrue(met)
        self.assertEqual(ratio, 2.0)
        
        # Not met at 3x threshold
        met, ratio = check_consolidation_b(oi_strike, oi_adjacent, threshold=3.0)
        self.assertFalse(met)

    def test_confluence(self):
        current_time = 1000000
        timestamps = [current_time - i * 60 for i in range(21)]
        timestamps.reverse()
        
        # Test 4: Both met
        res = check_consolidation_confluence(
            spot_prices=[10000]*21, timestamps=timestamps, current_time=current_time,
            oi_strike=2000, oi_adjacent=[1000]*4,
            range_points=30, duration_min=20, oi_threshold=2.0
        )
        self.assertTrue(res['consolidation_confluence_met'])
        
        # Only A met
        res = check_consolidation_confluence(
            spot_prices=[10000]*21, timestamps=timestamps, current_time=current_time,
            oi_strike=1000, oi_adjacent=[1000]*4,
            range_points=30, duration_min=20, oi_threshold=2.0
        )
        self.assertTrue(res['condition_a_met'])
        self.assertFalse(res['condition_b_met'])
        self.assertFalse(res['consolidation_confluence_met'])
        
        # Only B met
        res = check_consolidation_confluence(
            spot_prices=[10000 + i*5 for i in range(21)], timestamps=timestamps, current_time=current_time,
            oi_strike=2000, oi_adjacent=[1000]*4,
            range_points=30, duration_min=20, oi_threshold=2.0
        )
        self.assertFalse(res['condition_a_met'])
        self.assertTrue(res['condition_b_met'])
        self.assertFalse(res['consolidation_confluence_met'])

if __name__ == '__main__':
    unittest.main()
