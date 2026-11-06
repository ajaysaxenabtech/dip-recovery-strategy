import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from silver_fof import aligned, paper, rsi14, screen, trades


class SilverFoFTests(unittest.TestCase):
    def setUp(self):
        self.dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(210)]
        self.etf = [100.0] * 199 + [97.0] + [100.0] * 10
        self.fof = [10.0] * 200 + [9.0, 9.9] + [10.0] * 8

    def test_signal_uses_only_prices_through_signal_date(self):
        base = screen(self.etf, 199, "dip_only")
        altered = self.etf[:200] + [100000.0] * 10
        self.assertEqual(base, screen(altered, 199, "dip_only"))
        self.assertTrue(base[0]["dip_at_least_2_5_pct"])
        self.assertEqual(50.0, rsi14([100.0] * 15, 14))

    def test_entry_is_next_nav_and_exit_load_applies(self):
        rows = trades(self.dates, self.etf, self.fof, "dip_only", 1,
                      self.dates[0], date.max, 0.002)
        self.assertEqual(1, len(rows))
        self.assertEqual(self.dates[200].isoformat(), rows[0]["entry_date"])
        self.assertEqual(self.dates[201].isoformat(), rows[0]["exit_date"])
        self.assertEqual(10.0, rows[0]["gross_pct"])
        self.assertEqual(0.25, rows[0]["exit_load_pct"])
        self.assertEqual(9.506, rows[0]["net_after_assumed_cost_pct"])

    def test_paper_rejects_stale_quote(self):
        snapshot = {
            "observed_at_ist": "2024-07-30T14:20:00+05:30",
            "quote_at_ist": "2024-07-30T14:10:00+05:30",
            "source": "test", "etf_indicative_nav": 96,
            "event_clear": True, "regime_clear": True, "funds_ready": True,
        }
        with self.assertRaisesRegex(ValueError, "five minutes"):
            paper(snapshot, self.dates, self.etf, self.fof)

    def test_duplicate_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            etf = Path(folder) / "etf.csv"
            fof = Path(folder) / "fof.csv"
            etf.write_text("Date,Nav\n2024-01-01,1\n2024-01-01,2\n", encoding="utf-8")
            fof.write_text("Date,Nav\n2024-01-01,1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                aligned(etf, fof)


if __name__ == "__main__":
    unittest.main()
