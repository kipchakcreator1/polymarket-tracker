"""No-key/nonorder tests for OPEN sports alerts distinct from new BUY."""
import unittest
from unittest import mock
from tools import v33_open_positions as op
from tools import v33_signal_bot as bot

W="0x"+"a"*40
C="0x"+"b"*64
T="12345"
NOW=1792223000
META={W:{"name":"Trader","winrate":.8}}


def held(**kwargs):
    row={"proxy_wallet":W,"condition_id":C,"token_id":T,
         "status":"OPEN","redeemable":False,
         "last_event_at":NOW-300,"entry_cost_usdc":75.,
         "current_size":150.,"unrealized_pnl":3,
         "event_slug":"nba-celtics-lakers",
         "title":"Celtics vs Lakers","outcome":"Celtics"}
    row.update(kwargs)
    return row


def quote(*unused):
    return {"price":.51,"shares":9.80392,"spread":.02,
            "best_ask":.51},None


def sports(state,group,now):
    state.setdefault("market_focus_cache",{})[group["condition"]]={
        "focus":"sports_btc","sports":True,"game_start":now+3600,
        "checked_at":now}
    return None


class OpenSportsAlertTests(unittest.TestCase):
    def test_only_current_open_validated_position_can_be_candidate(self):
        self.assertIsNotNone(op.available_open_position(held(),NOW))
        for row in [
            held(status="REDEEMABLE",redeemable=True),
            held(status="CLOSED"),
            held(redeemable=True),
            held(current_size=0),
            held(entry_cost_usdc=12),
            held(last_event_at=NOW-49*3600),
            held(last_event_at=NOW+1000),
            held(token_id=""),
            held(condition_id="bad")
        ]:
            self.assertIsNone(op.available_open_position(row,NOW))

    def test_open_telegram_is_labeled_not_a_new_buy_and_capped(self):
        state=bot.blank_state()
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"recent_form_reason",return_value=None), \
             mock.patch.object(bot,"fetch_open_positions",return_value=[held()]), \
             mock.patch.object(bot,"market_focus_reason",side_effect=sports), \
             mock.patch.object(bot,"political_market_reason",return_value=None), \
             mock.patch.object(bot,"check_trade",side_effect=quote), \
             mock.patch.object(bot.v32,"market_accepts_orders",return_value=True), \
             mock.patch.object(bot.v2,"send_telegram",return_value=471) as send:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),1)
            self.assertEqual(op.scan(state,META,NOW+10,clock=lambda:NOW+10),0)
        self.assertEqual(send.call_count,1)
        message=send.call_args.args[0]
        self.assertIn("ОТКРЫТАЯ СПОРТИВНАЯ ПОЗИЦИЯ",message)
        self.assertIn("НЕ новый BUY",message)
        self.assertEqual(state["last_telegram_acceptance"]["kind"],"open_position")
        self.assertEqual(state["counters"]["open_alerts_sent"],1)
        self.assertEqual(len(state["open_position_evidence"]),1)
        self.assertEqual(len(state["open_sent"]),1)
        self.assertNotIn("alerts_sent",state["counters"])
        self.assertEqual(state["sent"],{})  # NEVER mark this as a fresh BUY

    def test_late_telegram_ack_keeps_open_dedupe_for_24_hours(self):
        # Real public API verification can take seconds after checked_at.
        # The 24-hour repeat key must survive that normal clock skew.
        state=bot.blank_state()
        times=iter((NOW,NOW+2,NOW+3700,NOW+3702))
        with (
            mock.patch.object(bot,"MARKET_FOCUS","sports_btc"),
            mock.patch.object(bot,"recent_form_reason",return_value=None),
            mock.patch.object(bot,"fetch_open_positions",return_value=[held()]),
            mock.patch.object(bot,"market_focus_reason",side_effect=sports),
            mock.patch.object(bot,"political_market_reason",return_value=None),
            mock.patch.object(bot,"check_trade",side_effect=quote),
            mock.patch.object(bot.v32,"market_accepts_orders",return_value=True),
            mock.patch.object(bot.v2,"send_telegram",return_value=471) as send,
        ):
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:next(times)),1)
            self.assertEqual(len(state["open_sent"]),1)
            self.assertEqual(op.scan(state,META,NOW+3700,
                                     clock=lambda:next(times)),0)
        self.assertEqual(send.call_count,1)
        self.assertEqual(state["counters"]["open_alerts_sent"],1)

    def test_no_alert_when_risk_unavailable(self):
        state=bot.blank_state()
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"recent_form_reason",
                               side_effect=TimeoutError("api unavailable")), \
             mock.patch.object(bot.v2,"send_telegram") as send:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),0)
        send.assert_not_called()
        self.assertEqual(state["counters"]["open_risk_api_incomplete"],1)

    def test_no_alert_when_future_game_start_not_verified(self):
        state=bot.blank_state()
        def no_game(state,group,now):
            state.setdefault("market_focus_cache",{})[C]={
                "game_start":None,"sports":True}
            return None
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"recent_form_reason",return_value=None), \
             mock.patch.object(bot,"fetch_open_positions",return_value=[held()]), \
             mock.patch.object(bot,"market_focus_reason",side_effect=no_game), \
             mock.patch.object(bot,"check_trade") as check, \
             mock.patch.object(bot.v2,"send_telegram") as sender:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),0)
        check.assert_not_called()
        sender.assert_not_called()

    def test_pending_fresh_buy_prevents_duplicate_open_notification(self):
        state=bot.blank_state()
        state["sent"][W+"|"+C+"|"+T]=NOW-120
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"recent_form_reason",return_value=None), \
             mock.patch.object(bot,"fetch_open_positions",return_value=[held()]), \
             mock.patch.object(bot,"market_focus_reason") as focus, \
             mock.patch.object(bot.v2,"send_telegram") as sender:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),0)
        sender.assert_not_called()
        focus.assert_not_called()

    def test_unverified_politics_never_sends(self):
        state=bot.blank_state()
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"recent_form_reason",return_value=None), \
             mock.patch.object(bot,"fetch_open_positions",return_value=[held()]), \
             mock.patch.object(bot,"market_focus_reason",side_effect=sports), \
             mock.patch.object(bot,"political_market_reason",
                               side_effect=ValueError("missing tags")), \
             mock.patch.object(bot.v2,"send_telegram") as sender:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),0)
        sender.assert_not_called()

    def test_hourly_cap_blocks_send_even_with_new_wallet(self):
        state=bot.blank_state()
        state["open_alert_times"]=[NOW-20,NOW-100,NOW-200]
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"fetch_open_positions") as fetch, \
             mock.patch.object(bot.v2,"send_telegram") as sender:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),0)
        fetch.assert_not_called()
        sender.assert_not_called()
        self.assertEqual(state["counters"]["open_hourly_cap"],1)

    def test_other_sport_open_not_changed_into_btc_alert(self):
        state=bot.blank_state()
        with mock.patch.object(bot,"MARKET_FOCUS","sports_btc"), \
             mock.patch.object(bot,"recent_form_reason",return_value=None), \
             mock.patch.object(bot,"fetch_open_positions",return_value=[
                 held(title="Bitcoin up or down")]), \
             mock.patch.object(bot,"market_focus_reason") as focus, \
             mock.patch.object(bot.v2,"send_telegram") as sender:
            self.assertEqual(op.scan(state,META,NOW,clock=lambda:NOW),0)
        focus.assert_not_called()
        sender.assert_not_called()

if __name__=="__main__":
    unittest.main()
