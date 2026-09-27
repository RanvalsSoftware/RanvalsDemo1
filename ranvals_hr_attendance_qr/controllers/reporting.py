"""Authenticated exports for frozen attendance-report snapshots."""

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import content_disposition, request
from werkzeug.exceptions import NotFound


class RanvalsQRAttendanceReportController(http.Controller):
    @http.route(
        "/ranvals/qr-attendance/report/<int:batch_id>/<string:kind>.csv",
        type="http",
        auth="user",
        methods=["GET"],
        sitemap=False,
        readonly=True,
    )
    def download_csv(self, batch_id, kind, **kwargs):
        if kind not in {"detail", "payroll"}:
            raise NotFound()
        batch = request.env["ranvals.qr.report.batch"].browse(batch_id).exists()
        if not batch:
            raise NotFound()
        try:
            batch.check_access("read")
            content = batch._csv_content(kind)
        except AccessError:
            # Do not reveal whether a report id exists in another company.
            raise NotFound() from None
        filename = (
            f"attendance_{kind}_{batch.date_from}_{batch.date_to}_R{batch.revision}.csv"
        )
        return request.make_response(
            content,
            headers=[
                ("Content-Type", "text/csv; charset=utf-8"),
                ("Content-Disposition", content_disposition(filename)),
                ("Cache-Control", "no-store, max-age=0"),
                ("Pragma", "no-cache"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
