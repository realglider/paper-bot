import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kucoin"))
import spot_bot as f

DAY = 86_400_000


def day(i, o, h, l, c, q=2_000_000):
    return [i * DAY, o, h, l, c, 0, q]


def flat(n):
    return [day(i, 100, 101, 99, 100) for i in range(n)]


class T(unittest.TestCase):
    def test_parse_candle(self):
        # KuCoin: Zeit, Eroeffnung, Schluss, Hoch, Tief, Volumen, Umsatz
        self.assertEqual(f.parse_candle(["1794510000", "71752.3", "71566.1", "71764", "71546", "8.3", "595971.7"]),
                         [1794510000000, 71752.3, 71764.0, 71546.0, 71566.1, 8.3, 595971.7])

    def test_excluded(self):
        for c in ("USDC", "BTC3L", "ETH3S", "PAXG", "BTCUP", "ADADOWN"):
            self.assertTrue(f.is_excluded(c), c)
        for c in ("BTC", "SOL", "PEPE"):
            self.assertFalse(f.is_excluded(c), c)

    def test_signal(self):
        base = flat(60)
        self.assertIsNone(f.signal(base[:30]))                                   # zu wenig Historie
        self.assertTrue(f.signal(base + [day(60, 100, 106, 100, 105, 7_000_000)])["buy"])
        self.assertFalse(f.signal(base + [day(60, 100, 106, 100, 105, 5_000_000)])["buy"])   # Umsatz nur 2,5x
        self.assertFalse(f.signal(base + [day(60, 100, 100.9, 100, 100.5, 7_000_000)])["buy"])  # kein neues Hoch
        self.assertFalse(f.signal(base + [day(60, 100, 170, 100, 160, 7_000_000)])["buy"])   # +60 % an einem Tag
        thin = [day(i, 100, 101, 99, 100, 300_000) for i in range(60)]
        self.assertFalse(f.signal(thin + [day(60, 100, 106, 100, 105, 3_000_000)])["buy"])   # zu illiquide
        down = [day(i, 200 - i, 201 - i, 199 - i, 200 - i) for i in range(60)]              # Abwaertstrend
        s = f.signal(down + [day(60, 141, 141.5, 140, 141, 7_000_000)])
        self.assertFalse(s["buy"])

    def test_stop_level(self):
        self.assertAlmostEqual(f.stop_level(100, 100), 88)          # fester Stop
        self.assertAlmostEqual(f.stop_level(100, 110), 93.5)        # nachgezogen
        self.assertAlmostEqual(f.stop_level(100, 130), 110.5)       # Stop schon im Gewinn

    def test_exit_hit(self):
        cs = [[1, 100, 101, 99, 100], [2, 100, 100, 87, 90], [3, 90, 150, 90, 145]]
        self.assertEqual(f.exit_hit(88, 140, cs), (2, 88, "stop"))
        self.assertEqual(f.exit_hit(80, 140, cs), (3, 140, "target"))
        self.assertIsNone(f.exit_hit(80, 200, cs))
        self.assertEqual(f.exit_hit(88, 140, [[5, 85, 86, 84, 85]]), (5, 85, "stop"))        # Kurslücke
        self.assertEqual(f.exit_hit(88, 140, [[5, 100, 150, 80, 120]]), (5, 88, "stop"))     # beides: Stop zaehlt

    def test_buy_qty(self):
        self.assertAlmostEqual(f.buy_qty(500, 50, 0.001), 2.0)
        self.assertEqual(f.buy_qty(100, 50, 0.001), 0.0)             # reicht nicht fuer die Gebuehr
        self.assertAlmostEqual(f.buy_qty(500, 3.3, 1), 30)
        self.assertAlmostEqual(f.buy_qty(500, 70000, 0.00000001) * 70000, 100, places=2)

    def test_account_roundtrip(self):
        acc = f.Account({}, [], 1_000_000)
        acc.buy("SOL", 2.0, 50.0, "Test")
        self.assertAlmostEqual(acc.s["cash"], 500 - 100 - 0.1)
        acc.marks["SOL"] = 55.0
        self.assertAlmostEqual(acc.equity(), 500 - 0.1 + 10)
        net = acc.sell("SOL", 70.0, 2_000_000, "Gewinnziel")
        self.assertAlmostEqual(net, 40 - 0.1 - 0.14)
        self.assertAlmostEqual(acc.equity(), 500 + net)
        self.assertEqual(acc.s["last_exit"]["SOL"], 2_000_000)
        self.assertAlmostEqual(acc.trades[0]["ret"], round(net / 100, 4))


if __name__ == "__main__":
    unittest.main()
