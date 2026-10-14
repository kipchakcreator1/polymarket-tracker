"""The experimental 3.2 bot uses its OWN token and the SAME DM chat ID."""
import unittest
from unittest.mock import patch

from tools import v32_telegram_entry as route
import smart_tracker_v3 as v3


BASE={
    "TELEGRAM_V31_BOT_TOKEN":"old_dummy_token",
    "TELEGRAM_V32_BOT_TOKEN":"new_dummy_token",
    "TELEGRAM_CHAT_ID":"12345678"
}


class SeparateTelegramRouteTests(unittest.TestCase):
    def test_valid_private_chat_id_can_be_shared_by_two_different_bots(self):
        bot, chat=route.validate_route(BASE)
        self.assertEqual(bot,"new_dummy_token")
        self.assertEqual(chat,"12345678")

    def test_private_group_destination_also_works(self):
        bot, chat=route.validate_route({**BASE, "TELEGRAM_CHAT_ID":"-1009876543210"})
        self.assertEqual(bot,"new_dummy_token")
        self.assertEqual(chat,"-1009876543210")

    def test_missing_new_bot_token_fails(self):
        with self.assertRaisesRegex(ValueError,"New 3.2"):
            route.validate_route({**BASE,"TELEGRAM_V32_BOT_TOKEN":""})

    def test_missing_old_bot_token_fails(self):
        with self.assertRaisesRegex(ValueError,"Original 3.1"):
            route.validate_route({**BASE,"TELEGRAM_V31_BOT_TOKEN":""})

    def test_same_bot_token_fails_even_with_private_chat(self):
        with self.assertRaisesRegex(ValueError,"matches the original"):
            route.validate_route({
                **BASE,"TELEGRAM_V32_BOT_TOKEN":BASE["TELEGRAM_V31_BOT_TOKEN"]
            })

    def test_invalid_destination_is_rejected(self):
        for candidate in ("", "example", "123 hi", "https://t.me/foo"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    route.validate_route({**BASE,"TELEGRAM_CHAT_ID":candidate})

    def test_fail_closed_before_tracker_runs(self):
        with patch.dict("os.environ",{
            **BASE,"TELEGRAM_V32_BOT_TOKEN":""},clear=True):
            with patch.object(v3,"main") as main:
                with self.assertRaisesRegex(ValueError,"New 3.2"):
                    route.run()
                main.assert_not_called()

    def test_success_switches_to_new_bot_before_tracker_runs(self):
        with patch.dict("os.environ",BASE,clear=True):
            with patch.object(v3,"main") as main:
                route.run()
                main.assert_called_once_with()
                import os
                self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"],BASE["TELEGRAM_V32_BOT_TOKEN"])
                self.assertEqual(os.environ["TELEGRAM_CHAT_ID"],BASE["TELEGRAM_CHAT_ID"])
                self.assertEqual(os.environ["V32_TELEGRAM_ALERT_LABEL"],"1")

    def test_experimental_label_is_added_only_when_enabled(self):
        g={"name":"Fake", "wallet":"0x"+"a"*40,
           "condition":"0x"+"b"*64,"title":"Test","last":100,
           "outcomes":{},"url":""}
        with patch.dict("os.environ",{"V32_TELEGRAM_ALERT_LABEL":"1"}):
            labeled=v3.format_alert(g,"BUY",holdings={},meta={},now=200)
        with patch.dict("os.environ",{"V32_TELEGRAM_ALERT_LABEL":"0"}):
            regular=v3.format_alert(g,"BUY",holdings={},meta={},now=200)
        self.assertIn("TRACKER 3.2",labeled.upper())
        self.assertNotIn("TRACKER 3.2",regular.upper())


if __name__=="__main__":
    unittest.main()
