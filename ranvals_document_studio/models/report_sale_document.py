from collections.abc import Mapping

from odoo import api, models, _
from odoo.exceptions import UserError

from odoo.addons.ranvals_document_studio.tools.common import check_record_access


class ReportRdsSaleDocument(models.AbstractModel):
    _name = "report.ranvals_document_studio.report_sale_document"
    _table = "rds_report_sale"
    _description = "DocuCraft Satış Raporu"

    # A report that is opened from the Studio report list has no wizard data.
    # The six subclasses below therefore pin their report action to one of the
    # seeded Document Studio themes.  The generic report keeps accepting a
    # template from the export wizard.
    _rds_fixed_template_xmlid = False

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
    def _get_rds_template(self, data, docs):
        # A wizard export always wins over the action's theme.  This keeps a
        # copied/customised rds.template (custom colours, fields and blocks)
        # intact while still letting a direct Print-menu action have a stable
        # default theme when it is called without report data.
        requested_template_id = data.get("rds_template_id")
        template = self.env["rds.template"].browse(requested_template_id).exists()
        if requested_template_id and not template:
            raise UserError(_("Seçilen DocuCraft şablonu bulunamadı."))
        if not template and self._rds_fixed_template_xmlid:
            template = self.env.ref(self._rds_fixed_template_xmlid, raise_if_not_found=False)
            if template and not template.active:
                template = self.env["rds.template"].browse()
        if not template and docs:
            template = self.env["rds.template"].get_default_for(
                "sale.order",
                company=docs[:1].company_id,
                record=docs if len(docs) == 1 else None,
            )
        return template

    @api.model
    def _get_report_values(self, docids, data=None):
        data = self._rds_validate_report_data(data)
        docs = self.env["sale.order"].browse(docids).exists()
        check_record_access(docs, "read")
        if data.get("lang"):
            docs = docs.with_context(
                lang=data["lang"], rds_requested_lang=data["lang"]
            )
        else:
            docs = docs.with_context(rds_requested_lang=False)
        template = self._get_rds_template(data, docs)
        if docs and not template:
            raise UserError(_("Kullanılabilir DocuCraft şablonu bulunamadı."))
        if template:
            check_record_access(template, "read")
            if not template.active:
                raise UserError(_("Seçilen DocuCraft şablonu etkin değildir."))
            if template.target_model_id and template.target_model_id.model != "sale.order":
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
            "doc_model": "sale.order",
            "docs": docs,
            "rds_template": template,
            "rds_data": data,
        }


class ReportRdsSaleBeautyDocument(ReportRdsSaleDocument):
    _name = "report.ranvals_document_studio.report_sale_beauty_document"
    _table = "rds_report_sale_beauty"
    _description = "DocuCraft Signature Burgundy Satış Raporu"
    _rds_fixed_template_xmlid = "ranvals_document_studio.template_beauty_premium"


class ReportRdsSaleConstructionDocument(ReportRdsSaleDocument):
    _name = "report.ranvals_document_studio.report_sale_construction_document"
    _table = "rds_report_sale_construction"
    _description = "DocuCraft Executive Navy Satış Raporu"
    _rds_fixed_template_xmlid = "ranvals_document_studio.template_construction_navy"


class ReportRdsSaleTechnologyDocument(ReportRdsSaleDocument):
    _name = "report.ranvals_document_studio.report_sale_technology_document"
    _table = "rds_report_sale_technology"
    _description = "DocuCraft Horizon Blue Satış Raporu"
    _rds_fixed_template_xmlid = "ranvals_document_studio.template_technology_blue"


class ReportRdsSaleIndustrialDocument(ReportRdsSaleDocument):
    _name = "report.ranvals_document_studio.report_sale_industrial_document"
    _table = "rds_report_sale_industrial"
    _description = "DocuCraft Atlas Steel Satış Raporu"
    _rds_fixed_template_xmlid = "ranvals_document_studio.template_industrial_red"


class ReportRdsSaleEcoDocument(ReportRdsSaleDocument):
    _name = "report.ranvals_document_studio.report_sale_eco_document"
    _table = "rds_report_sale_eco"
    _description = "DocuCraft Sage Reserve Satış Raporu"
    _rds_fixed_template_xmlid = "ranvals_document_studio.template_eco_green"


class ReportRdsSaleFurnitureDocument(ReportRdsSaleDocument):
    _name = "report.ranvals_document_studio.report_sale_furniture_document"
    _table = "rds_report_sale_furniture"
    _description = "DocuCraft Copper Atelier Satış Raporu"
    _rds_fixed_template_xmlid = "ranvals_document_studio.template_furniture_terracotta"
