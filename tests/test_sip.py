import tempfile
import unittest
from pathlib import Path

from app.sip import AmiClient, SipError, normalize_server, originate_test_call, render_pjsip_config, write_pjsip_config


class SipConfigTests(unittest.TestCase):
    def test_normalizes_mango_server(self):
        self.assertEqual(normalize_server("@VPBX400338531.mangosip.ru"), "vpbx400338531.mangosip.ru")

    def test_renders_locked_mango_registration(self):
        config = render_pjsip_config({
            "mango_sip_server": "vpbx123.mangosip.ru",
            "mango_sip_login": "123/225",
            "mango_sip_password": "strong#password",
            "mango_sip_port": "5060",
            "mango_extension": "225",
        })
        self.assertIn("type=registration", config)
        self.assertIn("allow=alaw", config)
        self.assertIn("dtmf_mode=rfc4733", config)
        self.assertIn("context=mango-inbound-locked", config)
        self.assertIn("expiration=180", config)
        self.assertIn("from_user=123/225", config)

    def test_rejects_configuration_injection(self):
        with self.assertRaises(SipError):
            render_pjsip_config({
                "mango_sip_server": "mangosip.ru\n[malicious]",
                "mango_sip_login": "123/225",
                "mango_sip_password": "password",
            })

    def test_writes_private_configuration_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pjsip_mango.conf"
            write_pjsip_config({
                "mango_sip_server": "vpbx123.mangosip.ru",
                "mango_sip_login": "123/225",
                "mango_sip_password": "password",
            }, str(target))
            self.assertTrue(target.exists())
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_ami_originate_requires_success_response(self):
        client = AmiClient("asterisk", 5038, "user", "secret")
        client.action = lambda *args, **kwargs: ["Response: Success"]
        client.originate(
            "PJSIP/+79991234567@mango-endpoint",
            "AudioSocket",
            "00000000-0000-0000-0000-000000000001,app:9092",
            "+73012555777",
        )

    def test_ami_originate_surfaces_rejection(self):
        client = AmiClient("asterisk", 5038, "user", "secret")
        client.action = lambda *args, **kwargs: ["Response: Error", "Message: Originate failed"]
        with self.assertRaises(SipError):
            client.originate("PJSIP/test", "AudioSocket", "data", "+73012555777")

    def test_test_call_removes_plus_for_mango_sip(self):
        import app.sip as sip

        captured = {}
        original = sip.ami_client
        client = AmiClient("asterisk", 5038, "user", "secret")
        client.originate = lambda channel, application, data, caller_id: captured.update(
            channel=channel, application=application, data=data, caller_id=caller_id
        )
        sip.ami_client = lambda: client
        try:
            originate_test_call(
                "+79991234567",
                "00000000-0000-0000-0000-000000000001",
                "+73012555777",
            )
        finally:
            sip.ami_client = original

        self.assertEqual(captured["channel"], "PJSIP/89991234567@mango-endpoint")
        self.assertEqual(captured["caller_id"], "73012555777")


if __name__ == "__main__":
    unittest.main()
