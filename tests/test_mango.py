import hashlib
import json
import unittest
from urllib.parse import parse_qs

import httpx

from app.mango import MangoClient, MangoEventError, describe_route_result, parse_call_event, parse_route_result, phones_match, should_route_test_call, verify_signature


class MangoEventTests(unittest.TestCase):
    def test_matches_mango_international_dialing_prefix(self):
        self.assertTrue(phones_match("81079270120777", "+79270120777"))

    def test_explains_expired_route_call(self):
        self.assertIn("завершился", describe_route_result(4101))

    def test_verifies_mango_signature_over_original_json_string(self):
        api_key = "key"
        api_salt = "salt"
        raw_json = '{"call_id":"call-1","seq":2}'
        signature = hashlib.sha256(f"{api_key}{raw_json}{api_salt}".encode()).hexdigest()
        self.assertTrue(verify_signature(api_key, api_salt, api_key, raw_json, signature))
        self.assertFalse(verify_signature(api_key, api_salt, api_key, raw_json + " ", signature))
        self.assertFalse(verify_signature(api_key, api_salt, "another-key", raw_json, signature))

    def test_parses_call_event(self):
        raw_json = json.dumps({
            "entry_id": "entry-1",
            "call_id": "call-1",
            "seq": 3,
            "timestamp": 1790000000,
            "call_state": "Connected",
            "location": "abonent",
            "from": {"number": "79000000000"},
            "to": {"number": "sip:test@mangosip.ru", "extension": "225"},
        })
        event = parse_call_event(raw_json)
        self.assertEqual(event.call_id, "call-1")
        self.assertEqual(event.sequence, 3)
        self.assertEqual(event.call_state, "Connected")
        self.assertEqual(event.to_extension, "225")

    def test_rejects_event_without_call_id(self):
        with self.assertRaises(MangoEventError):
            parse_call_event('{"seq":1}')

    def test_routes_only_matching_appeared_call_before_extension_225(self):
        event = parse_call_event(json.dumps({
            "call_id": "call-1", "seq": 1, "call_state": "Appeared", "location": "ivr",
            "from": {"number": "8 (927) 012-07-77"}, "to": {"number": "374747"},
        }))
        self.assertTrue(should_route_test_call(event, enabled=True, test_phone="+79270120777", target_extension="225"))
        self.assertFalse(should_route_test_call(event, enabled=False, test_phone="+79270120777", target_extension="225"))
        self.assertFalse(should_route_test_call(event, enabled=True, test_phone="+79990000000", target_extension="225"))
        already_routed = parse_call_event(json.dumps({
            "call_id": "call-2", "seq": 1, "call_state": "Appeared", "location": "abonent",
            "from": {"number": "79270120777"}, "to": {"extension": "225"},
        }))
        self.assertFalse(should_route_test_call(already_routed, enabled=True, test_phone="+79270120777", target_extension="225"))

    def test_does_not_route_leg_created_by_previous_route_command(self):
        event = parse_call_event(json.dumps({
            "call_id": "new-leg", "seq": 1, "call_state": "Appeared", "location": "abonent",
            "from": {"number": "79270120777", "taken_from_call_id": "original-leg"},
            "to": {"number": "sip:user9@vpbx.example"},
        }))
        self.assertFalse(should_route_test_call(
            event, enabled=True, test_phone="+79270120777", target_extension="225", target_sip_login="user9",
        ))

    def test_does_not_route_call_already_addressed_to_robot_sip_user(self):
        event = parse_call_event(json.dumps({
            "call_id": "robot-leg", "seq": 1, "call_state": "Appeared", "location": "abonent",
            "from": {"number": "79270120777"}, "to": {"number": "sip:user9@vpbx.example"},
        }))
        self.assertFalse(should_route_test_call(
            event, enabled=True, test_phone="+79270120777", target_extension="225", target_sip_login="user9",
        ))

    def test_parses_route_result(self):
        self.assertEqual(parse_route_result('{"command_id":"cmd-1","result":1000}'), ("cmd-1", 1000))


class MangoClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_signed_route_command(self):
        captured = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = (await request.aread()).decode()
            return httpx.Response(200, json={})

        async with MangoClient("key", "salt", transport=httpx.MockTransport(handler)) as client:
            await client.route_call("call-1", "225", "cmd-1")

        self.assertEqual(captured["url"], "https://app.mango-office.ru/vpbx/commands/route")
        form = parse_qs(captured["body"])
        self.assertEqual(form["vpbx_api_key"], ["key"])
        raw_json = form["json"][0]
        self.assertEqual(json.loads(raw_json), {"command_id": "cmd-1", "call_id": "call-1", "to_number": "225"})
        expected = hashlib.sha256(f"key{raw_json}salt".encode()).hexdigest()
        self.assertEqual(form["sign"], [expected])


if __name__ == "__main__":
    unittest.main()
