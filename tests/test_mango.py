import hashlib
import json
import unittest

from app.mango import MangoEventError, parse_call_event, verify_signature


class MangoEventTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
