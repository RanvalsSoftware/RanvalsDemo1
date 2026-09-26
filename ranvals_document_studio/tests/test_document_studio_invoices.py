import json

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestRdsDocumentStudioInvoices(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "RDS Test Invoice Customer",
                "lang": "en_US",
            }
        )
        cls.sale_journal = cls.env["account.journal"].search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", cls.env.company.id),
            ],
            limit=1,
        )
        if not cls.sale_journal:
            cls.sale_journal = cls.env["account.journal"].create(
                {
                    "name": "RDS Test Sales Journal",
                    "code": "RDST",
                    "type": "sale",
                    "company_id": cls.env.company.id,
                }
            )

    def _draft_customer_invoice(self):
        return self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "journal_id": self.sale_journal.id,
                "invoice_date": fields.Date.context_today(self.env.user),
            }
        )

    def test_document_studio_invoices_menu_opens_only_supported_move_types(self):
        action = self.env.ref("ranvals_document_studio.action_rds_invoice")
        menu = self.env.ref("ranvals_document_studio.menu_rds_invoices")
        root_menu = self.env.ref("ranvals_document_studio.menu_rds_root")

        self.assertEqual(action.res_model, "account.move")
        self.assertEqual(action.view_mode, "list,form")
        self.assertEqual(
            [item.view_mode for item in action.view_ids.sorted("sequence")],
            ["list", "form"],
        )
        self.assertEqual(
            action.view_ids.sorted("sequence").mapped("view_id"),
            self.env.ref("ranvals_document_studio.view_account_move_list_rds")
            | self.env.ref(
                "ranvals_document_studio.view_account_move_form_rds_primary"
            ),
        )
        self.assertEqual(menu.parent_id, root_menu)
        self.assertEqual(menu.action, action)

        domain = safe_eval(action.domain or "[]")
        move_type_terms = [
            term for term in domain if term[0] == "move_type" and term[1] == "in"
        ]
        self.assertEqual(len(move_type_terms), 1)
        self.assertEqual(
            set(move_type_terms[0][2]),
            {"out_invoice", "out_refund", "in_invoice", "in_refund"},
        )
        self.assertEqual(safe_eval(action.context)["default_move_type"], "out_invoice")

        dedicated_form = self.env.ref(
            "ranvals_document_studio.view_account_move_form_rds_primary"
        )
        self.assertEqual(dedicated_form.mode, "primary")
        self.assertEqual(dedicated_form.inherit_id, self.env.ref("account.view_move_form"))
        dedicated_arch = etree.fromstring(dedicated_form.arch_db.encode())
        form_patch = (
            dedicated_arch
            if dedicated_arch.tag == "xpath"
            else dedicated_arch.xpath(".//xpath[@expr='//form']")[0]
        )
        self.assertEqual(form_patch.get("expr"), "//form")
        js_class = form_patch.xpath("./attribute[@name='js_class']")[0]
        self.assertEqual(js_class.text, "rds_account_move_sidebar_form")

    def test_invoice_list_header_exposes_document_studio_export_button(self):
        view = self.env.ref("ranvals_document_studio.view_account_move_list_rds")
        arch = etree.fromstring(view.arch_db.encode())
        buttons = arch.xpath(".//button[@name='action_open_rds_export']")

        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get("type"), "object")
        self.assertEqual(arch.get("js_class"), "rds_sidebar_list")
        self.assertEqual(arch.get("create"), "true")

    def test_bound_server_action_opens_export_wizard_for_selected_invoices(self):
        invoice = self._draft_customer_invoice()
        server_action = self.env.ref(
            "ranvals_document_studio.server_action_account_rds_export"
        )

        self.assertEqual(server_action.model_id.model, "account.move")
        self.assertEqual(server_action.binding_model_id.model, "account.move")
        self.assertEqual(server_action.binding_type, "action")
        self.assertIn(
            self.env.ref("account.group_account_invoice"), server_action.group_ids
        )
        self.assertEqual(
            set(server_action.binding_view_types.split(",")), {"list", "form"}
        )
        self.assertEqual(server_action.state, "code")
        self.assertIn("records.action_open_rds_export()", server_action.code)

        result = server_action.with_context(
            active_model="account.move", active_ids=invoice.ids
        ).run()

        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["res_model"], "rds.export.wizard")
        self.assertEqual(result["target"], "new")
        self.assertEqual(result["context"]["active_model"], "account.move")
        self.assertEqual(result["context"]["active_ids"], invoice.ids)
        self.assertEqual(
            json.loads(result["context"]["default_res_ids_json"]), invoice.ids
        )

    def test_non_invoice_selection_is_rejected_instead_of_silently_filtered(self):
        journal_entry = self.env["account.move"].new({"move_type": "entry"})

        with self.assertRaisesRegex(UserError, "yalnız fatura"):
            journal_entry.action_open_rds_export()

    def test_invoice_context_uses_delivery_address_and_explicit_draft_title(self):
        shipping_partner = self.env["res.partner"].create(
            {
                "name": "RDS Invoice Delivery",
                "street": "Delivery Street 19",
                "type": "delivery",
                "parent_id": self.partner.id,
            }
        )
        invoice = self._draft_customer_invoice()
        invoice.partner_shipping_id = shipping_partner
        template = self.env["rds.template"].get_default_for(
            "account.move", company=invoice.company_id
        )

        context = invoice._rds_document_context(template, "en_US")

        self.assertIn("DRAFT", context["title"])
        self.assertEqual(
            context["shipping_partner_info"]["name"], shipping_partner.name
        )
        self.assertTrue(
            any(
                "Delivery Street 19" in metadata["value"]
                for metadata in context["metadata"]
            )
        )

    def test_report_root_prefers_requested_then_partner_language(self):
        report_view = self.env.ref(
            "ranvals_document_studio.report_invoice_document"
        )
        self.assertIn(
            "rds_requested_lang') or doc.partner_id.lang",
            report_view.arch_db,
        )

    def test_report_provider_rejects_malformed_template_and_language_values(self):
        provider = self.env[
            "report.ranvals_document_studio.report_invoice_document"
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
