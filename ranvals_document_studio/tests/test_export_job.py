import base64
import json
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.ranvals_document_studio.models.rds_export_job import (
    MAX_JOB_ATTEMPTS,
    RdsExportJob,
)
from odoo.addons.ranvals_document_studio.tools.archive import build_zip_archive
from odoo.addons.ranvals_document_studio.wizard.rds_export_wizard import (
    RdsExportWizard,
)


@tagged("post_install", "-at_install")
class TestRdsExportJob(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(
            cls.env,
            login="rds_background_owner",
            groups="base.group_user",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="rds_background_other",
            groups="base.group_user",
        )
        cls.template = cls.env.ref(
            "ranvals_document_studio.template_technology_blue"
        )
        cls.studio_user = new_test_user(
            cls.env,
            login="rds_background_studio_user",
            groups=(
                "base.group_system,"
                "sales_team.group_sale_salesman,"
                "sale.group_proforma_sales,"
                "ranvals_document_studio.group_rds_manager"
            ),
        )
        cls.studio_partner = cls.env["res.partner"].create(
            {"name": "RDS background Studio partner", "lang": "en_US"}
        )
        cls.studio_order = cls.env["sale.order"].create(
            {
                "partner_id": cls.studio_partner.id,
                "user_id": cls.studio_user.id,
            }
        )
        cls.proforma_report = cls.env.ref(
            "sale.action_report_pro_forma_invoice"
        )

    def _wizard(self, *, output_format="pdf", record_ids=None, processing_mode="background"):
        record_ids = record_ids or [self.owner.partner_id.id]
        return self.env["rds.export.wizard"].with_user(self.owner).create(
            {
                "res_model": "res.partner",
                "res_ids_json": json.dumps(record_ids),
                "template_id": self.template.id,
                "output_format": output_format,
                "processing_mode": processing_mode,
            }
        )

    def _studio_wizard(self, report, *, processing_mode="auto"):
        return self.env["rds.export.wizard"].with_user(self.studio_user).create(
            {
                "res_model": "sale.order",
                "res_ids_json": json.dumps(self.studio_order.ids),
                "template_id": self.template.id,
                "output_format": "zip",
                "processing_mode": processing_mode,
                "rds_source_report_id": report.id,
            }
        )

    def test_background_job_renders_as_requester_and_stores_protected_file(self):
        wizard = self._wizard()
        seen = {}

        def build_output(render_wizard):
            seen["user"] = render_wizard.env.user
            seen["mode"] = render_wizard.processing_mode
            record = render_wizard._get_records()
            content = b"%PDF-background"
            item = ("background.pdf", content, "application/pdf")
            return "background.pdf", content, "application/pdf", [(record, [item])]

        job = self.env["rds.export.job"]._enqueue_from_wizard(wizard)
        self.assertRegex(job.name, r"^RDS-JOB-\d{6}$")
        with (
            patch.object(RdsExportWizard, "_build_output", autospec=True, side_effect=build_output),
            patch.object(RdsExportJob, "_notify_requester", autospec=True) as notify,
        ):
            self.assertTrue(job.sudo()._process_one())

        job.invalidate_recordset()
        self.assertEqual(job.state, "done")
        self.assertEqual(job.progress, 100)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.file_name, "background.pdf")
        self.assertEqual(job.file_mimetype, "application/pdf")
        self.assertEqual(job.file_size, len(b"%PDF-background"))
        self.assertEqual(base64.b64decode(job.sudo().file_data), b"%PDF-background")
        self.assertEqual(seen["user"], self.owner)
        # A worker-created wizard must not auto-enqueue itself again.
        self.assertEqual(seen["mode"], "immediate")
        notify.assert_called_once()

    def test_queue_action_and_automatic_thresholds(self):
        wizard = self._wizard(output_format="pdf")
        with patch.object(type(self.env["ir.cron"]), "_trigger", autospec=True):
            action = wizard.action_export()
        job = self.env["rds.export.job"].with_user(self.owner).browse(action["res_id"])
        self.assertEqual(action["res_model"], "rds.export.job")
        self.assertEqual(job.state, "queued")
        self.assertEqual(job.user_id, self.owner)

        immediate = self._wizard(processing_mode="auto")
        self.assertFalse(immediate._should_enqueue_export())
        immediate.output_format = "zip"
        self.assertTrue(immediate._should_enqueue_export())

        partners = self.env["res.partner"].create(
            [{"name": "Queue threshold %s" % index} for index in range(4)]
        )
        bulk = self._wizard(record_ids=partners.ids, processing_mode="auto")
        self.assertTrue(bulk._should_enqueue_export())

    def test_job_is_private_and_direct_user_creation_is_blocked(self):
        job = self.env["rds.export.job"]._enqueue_from_wizard(self._wizard())
        self.assertEqual(
            self.env["rds.export.job"].with_user(self.other_user).search_count(
                [("id", "=", job.id)]
            ),
            0,
        )
        with self.assertRaises(AccessError):
            job.with_user(self.other_user).read(["state"])
        with self.assertRaises(AccessError):
            self.env["rds.export.job"].with_user(self.owner).create(
                {
                    "user_id": self.owner.id,
                    "company_id": self.env.company.id,
                    "template_id": self.template.id,
                    "res_model": "res.partner",
                    "res_ids_json": "[%s]" % self.owner.partner_id.id,
                    "record_count": 1,
                    "output_format": "pdf",
                }
            )

    def test_live_source_acl_failure_is_safe_and_retry_is_bounded(self):
        restricted = self.env["ir.config_parameter"].sudo().create(
            {"key": "rds.background.restricted", "value": "secret"}
        )
        job = self.env["rds.export.job"].sudo().create(
            {
                "user_id": self.owner.id,
                "company_id": self.env.company.id,
                "template_id": self.template.id,
                "res_model": restricted._name,
                "res_ids_json": json.dumps(restricted.ids),
                "record_count": 1,
                "output_format": "pdf",
            }
        )
        with patch.object(RdsExportJob, "_notify_requester", autospec=True):
            self.assertFalse(job._process_one())
        self.assertEqual(job.state, "failed")
        self.assertNotIn("secret", job.error_message or "")
        self.assertFalse(job.file_data)

        job.write({"attempts": MAX_JOB_ATTEMPTS})
        with self.assertRaises(UserError):
            job.with_user(self.owner).action_retry()

    def test_cancel_and_download_recheck_live_source(self):
        source = self.env["res.partner"].create({"name": "Temporary export source"})
        job = self.env["rds.export.job"]._enqueue_from_wizard(
            self._wizard(record_ids=source.ids)
        )
        self.assertTrue(job.action_cancel())
        self.assertEqual(job.state, "cancelled")
        self.assertTrue(job.action_retry())
        self.assertEqual(job.state, "queued")

        job.sudo().write(
            {
                "state": "done",
                "file_name": "ready.pdf",
                "file_mimetype": "application/pdf",
                "file_size": 10,
                "file_data": base64.b64encode(b"%PDF-ready"),
            }
        )
        source.sudo().unlink()
        with self.assertRaises(AccessError):
            job._get_stored_file()

    def test_job_uses_source_company_when_it_is_not_current(self):
        foreign_company = self.env["res.company"].create(
            {"name": "DocuCraft Background Foreign Company"}
        )
        self.owner.write({"company_ids": [Command.link(foreign_company.id)]})
        source = self.env["res.partner"].create(
            {
                "name": "Foreign-company export source",
                "company_id": foreign_company.id,
            }
        )
        wizard = self._wizard(record_ids=source.ids)
        self.assertNotEqual(wizard.env.company, foreign_company)

        job = self.env["rds.export.job"]._enqueue_from_wizard(wizard)
        self.assertEqual(job.company_id, foreign_company)
        seen = {}

        def build_output(render_wizard):
            seen["company"] = render_wizard.env.company
            record = render_wizard._get_records()
            content = b"%PDF-foreign-company"
            item = ("foreign.pdf", content, "application/pdf")
            return "foreign.pdf", content, "application/pdf", [(record, [item])]

        with (
            patch.object(RdsExportWizard, "_build_output", autospec=True, side_effect=build_output),
            patch.object(RdsExportJob, "_notify_requester", autospec=True),
        ):
            self.assertTrue(job.sudo()._process_one())
        self.assertEqual(seen["company"], foreign_company)

    def test_auto_zip_keeps_studio_report_identity_and_proforma_context(self):
        wizard = self._studio_wizard(self.proforma_report)
        with patch.object(type(self.env["ir.cron"]), "_trigger", autospec=True):
            action = wizard.action_export()

        job = self.env["rds.export.job"].with_user(self.studio_user).browse(
            action["res_id"]
        )
        self.assertEqual(job.output_format, "zip")
        self.assertTrue(job.source_report_expected)
        self.assertEqual(job.rds_source_report_id, self.proforma_report)
        seen = {}

        def base_record_context(_wizard, record, _language_code):
            seen["proforma"] = bool(record.env.context.get("proforma"))
            return {"title": "PRO-FORMA INVOICE"}

        def build_output(render_wizard):
            seen["report_id"] = render_wizard.rds_source_report_id.id
            record = render_wizard._get_records()
            render_wizard._record_context(record, "en_US")
            content = build_zip_archive(
                [("proforma.txt", b"studio-proforma")]
            )
            item = ("proforma.zip", content, "application/zip")
            return "proforma.zip", content, "application/zip", [(record, [item])]

        with (
            patch.object(
                RdsExportWizard,
                "_record_context",
                autospec=True,
                side_effect=base_record_context,
            ),
            patch.object(
                RdsExportWizard,
                "_build_output",
                autospec=True,
                side_effect=build_output,
            ),
            patch.object(RdsExportJob, "_notify_requester", autospec=True),
        ):
            self.assertTrue(job.sudo()._process_one())

        self.assertEqual(seen["report_id"], self.proforma_report.id)
        self.assertTrue(seen["proforma"])

    def test_worker_rechecks_source_report_groups_before_rendering(self):
        restricted_report = self.proforma_report.copy(
            {
                "name": "Restricted background Studio pro-forma",
                "group_ids": [
                    Command.set(
                        [
                            self.env.ref(
                                "ranvals_document_studio.group_rds_manager"
                            ).id
                        ]
                    )
                ],
            }
        )
        job = self.env["rds.export.job"]._enqueue_from_wizard(
            self._studio_wizard(restricted_report, processing_mode="background")
        )
        self.studio_user.write(
            {
                "group_ids": [
                    Command.unlink(
                        self.env.ref(
                            "ranvals_document_studio.group_rds_manager"
                        ).id
                    )
                ]
            }
        )

        with (
            patch.object(RdsExportWizard, "_build_output", autospec=True) as build,
            patch.object(RdsExportJob, "_notify_requester", autospec=True),
        ):
            self.assertFalse(job.sudo()._process_one())

        self.assertEqual(job.state, "failed")
        self.assertFalse(job.file_data)
        build.assert_not_called()

    def test_deleted_studio_source_report_fails_closed(self):
        disposable_report = self.proforma_report.copy(
            {"name": "Disposable background Studio pro-forma"}
        )
        job = self.env["rds.export.job"]._enqueue_from_wizard(
            self._studio_wizard(disposable_report, processing_mode="background")
        )
        disposable_report.unlink()
        job.invalidate_recordset(["rds_source_report_id", "source_report_expected"])
        self.assertTrue(job.source_report_expected)
        self.assertFalse(job.rds_source_report_id)

        with (
            patch.object(RdsExportWizard, "_build_output", autospec=True) as build,
            patch.object(RdsExportJob, "_notify_requester", autospec=True),
        ):
            self.assertFalse(job.sudo()._process_one())

        self.assertEqual(job.state, "failed")
        self.assertFalse(job.file_data)
        self.assertIn("source Studio report", job.error_message)
        build.assert_not_called()
