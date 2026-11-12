import datetime as dt, math, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import paper_bot as b


class T(unittest.TestCase):
    def test_prob(self):
        self.assertAlmostEqual(b.prob_up(100, 100, 0.001, 30), 0.5, places=6)
        self.assertGreater(b.prob_up(100.5, 100, 0.001, 30), 0.8)
        self.assertLess(b.prob_up(99.5, 100, 0.001, 30), 0.2)
        # gleicher Abstand, weniger Restzeit -> sicherer
        self.assertGreater(b.prob_up(100.2, 100, 0.001, 5), b.prob_up(100.2, 100, 0.001, 50))
        self.assertEqual(b.prob_up(101, 100, 0.001, 0), 1.0)
        self.assertEqual(b.prob_up(110, 100, 0.001, 30), 0.99)   # gekappt

    def test_sigma(self):
        closes = [100 * math.exp(0.001 * (1 if i % 2 else -1) * (i % 2)) for i in range(100)]
        self.assertAlmostEqual(b.sigma_per_minute(closes), 0.001, places=6)
        with self.assertRaises(ValueError):
            b.sigma_per_minute([1, 2, 3])

    def test_fee(self):
        self.assertAlmostEqual(b.fee_per_share(0.5, {"rate": 0.07, "exponent": 1}), 0.0175)
        self.assertEqual(b.fee_per_share(0.5, {"rate": 0.0, "exponent": 1}), 0.0)

    def test_book(self):
        lv = b.best_levels({"bids": [{"price": "0.5", "size": "4"}, {"price": "0.52", "size": "9"}],
                            "asks": [{"price": "0.55", "size": "1"}, {"price": "0.53", "size": "26"}]})
        self.assertEqual((lv["bid"], lv["ask"], lv["ask_size"]), (0.52, 0.53, 26.0))
        self.assertIsNone(b.best_levels({})["ask"])

    def test_decide(self):
        fee = {"rate": 0.07, "exponent": 1}
        up = {"ask": 0.50, "ask_size": 500}
        down = {"ask": 0.52, "ask_size": 500}
        d = b.decide(0.62, up, down, fee, 30)
        self.assertEqual(d["side"], "Up")
        self.assertEqual(d["shares"], 20)
        self.assertAlmostEqual(d["edge"], 0.62 - 0.50 - 0.0175, places=4)
        self.assertAlmostEqual(d["cost"], 20 * 0.5 + 20 * 0.0175, places=4)
        self.assertIsNone(b.decide(0.55, up, down, fee, 30))     # Vorteil zu klein
        self.assertIsNone(b.decide(0.62, up, down, fee, 1))      # zu spaet
        self.assertIsNone(b.decide(0.62, {"ask": 0.5, "ask_size": 3}, down, fee, 30))  # zu duenn
        self.assertEqual(b.decide(0.30, up, down, fee, 30)["side"], "Down")

    def test_slug(self):
        t = dt.datetime(2026, 11, 12, 10, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(b.slug_for("bitcoin", t), "bitcoin-up-or-down-november-12-2026-5am-et")
        t = dt.datetime(2026, 7, 4, 4, 0, tzinfo=dt.timezone.utc)   # Sommerzeit, Mitternacht ET
        self.assertEqual(b.slug_for("dogecoin", t), "dogecoin-up-or-down-july-4-2026-12am-et")
        t = dt.datetime(2026, 7, 4, 16, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(b.slug_for("xrp", t, False), "xrp-up-or-down-july-4-12pm-et")


if __name__ == "__main__":
    unittest.main()
