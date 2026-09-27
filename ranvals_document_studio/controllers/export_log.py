import base64
import binascii
from pathlib import PurePath

from werkzeug.exceptions import NotFound, RequestEntityTooLarge

from odoo import _, http
from odoo.http import content_disposition, request

from ..tools.archive import build_zip_archive
from ..tools.common import check_record_access, safe_filename


MAX_BULK_DOWNLOAD_RECORDS = 50
MAX_BULK_DOWNLOAD_BYTES = 100 * 1024 * 1024
SAFE_DOWNLOAD_MIMETYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip",
    "image/png",
}


def _safe_download_name(value, default="document"):
    """Sanitize a name while keeping a short extension inside 120 chars."""
    raw_name = str(value or default)
    suffix = PurePath(raw_name).suffix
    if not suffix[1:].isalnum() or len(suffix) > 11:
        suffix = ""
    stem = raw_name[:-len(suffix)] if suffix else raw_name
    safe_stem = safe_filename(stem, default=default)
    return "%s%s" % (safe_stem[: 120 - len(suffix)], suffix.lower())


def _safe_mimetype(value):
    return value if value in SAFE_DOWNLOAD_MIMETYPES else "application/octet-stream"


def _unique_archive_name(file_name, used_names):
    candidate = file_name
    stem = PurePath(file_name).stem
    suffix = PurePath(file_name).suffix
    occurrence = 2
    while candidate in used_names:
        candidate = "%s_(%s)%s" % (stem, occurrence, suffix)
        occurrence += 1
    used_names.add(candidate)
    return candidate


def _download_headers(file_name, mimetype, content, *, inline=False):
    return [
        ("Content-Type", mimetype),
        ("Content-Length", len(content)),
        (
            "Content-Disposition",
            content_disposition(file_name, "inline" if inline else "attachment"),
        ),
        ("Cache-Control", "private, no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ]


class RdsExportLogDownloadController(http.Controller):

    @staticmethod
    def _wizard_payload(wizard_id):
        wizard = request.env["rds.export.wizard"].browse(wizard_id).exists()
        if not wizard:
            raise NotFound()
        check_record_access(wizard)
        # Re-apply live source document ACLs between rendering and transfer.
        wizard._get_records()
        file_data = wizard.sudo().file_data
        if not file_data or not wizard.file_name:
            raise NotFound()
        try:
            content = base64.b64decode(file_data, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise NotFound() from error
        if not content or len(content) > MAX_BULK_DOWNLOAD_BYTES:
            raise RequestEntityTooLarge()
        return wizard, content

    @http.route(
        "/ranvals_document_studio/export_wizard/<int:wizard_id>",
        type="http",
        auth="user",
        methods=["GET", "POST"],
    )
    def download_export_wizard(self, wizard_id, token=None):
        """Download a freshly rendered wizard result without opening a popup."""
        wizard, content = self._wizard_payload(wizard_id)
        file_name = _safe_download_name(wizard.file_name)
        mimetype = _safe_mimetype(wizard.file_mimetype)
        return request.make_response(
            content,
            _download_headers(file_name, mimetype, content),
        )

    @http.route(
        "/ranvals_document_studio/export_wizard/<int:wizard_id>/preview",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def preview_export_wizard(self, wizard_id):
        """Display only a validated PDF after checking current source ACLs."""
        wizard, content = self._wizard_payload(wizard_id)
        if wizard.file_mimetype != "application/pdf" or not content.startswith(b"%PDF"):
            raise NotFound()
        file_name = _safe_download_name(wizard.file_name)
        return request.make_response(
            content,
            _download_headers(
                file_name,
                "application/pdf",
                content,
                inline=True,
            ),
        )

    @http.route(
        "/ranvals_document_studio/export_logs/<string:log_ids>",
        type="http",
        auth="user",
        # ``POST`` is used by Odoo's XHR/Blob download helper.  Keep ``GET``
        # for existing bookmarked links and older history views.  Odoo's
        # default CSRF protection remains enabled for the unsafe method.
        methods=["GET", "POST"],
    )
    def download_export_logs(self, log_ids, token=None):
        # ``download()`` always includes its compatibility token.  Accept it
        # explicitly so Odoo's route wrapper does not emit an ignored-argument
        # warning for every successful XHR/Blob transfer.
        raw_ids = log_ids.split(",")
        if len(raw_ids) > MAX_BULK_DOWNLOAD_RECORDS:
            raise RequestEntityTooLarge()
        try:
            log_ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except ValueError as error:
            raise NotFound() from error
        if not log_ids or any(log_id <= 0 for log_id in log_ids):
            raise NotFound()
        if len(log_ids) > MAX_BULK_DOWNLOAD_RECORDS:
            raise RequestEntityTooLarge()
        logs = request.env["rds.export.log"].browse(log_ids).exists()
        check_record_access(logs)
        documents = []
        total_size = 0
        for log in logs:
            if not log.has_stored_file:
                continue
            if log.file_size and log.file_size > MAX_BULK_DOWNLOAD_BYTES:
                raise RequestEntityTooLarge()
            file_name, content, mimetype = log._get_stored_file()
            total_size += len(content)
            if total_size > MAX_BULK_DOWNLOAD_BYTES:
                raise RequestEntityTooLarge()
            documents.append(
                {
                    "file_name": _safe_download_name(file_name),
                    "content": content,
                    "mimetype": _safe_mimetype(mimetype),
                }
            )

        if not documents:
            raise NotFound()
        if len(documents) == 1:
            document = documents[0]
            return request.make_response(
                document["content"],
                _download_headers(
                    document["file_name"],
                    document["mimetype"],
                    document["content"],
                ),
            )

        used_names = set()
        content = build_zip_archive(
            (
                _unique_archive_name(document["file_name"], used_names),
                document["content"],
            )
            for document in documents
        )
        if len(content) > MAX_BULK_DOWNLOAD_BYTES:
            raise RequestEntityTooLarge()
        file_name = _safe_download_name(
            _("DocuCraft Exports") + ".zip"
        )
        return request.make_response(
            content,
            _download_headers(file_name, "application/zip", content),
        )
