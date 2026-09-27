"""Dependency-free tests: python tests/test_security_helpers.py."""

import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "ranvals_qr_security",
    Path(__file__).resolve().parents[1] / "security_utils.py",
)
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)


class SecurityHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pin_hash = security.hash_pin("58420936")  # Synthetic test value.

    def test_pin_roundtrip_salt_and_rehash_policy(self):
        self.assertTrue(security.verify_pin("58420936", self.pin_hash))
        self.assertFalse(security.pin_needs_rehash(self.pin_hash))
        self.assertTrue(security.is_valid_pin_hash(self.pin_hash))
        other = security.hash_pin("58420936")
        self.assertNotEqual(self.pin_hash, other)
        self.assertNotIn("58420936", self.pin_hash)

    def test_pin_mismatch_and_invalid_inputs(self):
        self.assertFalse(security.verify_pin("58420935", self.pin_hash))
        for value in ("", "123", "x" * 10_000, None, "１２３４５６", "123456\n"):
            self.assertFalse(security.verify_pin(value, self.pin_hash))
            with self.assertRaises(ValueError):
                security.hash_pin(value)

    def test_hash_parser_rejects_hostile_costs_and_lengths(self):
        invalid = (
            "",
            "x",
            None,
            "pbkdf2_sha256$99999999999$00$00",
            "pbkdf2_sha256$600000$00$00",
            "pbkdf2_sha256$299999$" + "00" * 16 + "$" + "00" * 32,
        )
        for value in invalid:
            self.assertFalse(security.verify_pin("58420936", value))
            self.assertFalse(security.is_valid_pin_hash(value))

    def test_code_normalization(self):
        self.assertEqual(security.normalize_code("  ranvals123  "), "RANVALS123")
        for value in (None, "a b", "şekil", "A" * 21, "<script>", "abc"):
            self.assertEqual(security.normalize_code(value), "")

    def test_network_allowlist_is_fail_closed_and_canonical(self):
        self.assertFalse(security.is_allowed_ip("8.8.8.8", ""))
        self.assertTrue(security.is_allowed_ip("8.8.8.8", "8.8.8.8/32"))
        self.assertFalse(security.is_allowed_ip("1.1.1.1", "8.8.8.8/32"))
        networks = "8.8.8.8/32 # test only\n\n1.1.1.1/32"
        self.assertTrue(security.is_allowed_ip("1.1.1.1", networks))
        self.assertTrue(
            security.is_allowed_ip(
                "2606:4700:4700::1111", "2606:4700:4700::/64"
            )
        )
        self.assertTrue(security.is_allowed_ip("::ffff:8.8.8.8", "8.8.8.8/32"))

    def test_network_rejects_proxy_private_and_overbroad_values(self):
        invalid_networks = (
            "0.0.0.0/0",
            "::/0",
            "8.8.0.0/16",
            "192.168.1.0/24",
            "127.0.0.1",
            "10.0.0.0/24",
            "8.8.8.8/24",
        )
        for value in invalid_networks:
            with self.assertRaises(ValueError):
                security.parse_networks(value)
        for value in ("8.8.8.8, 1.1.1.1", "for=8.8.8.8", "8.8.8.8:123", None, "127.0.0.1"):
            self.assertFalse(security.is_allowed_ip(value, "8.8.8.8/32"))

    def test_network_count_is_bounded(self):
        text = "\n".join(f"1.1.1.{index}/32" for index in range(1, 66))
        with self.assertRaises(ValueError):
            security.parse_networks(text)

    def test_https_base_validation(self):
        self.assertEqual(
            security.validate_base_url("https://example.test/"), "https://example.test"
        )
        self.assertEqual(
            security.validate_base_url("https://example.test:8443"),
            "https://example.test:8443",
        )
        invalid = (
            "http://example.test",
            "https://x/y",
            "https://u:p@x",
            "https://x?key=1",
            "https://x?",
            "https://x/#x",
            "https://x#",
            "https://x:bad",
            "https://x:",
            "https://bad_host.example",
            "javascript:alert(1)",
            "https://x y",
            "https://x\\y",
            None,
        )
        for value in invalid:
            with self.assertRaises(ValueError):
                security.validate_base_url(value)

    def test_intent_roundtrip_tamper_expiry_and_clock_rules(self):
        token = security.sign_intent(
            "server-secret", {"action": "in", "credential_id": 5}, now=1_000
        )
        payload = security.verify_intent("server-secret", token, now=1_001)
        self.assertEqual(payload["credential_id"], 5)
        self.assertIn("nonce", payload)

        with self.assertRaises(ValueError):
            security.verify_intent("other-secret", token, now=1_001)
        body, signature = token.split(".")
        with self.assertRaises(ValueError):
            security.verify_intent(
                "server-secret", "a" + body[1:] + "." + signature, now=1_001
            )

        expiring = security.sign_intent("secret", {}, now=1_000, ttl=10)
        with self.assertRaises(ValueError):
            security.verify_intent("secret", expiring, now=1_010)
        future = security.sign_intent("secret", {}, now=1_100)
        with self.assertRaises(ValueError):
            security.verify_intent("secret", future, now=1_000)

    def test_intent_limits_randomness_and_malformed_values(self):
        with self.assertRaises(ValueError):
            security.sign_intent("secret", {}, ttl=0)
        with self.assertRaises(ValueError):
            security.sign_intent("secret", {}, ttl=security.MAX_INTENT_TTL + 1)
        self.assertNotEqual(
            security.sign_intent("secret", {}, now=1_000),
            security.sign_intent("secret", {}, now=1_000),
        )
        for value in ("", "a.b.c", None, "x" * 9_000, "a.b"):
            with self.assertRaises(ValueError):
                security.verify_intent("secret", value)

    def test_token_digest(self):
        digest = security.digest_token("test-device-token")
        self.assertEqual(len(digest), 64)
        self.assertNotIn("test-device-token", digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
