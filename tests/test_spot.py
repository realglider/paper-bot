import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kucoin"))
import spot_bot as f


def bar(t, o, h, l, c):
    return [t, o, h, l, c, 0]


class T(unittest.TestCase):
    def test_atr(self):
        bars = [bar(i, 100, 102, 98, 100) for i in range(10)]
        a = f.atr_series(bars, 5)
        self.assertEqual(a[:4], [None] * 4)
        self.assertAlmostEqual(a[-1], 4.0)
        self.assertGreater(f.atr_series(bars + [bar(10, 110, 111, 109, 110)], 5)[-1], 4.0)

    def test_parse_candle(self):
        # KuCoin: Zeit, Eroeffnung, Schluss, Hoch, Tief
        self.assertEqual(f.parse_candle(["1794510000", "71752.3", "71566.1", "71764", "71546", "8.3", "1"]),
                         [1794510000000, 71752.3, 71764.0, 71546.0, 71566.1, 8.3])

    def test_position_size(self):
        qty, value = f.position_size(1000, 1000, 70000, 0.00000001)      # 10 % von 1000 = 100
        self.assertAlmostEqual(value, 100, places=2)
        qty, value = f.position_size(2000, 2000, 50, 0.001)              # 10 % von 2000 = 200
        self.assertAlmostEqual(qty, 4.0)
        qty, value = f.position_size(600, 600, 50, 0.001)                # kleines Konto: trotzdem 100
        self.assertAlmostEqual(value, 100)
        self.assertEqual(f.position_size(1000, 99, 50, 0.001), (0.0, 0.0))   # zu wenig frei
        qty, value = f.position_size(3000, 150, 50, 0.001)               # nur noch 150 frei
        self.assertLessEqual(value * (1 + f.TAKER_FEE), 150)
        self.assertGreaterEqual(value, 100)
        qty, value = f.position_size(1000, 1000, 3.3, 1)                 # ganze Stuecke
        self.assertEqual(qty, 30)

    def test_stop_hit(self):
        cs = [bar(1, 100, 101, 99, 100), bar(2, 100, 100, 95, 96), bar(3, 96, 97, 90, 91)]
        self.assertEqual(f.stop_hit(97, cs), (2, 97))
        self.assertIsNone(f.stop_hit(80, cs))
        self.assertEqual(f.stop_hit(98, [bar(5, 94, 95, 93, 94)]), (5, 94))   # Kurslücke

    def test_trail(self):
        k = f.STOP_ATR
        self.assertEqual(f.trail(90, 100 + k * 5, 5), 100)
        self.assertEqual(f.trail(90, 80 + k * 5, 5), 90)

    def test_signals(self):
        n = max(f.DONCHIAN_N, f.ATR_N * 5) + 5
        flat = [bar(i, 100, 101, 99, 100) for i in range(n)]
        s = f.signals(flat + [bar(n, 100, 103, 100, 102.5)])
        self.assertTrue(s["enter"] and not s["exit"])
        s = f.signals(flat + [bar(n, 100, 100, 97, 97.5)])
        self.assertTrue(s["exit"] and not s["enter"])
        s = f.signals(flat + [bar(n, 100, 100.5, 99.5, 100)])
        self.assertFalse(s["enter"] or s["exit"])
        self.assertIsNone(f.signals(flat[:50]))

    def test_account_roundtrip(self):
        acc = f.Account({}, [], 1_000_000)
        acc.buy("BTC", 0.002, 70000.0, 67900.0, 700.0, "Test")
        self.assertAlmostEqual(acc.s["cash"], 1000 - 140 - 140 * f.TAKER_FEE)
        self.assertAlmostEqual(acc.equity(), 1000 - 140 * f.TAKER_FEE)
        acc.marks["BTC"] = 71000.0
        self.assertAlmostEqual(acc.invested(), 142.0)
        net = acc.sell("BTC", 71000.0, 2_000_000, "Test")
        self.assertAlmostEqual(net, 2.0 - 140 * f.TAKER_FEE - 142 * f.TAKER_FEE)
        self.assertAlmostEqual(acc.equity(), 1000 + net)
        self.assertAlmostEqual(acc.s["cash"], 1000 + net)
        self.assertEqual(acc.pos, {})


if __name__ == "__main__":
    unittest.main()
