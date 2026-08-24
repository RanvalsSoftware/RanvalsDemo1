import hashlib
import hmac

from odoo.tests import TransactionCase, tagged

from ..tools import extract_leadgen_events, field_data_to_dict, verify_hub_signature


@tagged("post_install", "-at_install")
class TestMetaLeadTools(TransactionCase):
    def test_signature_and_batch_extraction(self):
        body = b'{"object":"page","entry":[]}'
        secret = "test-secret"
        signature = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        self.assertTrue(verify_hub_signature(body, signature, secret))
        self.assertFalse(verify_hub_signature(body + b" ", signature, secret))

        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "123",
                    "changes": [
                        {"field": "leadgen", "value": {"leadgen_id": "1"}},
                        {"field": "leadgen", "value": {"leadgen_id": "2"}},
                    ],
                }
            ],
        }
        self.assertEqual(
            [event["leadgen_id"] for event in extract_leadgen_events(payload)],
            ["1", "2"],
        )

    def test_multivalue_answers_are_preserved(self):
        answers = field_data_to_dict(
            [
                {"name": "Product", "values": ["A", "B"]},
                {"name": "product", "values": ["C"]},
            ]
        )
        self.assertEqual(answers["product"], ["A", "B", "C"])

