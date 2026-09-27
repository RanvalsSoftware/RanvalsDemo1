import base64

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.ranvals_document_studio.controllers.export_log import (
    _safe_download_name,
    _safe_mimetype,
    _unique_archive_name,
)


@tagged("post_install", "-at_install")
class TestRdsDashboards(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.env.ref(
            "ranvals_document_studio.template_technology_blue"
        )
        cls.history_user = new_test_user(
            cls.env,
            login="rds_history_user",
            groups="base.group_user",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="rds_other_history_user",
            groups="base.group_user",
        )
        cls.manager_user = new_test_user(
            cls.env,
            login="rds_history_manager",
            groups="base.group_user,ranvals_document_studio.group_rds_manager",
        )

    def _create_log(self, user, output_format, file_name, record_name):
        content = ("content-%s" % file_name).encode()
        return self.env["rds.export.log"].create(
            {
                "name": "%s export" % record_name,
                "user_id": user.id,
                "company_id": self.env.company.id,
                "template_id": self.template.id,
                "res_model": "res.partner",
                "res_id": user.partner_id.id,
                "record_name": record_name,
                "output_format": output_format,
                "file_name": file_name,
                "file_size": len(content),
                "file_mimetype": "application/octet-stream",
                "file_data": base64.b64encode(content),
            }
        )

    def test_internal_user_cannot_manufacture_history_file(self):
        with self.assertRaises(AccessError):
            self.env["rds.export.log"].with_user(self.history_user).create(
                {
                    "name": "Untrusted upload",
                    "user_id": self.history_user.id,
                    "company_id": self.env.company.id,
                    "template_id": self.template.id,
                    "res_model": "res.partner",
                    "res_id": self.history_user.partner_id.id,
                    "output_format": "pdf",
                    "file_name": "untrusted.pdf",
                    "file_data": base64.b64encode(b"not a trusted export"),
                }
            )

    def test_manager_cannot_retarget_immutable_history(self):
        export_log = self._create_log(
            self.history_user,
            "pdf",
            "immutable.pdf",
            "Immutable History",
        )

        with self.assertRaises(AccessError):
            export_log.with_user(self.manager_user).write(
                {
                    "res_model": "res.partner",
                    "res_id": self.manager_user.partner_id.id,
                }
            )

    def test_history_dashboard_obeys_user_rule_and_filters(self):
        pdf_log = self._create_log(
            self.history_user, "pdf", "invoice-test.pdf", "Invoice Test"
        )
        docx_log = self._create_log(
            self.history_user, "docx", "quotation.docx", "Quotation"
        )
        self._create_log(
            self.other_user, "png", "private.png", "Other User Record"
        )

        dashboard = self.env["rds.dashboard"].with_user(self.history_user)
        values = dashboard.get_export_dashboard()
        self.assertEqual(
            values["stats"],
            {
                "total": 2,
                "pdf": 1,
                "docx_editable": 0,
                "docx": 1,
                "png": 0,
                "zip": 0,
            },
        )
        self.assertEqual(values["filtered_total"], 2)
        self.assertEqual(
            {item["id"] for item in values["records"]},
            {pdf_log.id, docx_log.id},
        )

        filtered = dashboard.get_export_dashboard("invoice", "pdf")
        self.assertEqual(filtered["filtered_total"], 1)
        self.assertEqual(filtered["records"][0]["id"], pdf_log.id)
        self.assertTrue(filtered["records"][0]["can_download"])
        self.assertEqual(filtered["records"][0]["status_key"], "ready")

    def test_history_disables_download_after_source_access_is_lost(self):
        export_log = self._create_log(
            self.history_user,
            "pdf",
            "restricted-source.pdf",
            "Restricted Source",
        )
        restricted_source = self.env["ir.config_parameter"].search([], limit=1)
        export_log.write(
            {
                "res_model": "ir.config_parameter",
                "res_id": restricted_source.id,
            }
        )

        dashboard = self.env["rds.dashboard"].with_user(self.history_user)
        values = dashboard.get_export_dashboard("restricted-source")
        self.assertEqual(values["records"][0]["status_key"], "denied")
        self.assertEqual(values["records"][0]["status_label"], "Access Denied")
        self.assertFalse(values["records"][0]["can_open_record"])
        self.assertFalse(values["records"][0]["can_download"])
        with self.assertRaises(AccessError):
            export_log.with_user(self.history_user)._get_stored_file()

    def test_stored_file_and_download_action(self):
        export_log = self._create_log(
            self.history_user, "pdf", "secure-download.pdf", "Secure Download"
        ).with_user(self.history_user)

        file_name, content, mimetype = export_log._get_stored_file()
        self.assertEqual(file_name, "secure-download.pdf")
        self.assertEqual(content, b"content-secure-download.pdf")
        self.assertEqual(mimetype, "application/octet-stream")
        action = export_log.action_download()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(
            action["tag"], "ranvals_document_studio.download_export"
        )
        self.assertEqual(
            action["params"]["url"],
            "/ranvals_document_studio/export_logs/%s" % export_log.id,
        )
        with self.assertRaises(AccessError):
            export_log.read(["file_data"])

    def test_legacy_attachment_remains_downloadable(self):
        content = b"legacy-attachment"
        attachment = self.env["ir.attachment"].create(
            {
                "name": "legacy.pdf",
                "datas": base64.b64encode(content),
                "mimetype": "application/pdf",
                "res_model": "res.partner",
                "res_id": self.history_user.partner_id.id,
            }
        )
        export_log = self.env["rds.export.log"].create(
            {
                "name": "Legacy export",
                "user_id": self.history_user.id,
                "company_id": self.env.company.id,
                "template_id": self.template.id,
                "res_model": "res.partner",
                "res_id": self.history_user.partner_id.id,
                "record_name": "Legacy",
                "output_format": "pdf",
                "file_name": "legacy.pdf",
                "file_size": len(content),
                "attachment_id": attachment.id,
            }
        )

        self.assertTrue(export_log.has_stored_file)
        self.assertTrue(export_log.attached_to_record)
        self.assertEqual(export_log._get_stored_file()[1], content)

        attachment.unlink()
        self.assertFalse(export_log.has_stored_file)
        self.assertFalse(export_log.attached_to_record)

    def test_download_helpers_preserve_extension_and_unique_zip_names(self):
        long_name = ("a" * 140) + ".DOCX"
        safe_name = _safe_download_name(long_name)
        self.assertEqual(len(safe_name), 120)
        self.assertTrue(safe_name.endswith(".docx"))
        self.assertEqual(_safe_mimetype("text/html"), "application/octet-stream")
        self.assertEqual(_safe_mimetype("application/pdf"), "application/pdf")

        used_names = set()
        self.assertEqual(_unique_archive_name("a.pdf", used_names), "a.pdf")
        self.assertEqual(_unique_archive_name("a.pdf", used_names), "a_(2).pdf")
        self.assertEqual(
            _unique_archive_name("a_(2).pdf", used_names),
            "a_(2)_(2).pdf",
        )

    def test_connector_dashboard_requires_system_group(self):
        with self.assertRaises(AccessError):
            self.env["rds.dashboard"].with_user(
                self.history_user
            ).get_connector_dashboard()

        values = self.env["rds.dashboard"].get_connector_dashboard()
        expected = {
            "account",
            "sale_management",
            "purchase",
            "web_studio",
        }
        self.assertEqual(values["stats"]["total"], len(expected))
        self.assertEqual(
            {item["technical_name"] for item in values["connectors"]}, expected,
        )

    def test_dashboard_actions_and_paths_are_wired(self):
        connector_action = self.env.ref(
            "ranvals_document_studio.action_rds_connectors_dashboard"
        )
        history_action = self.env.ref(
            "ranvals_document_studio.action_rds_export_dashboard"
        )
        self.assertEqual(
            connector_action.tag,
            "ranvals_document_studio.connectors_dashboard",
        )
        self.assertEqual(connector_action.path, "rds-connectors")
        self.assertEqual(
            history_action.tag,
            "ranvals_document_studio.export_dashboard",
        )
        self.assertEqual(history_action.path, "rds-export-history")

    def test_template_and_history_views_keep_scoped_sidebar(self):
        template_action = self.env.ref(
            "ranvals_document_studio.action_rds_template"
        )
        template_views = template_action.view_ids.sorted("sequence")
        self.assertEqual(
            [item.view_mode for item in template_views], ["list", "form"]
        )
        self.assertEqual(
            template_views.mapped("view_id"),
            self.env.ref("ranvals_document_studio.view_rds_template_list")
            | self.env.ref("ranvals_document_studio.view_rds_template_form"),
        )

        history_action = self.env.ref(
            "ranvals_document_studio.action_rds_export_log"
        )
        self.assertEqual(
            [item.view_mode for item in history_action.view_ids.sorted("sequence")],
            ["list", "form"],
        )

        for view_xmlid, expected_class in (
            ("ranvals_document_studio.view_rds_template_list", "rds_sidebar_list"),
            ("ranvals_document_studio.view_rds_template_form", "rds_sidebar_form"),
            ("ranvals_document_studio.view_rds_export_log_list", "rds_sidebar_list"),
            ("ranvals_document_studio.view_rds_export_log_form", "rds_sidebar_form"),
        ):
            arch = etree.fromstring(self.env.ref(view_xmlid).arch_db.encode())
            self.assertEqual(arch.get("js_class"), expected_class)
