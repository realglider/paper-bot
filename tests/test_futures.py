import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kucoin"))
import futures_bot as f


def bar(t, o, h, l, c):
    return [t, o, h, l, c, 0]


class T(unittest.TestCase):
    def test_atr(self):
        bars = [bar(i, 100, 102, 98, 100) for i in range(10)]
        a = f.atr_series(bars, 5)
        self.assertEqual(a[:4], [None] * 4)
        self.assertAlmostEqual(a[-1], 4.0)
        # Kurslücke zaehlt zur Spanne
        self.assertGreater(f.atr_series(bars + [bar(10, 110, 111, 109, 110)], 5)[-1], 4.0)

    def test_ema(self):
        self.assertIsNone(f.ema_last([1, 2], 5))
        self.assertAlmostEqual(f.ema_last([5.0] * 50, 10), 5.0)

    def test_position_size(self):
        # 1000 $ Konto, Stop 5 % entfernt -> Positionswert 100 $ (0,5 % Risiko)
        lots, notional = f.position_size(1000, 100, 5, 0, 0.1)
        self.assertEqual(lots, 10)
        self.assertAlmostEqual(notional, 1000 * f.RISK_PER_TRADE / 0.05)
        self.assertAlmostEqual(notional * 5 / 100, 5)              # Verlust am Stop = 5 $
        # sehr enger Stop: Deckel bei 1,5 x Konto
        self.assertAlmostEqual(f.position_size(1000, 100, 0.1, 0, 0.1)[1], 1500)
        # Gesamtdeckel 3 x Konto
        self.assertAlmostEqual(f.position_size(1000, 100, 0.1, 2800, 0.1)[1], 200)
        self.assertEqual(f.position_size(1000, 100, 5, 3000, 0.1), (0, 0.0))
        # Kontrakt zu gross fuer das Konto
        self.assertEqual(f.position_size(1000, 70000, 3500, 0, 1.0), (0, 0.0))

    def test_stop_hit(self):
        cs = [bar(1, 100, 101, 99, 100), bar(2, 100, 100, 95, 96), bar(3, 96, 97, 90, 91)]
        self.assertEqual(f.stop_hit(1, 97, cs), (2, 97))
        self.assertIsNone(f.stop_hit(1, 80, cs))
        self.assertEqual(f.stop_hit(-1, 100.5, cs), (1, 100.5))
        # Eroeffnung schon hinter dem Stop -> Ausfuehrung zur Eroeffnung
        self.assertEqual(f.stop_hit(1, 98, [bar(5, 94, 95, 93, 94)]), (5, 94))
        self.assertEqual(f.stop_hit(-1, 98, [bar(5, 103, 104, 102, 103)]), (5, 103))

    def test_trail(self):
        k = f.STOP_ATR
        self.assertEqual(f.trail(1, 90, 100 + k * 5, 5), 100)   # zieht nach
        self.assertEqual(f.trail(1, 90, 80 + k * 5, 5), 90)     # geht nie zurueck
        self.assertEqual(f.trail(-1, 110, 100 - k * 5, 5), 100)
        self.assertEqual(f.trail(-1, 110, 120 - k * 5, 5), 110)

    def test_pnl(self):
        g, fee = f.close_pnl(1, 2, 100, 110)
        self.assertAlmostEqual(g, 20)
        self.assertAlmostEqual(fee, 2 * 110 * f.TAKER_FEE)
        self.assertAlmostEqual(f.close_pnl(-1, 2, 100, 110)[0], -20)

    def test_signals_donchian(self):
        n = max(f.DONCHIAN_N, f.ATR_N * 5) + 5
        flat = [bar(i, 100, 101, 99, 100) for i in range(n)]
        s = f.signals(flat + [bar(n, 100, 103, 100, 102.5)])
        self.assertTrue(s["enter_long"] and not s["enter_short"] and not s["exit_long"])
        s = f.signals(flat + [bar(n, 100, 100, 97, 97.5)])
        self.assertTrue(s["enter_short"] and s["exit_long"])
        s = f.signals(flat + [bar(n, 100, 100.5, 99.5, 100)])
        self.assertFalse(s["enter_long"] or s["enter_short"])
        self.assertIsNone(f.signals(flat[:50]))

    def test_account_roundtrip(self):
        acc = f.Account({}, [], 1_000_000)
        acc.open("XBT", 1, 2, 0.001, 70000.0, 67900.0, 700.0, "Test")
        self.assertAlmostEqual(acc.s["cash"], 1000 - 140 * f.TAKER_FEE)
        acc.marks["XBT"] = 71000.0
        self.assertAlmostEqual(acc.equity(), 1000 - 140 * f.TAKER_FEE + 2.0)
        self.assertAlmostEqual(acc.gross(), 142.0)
        net = acc.close("XBT", 71000.0, 2_000_000, "Test")
        self.assertAlmostEqual(net, 2.0 - 140 * f.TAKER_FEE - 142 * f.TAKER_FEE)
        self.assertAlmostEqual(acc.equity(), 1000 + net)
        self.assertEqual(acc.trades[0]["side"], "long")
        self.assertAlmostEqual(acc.trades[0]["r"], round(net / 4.2, 2))


if __name__ == "__main__":
    unittest.main()
