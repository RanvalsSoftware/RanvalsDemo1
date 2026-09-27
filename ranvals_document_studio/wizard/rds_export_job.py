from odoo import fields, models


class RdsExportWizardBackground(models.TransientModel):
    _inherit = "rds.export.wizard"

    processing_mode = fields.Selection(
        [
            ("auto", "Automatic – Recommended"),
            ("immediate", "Generate and Download Now"),
            ("background", "Prepare in Background"),
        ],
        required=True,
        default="auto",
        help=(
            "Automatic mode sends large, multi-record, or all-format exports "
            "to the background and downloads small documents immediately."
        ),
    )

    def _should_enqueue_export(self):
        self.ensure_one()
        if self.processing_mode == "background":
            return True
        if self.processing_mode == "immediate":
            return False
        return self.record_count > 3 or self.output_format == "zip"

    def action_export(self):
        self.ensure_one()
        if not self._should_enqueue_export():
            return super().action_export()
        job = self.env["rds.export.job"]._enqueue_from_wizard(self)
        cron = self.env.ref(
            "ranvals_document_studio.ir_cron_rds_export_jobs",
            raise_if_not_found=False,
        )
        if cron:
            cron.sudo()._trigger()
        return {
            "type": "ir.actions.act_window",
            "name": job.name,
            "res_model": "rds.export.job",
            "res_id": job.id,
            "view_mode": "form",
            "target": "current",
        }
