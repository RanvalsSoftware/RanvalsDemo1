from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRdsPurchaseDocument(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "RDS Purchase Vendor",
                "lang": "en_US",
            }
        )
        cls.delivery_partner = cls.env["res.partner"].create(
            {
                "name": "RDS Dropship Address",
                "street": "Dropship Street 19",
                "type": "delivery",
            }
        )

    def _purchase_order(self):
        return self.env["purchase.order"].create(
            {
                "partner_id": self.vendor.id,
                "dest_address_id": self.delivery_partner.id,
            }
        )

    def test_server_action_is_limited_to_purchase_users(self):
        server_action = self.env.ref(
            "ranvals_document_studio.server_action_purchase_rds_export"
        )
        self.assertIn(
            self.env.ref("purchase.group_purchase_user"), server_action.group_ids
        )

    def test_document_context_keeps_vendor_and_shipping_addresses(self):
        order = self._purchase_order()
        template = self.env["rds.template"].get_default_for(
            "purchase.order", company=order.company_id
        )

        context = order._rds_document_context(template, "en_US")

        self.assertEqual(context["partner_info"]["name"], self.vendor.name)
        self.assertEqual(
            context["shipping_partner_info"]["name"], self.delivery_partner.name
        )
        self.assertTrue(
            any(
                "Dropship Street 19" in metadata["value"]
                for metadata in context["metadata"]
            )
        )
        self.assertFalse(
            context["bank"],
            "Purchase documents must not expose an arbitrary buyer bank account.",
        )

    def test_report_root_prefers_requested_then_partner_language(self):
        report_view = self.env.ref(
            "ranvals_document_studio.report_purchase_document"
        )
        self.assertIn(
            "rds_requested_lang') or doc.partner_id.lang",
            report_view.arch_db,
        )

    def test_report_provider_rejects_malformed_template_and_language_values(self):
        provider = self.env[
            "report.ranvals_document_studio.report_purchase_document"
        ]
        malformed_payloads = (
            [],
            {"rds_template_id": True},
            {"rds_template_id": "1"},
            {"rds_template_id": [1]},
            {"rds_template_id": 0},
            {"lang": False},
            {"lang": ""},
            {"lang": "xx_INVALID"},
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload), self.assertRaises(UserError):
                provider._get_report_values([], payload)
