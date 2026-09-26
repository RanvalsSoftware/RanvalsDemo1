"""Registry/render tests: run on a licensed Odoo 19 + Studio staging instance.

These are NOT executed by the local syntax/helper checks.
"""
from types import MappingProxyType
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStudioReportDesign(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = cls.env.ref("sale.action_report_saleorder")
        cls.proforma = cls.env.ref("sale.action_report_pro_forma_invoice")
        cls.beauty = cls.env.ref("ranvals_document_studio.template_beauty_premium")
        cls.technology = cls.env.ref("ranvals_document_studio.template_technology_blue")
        cls.partner = cls.env["res.partner"].create({"name": "RDS Selector Test", "lang": "en_US"})
        cls.order = cls.env["sale.order"].create({"partner_id": cls.partner.id})
        sale_journal = cls.env["account.journal"].search([
            ("type", "=", "sale"),
            ("company_id", "=", cls.env.company.id),
        ], limit=1)
        if not sale_journal:
            sale_journal = cls.env["account.journal"].create({
                "name": "RDS Studio Test Sales Journal",
                "code": "RDSD",
                "type": "sale",
                "company_id": cls.env.company.id,
            })
        cls.invoice = cls.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": cls.partner.id,
            "journal_id": sale_journal.id,
        })
        cls.purchase = cls.env["purchase.order"].create({"partner_id": cls.partner.id})
        cls.invoice_report = cls.env["ir.actions.report"].search([
            ("model", "=", "account.move"),
            ("report_type", "in", ("qweb-pdf", "qweb-html")),
        ], limit=1)
        cls.purchase_report = cls.env["ir.actions.report"].search([
            ("model", "=", "purchase.order"),
            ("report_type", "in", ("qweb-pdf", "qweb-html")),
        ], limit=1)
        cls.env["rds.report.design"].search([
            ("report_id", "in", (cls.report.id, cls.proforma.id)),
            ("company_id", "=", cls.order.company_id.id),
        ]).unlink()

    def test_report_identity_and_source_unchanged(self):
        original = (self.report.report_name, self.report.report_file)
        view = self.env["ir.ui.view"]._get_template_view(self.report.report_name)
        arch = view.arch_db
        self.report.rds_apply_design(self.order.id, self.beauty.id)
        self.assertEqual((self.report.report_name, self.report.report_file), original)
        self.assertEqual(view.arch_db, arch)

    def test_report_choices_are_independent_and_clearable(self):
        self.report.rds_apply_design(self.order.id, self.beauty.id)
        self.proforma.rds_apply_design(self.order.id, self.technology.id)
        self.assertEqual(self.report.rds_get_design_options(self.order.id)["template_id"], self.beauty.id)
        self.assertEqual(self.proforma.rds_get_design_options(self.order.id)["template_id"], self.technology.id)

    def test_invoice_and_purchase_reports_are_supported(self):
        self.assertTrue(self.invoice_report, "An account.move QWeb report is required")
        self.assertTrue(self.purchase_report, "A purchase.order QWeb report is required")

        # Establish the independent pro-forma choice inside this test.  Every
        # TransactionCase method is isolated and must not rely on another
        # method's binding.
        self.proforma.rds_apply_design(self.order.id, self.technology.id)

        invoice_options = self.invoice_report.rds_get_design_options(self.invoice.id)
        purchase_options = self.purchase_report.rds_get_design_options(self.purchase.id)

        self.assertTrue(invoice_options["supported"])
        self.assertTrue(purchase_options["supported"])
        self.assertEqual(invoice_options["record_id"], self.invoice.id)
        self.assertEqual(purchase_options["record_id"], self.purchase.id)

        self.invoice_report.rds_apply_design(self.invoice.id, self.beauty.id)
        self.purchase_report.rds_apply_design(self.purchase.id, self.technology.id)
        self.assertEqual(
            self.invoice_report.rds_get_design_options(self.invoice.id)["template_id"],
            self.beauty.id,
        )
        self.assertEqual(
            self.purchase_report.rds_get_design_options(self.purchase.id)["template_id"],
            self.technology.id,
        )
        self.report.rds_apply_design(self.order.id, False)
        self.assertFalse(self.report.rds_get_design_options(self.order.id)["template_id"])
        self.assertEqual(self.proforma.rds_get_design_options(self.order.id)["template_id"], self.technology.id)

    def test_same_choice_does_not_duplicate_binding(self):
        for _index in range(2):
            self.report.rds_apply_design(self.order.id, self.beauty.id)
        self.assertEqual(self.env["rds.report.design"].search_count([
            ("report_id", "=", self.report.id), ("company_id", "=", self.order.company_id.id),
        ]), 1)

    def test_archived_binding_can_still_be_cleared(self):
        template = self.beauty.copy()
        self.report.rds_apply_design(self.order.id, template.id)
        template.active = False

        options = self.report.rds_get_design_options(self.order.id)
        self.assertTrue(options["has_saved_configuration"])
        self.assertFalse(options["template_id"])

        self.report.rds_apply_design(self.order.id, False)
        self.assertFalse(self.report.rds_get_design_options(self.order.id)["has_saved_configuration"])

    def test_all_six_designs_render_real_html(self):
        for suffix in ("beauty_premium", "construction_navy", "technology_blue", "industrial_red", "eco_green", "furniture_terracotta"):
            template = self.env.ref("ranvals_document_studio.template_%s" % suffix)
            self.report.rds_apply_design(self.order.id, template.id)
            content, kind = self.env["ir.actions.report"]._render_qweb_html(self.report.id, self.order.ids)
            self.assertEqual(kind, "html")
            self.assertIn(self.order.name.encode(), content)
            self.assertIn(template.primary_color.lower().encode(), content.lower())

    def test_proforma_title_survives_design_selection(self):
        self.proforma.rds_apply_design(self.order.id, self.beauty.id)
        content, _kind = self.env["ir.actions.report"].with_context(lang="en_US")._render_qweb_html(
            self.proforma.id, self.order.ids, {"lang": "en_US"}
        )
        self.assertIn(b"PRO-FORMA", content.upper())

    def test_explicit_wizard_choice_wins(self):
        self.report.rds_apply_design(self.order.id, self.beauty.id)
        _report, plan = self.env["ir.actions.report"]._rds_render_plan(
            self.report.id, self.order.ids, {"rds_template_id": self.technology.id}
        )
        self.assertEqual(plan[0][1].id, self.technology.id)

    def test_invalid_archived_and_foreign_company_templates(self):
        archived = self.beauty.copy({"active": False})
        with self.assertRaises(ValidationError):
            self.report.rds_apply_design(self.order.id, archived.id)
        other = self.env["res.company"].create({"name": "Other RDS Company"})
        foreign = self.beauty.copy({"company_id": other.id})
        with self.assertRaises(ValidationError):
            self.report.rds_apply_design(self.order.id, foreign.id)

    def test_portal_cannot_write_selection(self):
        with self.assertRaises(AccessError):
            self.report.with_user(self.env.ref("base.public_user")).rds_apply_design(self.order.id, self.beauty.id)

    def test_direct_unlink_respects_report_groups(self):
        restricted_report = self.report.copy({
            "name": "Restricted RDS report",
            "group_ids": [Command.set([self.env.ref("base.group_system").id])],
        })
        binding = self.env["rds.report.design"].sudo().create({
            "report_id": restricted_report.id,
            "company_id": self.order.company_id.id,
            "template_id": self.beauty.id,
        })
        manager = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "RDS restricted report manager",
            "login": "rds-restricted-report-manager",
            "company_id": self.order.company_id.id,
            "company_ids": [Command.set(self.order.company_id.ids)],
            "group_ids": [Command.set([
                self.env.ref("ranvals_document_studio.group_rds_manager").id,
            ])],
        })

        with self.assertRaises(AccessError):
            binding.with_user(manager).unlink()

    def test_bad_format_and_bad_ids_rejected(self):
        with self.assertRaises(ValidationError):
            self.report.rds_export_design(self.order.id, "exe")
        with self.assertRaises(ValidationError):
            self.report.rds_apply_design(self.order.id, True)
        self.assertEqual(self.report._rds_positive_id(2_147_483_647), 2_147_483_647)
        with self.assertRaises(ValidationError):
            self.report._rds_positive_id(2_147_483_648)

    def test_studio_selector_accepts_editable_word(self):
        expected = {"type": "ir.actions.client", "tag": "test.word.download"}
        export_model = type(self.env["rds.export.wizard"])
        with patch.object(
            export_model,
            "action_export",
            autospec=True,
            return_value=expected,
        ):
            result = self.report.rds_export_design(
                self.order.id,
                "docx_editable",
            )

        self.assertEqual(result, expected)
        wizard = self.env["rds.export.wizard"].search(
            [("res_model", "=", "sale.order")],
            order="id desc",
            limit=1,
        )
        self.assertEqual(wizard.output_format, "docx_editable")

    def test_report_data_requires_a_mapping(self):
        immutable = MappingProxyType({"lang": "en_US"})
        self.assertEqual(self.report._rds_report_data(immutable), {"lang": "en_US"})
        self.assertEqual(self.report._rds_report_data(None), {})
        self.assertEqual(self.report._rds_report_data(False), {})

        for invalid in ([], ["lang", "en_US"], "lang=en_US", 1, True):
            with self.subTest(invalid=invalid), self.assertRaises(UserError):
                self.report._rds_render_plan(self.report.id, self.order.ids, invalid)

    def test_malformed_report_data_is_rejected_before_html_or_pdf_render(self):
        for renderer in ("_render_qweb_html", "_render_qweb_pdf"):
            with self.subTest(renderer=renderer), self.assertRaises(UserError):
                getattr(self.env["ir.actions.report"], renderer)(
                    self.report.id,
                    self.order.ids,
                    data="not-a-mapping",
                )

    def test_saved_language_keeps_zip_export_available(self):
        self.env["rds.report.language"].search([
            ("report_id", "=", self.report.id),
            ("company_id", "=", self.order.company_id.id),
        ]).unlink()
        self.env["rds.report.language"].create({
            "report_id": self.report.id,
            "company_id": self.order.company_id.id,
            "language_code": "en_US",
        })
        expected = {"type": "ir.actions.client", "tag": "test.download"}
        export_model = type(self.env["rds.export.wizard"])
        with patch.object(
            export_model,
            "action_export",
            autospec=True,
            return_value=expected,
        ) as action_export:
            result = self.report.rds_export_design(self.order.id, "zip")

        self.assertEqual(result, expected)
        action_export.assert_called_once()
        wizard = self.env["rds.export.wizard"].search(
            [("res_model", "=", "sale.order")], order="id desc", limit=1
        )
        self.assertEqual(wizard.output_format, "zip")
        self.assertEqual(wizard.language_id.code, "en_US")

    def test_language_setting_is_independent_and_clearable(self):
        self.env["rds.report.language"].search([
            ("report_id", "=", self.report.id),
            ("company_id", "=", self.order.company_id.id),
        ]).unlink()
        self.report.rds_apply_design(self.order.id, self.beauty.id, "en_US")
        options = self.report.rds_get_design_options(self.order.id)
        self.assertEqual(options["template_id"], self.beauty.id)
        self.assertEqual(options["language_code"], "en_US")

        # Omitting the language argument preserves it while the design is
        # restored to the native/default report.
        self.report.rds_apply_design(self.order.id, False)
        options = self.report.rds_get_design_options(self.order.id)
        self.assertFalse(options["template_id"])
        self.assertEqual(options["language_code"], "en_US")
        self.assertTrue(options["has_saved_configuration"])

        # Explicit false restores automatic customer language and deletes the
        # now-empty configuration.
        self.report.rds_apply_design(self.order.id, False, False)
        options = self.report.rds_get_design_options(self.order.id)
        self.assertFalse(options["language_code"])
        self.assertFalse(options["has_saved_configuration"])

    def test_existing_attachment_can_be_bypassed_without_deletion(self):
        attachment = self.env["ir.attachment"].create({
            "name": "selector-old.pdf", "raw": b"%PDF-1.4 test",
            "res_model": "sale.order", "res_id": self.order.id,
        })
        self.assertFalse(self.report.with_context(rds_sidebar_ignore_attachment=True).retrieve_attachment(self.order))
        self.assertTrue(attachment.exists())

    def test_rendered_html_merge_rejects_missing_documents(self):
        with self.assertRaises(UserError):
            self.env["ir.actions.report"]._rds_merge_rendered_html([])

    def test_rendered_html_merge_keeps_each_document(self):
        merged = self.env["ir.actions.report"]._rds_merge_rendered_html([
            b"<html><body><main><article id='first'>A</article></main></body></html>",
            b"<html><body><main><article id='second'>B</article></main></body></html>",
        ])
        self.assertIn(b'id="first"', merged)
        self.assertIn(b'id="second"', merged)

    def test_target_report_must_match_document_model(self):
        with patch.object(
            type(self.order),
            "_rds_report_action_xmlid",
            return_value="base.action_res_company_form",
        ), self.assertRaises(UserError):
            self.env["ir.actions.report"]._rds_target_report(
                self.report,
                self.order,
                self.beauty,
            )
