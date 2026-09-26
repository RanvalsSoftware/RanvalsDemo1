import json
from unittest.mock import patch

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
        "noir_executive": "template_noir_executive",
        "royal_ledger": "template_royal_ledger",
        "swiss_grid": "template_swiss_grid",
        "arctic_minimal": "template_arctic_minimal",
        "indigo_flow": "template_indigo_flow",
        "emerald_ledger": "template_emerald_ledger",
        "sandstone_classic": "template_sandstone_classic",
        "graphite_copper": "template_graphite_copper",
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

    def test_legacy_design_reports_are_kept_but_unbound(self):
        for action_xmlid in self.REPORT_ACTION_XMLIDS.values():
            action = self.env.ref("ranvals_document_studio.%s" % action_xmlid)
            self.assertFalse(action.binding_model_id)
            self.assertEqual(action.binding_type, "report")
            self.assertEqual(action.binding_view_types, "list,form")

    def test_legacy_design_reports_do_not_clutter_runtime_bindings(self):
        legacy_action_ids = {
            self.env.ref("ranvals_document_studio.%s" % xmlid).id
            for xmlid in self.REPORT_ACTION_XMLIDS.values()
        }
        bindings = self.env["ir.actions.actions"].get_bindings("sale.order")
        report_binding_ids = {binding["id"] for binding in bindings.get("report", [])}
        self.assertFalse(
            legacy_action_ids & report_binding_ids,
            "Legacy per-theme reports must not clutter the sale.order Print menu.",
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

    def test_each_template_routes_to_a_compatible_studio_report(self):
        order = self.env["sale.order"].create(
            {"partner_id": self.partner.id}
        )
        for layout_style, template_xmlid in self.TEMPLATE_XMLIDS.items():
            template = self.env.ref("ranvals_document_studio.%s" % template_xmlid)
            self.assertEqual(template.layout_style, layout_style)
            expected = self.REPORT_ACTION_XMLIDS.get(layout_style)
            expected_xmlid = (
                "ranvals_document_studio.%s" % expected
                if expected
                else "ranvals_document_studio.action_report_rds_sale_document"
            )
            self.assertEqual(order._rds_report_action_xmlid(template), expected_xmlid)

    def test_seeded_template_names_are_sector_neutral(self):
        expected_names = {
            "beauty": "Signature Burgundy",
            "construction": "Executive Navy",
            "technology": "Horizon Blue",
            "industrial": "Atlas Steel",
            "eco": "Sage Reserve",
            "furniture": "Copper Atelier",
            "noir_executive": "Noir Executive",
            "royal_ledger": "Royal Ledger",
            "swiss_grid": "Swiss Grid",
            "arctic_minimal": "Arctic Minimal",
            "indigo_flow": "Indigo Flow",
            "emerald_ledger": "Emerald Ledger",
            "sandstone_classic": "Sandstone Classic",
            "graphite_copper": "Graphite Copper",
        }
        for layout_style, template_xmlid in self.TEMPLATE_XMLIDS.items():
            template = self.env.ref("ranvals_document_studio.%s" % template_xmlid)
            self.assertEqual(template.name, expected_names[layout_style])

    def test_all_templates_have_safe_dynamic_previews(self):
        for layout_style in self.TEMPLATE_XMLIDS:
            template = self.env.ref(
                "ranvals_document_studio.%s"
                % self.TEMPLATE_XMLIDS[layout_style]
            )
            preview = str(template.preview_html)
            self.assertFalse(template.preview_path)
            self.assertIn(template.name, preview)
            self.assertIn(template.primary_color, preview)
            self.assertNotIn("Önizleme bulunmuyor", preview)

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

    def test_source_report_custom_fields_force_the_selected_docucraft_design(self):
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        template = self.env.ref(
            "ranvals_document_studio.template_graphite_copper"
        )
        source_report = self.env.ref("sale.action_report_saleorder")
        wizard = self.env["rds.export.wizard"].create(
            {
                "res_model": order._name,
                "res_ids_json": json.dumps(order.ids),
                "template_id": template.id,
                "output_format": "pdf",
                "field_selection_mode": "custom",
                # An empty custom list is intentional: it means that both
                # metadata and line fields are disabled for this export.
                "rds_source_report_id": source_report.id,
            }
        )
        captured = {}

        def render_pdf(_service, report_ref, res_ids=None, data=None):
            captured.update(
                {
                    "report_ref": report_ref,
                    "res_ids": res_ids,
                    "data": data,
                }
            )
            return b"%PDF-docucraft-custom-fields", "pdf"

        report_model = type(self.env["ir.actions.report"])
        with patch.object(
            report_model,
            "_render_qweb_pdf",
            autospec=True,
            side_effect=render_pdf,
        ):
            content = wizard._render_pdf(order, "en_US")

        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(captured["report_ref"], source_report.id)
        self.assertEqual(captured["res_ids"], order.ids)
        self.assertEqual(captured["data"]["rds_template_id"], template.id)
        self.assertEqual(captured["data"]["model_name"], order._name)
        self.assertEqual(captured["data"]["rds_export_field_specs"], [])

    def test_source_report_inherited_fields_keep_native_report_behaviour(self):
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        template = self.env.ref(
            "ranvals_document_studio.template_graphite_copper"
        )
        source_report = self.env.ref("sale.action_report_saleorder")
        wizard = self.env["rds.export.wizard"].create(
            {
                "res_model": order._name,
                "res_ids_json": json.dumps(order.ids),
                "template_id": template.id,
                "output_format": "pdf",
                "field_selection_mode": "inherit",
                "rds_source_report_id": source_report.id,
            }
        )
        captured = {}

        def render_pdf(_service, report_ref, res_ids=None, data=None):
            captured.update({"report_ref": report_ref, "data": data})
            return b"%PDF-native-source-report", "pdf"

        report_model = type(self.env["ir.actions.report"])
        with patch.object(
            report_model,
            "_render_qweb_pdf",
            autospec=True,
            side_effect=render_pdf,
        ):
            content = wizard._render_pdf(order, "en_US")

        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(captured["report_ref"], source_report.id)
        self.assertEqual(captured["data"], {"lang": "en_US"})
