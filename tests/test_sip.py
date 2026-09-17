import tempfile
import unittest
from pathlib import Path

from app.sip import SipError, normalize_server, render_pjsip_config, write_pjsip_config


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
        self.assertIn("from_user=225", config)

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


if __name__ == "__main__":
    unittest.main()
