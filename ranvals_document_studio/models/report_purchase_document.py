from collections.abc import Mapping

from odoo import api, models, _
from odoo.exceptions import UserError

from odoo.addons.ranvals_document_studio.tools.common import check_record_access


class ReportRdsPurchaseDocument(models.AbstractModel):
    _name = "report.ranvals_document_studio.report_purchase_document"
    _table = "rds_report_purchase"
    _description = "DocuCraft Satın Alma Raporu"

    @api.model
    def _rds_validate_report_data(self, data):
        if data is None:
            return {}
        if not isinstance(data, Mapping):
            raise UserError(_("DocuCraft rapor verisi geçerli bir nesne olmalıdır."))
        values = dict(data)
        if "rds_template_id" in values:
            template_id = values["rds_template_id"]
            if type(template_id) is not int or template_id <= 0:
                raise UserError(_("DocuCraft şablon kimliği pozitif bir tam sayı olmalıdır."))
        if "lang" in values:
            language_code = values["lang"]
            is_active_language = (
                isinstance(language_code, str)
                and 0 < len(language_code) <= 64
                and self.env["res.lang"].search_count(
                    [("code", "=", language_code), ("active", "=", True)],
                    limit=1,
                )
            )
            if not is_active_language:
                raise UserError(_("Seçilen belge dili etkin veya geçerli değildir."))
        if "rds_export_field_specs" in values and not isinstance(
            values["rds_export_field_specs"], (list, tuple)
        ):
            raise UserError(_("Belge alanı seçimi geçerli bir liste olmalıdır."))
        return values

    @api.model
    def _get_report_values(self, docids, data=None):
        data = self._rds_validate_report_data(data)
        docs = self.env["purchase.order"].browse(docids).exists()
        check_record_access(docs, "read")
        if data.get("lang"):
            docs = docs.with_context(
                lang=data["lang"], rds_requested_lang=data["lang"]
            )
        else:
            docs = docs.with_context(rds_requested_lang=False)
        requested_template_id = data.get("rds_template_id")
        template = self.env["rds.template"].browse(requested_template_id).exists()
        if requested_template_id and not template:
            raise UserError(_("Seçilen DocuCraft şablonu bulunamadı."))
        if not template and docs:
            template = self.env["rds.template"].get_default_for(
                "purchase.order",
                company=docs[:1].company_id,
                record=docs if len(docs) == 1 else None,
            )
        if docs and not template:
            raise UserError(_("Kullanılabilir DocuCraft şablonu bulunamadı."))
        if template:
            check_record_access(template, "read")
            if not template.active:
                raise UserError(_("Seçilen DocuCraft şablonu etkin değildir."))
            if template.target_model_id and template.target_model_id.model != "purchase.order":
                raise UserError(_("Seçilen şablon bu belge modeli için tanımlı değildir."))
            if template.company_id and docs.filtered(lambda record: record.company_id != template.company_id):
                raise UserError(_("Şirkete özel şablon başka bir şirketin belgesinde kullanılamaz."))
        if docs and template and "rds_export_field_specs" in data:
            normalized_specs = template.normalize_export_field_specs(
                docs[:1],
                data["rds_export_field_specs"],
                lang=data.get("lang"),
            )
            data["rds_export_field_specs"] = normalized_specs
            docs = docs.with_context(rds_export_field_specs=normalized_specs)
        return {
            "doc_ids": docs.ids,
            "doc_model": "purchase.order",
            "docs": docs,
            "rds_template": template,
            "rds_data": data,
        }
