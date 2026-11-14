import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kucoin"))
import listing_bot as b

DAY = b.DAY
T0 = 1_800_000_000_000 - 1_800_000_000_000 % DAY       # ein Tagesanfang


class T(unittest.TestCase):
    def test_is_stock(self):
        self.assertTrue(b.is_stock({"sourceExchanges": ["binance_index", "finnhub"]}))
        self.assertFalse(b.is_stock({"sourceExchanges": ["binance", "okx"]}))
        self.assertFalse(b.is_stock({}))

    def test_entry_timing(self):
        listed = T0 + 9 * 3_600_000                     # Listing um 09:00
        self.assertEqual(b.closed_days(listed, T0), 0)
        self.assertEqual(b.closed_days(listed, T0 + DAY), 1)       # Listing-Kerze fertig
        for d in (1, 2, 3):
            self.assertEqual(b.entry_due(listed, T0 + d * DAY), "wait")
        self.assertEqual(b.entry_due(listed, T0 + 4 * DAY), "enter")   # vier Kerzen fertig
        self.assertEqual(b.entry_due(listed, T0 + 5 * DAY), "enter")   # ein Tag Verspaetung erlaubt
        self.assertEqual(b.entry_due(listed, T0 + 6 * DAY), "missed")
        self.assertEqual(b.entry_date(listed), T0 + 4 * DAY)

    def test_sizes(self):
        self.assertEqual(b.short_lots(1000, 2.0, 1.0), 25)             # 5 % von 1000 = 50 USDT
        self.assertEqual(b.short_lots(1000, 120.0, 0.01), 41)
        self.assertEqual(b.short_lots(1000, 80.0, 1.0), 0)             # ein Kontrakt waere schon 80 USDT
        self.assertEqual(b.hedge_lots(0, 65000, 0.001), 0)
        self.assertEqual(b.hedge_lots(50, 65000, 0.001), 1)            # 65 USDT je Kontrakt
        self.assertEqual(b.hedge_lots(30, 65000, 0.001), 0)
        self.assertEqual(b.hedge_lots(500, 65000, 0.001), 8)

    def test_account(self):
        acc = b.Account({}, [], T0)
        acc.open_short("NEW", 25, 1.0, 2.0, T0 - 4 * DAY, T0 + 30 * DAY)
        fee_in = 50 * b.TAKER_FEE
        self.assertAlmostEqual(acc.s["cash"], 1000 - fee_in)
        self.assertAlmostEqual(acc.s["positions"]["NEW"]["stop"], 3.0)
        acc.marks["NEW"] = 1.6                                         # Coin faellt 20 %
        self.assertAlmostEqual(acc.equity(), 1000 - fee_in + 10)
        acc.set_hedge(1, 0.001, 65000.0, 65000.0)
        h = acc.s["hedge"]
        self.assertEqual(h["lots"], 1)
        acc.marks["XBT"] = 66300.0
        self.assertAlmostEqual(acc.equity(), 1000 - fee_in + 10 + 0.001 * (66300 - h["entry"]) - h["entry"] * 0.001 * b.TAKER_FEE)
        acc.close_short("NEW", 1.6, T0 + DAY, "Test")
        t = acc.trades[0]
        self.assertAlmostEqual(t["pnl"], round(10 - fee_in - 40 * b.TAKER_FEE, 4))
        self.assertAlmostEqual(t["ret"], round(t["pnl"] / 50, 4))
        acc.set_hedge(0, 0.001, 66300.0, 66300.0)
        self.assertEqual(acc.s["hedge"]["qty"], 0.0)
        self.assertAlmostEqual(acc.equity(), acc.s["cash"])
        self.assertGreater(acc.equity(), 1000 + 9)

    def test_stop_loss_is_half_the_position(self):
        acc = b.Account({}, [], T0)
        acc.open_short("NEW", 25, 1.0, 2.0, T0, T0 + 30 * DAY)
        acc.close_short("NEW", 3.0, T0 + DAY, "Stop +50 %")
        self.assertAlmostEqual(acc.trades[0]["pnl"], round(-25 - 50 * b.TAKER_FEE - 75 * b.TAKER_FEE, 4))


if __name__ == "__main__":
    unittest.main()
