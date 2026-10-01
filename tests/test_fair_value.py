import unittest
from src.engine.fair_value import calculate_fair_value_metrics

class TestFairValue(unittest.TestCase):
    def test_zero_deviation(self):
        # CE == PE, spot == strike.
        # F = S (implying r=0).
        # C - P = 0.
        # F - K(S/F) = 100 - 100(100/100) = 0.
        res = calculate_fair_value_metrics(
            ce_price=10.0, pe_price=10.0,
            spot_price=100.0, futures_price=100.0,
            strike=100.0, data_quality='SYNTHETIC_TEST_DATA'
        )
        self.assertAlmostEqual(res['theoretical_ce_minus_pe'], 0.0)
        self.assertAlmostEqual(res['actual_ce_minus_pe'], 0.0)
        self.assertAlmostEqual(res['raw_deviation'], 0.0)
    
    def test_synthetic_inputs(self):
        # F = 105, S = 100, K = 100. 
        # C - P should be 105 - 100*(100/105) = 105 - 95.238 = 9.7619
        res = calculate_fair_value_metrics(
            ce_price=20.0, pe_price=10.2381,
            spot_price=100.0, futures_price=105.0,
            strike=100.0, data_quality='LIVE'
        )
        self.assertAlmostEqual(res['theoretical_ce_minus_pe'], 9.7619, places=3)
        self.assertAlmostEqual(res['actual_ce_minus_pe'], 9.7619, places=3)
        self.assertAlmostEqual(res['raw_deviation'], 0.0, places=3)
        
    def test_data_quality_propagates(self):
        res = calculate_fair_value_metrics(
            ce_price=10.0, pe_price=10.0,
            spot_price=100.0, futures_price=100.0,
            strike=100.0, data_quality='SYNTHETIC_TEST_DATA'
        )
        self.assertEqual(res['data_quality'], 'SYNTHETIC_TEST_DATA')
        
    def test_ce_pe_ratio(self):
        # CE expensive
        res1 = calculate_fair_value_metrics(20.0, 10.0, 100.0, 100.0, 100.0, 'LIVE')
        self.assertEqual(res1['ce_pe_ratio'], 2.0)
        
        # PE expensive
        res2 = calculate_fair_value_metrics(10.0, 30.0, 100.0, 100.0, 100.0, 'LIVE')
        self.assertEqual(res2['ce_pe_ratio'], 3.0)
        
        self.assertTrue(res1['ce_pe_ratio'] >= 1.0)
        self.assertTrue(res2['ce_pe_ratio'] >= 1.0)

if __name__ == '__main__':
    unittest.main()
