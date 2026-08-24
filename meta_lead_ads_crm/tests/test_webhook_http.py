import hashlib
import hmac
import json
from urllib.parse import urlencode

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestMetaLeadWebhookHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secret = "http-test-app-secret"
        cls.verify_token = "http-test-verify-token"
        cls.page_id = "112233445566"
        cls.connection = cls.env["meta.lead.connection"].create(
            {
                "name": "HTTP Test Page",
                "company_id": cls.env.company.id,
                "page_id": cls.page_id,
                "app_secret": cls.secret,
                "access_token": "http-test-page-token",
                "verify_token": cls.verify_token,
                "webhook_enabled": True,
            }
        )

    def _post(self, payload, secret=None):
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            (secret or self.secret).encode(), body, hashlib.sha256
        ).hexdigest()
        return self.url_open(
            "/meta_lead_ads/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )

    def test_get_verification_accepts_only_the_configured_token(self):
        query = urlencode(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": self.verify_token,
                "hub.challenge": "246810",
            }
        )
        response = self.url_open(f"/meta_lead_ads/webhook?{query}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "246810")

        wrong_query = urlencode(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "246810",
            }
        )
        response = self.url_open(f"/meta_lead_ads/webhook?{wrong_query}")
        self.assertEqual(response.status_code, 403)

    def test_get_verification_supports_non_ascii_tokens(self):
        token = "doğrulama-🔐"
        self.connection.verify_token = token
        query = urlencode(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": token,
                "hub.challenge": "unicode-ok",
            }
        )
        response = self.url_open(f"/meta_lead_ads/webhook?{query}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "unicode-ok")

    def test_oversized_body_is_rejected_before_signature_processing(self):
        response = self.url_open(
            "/meta_lead_ads/webhook",
            data=b"x" * ((1024 * 1024) + 1),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_valid_signed_batch_is_queued_once(self):
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": self.page_id,
                    "changes": [
                        {
                            "field": "leadgen",
                            "value": {
                                "leadgen_id": "HTTP-LEAD-1",
                                "page_id": self.page_id,
                                "form_id": "900",
                            },
                        },
                        {
                            "field": "leadgen",
                            "value": {
                                "leadgen_id": "HTTP-LEAD-2",
                                "page_id": self.page_id,
                                "form_id": "900",
                            },
                        },
                    ],
                }
            ],
        }
        first = self._post(payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["queued"], 2)
        second = self._post(payload)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["queued"], 0)
        self.assertEqual(second.json()["duplicates"], 2)
        self.assertEqual(
            self.env["meta.lead.event"].search_count(
                [("meta_leadgen_id", "in", ["HTTP-LEAD-1", "HTTP-LEAD-2"])]
            ),
            2,
        )

    def test_invalid_signature_cannot_create_an_event(self):
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": self.page_id,
                    "changes": [
                        {
                            "field": "leadgen",
                            "value": {
                                "leadgen_id": "HTTP-FORBIDDEN",
                                "page_id": self.page_id,
                            },
                        }
                    ],
                }
            ],
        }
        response = self._post(payload, secret="wrong-secret")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            self.env["meta.lead.event"].search(
                [("meta_leadgen_id", "=", "HTTP-FORBIDDEN")]
            )
        )
