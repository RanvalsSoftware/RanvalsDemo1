import base64
from io import BytesIO
from zipfile import ZipFile

from odoo import http
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestRdsExportLogDownload(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "rds-http-pass"
        cls.owner = new_test_user(
            cls.env,
            login="rds_http_owner",
            password=cls.password,
            groups="base.group_user",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="rds_http_other",
            password=cls.password,
            groups="base.group_user",
        )
        cls.template = cls.env.ref(
            "ranvals_document_studio.template_technology_blue"
        )

    @classmethod
    def _create_log(cls, file_name, payload, mimetype="application/pdf"):
        return cls.env["rds.export.log"].sudo().create(
            {
                "name": "%s export" % file_name,
                "user_id": cls.owner.id,
                "company_id": cls.env.company.id,
                "template_id": cls.template.id,
                "res_model": "res.partner",
                "res_id": cls.owner.partner_id.id,
                "record_name": "HTTP Test",
                "output_format": "pdf",
                "file_name": file_name,
                "file_size": len(payload),
                "file_mimetype": mimetype,
                "file_data": base64.b64encode(payload),
            }
        )

    @classmethod
    def _create_job(cls, file_name, payload, mimetype="application/pdf"):
        return cls.env["rds.export.job"].sudo().create(
            {
                "user_id": cls.owner.id,
                "company_id": cls.env.company.id,
                "template_id": cls.template.id,
                "res_model": "res.partner",
                "res_ids_json": "[%s]" % cls.owner.partner_id.id,
                "record_count": 1,
                "record_names": cls.owner.partner_id.display_name,
                "output_format": "pdf",
                "state": "done",
                "progress": 100,
                "file_name": file_name,
                "file_size": len(payload),
                "file_mimetype": mimetype,
                "file_data": base64.b64encode(payload),
            }
        )

    def test_owner_can_download_with_safe_headers(self):
        payload = b"%PDF-1.4\nRDS\n"
        export_log = self._create_log("owner-test.pdf", payload)
        self.authenticate(self.owner.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_logs/%s" % export_log.id,
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, payload)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertEqual(response.headers["Content-Length"], str(len(payload)))
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        generic_response = self.url_open(
            "/web/content/rds.export.log/%s/file_data/owner-test.pdf?download=true"
            % export_log.id,
            allow_redirects=False,
        )
        self.assertIn(generic_response.status_code, (403, 404))

    def test_owner_can_download_via_core_helper_post(self):
        payload = b"%PDF-1.4\nRDS POST\n"
        export_log = self._create_log("owner-post.pdf", payload)
        self.authenticate(self.owner.login, self.password)

        # ``@web/core/network/download`` posts both a helper token and the
        # current session's CSRF token.  Exercise that exact transport so a
        # future GET-only route regression cannot silently break the client
        # action while the legacy direct-link test still passes.
        response = self.url_open(
            "/ranvals_document_studio/export_logs/%s" % export_log.id,
            data={
                "token": "dummy-because-api-expects-one",
                "csrf_token": http.Request.csrf_token(self),
            },
            method="POST",
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, payload)
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_fresh_wizard_download_uses_same_popup_free_transport(self):
        payload = b"PK\x03\x04DOCX"
        wizard = self.env["rds.export.wizard"].with_user(self.owner).create(
            {
                "res_model": "res.partner",
                "res_ids_json": "[%s]" % self.owner.partner_id.id,
                "template_id": self.template.id,
                "output_format": "docx",
            }
        )
        wizard.sudo().write(
            {
                "file_name": "fresh-document.docx",
                "file_mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "file_data": base64.b64encode(payload),
            }
        )
        self.authenticate(self.owner.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_wizard/%s" % wizard.id,
            data={
                "token": "download-helper-token",
                "csrf_token": http.Request.csrf_token(self),
            },
            method="POST",
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, payload)
        self.assertIn("fresh-document.docx", response.headers["Content-Disposition"])
        self.assertEqual(
            response.headers["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        generic_response = self.url_open(
            "/web/content/rds.export.wizard/%s/file_data/fresh-document.docx"
            % wizard.id,
            allow_redirects=False,
        )
        self.assertIn(generic_response.status_code, (403, 404))

    def test_fresh_wizard_pdf_preview_is_inline_and_access_controlled(self):
        payload = b"%PDF-1.4\nRDS PREVIEW\n"
        wizard = self.env["rds.export.wizard"].with_user(self.owner).create(
            {
                "res_model": "res.partner",
                "res_ids_json": "[%s]" % self.owner.partner_id.id,
                "template_id": self.template.id,
                "output_format": "pdf",
            }
        )
        wizard.sudo().write(
            {
                "file_name": "fresh-preview.pdf",
                "file_mimetype": "application/pdf",
                "file_data": base64.b64encode(payload),
            }
        )
        self.authenticate(self.owner.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_wizard/%s/preview" % wizard.id,
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, payload)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertIn("inline", response.headers["Content-Disposition"])

    def test_core_helper_post_keeps_csrf_protection(self):
        export_log = self._create_log("csrf-protected.pdf", b"protected")
        self.authenticate(self.owner.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_logs/%s" % export_log.id,
            data={"token": "dummy-because-api-expects-one"},
            method="POST",
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)

    def test_other_user_is_denied_by_record_rule(self):
        export_log = self._create_log("private.pdf", b"private")
        self.authenticate(self.other_user.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_logs/%s" % export_log.id,
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_background_job_download_is_owner_only_and_popup_free(self):
        payload = b"%PDF-1.4\nRDS BACKGROUND\n"
        job = self._create_job("background-ready.pdf", payload)
        self.authenticate(self.owner.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_jobs/%s" % job.id,
            data={
                "token": "background-helper-token",
                "csrf_token": http.Request.csrf_token(self),
            },
            method="POST",
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, payload)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertIn("background-ready.pdf", response.headers["Content-Disposition"])

        generic_response = self.url_open(
            "/web/content/rds.export.job/%s/file_data/background-ready.pdf"
            % job.id,
            allow_redirects=False,
        )
        self.assertIn(generic_response.status_code, (403, 404))

        self.authenticate(self.other_user.login, self.password)
        denied = self.url_open(
            "/ranvals_document_studio/export_jobs/%s" % job.id,
            allow_redirects=False,
        )
        self.assertEqual(denied.status_code, 403)

    def test_bulk_download_deduplicates_colliding_names(self):
        logs = (
            self._create_log("a.pdf", b"first")
            | self._create_log("a.pdf", b"second")
            | self._create_log("a_(2).pdf", b"third")
        )
        self.authenticate(self.owner.login, self.password)

        response = self.url_open(
            "/ranvals_document_studio/export_logs/%s"
            % ",".join(str(log_id) for log_id in logs.ids),
            allow_redirects=False,
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["Content-Type"], "application/zip")
        with ZipFile(BytesIO(response.content)) as archive:
            self.assertEqual(
                archive.namelist(),
                ["a.pdf", "a_(2).pdf", "a__2.pdf"],
            )
            self.assertEqual(archive.read("a.pdf"), b"first")
            self.assertEqual(archive.read("a_(2).pdf"), b"second")
            self.assertEqual(archive.read("a__2.pdf"), b"third")
