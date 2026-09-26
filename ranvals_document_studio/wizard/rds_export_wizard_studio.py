from odoo import fields, models, _
from odoo.exceptions import ValidationError, UserError


class RdsExportWizard(models.TransientModel):
    _inherit = "rds.export.wizard"

    rds_source_report_id = fields.Many2one("ir.actions.report", readonly=True, ondelete="set null")

    def _rds_source_report(self, record):
        self.ensure_one()
        report = self.rds_source_report_id
        if report:
            if record._name != report.model or self.res_model != report.model:
                raise ValidationError(_("Kaynak rapor ve belge modeli uyuşmuyor."))
            report._rds_sidebar_scope(record.id)
        return report

    def _render_pdf(self, record, language_code):
        report = self._rds_source_report(record)
        if not report:
            return super()._render_pdf(record, language_code)
        # The selected report action is kept; its per-company design is resolved
        # by the integration. No explicit theme is forced over the native report.
        content, _kind = self.env["ir.actions.report"].with_context(lang=language_code)._render_qweb_pdf(
            report.id, res_ids=[record.id], data={"lang": language_code}
        )
        if not content or not content.startswith(b"%PDF"):
            raise UserError(_("PDF motoru geçerli bir PDF çıktısı döndürmedi."))
        return content

    def _record_context(self, record, language_code):
        report = self._rds_source_report(record)
        if report:
            record = record.with_context(proforma=report._rds_is_proforma())
        return super()._record_context(record, language_code)
