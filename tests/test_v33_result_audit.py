"""Offline tests: no real API requests, orders or Telegram."""
import unittest
from unittest import mock

from tools import v33_result_audit as a

W = "0x" + "a"*40
C = "0x" + "1"*64
G = {"conditionId": C, "category": "Sports",
     "clobTokenIds": '["yes","no"]', "outcomes": '["Yes","No"]'}
R = {"condition_id": C, "status": "resolved",
     "resolved_at": "2026-10-15T00:00:00Z", "payouts": [0, 1000000]}


def fill(token="yes", side="BUY", price=.4, timestamp=1791950000):
    return {"condition_id": C, "token_id": token, "proxy_wallet": W,
            "side": side, "price": price, "size": 100, "timestamp": timestamp}


class TruthTests(unittest.TestCase):
    def test_proven_sports_loss_even_after_profitable_cashout(self):
        rows = [fill(), fill(side="SELL", price=.65)]
        selected = a.candidate_positions(rows)
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0][3])
        self.assertEqual(a.verdict(G, R, selected[0][1]), "loss")

    def test_real_win_and_loss_follow_final_payout(self):
        self.assertEqual(a.verdict(G, R, "no"), "win")
        self.assertEqual(a.verdict(G, R, "yes"), "loss")

    def test_disputed_unsettled_unknown(self):
        self.assertEqual(a.verdict(G, dict(R, status="proposed"), "yes"), "unknown")
        self.assertEqual(a.verdict(G, dict(R, resolved_at=None), "yes"), "unknown")

    def test_push_is_not_inflated(self):
        self.assertEqual(a.verdict(G, dict(R, payouts=[500000,500000]), "yes"),
                         "push_or_partial")

    def test_token_mismatch_fails_closed(self):
        self.assertEqual(a.verdict(G, R, "unknown-token"), "unknown")

    def test_only_official_sports_count(self):
        self.assertEqual(a.verdict(dict(G, category="Entertainment"), R, "no"),
                         "not_verified_sport")

    def test_both_sides_are_not_independent_predictions(self):
        self.assertEqual(a.candidate_positions([fill(),fill(token="no")]), [])
        self.assertEqual(len(a.candidate_positions([fill(),fill(price=.55)])), 1)

    def test_partial_trade_history_not_certified(self):
        with mock.patch.object(a, "fetch_wallet_trades", return_value=([fill()], False)), \
             mock.patch.object(a, "classify", return_value=("loss",1792000000)):
            sample = a.sample_wallet(W, {}, {})
        self.assertFalse(sample["trade_history_complete"])
        self.assertFalse(sample["certified_sample"])
        self.assertEqual(sample["losses"], 1)

    def test_twenty_verified_wins_plus_one_unknown_is_not_certified(self):
        # A seemingly 100% sample can be cherry-picked by incomplete
        # settlement coverage. Never publish it as certified last-20 accuracy.
        trades=[]
        for i in range(21):
            row=fill(timestamp=1791950000-i)
            row["condition_id"]="0x"+format(i+1,"064x")
            trades.append(row)
        answers=[("win",1792000000)]*20 + [
            ("unknown_resolution_incomplete",None)]
        with mock.patch.object(a,"fetch_wallet_trades",
                               return_value=(trades,True)), \
             mock.patch.object(a,"classify",side_effect=answers):
            result=a.sample_wallet(W,{}, {})
        self.assertEqual(result["resolved_sports_sample"],20)
        self.assertFalse(result["certified_sample"])
        self.assertEqual(result["unverified_final_or_mapping_count"],1)
        self.assertEqual(result["directional_win_fraction_observed"],1.0)

    def test_fewer_than_twenty_observations_not_certified(self):
        with mock.patch.object(a, "fetch_wallet_trades", return_value=([fill()], True)), \
             mock.patch.object(a, "classify", return_value=("win",1792000000)):
            sample = a.sample_wallet(W, {}, {})
        self.assertFalse(sample["certified_sample"])
        self.assertEqual(sample["wins"], 1)

    def test_cursor_success_and_unsafe_reuse(self):
        pages=[{"data":[fill()], "pagination":{"has_more":True,"next_cursor":"x"}},
               {"data":[], "pagination":{"has_more":False,"next_cursor":None}}]
        with mock.patch.object(a.api,"request_json",side_effect=pages) as req:
            items, complete=a.fetch_wallet_trades(W)
        self.assertTrue(complete)
        self.assertEqual(len(items),1)
        self.assertEqual(req.call_args_list[-1].args[1]["cursor"],"x")
        with mock.patch.object(a.api,"request_json",return_value=pages[0]):
            with self.assertRaises(ValueError):
                a.fetch_wallet_trades(W)

    def test_trade_history_asks_for_both_maker_and_taker_in_time_window(self):
        now=1792200000
        earlier=1792000000
        with mock.patch.object(a.api, "request_json",
             return_value={"data":[fill(timestamp=earlier)],
                           "pagination":{"has_more":False}}) as req:
            rows, complete=a.fetch_wallet_trades(W, now)
        self.assertTrue(complete)
        self.assertEqual(len(rows),1)
        params=req.call_args.args[1]
        self.assertEqual(params["taker_only"], "false")
        self.assertEqual(params["start"], now-a.TRADE_LOOKBACK_DAYS*86400)
        self.assertEqual(params["end"],now)

    def test_time_window_violation_is_not_called_complete(self):
        now=1792200000
        old=fill(timestamp=now-a.TRADE_LOOKBACK_DAYS*86400-1)
        with mock.patch.object(a.api,"request_json",
             return_value={"data":[old],"pagination":{"has_more":False}}):
            with self.assertRaisesRegex(ValueError,"ignored requested time window"):
                a.fetch_wallet_trades(W,now)

    def test_first_buy_is_not_earlier_sell_activity(self):
        rows=[fill(side="SELL",timestamp=1791900000),
              fill(side="BUY",timestamp=1792000000)]
        selected=a.candidate_positions(rows)
        self.assertEqual(selected[0][2],1792000000)

    def test_closed_first_scan_must_not_hide_newer_redeemable_loss(self):
        # Reproduces old 220-market bug: CLOSED was enumerated before
        # REDEEMABLE, so 221 old profitable exits evicted a new $600 loss.
        closed=[]
        trades=[]
        for i in range(221):
            condition="0x"+format(i+1,"064x")
            closed.append({"proxy_wallet":W,"condition_id":condition,
                           "token_id":"yes","status":"CLOSED",
                           "realized_pnl":10,"last_event_at":1000+i})
            trade=fill(timestamp=1000+i)
            trade["condition_id"]=condition
            trades.append(trade)
        bad_condition="0x"+format(9999,"064x")
        loser={"proxy_wallet":W,"condition_id":bad_condition,
               "token_id":"yes","status":"REDEEMABLE","redeemable":True,
               "realized_pnl":0,"unrealized_pnl":-600,
               "event_slug":"ufc-recent-loss-2026","last_event_at":20000}
        bad_buy=fill(timestamp=20000)
        bad_buy["condition_id"]=bad_condition
        trades.append(bad_buy)
        def lookup(condition,token,gamma,resolution):
            return ("loss",30001) if condition==bad_condition else ("win",30000)
        with mock.patch.object(a,"fetch_position_history",
                               return_value=(closed+[loser],True)), \
             mock.patch.object(a,"classify",side_effect=lookup):
            result=a.position_truth_sample(W,{},{},
                {"rows":trades,"complete":True})
        self.assertFalse(result["position_market_scan_complete"])
        self.assertEqual(result["position_markets_total"],222)
        self.assertEqual(result["position_scanned_status_counts"]["REDEEMABLE"],1)
        self.assertGreaterEqual(result["position_scanned_status_counts"]["CLOSED"],1)
        self.assertEqual(result["losses"],1)
        self.assertEqual(result["events"][0]["direction"],"loss")
        self.assertFalse(result["certified_sample"])

    def test_position_truth_requires_trade_buy_provenance_to_certify(self):
        closed={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
                "status":"CLOSED","first_entry_at":1791900000,
                "realized_pnl":50}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([closed],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792200000)):
            no_trade=a.position_truth_sample(
                W,{}, {}, {"rows":[],"complete":True})
            matched=a.position_truth_sample(
                W,{}, {}, {"rows":[fill(timestamp=1792000000)],"complete":True})
        self.assertEqual(no_trade["resolved_sports_sample"],0)
        self.assertEqual(no_trade["status_counts"]["trade_entry_unverified"],1)
        self.assertEqual(matched["losses"],1)
        self.assertFalse(matched["certified_sample"])

    def test_redeemable_positions_include_archived_losses(self):
        empty={"data":[],"pagination":{"has_more":False}}
        with mock.patch.object(a.api,"request_json",
                               side_effect=[empty,empty]) as req:
            a.fetch_position_history(W)
        self.assertEqual(req.call_args_list[1].args[1]["include_archived"],"true")

    def test_closed_profitable_position_can_be_wrong_sports_prediction(self):
        closed={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
                "status":"CLOSED","first_entry_at":1791900000,
                "realized_pnl":25}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([closed],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792000000)):
            sample=a.position_truth_sample(W,{}, {})
        self.assertEqual(sample["losses"],1)
        self.assertEqual(sample["profitable_closed_wrong_direction"],1)
        self.assertFalse(sample["certified_sample"])

    def test_unredeemed_losing_sports_position_is_not_ignored(self):
        loser={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
               "status":"REDEEMABLE","redeemable":True,
               "first_entry_at":1791900000,
               "realized_pnl":0,"unrealized_pnl":-600}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([loser],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792000000)):
            sample=a.position_truth_sample(W,{}, {})
        self.assertEqual(sample["losses"],1)

    def test_closed_and_redeemable_are_both_queried(self):
        valid=lambda status: {"data":[],"pagination":{
            "has_more":False,"next_cursor":None}}
        with mock.patch.object(a.api,"request_json",
                               side_effect=[valid("CLOSED"),valid("REDEEMABLE")]) as req:
            rows,complete=a.fetch_position_history(W)
        self.assertTrue(complete)
        self.assertEqual(rows,[])
        self.assertEqual([x.args[1]["status"] for x in req.call_args_list],
                         ["CLOSED","REDEEMABLE"])

    def test_partial_position_pages_cannot_certify_quality(self):
        closed={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
                "status":"CLOSED","first_entry_at":1791900000,
                "realized_pnl":25}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([closed],False)), \
             mock.patch.object(a,"classify",
                               return_value=("win",1792000000)):
            value=a.position_truth_sample(W,{}, {})
        self.assertFalse(value["certified_sample"])
        self.assertFalse(value["position_history_complete"])

    def test_profitable_closed_rate_separate_from_unredeemed_loss_cluster(self):
        rows=[{"condition_id":"0x"+str(i+1).zfill(64),
               "token_id":str(i+1),"status":"CLOSED","realized_pnl":100}
              for i in range(20)]
        rows += [{"condition_id":"0x"+str(i+100).zfill(64),
                  "token_id":str(i+100),"status":"REDEEMABLE",
                  "redeemable":True,"realized_pnl":0,
                  "unrealized_pnl":-600} for i in range(12)]
        result=a.position_accounting_snapshot(rows,True)
        self.assertEqual(result["profitable_closed_fraction"],1.0)
        self.assertEqual(result["unredeemed_major_losers_100usd"],12)
        self.assertEqual(result["unredeemed_loss_usd_observed"],-7200)
        self.assertTrue(result["not_sports_winrate"])

    def test_position_accounting_deduplicates_same_market_token(self):
        x={"condition_id":C,"token_id":"yes","status":"REDEEMABLE",
           "redeemable":True,"realized_pnl":0,"unrealized_pnl":-600}
        counts=a.position_accounting_snapshot([x,x],False)
        self.assertEqual(counts["unredeemed_losers"],1)
        self.assertFalse(counts["positions_complete"])

    def test_archived_ufc_market_uses_v2_token_final_index_without_gamma(self):
        raw={"token_id":"yes","condition_id":C,"event_slug":"ufc-fight-night",
             "outcome_index":0,"resolved":True,"closed":True,
             "final_price":0}
        with mock.patch.object(a,"market_data",return_value=None), \
             mock.patch.object(a,"resolution_data",return_value=R), \
             mock.patch.object(a,"v2_token_data",return_value=raw) as match:
            decision, ended=a.classify(C,"yes",{},{})
        self.assertEqual(decision,"loss")
        self.assertIsInstance(ended,int)
        match.assert_called_once()

    def test_archived_ufc_winning_token_matches_v2_payout(self):
        row={"token_id":"no","condition_id":C,"event_slug":"ufc-fight-night",
             "outcome_index":1,"resolved":True,"closed":True,"final_price":1}
        self.assertEqual(a.v2_token_verdict(row,R),"win")

    def test_v2_token_refuses_bad_index_ambiguous_and_mismatched_price(self):
        row={"token_id":"yes","condition_id":C,"event_slug":"ufc-fight-night",
             "outcome_index":999,"resolved":True,"closed":True}
        self.assertEqual(a.v2_token_verdict(row,R),"unknown_token_index")
        row["outcome_index"]=0
        row["final_price"]=1
        self.assertEqual(a.v2_token_verdict(row,R),
                         "unknown_final_price_mismatch")
        row["final_price"]=0
        row["event_slug"]="will-ufc-cancel-this-year"
        self.assertEqual(a.v2_token_verdict(row,R),"not_verified_sport")

    def test_ufc_accuracy_never_pooled_with_other_sports(self):
        self.assertEqual(a.event_league("ufc-fight-night-2026"),"UFC")
        self.assertEqual(a.event_league("nba-lal-bos-2026"),"NBA")
        self.assertEqual(a.event_league("will-ufc-be-legal"),"UNVERIFIED")
        row={"proxy_wallet":W,"condition_id":C,"token_id":"yes",
             "event_slug":"ufc-fight-night-2026",
             "status":"REDEEMABLE","redeemable":True,
             "realized_pnl":0,"unrealized_pnl":-600}
        with mock.patch.object(a,"fetch_position_history",
                               return_value=([row],True)), \
             mock.patch.object(a,"classify",
                               return_value=("loss",1792200000)):
            value=a.position_truth_sample(
                W,{}, {}, {"rows":[fill()],"complete":True})
        self.assertEqual(value["by_league"]["UFC"]["losses"],1)
        self.assertEqual(value["by_league"]["UFC"]["verified_direction_fraction"],0)

    def test_v2_token_exact_join_rejects_other_condition(self):
        value={"data":[{"token_id":"yes","condition_id":"0x"+"f"*64,
                        "outcome_index":0}]}
        with mock.patch.object(a.api,"request_json",return_value=value):
            self.assertIsNone(a.v2_token_data(C,"yes",{}))

    def test_shadow_quote_outcome_is_hypothetical_not_sent_buy(self):
        key=W+"|"+C+"|yes"
        shadow={key:{"topic":"sports","hypothetical_copy_ask":.5,
                     "hypothetical_copy_stake_usd":5,
                     "decision":"blocked_recent"}}
        with mock.patch.object(a,"classify",
                               return_value=("loss",1792200000)):
            rows=a.audit_signals({key:1791900000},{},{},
                                 signal_evidence=shadow,max_checks=1)
        self.assertEqual(len(rows),1)
        self.assertAlmostEqual(rows[0]["paper_hold_pnl_no_fees"],-5.0)
        self.assertEqual(rows[0]["result"],"loss")

    def test_cached_pending_resolution_does_not_starve_other_checks(self):
        key1=W+"|"+C+"|yes"
        key2=W+"|"+("0x"+"2"*64)+"|no"
        now=int(a.time.time())
        old=[{"wallet":W,"condition":C,"token_id":"yes",
              "result":"unknown_resolution_pending",
              "checked_at":now,"alert_at":now,"settled_at":None}]
        with mock.patch.object(a,"classify",
                               return_value=("loss",now)) as remote:
            rows=a.audit_signals({key1:now,key2:now-100},{},{},
                                 old,max_checks=1)
        self.assertEqual(remote.call_count,1)
        self.assertEqual(sum(row["result"]=="loss" for row in rows),1)

    def test_forward_paper_hold_requires_recorded_ask(self):
        key=W+"|"+C+"|yes"
        prior=[{"wallet":W,"condition":C,"token_id":"yes",
                "alert_at":1234,"result":"win","settled_at":2000}]
        proof={key:{"topic":"sports","hypothetical_copy_ask":.5,
                    "hypothetical_copy_stake_usd":5}}
        with mock.patch.object(a,"classify") as remote:
            rows=a.audit_signals({key:1234},{},{},prior,proof)
        remote.assert_not_called()
        self.assertAlmostEqual(rows[0]["paper_hold_pnl_no_fees"],5.0)
        no_proof=a.audit_signals({key:1234},{},{},prior,{})
        self.assertNotIn("paper_hold_pnl_no_fees",no_proof[0])

    def test_prior_resolved_alert_does_not_requery(self):
        key=W+"|"+C+"|yes"
        previous=[{"wallet":W,"condition":C,"token_id":"yes",
                   "alert_at":1234,"result":"loss","settled_at":2000}]
        with mock.patch.object(a,"classify") as remote:
            result=a.audit_signals({key:1234},{},{},previous)
        remote.assert_not_called()
        self.assertEqual(result[0]["result"],"loss")


    def test_old_unchecked_shadow_precedes_forty_new_market_quotes(self):
        now = 1792305000
        old_condition = "0x" + "a"*64
        old_key = W+"|"+old_condition+"|yes"
        previous = [{"wallet": W, "condition": old_condition, "token_id": "yes",
                     "result": "not_checked", "alert_at": now-3600,
                     "checked_at": 0, "settled_at": None}]
        new = {W+"|"+("0x"+format(i+5, "064x"))+"|yes": now-120
               for i in range(40)}
        with mock.patch.object(a.time, "time", return_value=now):
            with mock.patch.object(a, "classify", return_value=("loss",now)) as remote:
                rows = a.audit_signals(new, {}, {}, previous, max_checks=1,
                                       historical_shadow=True)
        self.assertEqual(remote.call_count, 1)
        by_key = {x["wallet"]+"|"+x["condition"]+"|"+x["token_id"]: x for x in rows}
        self.assertEqual(by_key[old_key]["result"], "loss")
        self.assertEqual(sum(x["result"] == "not_checked" for x in rows), 40)

    def test_shadow_book_quote_archives_then_resolves_after_ring_eviction(self):
        now = 1792305000
        stamp = now-700
        key = W+"|"+C+"|yes"
        quote = {key: {"topic":"sports", "decision":"blocked_recent",
                       "hypothetical_copy_ask": .5,
                       "hypothetical_copy_stake_usd": 5,
                       "hypothetical_copy_shares": 10,
                       "book_quoted_at":stamp, "quoted_at":stamp,
                       "source_trade_at":stamp-25}}
        with mock.patch.object(a.time, "time", return_value=now):
            with mock.patch.object(
                    a, "classify", return_value=("unknown_resolution_pending",None)):
                first = a.audit_signals({key:stamp}, {}, {}, None, quote,
                                        max_checks=1, historical_shadow=True)
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]["result"], "unknown_resolution_pending")
        self.assertAlmostEqual(first[0]["original_book_quote"]
                               ["hypothetical_copy_ask"], .5)
        self.assertNotIn("paper_hold_pnl_no_fees",first[0])
        with mock.patch.object(a.time,"time",return_value=now+3601):
            with mock.patch.object(a,"classify",return_value=("loss",now+3000)) as remote:
                recovered = a.audit_signals({}, {}, {}, first, {},
                                            max_checks=1,historical_shadow=True)
        self.assertEqual(remote.call_count,1)
        self.assertEqual(len(recovered),1)
        self.assertEqual(recovered[0]["result"],"loss")
        self.assertEqual(recovered[0]["paper_hold_pnl_no_fees"],-5.0)
        self.assertEqual(recovered[0]["original_book_quote"]["book_quoted_at"],stamp)

    def test_shadow_archived_quote_cannot_be_replaced_by_later_book_snapshot(self):
        now = 1792305000
        stamp = now-700
        key = W+"|"+C+"|yes"
        first = [{"wallet":W,"condition":C,"token_id":"yes",
                  "result":"unknown_resolution_pending", "checked_at":now-4000,
                  "alert_at":stamp, "settled_at":None,
                  "original_book_quote":{
                    "topic":"sports","hypothetical_copy_ask":.5,
                    "hypothetical_copy_stake_usd":5,
                    "hypothetical_copy_shares":10,
                    "book_quoted_at":stamp,"source_trade_at":stamp-20}}]
        manipulated = {key: {"topic":"sports", "hypothetical_copy_ask":.2,
                             "hypothetical_copy_stake_usd":5,
                             "hypothetical_copy_shares":25,
                             "book_quoted_at":stamp}}
        with mock.patch.object(a.time,"time",return_value=now):
            with mock.patch.object(a,"classify",return_value=("win",now)) as remote:
                rows=a.audit_signals({key:stamp},{},{},first,manipulated,
                                     max_checks=1,historical_shadow=True)
        self.assertEqual(remote.call_count,1)
        self.assertEqual(rows[0]["original_book_quote"]["hypothetical_copy_ask"],.5)
        self.assertEqual(rows[0]["paper_hold_pnl_no_fees"],5.0)

    def test_shadow_no_original_quote_must_not_inherit_opaque_profit(self):
        now = 1792305000
        stamp = now-700
        key = W+"|"+C+"|yes"
        previous = [{"wallet":W,"condition":C,"token_id":"yes",
                     "result":"win","alert_at":stamp,
                     "settled_at":now-100,
                     "paper_hold_pnl_no_fees":900.0,"paper_stake_usd":5}]
        with mock.patch.object(a.time,"time",return_value=now):
            rows=a.audit_signals({}, {}, {}, previous, {},
                                 historical_shadow=True)
        self.assertEqual(len(rows),1)
        self.assertNotIn("paper_hold_pnl_no_fees",rows[0])
        self.assertNotIn("original_book_quote",rows[0])

    def test_expired_shadow_archive_rows_are_not_dressed_as_current(self):
        now=1792305000
        old=now-a.SHADOW_RETENTION_SECONDS-60
        previous=[{"wallet":W,"condition":C,"token_id":"yes",
                   "result":"loss","alert_at":old,"settled_at":old+100}]
        with mock.patch.object(a.time,"time",return_value=now):
            with mock.patch.object(a,"classify") as remote:
                rows=a.audit_signals({}, {}, {}, previous, {},
                                     historical_shadow=True)
        remote.assert_not_called()
        self.assertEqual(rows,[])


if __name__=="__main__":
    unittest.main()
