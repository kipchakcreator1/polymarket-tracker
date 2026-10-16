"""Offline regression tests for v3.3 wallet research (no API or Telegram)."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tools import v33_wallet_universe as v33

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40


class UniverseTests(unittest.TestCase):
    def test_seed_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.json"
            state = Path(folder) / "research.json"
            source.write_text(
                '{"wallets":{"' + A + '":{"name":"Test","n":100,'
                '"winrate":0.91,"profit_factor":2.1,"checked_at":1234}}}')
            with mock.patch.object(v33, "STATE", state), mock.patch.object(
                    v33, "SOURCE", source):
                loaded = v33.load_universe(2000)
            self.assertEqual(loaded["wallets"][A]["winrate"], .91)
            self.assertFalse(state.exists())
            self.assertIn('"checked_at":1234', source.read_text())

    def test_rotating_markets_unique_and_bounded(self):
        markets = {
            "0x" + format(i, "064x"): {
                "condition": "0x" + format(i, "064x"), "vol": i}
            for i in range(400)}
        a = v33.choose_markets(markets, 3600, 120)
        b = v33.choose_markets(markets, 7200, 120)
        self.assertEqual(len(a), 120)
        self.assertEqual(len({m["condition"] for m in a}), 120)
        self.assertNotEqual({m["condition"] for m in a},
                            {m["condition"] for m in b})

    def test_sample_adds_candidates_up_to_cap_without_replacing_old(self):
        state = {"wallets": {A: {"name": "old"}}}
        sample = {A: {"markets": 1, "rows": 2, "mid_buys": 1,
                      "expensive_buys": 0, "buy_rows": 2},
                  B: {"markets": 2, "rows": 3, "mid_buys": 2,
                      "expensive_buys": 0, "buy_rows": 2},
                  C: {"markets": 1, "rows": 2, "mid_buys": 0,
                      "expensive_buys": 2, "buy_rows": 2}}
        with mock.patch.object(v33, "TARGET", 2):
            count = v33.incorporate_sample(state, sample, 3000)
        self.assertEqual(count, 1)
        self.assertEqual(len(state["wallets"]), 2)
        self.assertIn(B, state["wallets"])
        self.assertEqual(state["wallets"][A]["name"], "old")
        self.assertNotIn(C, state["wallets"])

    def test_price_observation_is_not_false_proof(self):
        now = 100000
        wallets = {
            A: {"n": 100, "winrate": .90, "recent_realized_pnl": 1000,
                "profit_factor": 2, "score_checked_at": now,
                "buy_rows_sampled": 12, "mid_buys_sampled": 8,
                "expensive_buys_sampled": 1},
            B: {"n": 100, "winrate": .99, "recent_realized_pnl": 2000,
                "profit_factor": 10, "score_checked_at": now,
                "buy_rows_sampled": 14, "mid_buys_sampled": 0,
                "expensive_buys_sampled": 13},
            C: {"n": 100, "winrate": 1, "recent_realized_pnl": 3000,
                "profit_factor": None, "score_checked_at": now}}
        shortlist, supported = v33.eligible_research_wallets(wallets, now)
        self.assertEqual([r["wallet"] for r in shortlist], [A, B])
        self.assertEqual(supported, 1)
        self.assertEqual(shortlist[1]["price_profile"], "unverified")

    def test_market_trade_data_scoped_and_price_filtered(self):
        now = 1000000
        condition = "0x" + "d" * 64
        def row(address, price, ts, match=True):
            return {"condition_id": condition if match else "0x" + "0" * 64,
                    "proxy_wallet": address, "timestamp": ts,
                    "side": "BUY", "price": price}
        sample = {"data": [
            row(A, 0.50, now), row(A, 0.99, now),
            row(B, 0.30, now - v33.RECENT - 10),
            row(C, 0.2, now, match=False),
            row("invalid", .4, now)]}
        with mock.patch.object(v33.api, "request_json", return_value=sample):
            count, wallets = v33.fetch_market_trades({"condition": condition}, now)
        self.assertEqual(count, 5)
        self.assertEqual(set(wallets), {A})
        self.assertEqual(wallets[A]["mid_buys"], 1)
        self.assertEqual(wallets[A]["expensive_buys"], 1)

    def test_dedicated_research_has_no_telegram_or_orders(self):
        text = Path(v33.__file__).read_text(encoding="utf-8")
        self.assertNotIn("send_telegram(", text)
        self.assertNotIn("place_order(", text)


    def test_crypto_discovery_uses_live_slug_not_generic_market_ranking(self):
        now = 1792144268
        queried = []
        condition = "0x" + "d"*64
        def api(path, params, **kwargs):
            self.assertEqual(path, "/events")
            slug = params["slug"]
            queried.append(slug)
            if slug == "btc-updown-5m-" + str(now//300*300):
                return [{"slug": slug, "markets": [{"conditionId": condition}]}]
            return []
        with mock.patch.object(v33.api, "request_json", side_effect=api):
            markets, errors = v33.discover_short_crypto_markets(now)
        self.assertEqual(errors, 0)
        self.assertEqual(len(queried), 24)
        self.assertEqual(len(markets), 1)
        self.assertTrue(markets[0]["crypto_short"])
        self.assertEqual(markets[0]["condition"], condition)

    def test_crypto_detection_requires_real_midprice_buy_and_source_usd(self):
        now = 1792144200
        condition = "0x" + "d"*64
        def row(wallet, ts, price, size, side="BUY"):
            return {"condition_id": condition, "proxy_wallet": wallet,
                    "side": side, "price": price, "size": size,
                    "timestamp": ts}
        payload = {"data": [row(A, now-20, .5, 30),  # $15
                            row(B, now-30, .5, 2),   # $1 dust
                            row(C, now-30, .99, 100),
                            row(B, now-30, .5, 100, "SELL"),
                            row(C, now-7200, .5, 100)]}
        with mock.patch.object(v33.api, "request_json", return_value=payload):
            n, seen = v33.fetch_market_trades(
                {"condition": condition, "crypto_short": True}, now)
        self.assertEqual(n, 5)
        self.assertEqual(seen[A]["crypto_last_buy"], now-20)
        self.assertEqual(seen[B]["crypto_last_buy"], 0)
        self.assertEqual(seen[C]["crypto_last_buy"], 0)

    def test_qualified_active_crypto_wallet_ranks_above_general_giant(self):
        now = 1792144200
        base = {"n": 100, "winrate": .75,
                "recent_realized_pnl": 500, "profit_factor": 2,
                "score_checked_at": now, "buy_rows_sampled": 20,
                "mid_buys_sampled": 12, "expensive_buys_sampled": 0}
        general = dict(base, mid_buys_sampled=5000,
                       recent_realized_pnl=999999)
        crypto = dict(base, crypto_last_buy=now-25)
        ranked, supported = v33.eligible_research_wallets(
            {A: general, B: crypto}, now, limit=1)
        self.assertEqual(ranked[0]["wallet"], B)
        self.assertEqual(ranked[0]["last_crypto_buy"], now-25)
        self.assertEqual(supported, 2)

    def test_crypto_recency_is_real_trade_timestamp_not_refresh_timestamp(self):
        now = 1792144200
        state = {"wallets": {A: {"crypto_last_buy": now-100}}}
        def sample(ts):
            return {A: {"markets": 1, "rows": 1, "mid_buys": 1,
                        "expensive_buys": 0, "buy_rows": 1,
                        "crypto_last_buy": ts}}
        v33.incorporate_sample(state, sample(now-25), now)
        self.assertEqual(state["wallets"][A]["crypto_last_buy"], now-25)
        v33.incorporate_sample(state, sample(now-25), now+3600)
        self.assertEqual(state["wallets"][A]["crypto_last_buy"], now-25)
        after, _ = v33.eligible_research_wallets({
            A: {"n": 100, "winrate": .75, "recent_realized_pnl": 500,
                "profit_factor": 2, "score_checked_at": now+86400,
                "buy_rows_sampled": 20, "mid_buys_sampled": 12,
                "expensive_buys_sampled": 0,
                "crypto_last_buy": now-25}}, now+2*86400)
        self.assertGreater(now+2*86400-after[0]["last_crypto_buy"],
                           v33.CRYPTO_ACTIVE_WINDOW)



    def test_full_universe_admits_new_crypto_without_evicting_good_wallet(self):
        now = 1792145000
        good = {"n": 100, "winrate": .8, "profit_factor": 2,
                "recent_realized_pnl": 200,
                "score_checked_at": now-1200,
                "last_market_seen": now-1200}
        weak = {"last_market_seen": now-4000,
                "score_checked_at": 0, "mid_buys_sampled": 1}
        state = {"wallets": {A: good, B: weak}}
        new = {C: {"markets": 1, "rows": 1, "mid_buys": 1,
                   "expensive_buys": 0, "buy_rows": 1,
                   "crypto_last_buy": now-30}}
        with mock.patch.object(v33, "TARGET", 2):
            n = v33.incorporate_sample(state, new, now)
        self.assertEqual(n, 1)
        self.assertEqual(set(state["wallets"]), {A, C})
        self.assertEqual(state["last_crypto_replaced"], 1)
        self.assertEqual(len(state["wallets"]), 2)

    def test_full_universe_does_not_evict_for_generic_market_trader(self):
        now = 1792145000
        state = {"wallets": {A: {"last_market_seen": now-4000}}}
        new = {B: {"markets": 1, "rows": 1, "mid_buys": 1,
                   "expensive_buys": 0, "buy_rows": 1,
                   "crypto_last_buy": 0}}
        with mock.patch.object(v33, "TARGET", 1):
            n = v33.incorporate_sample(state, new, now)
        self.assertEqual(n, 0)
        self.assertEqual(set(state["wallets"]), {A})
        self.assertEqual(state["last_crypto_replaced"], 0)

    def test_full_universe_protects_recent_crypto_wallets(self):
        now = 1792145000
        state = {"wallets": {A: {"crypto_last_buy": now-300}}}
        new = {B: {"markets": 1, "rows": 1, "mid_buys": 1,
                   "expensive_buys": 0, "buy_rows": 1,
                   "crypto_last_buy": now-5}}
        with mock.patch.object(v33, "TARGET", 1):
            n = v33.incorporate_sample(state, new, now)
        self.assertEqual(n, 0)
        self.assertEqual(set(state["wallets"]), {A})


if __name__ == "__main__":
    unittest.main()
