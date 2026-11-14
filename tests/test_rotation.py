import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kucoin"))
import rotation_bot as r

DAY = r.DAY


def days(closes, start=0, q=1e6):
    return [[(start + i) * DAY, c, c, c, c, 0, q] for i, c in enumerate(closes)]


def st(mom, trend=True, vol=0.8, volume=1e6):
    return {"mom": mom, "trend": trend, "vol": vol, "volume": volume, "close": 1.0, "hold_ok": mom > 0 and trend}


class T(unittest.TestCase):
    def test_excluded(self):
        for c in ("USDC", "BTC3L", "ETHUP", "PAXG", "WBTC"):
            self.assertTrue(r.excluded(c))
        for c in ("BTC", "SOL", "PEPE"):
            self.assertFalse(r.excluded(c))

    def test_contiguous(self):
        d = days([1.0] * 10)
        self.assertEqual(len(r.contiguous(d, 10 * DAY, 10)), 10)
        self.assertEqual(r.contiguous(d, 11 * DAY, 10), [])            # gestern fehlt
        self.assertEqual(r.contiguous(d[:4] + d[5:], 10 * DAY, 10), [])  # Luecke
        self.assertEqual(len(r.contiguous(d, 10 * DAY, 5)), 5)
        self.assertEqual(r.contiguous(d, 10 * DAY, 11), [])            # zu kurz

    def test_regime(self):
        self.assertEqual(r.regime([100.0] * 199 + [120.0]), 1.0)
        self.assertEqual(r.regime([100.0] * 199 + [80.0]), 0.0)
        # langfristig oben, kurzfristig eingebrochen: nur der 200-Tage-Trend haelt
        c = [50.0] * 100 + [100.0] * 99 + [80.0]
        self.assertAlmostEqual(r.regime(c), 1 / 3)

    def test_slots(self):
        self.assertEqual([r.slots_for(f) for f in (0, 1 / 3, 2 / 3, 1)], [0, 2, 3, 5])

    def test_vol_scale(self):
        self.assertEqual(r.vol_scale(0.4), 1.0)
        self.assertAlmostEqual(r.vol_scale(1.6), 0.5)
        self.assertEqual(r.vol_scale(0), 0.0)

    def test_coin_stats(self):
        up = [100 * 1.01 ** i for i in range(r.MIN_HISTORY)]
        s = r.coin_stats(days(up))
        self.assertTrue(s["trend"] and s["hold_ok"] and s["mom"] > 0)
        self.assertAlmostEqual(s["mom"], (1.01 ** 30 - 1 + 1.01 ** 90 - 1) / 2)
        self.assertAlmostEqual(s["vol"], 0.0, places=6)
        down = [100 * 0.99 ** i for i in range(r.MIN_HISTORY)]
        self.assertFalse(r.coin_stats(days(down))["hold_ok"])

    def test_rank_uses_only_most_traded(self):
        stats = {f"C{i}": st(0.1 + i / 100, volume=1e6 - i) for i in range(25)}
        ranks = r.rank(stats)
        self.assertEqual(len(ranks), r.UNIVERSE)
        self.assertNotIn("C24", ranks)                     # staerkster, aber nicht unter den 20 meistgehandelten
        self.assertEqual(ranks["C19"], 1)
        stats["C19"] = st(0.5, trend=False, volume=1e6 - 19)
        self.assertNotIn("C19", r.rank(stats))

    def test_plan(self):
        stats = {c: st(m) for c, m in (("A", .5), ("B", .4), ("C", .3), ("D", .2), ("E", .1), ("F", .05))}
        ranks = r.rank(stats)
        # volle Marktlage, Umschichtungstag, nichts gehalten -> die fuenf staerksten
        self.assertEqual(r.plan(1.0, ranks, stats, {}, True), ([], ["A", "B", "C", "D", "E"]))
        # kein Umschichtungstag -> keine Kaeufe
        self.assertEqual(r.plan(1.0, ranks, stats, {}, False), ([], []))
        # 1/3 -> zwei Plaetze
        self.assertEqual(r.plan(1 / 3, ranks, stats, {}, True), ([], ["A", "B"]))
        # gehaltener Coin mit gebrochenem Trend fliegt taeglich raus
        stats["B"] = st(-0.1)
        ranks = r.rank(stats)
        sells, buys = r.plan(1.0, ranks, stats, {"B": {}, "A": {}}, False)
        self.assertEqual(sells, [("B", "Trend gebrochen")])
        # Marktlage faellt von 5 auf 2 Plaetze: die schwaechsten gehen
        held = {c: {} for c in ("A", "C", "D", "E")}
        sells, buys = r.plan(1 / 3, ranks, stats, held, False)
        self.assertEqual(sorted(c for c, _ in sells), ["D", "E"])
        # Marktlage 0: alles raus
        sells, buys = r.plan(0.0, ranks, stats, held, True)
        self.assertEqual(len(sells), 4)
        self.assertEqual(buys, [])
        # gehaltener Coin ausserhalb der Top 5 bleibt, solange sein Trend haelt
        sells, buys = r.plan(1.0, ranks, stats, {"F": {}}, True)
        self.assertEqual(sells, [])
        self.assertEqual(buys, ["A", "C", "D", "E"])

    def test_account(self):
        acc = r.Account({}, [], 1_000_000)
        self.assertAlmostEqual(acc.equity(), 1000)
        acc.marks["BTC"] = 70000.0
        acc.trade_core(250.0, 70000.0, 70000.0, "Test")
        px = 70000 * (1 + r.SLIPPAGE)
        self.assertAlmostEqual(acc.s["btc"], 250 / px)
        self.assertAlmostEqual(acc.s["core_cash"], 500 - 250 * (1 + r.TAKER_FEE))
        acc.trade_core(-1e9, 77000.0, 77000.0, "Test")                 # alles verkaufen
        self.assertEqual(acc.s["btc"], 0.0)
        self.assertGreater(acc.s["core_cash"], 500)
        self.assertGreater(acc.trades[-1]["pnl"], 0)
        self.assertTrue(acc.buy("SOL", 100.0, 80.0, 0.7, "Test"))
        self.assertAlmostEqual(acc.s["rot_cash"], 500 - 100.1)
        acc.marks["SOL"] = 88.0
        self.assertGreater(acc.rot_equity(), 500)
        acc.sell("SOL", 88.0, "Test")
        self.assertEqual(acc.s["positions"], {})
        t = acc.trades[-1]
        self.assertAlmostEqual(t["pnl"], round(100 / (80 * 1.001) * 88 * 0.999 * 0.999 - 100.1, 2))
        self.assertFalse(acc.buy("X", 1.0, 1.0, 1.0, "zu klein"))


if __name__ == "__main__":
    unittest.main()
