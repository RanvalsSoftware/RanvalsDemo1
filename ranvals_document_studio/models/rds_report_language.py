"""Language is independent of the saved design and scoped to report/company."""
import json

from odoo import _, api, fields, models
from odoo.addons.ranvals_document_studio.models.rds_localization import (
    RDS_LANGUAGE_CODES,
    RDS_LANGUAGES,
)
from odoo.exceptions import AccessError, ValidationError


RDS_STUDIO_MODELS = frozenset(("sale.order", "account.move", "purchase.order"))


class RdsReportLanguage(models.Model):
    _name = "rds.report.language"
    _description = "Report Language per Company"
    _rec_name = "report_id"

    report_id = fields.Many2one("ir.actions.report", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", index=True)
    language_code = fields.Selection(RDS_LANGUAGES, required=True, string="Belge dili")
    if hasattr(models, "Constraint"):
        _report_company_unique = models.Constraint(
            "UNIQUE(report_id, company_id)",
            "Bir rapor ve şirket için yalnız bir belge dili seçilebilir.",
        )
    else:  # Odoo 17/18
        _sql_constraints = [
            (
                "rds_report_language_company_unique",
                "UNIQUE(report_id, company_id)",
                "Bir rapor ve şirket için yalnız bir belge dili seçilebilir.",
            )
        ]

    @api.constrains("report_id", "company_id", "language_code")
    def _check_scope(self):
        for row in self:
            row.report_id._rds_check_report_access()
            if row.report_id.model not in RDS_STUDIO_MODELS or row.report_id.report_type not in ("qweb-pdf", "qweb-html"):
                raise ValidationError(_("Bu panel yalnız satış, fatura ve satın alma QWeb raporlarında kullanılabilir."))
            if not self.env["res.lang"].search_count([("code", "=", row.language_code), ("active", "=", True)]):
                raise ValidationError(_("Bu dil henüz etkin değil."))

    def unlink(self):
        # The field constraint protects create/write, but unlink does not run
        # constraints.  Do not let a manager clear a restricted report's
        # language through a crafted model RPC.
        for row in self:
            row.report_id._rds_check_report_access()
        return super().unlink()


class IrActionsReportLanguage(models.Model):
    _inherit = "ir.actions.report"

    def _rds_language_binding(self, company):
        self.ensure_one()
        # Configuration lookup only. Document access is checked by the caller.
        return self.env["rds.report.language"].sudo().search([
            ("report_id", "=", self.id), ("company_id", "=", company.id)], limit=1)

    def _rds_saved_language(self, company, row=None):
        self.ensure_one()
        if row is None:
            row = self._rds_language_binding(company)
        code = row.language_code
        return code if code and self.env["res.lang"].search_count([
            ("code", "=", code), ("active", "=", True)]) else False

    def rds_get_design_options(self, record_id=False):
        values = super().rds_get_design_options(record_id)
        if values.get("supported"):
            company = self.env["res.company"].browse(values["company_id"])
            row = self._rds_language_binding(company)
            active_codes = set(self.env["res.lang"].search([
                ("code", "in", RDS_LANGUAGE_CODES),
                ("active", "=", True),
            ]).mapped("code"))
            values["languages"] = [
                {"code": code, "name": name, "active": code in active_codes}
                for code, name in RDS_LANGUAGES
            ]
            values["language_code"] = self._rds_saved_language(company, row=row)
            values["has_saved_configuration"] = bool(
                values.get("has_saved_configuration") or row
            )
        return values

    def rds_apply_design(self, record_id=False, template_id=False, language_code=None):
        # None preserves the setting for older callers. False explicitly restores
        # the automatic company language. The base method checks ACLs and locks
        # the report row.
        result = super().rds_apply_design(record_id, template_id)
        if language_code is None:
            return result
        record, company = self._rds_sidebar_scope(record_id)
        if not self._rds_can_manage_design():
            raise AccessError(_("Tasarım seçimini kaydetmek için DocuCraft yöneticisi olmalısınız."))
        if language_code and (language_code not in RDS_LANGUAGE_CODES or not self.env["res.lang"].search_count([
            ("code", "=", language_code), ("active", "=", True)])):
            raise ValidationError(_("Bu dil henüz etkin değil veya desteklenmiyor."))
        rows = self.env["rds.report.language"].search([("report_id", "=", self.id), ("company_id", "=", company.id)])
        if not language_code:
            rows.unlink()
        elif rows:
            rows.write({"language_code": language_code})
        else:
            self.env["rds.report.language"].create({
                "report_id": self.id,
                "company_id": company.id,
                "language_code": language_code,
            })
        return self.rds_get_design_options(record.id or False)

    def rds_export_design(self, record_id, output_format="pdf"):
        record, company = self._rds_sidebar_scope(record_id)
        code = self._rds_saved_language(company)
        if not code:
            return super().rds_export_design(record_id, output_format)
        if not record or output_format not in (
            "pdf",
            "docx",
            "docx_editable",
            "png",
            "zip",
        ):
            raise ValidationError(_("Geçersiz kayıt veya çıktı biçimi."))
        template = self._rds_default_export_template(record)
        lang = self.env["res.lang"].search([("code", "=", code)], limit=1)
        wizard = self.env["rds.export.wizard"].with_company(company).create({
            "res_model": record._name,
            "res_ids_json": json.dumps(record.ids),
            "template_id": template.id,
            "output_format": output_format,
            "language_mode": "manual",
            "language_id": lang.id,
            "rds_source_report_id": self.id,
        })
        return wizard.action_export()

    def _rds_render_plan(self, report_ref, docids, data=None):
        report, plan = super()._rds_render_plan(report_ref, docids, data)
        # Selecting only a language must also route the native report through
        # its effective template, otherwise native t-lang would override it.
        return report, [
            (
                record,
                template or (
                    report._rds_default_export_template(record)
                    if report._rds_saved_language(record.company_id)
                    else template
                ),
            )
            for record, template in plan
        ]

    @api.model
    def _render_qweb_html(self, report_ref, docids, data=None):
        data = self._rds_report_data(data)
        if self.env.context.get("rds_language_rendering") or data.get("lang") or self.env.user.share:
            return super()._render_qweb_html(report_ref, docids, data=data)
        report, plan = self._rds_render_plan(report_ref, docids, data)
        codes = [report._rds_saved_language(record.company_id) for record, _template in plan]
        if not any(codes):
            return super()._render_qweb_html(report_ref, docids, data=data)
        documents = []
        for (record, _template), code in zip(plan, codes, strict=True):
            values = dict(data)
            if code:
                values["lang"] = code
            renderer = self.with_context(rds_language_rendering=True)
            content, _kind = super(IrActionsReportLanguage, renderer)._render_qweb_html(report_ref, [record.id], data=values)
            documents.append(content)
        return self._rds_merge_rendered_html(documents), "html"
