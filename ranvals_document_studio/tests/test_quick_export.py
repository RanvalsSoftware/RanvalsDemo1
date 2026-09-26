import json
from unittest.mock import patch

from lxml import etree
from odoo import fields
from odoo.addons.ranvals_document_studio.models.rds_quick_export import (
    MAX_DATABASE_ID,
)
from odoo.addons.ranvals_document_studio.models.rds_template import RdsTemplate
from odoo.addons.ranvals_document_studio.wizard.rds_export_wizard import (
    RdsExportWizard,
)
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestRdsQuickExport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {"name": "DocuCraft Quick Export", "lang": "en_US"}
        )
        cls.orders = cls.env["sale.order"].create(
            [
                {"partner_id": cls.partner.id},
                {"partner_id": cls.partner.id},
            ]
        )
        sale_journal = cls.env["account.journal"].search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", cls.env.company.id),
            ],
            limit=1,
        )
        if not sale_journal:
            sale_journal = cls.env["account.journal"].create(
                {
                    "name": "DocuCraft Quick Export Journal",
                    "code": "RDQX",
                    "type": "sale",
                    "company_id": cls.env.company.id,
                }
            )
        cls.invoice = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "journal_id": sale_journal.id,
                "invoice_date": fields.Date.context_today(cls.env.user),
            }
        )
        cls.purchase = cls.env["purchase.order"].create(
            {"partner_id": cls.partner.id}
        )

    def _latest_wizard(self, model_name):
        return self.env["rds.export.wizard"].search(
            [("res_model", "=", model_name)], order="id desc", limit=1
        )

    def test_one_click_pdf_uses_effective_template_and_partner_language(self):
        order = self.orders[0]
        expected = self.env["rds.template"].get_default_for(
            "sale.order", company=order.company_id, record=order
        )
        with patch.object(
            RdsExportWizard,
            "action_export",
            return_value={"type": "ir.actions.client", "tag": "test.download"},
        ):
            action = order.action_rds_download_pdf()

        wizard = self._latest_wizard("sale.order")
        self.assertEqual(action["tag"], "test.download")
        self.assertEqual(wizard.template_id, expected)
        self.assertEqual(wizard.output_format, "pdf")
        self.assertEqual(wizard.language_id.code, "en_US")
        self.assertEqual(json.loads(wizard.res_ids_json), order.ids)

    def test_one_click_editable_word_is_available_on_all_business_models(self):
        for records in (self.orders[0], self.invoice, self.purchase):
            with (
                self.subTest(model=records._name),
                patch.object(
                    RdsExportWizard,
                    "action_export",
                    return_value={
                        "type": "ir.actions.client",
                        "tag": "test.download",
                    },
                ),
            ):
                records.action_rds_download_editable_word()
                wizard = self._latest_wizard(records._name)
                self.assertEqual(wizard.output_format, "docx_editable")
                self.assertEqual(json.loads(wizard.res_ids_json), records.ids)

    def test_multi_select_creates_one_safe_batch(self):
        with patch.object(
            RdsExportWizard,
            "action_export",
            return_value={"type": "ir.actions.client", "tag": "test.download"},
        ):
            self.orders.action_rds_download_editable_word()

        wizard = self._latest_wizard("sale.order")
        self.assertEqual(json.loads(wizard.res_ids_json), self.orders.ids)
        self.assertEqual(wizard.output_format, "docx_editable")

    def test_missing_template_opens_normal_selector_with_requested_format(self):
        empty = self.env["rds.template"].browse()
        with patch.object(RdsTemplate, "get_default_for", return_value=empty):
            action = self.orders[0].action_rds_download_editable_word()

        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "rds.export.wizard")
        self.assertEqual(action["target"], "new")
        self.assertEqual(
            action["context"]["default_output_format"], "docx_editable"
        )
        self.assertEqual(action["context"]["active_ids"], self.orders[0].ids)

    def test_invalid_format_and_non_invoice_move_are_rejected(self):
        with self.assertRaises(ValidationError):
            self.orders[0]._rds_quick_export("exe")
        with self.assertRaises(UserError):
            self.env["sale.order"].browse(
                MAX_DATABASE_ID + 1
            ).action_rds_download_pdf()
        journal_entry = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": self.invoice.journal_id.id,
                "date": fields.Date.context_today(self.env.user),
            }
        )
        with self.assertRaises(UserError):
            journal_entry.action_rds_download_pdf()

    def test_rpc_method_enforces_business_group(self):
        internal_user = new_test_user(
            self.env,
            login="rds_quick_export_internal",
            groups="base.group_user",
        )
        with self.assertRaises(AccessError):
            self.orders[0].with_user(internal_user).action_rds_download_pdf()

    def test_quick_server_actions_remain_callable_but_are_not_menu_entries(self):
        cases = (
            (
                "sale",
                "sales_team.group_sale_salesman",
                "sale.order",
            ),
            (
                "account",
                "account.group_account_invoice",
                "account.move",
            ),
            (
                "purchase",
                "purchase.group_purchase_user",
                "purchase.order",
            ),
        )
        for prefix, group_xmlid, model_name in cases:
            for suffix in ("pdf", "word"):
                with self.subTest(prefix=prefix, suffix=suffix):
                    action = self.env.ref(
                        f"ranvals_document_studio."
                        f"server_action_{prefix}_rds_quick_{suffix}"
                    )
                    self.assertEqual(action.binding_type, "report")
                    self.assertFalse(action.binding_model_id)
                    self.assertEqual(action.model_id.model, model_name)
                    self.assertEqual(
                        set(action.binding_view_types.split(",")), {"list", "form"}
                    )
                    self.assertIn(self.env.ref(group_xmlid), action.group_ids)

    def test_forms_expose_one_docucraft_print_button_without_replacing_native_print(self):
        for view_xmlid in (
            "ranvals_document_studio.view_sale_order_form_rds",
            "ranvals_document_studio.view_account_move_form_rds",
            "ranvals_document_studio.view_purchase_order_form_rds",
        ):
            with self.subTest(view=view_xmlid):
                arch = etree.fromstring(self.env.ref(view_xmlid).arch_db.encode())
                self.assertEqual(
                    len(arch.xpath(".//button[@name='action_rds_download_pdf']")),
                    0,
                )
                self.assertEqual(
                    len(
                        arch.xpath(
                            ".//button[@name='action_rds_download_editable_word']"
                        )
                    ),
                    0,
                )
                self.assertEqual(
                    len(arch.xpath(".//button[@name='action_open_rds_export']")),
                    1,
                )
                button = arch.xpath(
                    ".//button[@name='action_open_rds_export']"
                )[0]
                # View architecture is translated when a language catalogue
                # is installed; keep this assertion locale-neutral.
                self.assertIn("DocuCraft", button.get("string"))
                self.assertEqual(button.get("icon"), "fa-print")

        print_actions = (
            ("server_action_sale_rds_print", "sale.order"),
            ("server_action_account_rds_export", "account.move"),
            ("server_action_purchase_rds_export", "purchase.order"),
        )
        for xmlid, model_name in print_actions:
            with self.subTest(action=xmlid):
                action = self.env.ref(f"ranvals_document_studio.{xmlid}")
                self.assertEqual(action.name, "DocuCraft Yazdır")
                self.assertEqual(action.binding_type, "report")
                self.assertEqual(action.binding_model_id.model, model_name)

        native_sale_report = self.env.ref("sale.action_report_saleorder")
        self.assertEqual(native_sale_report.report_type, "qweb-pdf")
        self.assertEqual(native_sale_report.binding_model_id.model, "sale.order")
