"""No 3.2 notification may reach the production Telegram destination."""
import unittest
from unittest.mock import patch

from tools import v32_telegram_entry as route
import smart_tracker_v3 as v3


class SeparateTelegramRouteTests(unittest.TestCase):
    def test_valid_private_group_is_distinct_from_production(self):
        actual=route.validate_route({
            "TELEGRAM_BOT_TOKEN":"dummy-token",
            "TELEGRAM_V31_CHAT_ID":"12345678",
            "TELEGRAM_V32_CHAT_ID":"-1009876543210"})
        self.assertEqual(actual,"-1009876543210")

    def test_no_bot_token_fails(self):
        with self.assertRaisesRegex(ValueError,"token"):
            route.validate_route({
                "TELEGRAM_V31_CHAT_ID":"12345678",
                "TELEGRAM_V32_CHAT_ID":"-1009876543210"})

    def test_missing_second_destination_never_falls_back(self):
        with self.assertRaisesRegex(ValueError,"Separate 3.2"):
            route.validate_route({
                "TELEGRAM_BOT_TOKEN":"dummy-token",
                "TELEGRAM_V31_CHAT_ID":"12345678"})

    def test_missing_old_destination_never_sends(self):
        with self.assertRaisesRegex(ValueError,"Existing 3.1"):
            route.validate_route({
                "TELEGRAM_BOT_TOKEN":"dummy-token",
                "TELEGRAM_V32_CHAT_ID":"-1009876543210"})

    def test_same_chat_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"matches the live"):
            route.validate_route({
                "TELEGRAM_BOT_TOKEN":"dummy-token",
                "TELEGRAM_V31_CHAT_ID":"-1009876543210",
                "TELEGRAM_V32_CHAT_ID":"-1009876543210"})

    def test_invalid_destination_is_rejected(self):
        for candidate in ("", "example", "123 hi", "https://t.me/foo"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    route.validate_route({
                        "TELEGRAM_BOT_TOKEN":"dummy-token",
                        "TELEGRAM_V31_CHAT_ID":"12345678",
                        "TELEGRAM_V32_CHAT_ID":candidate})

    def test_entrypoint_checks_route_before_importing_tracker(self):
        with patch.dict("os.environ",{
            "TELEGRAM_BOT_TOKEN":"dummy-token",
            "TELEGRAM_V31_CHAT_ID":"12345678",
            "TELEGRAM_V32_CHAT_ID":"",
            "TELEGRAM_CHAT_ID":"12345678"},clear=True):
            with patch.object(v3,"main") as main:
                with self.assertRaisesRegex(ValueError,"Separate 3.2"):
                    route.run()
                main.assert_not_called()

    def test_experimental_label_is_added_only_when_enabled(self):
        g={"name":"Fake", "wallet":"0x"+"a"*40,
           "condition":"0x"+"b"*64,"title":"Test","last":100,
           "outcomes":{},"url":""}
        with patch.dict("os.environ",{"V32_TELEGRAM_ALERT_LABEL":"1"}):
            labeled=v3.format_alert(g,"BUY",holdings={},meta={},now=200)
        with patch.dict("os.environ",{"V32_TELEGRAM_ALERT_LABEL":"0"}):
            regular=v3.format_alert(g,"BUY",holdings={},meta={},now=200)
        self.assertIn("Tracker".upper(),labeled.upper())
        self.assertNotIn("TRACKER 3.2",regular.upper())


if __name__=="__main__":
    unittest.main()
