from werkzeug.exceptions import NotFound, RequestEntityTooLarge

from odoo import http
from odoo.http import request

from .export_log import (
    MAX_BULK_DOWNLOAD_BYTES,
    _download_headers,
    _safe_download_name,
    _safe_mimetype,
)


class RdsExportJobDownloadController(http.Controller):

    @http.route(
        "/ranvals_document_studio/export_jobs/<int:job_id>",
        type="http",
        auth="user",
        methods=["GET", "POST"],
    )
    def download_export_job(self, job_id, token=None):
        job = request.env["rds.export.job"].browse(job_id).exists()
        if not job:
            raise NotFound()
        try:
            file_name, content, mimetype = job._get_stored_file()
        except Exception as error:
            # Odoo's HTTP layer maps access errors, but a missing/expired job
            # must never expose model or filesystem details in a download URL.
            from odoo.exceptions import AccessError, UserError

            if isinstance(error, AccessError):
                raise
            if isinstance(error, UserError):
                raise NotFound() from error
            raise
        if len(content) > MAX_BULK_DOWNLOAD_BYTES:
            raise RequestEntityTooLarge()
        safe_name = _safe_download_name(file_name)
        safe_mimetype = _safe_mimetype(mimetype)
        return request.make_response(
            content,
            _download_headers(safe_name, safe_mimetype, content),
        )
