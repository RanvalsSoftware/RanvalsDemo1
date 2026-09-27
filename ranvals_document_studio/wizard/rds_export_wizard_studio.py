from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError


class RdsExportWizard(models.TransientModel):
    _inherit = "rds.export.wizard"

    rds_source_report_id = fields.Many2one("ir.actions.report", readonly=True, ondelete="set null")

    def _rds_source_report(self, record):
        self.ensure_one()
        report = self.rds_source_report_id
        if report:
            if record._name != report.model or self.res_model != report.model:
                raise ValidationError(_("The source report and document model do not match."))
            report._rds_sidebar_scope(record.id)
        return report

    def _render_pdf(self, record, language_code):
        report = self._rds_source_report(record)
        if not report:
            return super()._render_pdf(record, language_code)
        # Keep the selected source action so report ACLs and pro-forma semantics
        # remain intact.  In inherited mode its per-company design is resolved
        # exactly as before; a custom field selection explicitly chooses the
        # wizard's DocuCraft template below.
        data = {"lang": language_code}
        field_specs = self._selected_field_specs(
            record=record,
            language_code=language_code,
        )
        if field_specs is not None:
            # A per-export field choice belongs to the selected DocuCraft
            # design, not to the arbitrary QWeb source report that opened the
            # wizard.  Supplying the explicit template lets the report bridge
            # route PDF (and therefore pixel-perfect Word/PNG) through the
            # same layout/context as live preview and editable Word.  With
            # inherited fields we deliberately omit it, preserving the
            # source report's historical native/saved-design behaviour.
            data.update(
                {
                    "rds_template_id": self.template_id.id,
                    "model_name": record._name,
                    "rds_export_field_specs": field_specs,
                }
            )
        content, _kind = self.env["ir.actions.report"].with_context(lang=language_code)._render_qweb_pdf(
            report.id, res_ids=[record.id], data=data
        )
        if not content or not content.startswith(b"%PDF"):
            raise UserError(_("The PDF engine did not return a valid PDF output."))
        return content

    def _record_context(self, record, language_code):
        report = self._rds_source_report(record)
        if report:
            record = record.with_context(proforma=report._rds_is_proforma())
        return super()._record_context(record, language_code)
