from odoo import models
from ..tools.docx_language import localize_docx


DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RdsExportWizardLocalization(models.TransientModel):
    _inherit = "rds.export.wizard"

    def _record_context(self, record, language_code):
        localized = self.with_context(lang=language_code) if language_code else self
        return super(RdsExportWizardLocalization, localized)._record_context(record, language_code)

    def _export_record(self, record, language_code):
        outputs = super()._export_record(record, language_code)
        # ZIP exports contain a DOCX alongside PDF/PNG.  Localize by actual
        # member type rather than by the wizard's outer format.
        return [
            (
                name,
                localize_docx(content, language_code)
                if mimetype == DOCX_MIMETYPE
                else content,
                mimetype,
            )
            for name, content, mimetype in outputs
        ]
