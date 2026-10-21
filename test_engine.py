"""Offline smoke tests; these never connect to Deriv or place trades."""
import unittest
from deriv_synth_bot import Bars, Config, Risk, Strategy, profile

class EngineSmokeTests(unittest.TestCase):
    def test_market_profiles(self):
        self.assertEqual(profile("R_100"), {"duration": 5, "unit": "t", "bar_s": 0})
        self.assertEqual(profile("frxEURUSD"), {"duration": 5, "unit": "m", "bar_s": 60})

    def test_forex_bars_emit_previous_close(self):
        bars = Bars(60)
        self.assertIsNone(bars.push(120, 1.0))
        self.assertIsNone(bars.push(150, 1.1))
        self.assertEqual(bars.push(180, 1.2), 1.1)

    def test_risk_stops_at_loss_limit(self):
        cfg = Config(pat="demo", app_id="test", account_id="demo", max_daily_loss=2)
        risk = Risk(cfg)
        risk.record(-1)
        self.assertTrue(risk.can_trade()[0])
        risk.record(-1)
        self.assertFalse(risk.can_trade()[0])

    def test_strategy_warms_up(self):
        strategy = Strategy()
        for i in range(20):
            self.assertIsNone(strategy.update(100 + i * 0.1))

    def test_real_disabled_by_default(self):
        cfg = Config(pat="demo", app_id="test", account_id="demo")
        self.assertFalse(cfg.allow_real)

if __name__ == "__main__": unittest.main()
