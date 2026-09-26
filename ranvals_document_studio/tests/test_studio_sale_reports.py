from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRdsStudioSaleReports(TransactionCase):

    REPORT_ACTION_XMLIDS = {
        "beauty": "action_report_rds_sale_beauty_document",
        "construction": "action_report_rds_sale_construction_document",
        "technology": "action_report_rds_sale_technology_document",
        "industrial": "action_report_rds_sale_industrial_document",
        "eco": "action_report_rds_sale_eco_document",
        "furniture": "action_report_rds_sale_furniture_document",
    }

    TEMPLATE_XMLIDS = {
        "beauty": "template_beauty_premium",
        "construction": "template_construction_navy",
        "technology": "template_technology_blue",
        "industrial": "template_industrial_red",
        "eco": "template_eco_green",
        "furniture": "template_furniture_terracotta",
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "RDS Test Sale Customer",
                "lang": "en_US",
            }
        )

    def test_each_design_is_a_bound_sale_order_report(self):
        for action_xmlid in self.REPORT_ACTION_XMLIDS.values():
            action = self.env.ref("ranvals_document_studio.%s" % action_xmlid)
            self.assertEqual(action.binding_model_id.model, "sale.order")
            self.assertEqual(action.binding_type, "report")
            self.assertEqual(action.binding_view_types, "list,form")

    def test_all_design_reports_are_returned_by_runtime_bindings(self):
        expected_action_ids = {
            self.env.ref("ranvals_document_studio.%s" % xmlid).id
            for xmlid in self.REPORT_ACTION_XMLIDS.values()
        }
        bindings = self.env["ir.actions.actions"].get_bindings("sale.order")
        report_binding_ids = {binding["id"] for binding in bindings.get("report", [])}
        self.assertEqual(
            expected_action_ids & report_binding_ids,
            expected_action_ids,
            "All six Document Studio designs must be present in sale.order report bindings.",
        )

    def test_standard_sale_report_action_is_not_repointed(self):
        standard_action = self.env.ref("sale.action_report_saleorder")
        self.assertEqual(standard_action.report_name, "sale.report_saleorder")
        self.assertEqual(standard_action.report_file, "sale.report_saleorder")

    def test_standard_sale_document_has_document_studio_bridge(self):
        bridge_view = self.env.ref(
            "ranvals_document_studio.report_saleorder_document_rds_bridge"
        )
        self.assertEqual(
            bridge_view.inherit_id,
            self.env.ref("sale.report_saleorder_document"),
        )

    def test_standard_report_selects_default_template_and_supports_fallback(self):
        order = self.env["sale.order"].create(
            {"partner_id": self.partner.id}
        )
        self.assertTrue(order._rds_standard_report_template())
        self.assertFalse(
            order.with_context(
                rds_disable_standard_sale_report=True
            )._rds_standard_report_template()
        )

    def test_native_proforma_uses_proforma_title(self):
        order = self.env["sale.order"].create(
            {"partner_id": self.partner.id}
        )
        template = order._rds_standard_report_template()
        document_context = order.with_context(proforma=True)._rds_document_context(
            template, "en_US"
        )
        self.assertEqual(document_context["title"], "PRO-FORMA INVOICE")

    def test_print_menu_selector_is_bound_as_report(self):
        selector_action = self.env.ref(
            "ranvals_document_studio.server_action_sale_rds_print"
        )
        self.assertEqual(selector_action.model_id.model, "sale.order")
        self.assertEqual(selector_action.binding_model_id.model, "sale.order")
        self.assertEqual(selector_action.binding_type, "report")
        self.assertEqual(selector_action.binding_view_types, "list,form")

    def test_each_template_routes_to_its_studio_report(self):
        order = self.env["sale.order"].create(
            {"partner_id": self.partner.id}
        )
        for layout_style, template_xmlid in self.TEMPLATE_XMLIDS.items():
            template = self.env.ref("ranvals_document_studio.%s" % template_xmlid)
            self.assertEqual(template.layout_style, layout_style)
            self.assertEqual(
                order._rds_report_action_xmlid(template),
                "ranvals_document_studio.%s"
                % self.REPORT_ACTION_XMLIDS[layout_style],
            )

    def test_document_context_keeps_customer_invoice_and_delivery_addresses(self):
        invoice_partner = self.env["res.partner"].create(
            {
                "name": "RDS Billing Contact",
                "street": "Billing Street 19",
                "type": "invoice",
                "parent_id": self.partner.id,
            }
        )
        shipping_partner = self.env["res.partner"].create(
            {
                "name": "RDS Delivery Contact",
                "street": "Delivery Street 19",
                "type": "delivery",
                "parent_id": self.partner.id,
            }
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "partner_invoice_id": invoice_partner.id,
                "partner_shipping_id": shipping_partner.id,
            }
        )
        template = order._rds_standard_report_template()

        context = order._rds_document_context(template, "en_US")

        self.assertEqual(context["partner_info"]["name"], self.partner.name)
        self.assertEqual(
            context["invoice_partner_info"]["name"], invoice_partner.name
        )
        self.assertEqual(
            context["shipping_partner_info"]["name"], shipping_partner.name
        )
        self.assertTrue(
            any(
                "Billing Street 19" in metadata["value"]
                for metadata in context["metadata"]
            )
        )
        self.assertTrue(
            any(
                "Delivery Street 19" in metadata["value"]
                for metadata in context["metadata"]
            )
        )

    def test_document_context_never_silently_truncates_legal_notes(self):
        note_lines = ["Contract clause %02d" % index for index in range(1, 11)]
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "note": "\n".join(note_lines),
        })
        template = order._rds_standard_report_template()

        context = order._rds_document_context(template, "en_US")

        self.assertIn(note_lines[-1], context["notes"])
        self.assertGreaterEqual(len(context["notes"]), len(note_lines))

    def test_report_root_prefers_requested_then_partner_language(self):
        report_view = self.env.ref(
            "ranvals_document_studio.report_sale_document"
        )
        self.assertIn(
            "rds_requested_lang') or doc.partner_id.lang",
            report_view.arch_db,
        )

    def test_report_provider_rejects_malformed_template_and_language_values(self):
        provider = self.env[
            "report.ranvals_document_studio.report_sale_document"
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
