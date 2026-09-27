"""Optional Odoo 19 Studio integration; native report identities stay unchanged.

Only HTML/PDF rendering with an explicit per-report choice is routed. Studio's
QWeb/XML source editor and all unconfigured reports keep their original source.
The six existing themed report roots (and their Studio inherits) are reused.
"""
import json
from collections.abc import Mapping

from lxml import etree, html

from odoo import _, api, models
from odoo.addons.ranvals_document_studio.tools.common import check_record_access
from odoo.exceptions import AccessError, UserError, ValidationError


RDS_STUDIO_MODELS = frozenset(("sale.order", "account.move", "purchase.order"))
RDS_MAX_ID = 2_147_483_647  # Odoo 19 ``fields.Id`` is PostgreSQL int4.


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def _rds_can_manage_design(self):
        return self.env.is_superuser() or self.env.user.has_group("base.group_system") or self.env.user.has_group(
            "ranvals_document_studio.group_rds_manager"
        )

    @api.model
    def _rds_positive_id(self, value, allow_empty=False):
        if allow_empty and (value is False or value is None):
            return False
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value > RDS_MAX_ID
        ):
            raise ValidationError(_("Geçersiz kayıt kimliği."))
        return value

    @api.model
    def _rds_report_data(self, data):
        """Return mutable report data or reject malformed RPC input."""
        if data is None or data is False:
            return {}
        if not isinstance(data, Mapping):
            raise UserError(_("Rapor verisi bir anahtar/değer eşlemesi olmalıdır."))
        return dict(data)

    def _rds_check_report_access(self):
        self.ensure_one()
        check_record_access(self)
        group_ids = set(self.sudo().group_ids.ids)
        if not self.env.is_superuser() and group_ids and not group_ids.intersection(self.env.user._get_group_ids()):
            raise AccessError(_("Bu raporu kullanma yetkiniz yok."))

    def _rds_sidebar_scope(self, record_id=False):
        self._rds_check_report_access()
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(_("Bu işlem yalnız iç kullanıcılar içindir."))
        if self.model not in RDS_STUDIO_MODELS or self.report_type not in ("qweb-pdf", "qweb-html"):
            raise UserError(_("Bu panel yalnız desteklenen satış, fatura ve satın alma QWeb raporlarında kullanılabilir."))
        record_id = self._rds_positive_id(record_id, allow_empty=True)
        record = self.env[self.model].browse(record_id).exists()
        if record_id and not record:
            raise UserError(_("Önizleme kaydı bulunamadı."))
        check_record_access(record)
        company = record.company_id if record else self.env.company
        if company not in self.env.companies:
            raise AccessError(_("Belgenin şirketi etkin ve izinli şirketler arasında değil."))
        return record, company

    def _rds_design_domain(self, company):
        return [
            ("active", "=", True),
            "|", ("company_id", "=", False), ("company_id", "=", company.id),
            "|", ("target_model_id", "=", False), ("target_model_id.model", "=", self.model),
        ]

    def _rds_design_binding(self, company):
        """Return the raw binding; callers must not expose its sudo payload."""
        self.ensure_one()
        return self.env["rds.report.design"].sudo().search([
            ("report_id", "=", self.id), ("company_id", "=", company.id),
        ], limit=1)

    def _rds_saved_design(self, company, binding=None):
        """Elevate configuration lookup only, never business-record access."""
        self.ensure_one()
        if binding is None:
            binding = self._rds_design_binding(company)
        template = binding.template_id
        if not template or not template.active:
            return self.env["rds.template"]
        if template.company_id and template.company_id != company:
            return self.env["rds.template"]
        if template.target_model_id and template.target_model_id.model != self.model:
            return self.env["rds.template"]
        return template.with_env(self.env)

    def rds_get_design_options(self, record_id=False):
        self.ensure_one()
        self._rds_check_report_access()
        if self.model not in RDS_STUDIO_MODELS or self.report_type not in ("qweb-pdf", "qweb-html"):
            return {"supported": False}
        record, company = self._rds_sidebar_scope(record_id)
        templates = self.env["rds.template"].search(self._rds_design_domain(company))
        binding = self._rds_design_binding(company)
        saved = self._rds_saved_design(company, binding=binding)
        return {
            "supported": True,
            "report_id": self.id,
            "record_id": record.id or False,
            "company_id": company.id,
            "company_name": company.display_name,
            "can_manage": self._rds_can_manage_design(),
            # Invalid/archived bindings are deliberately not exposed, but the
            # UI still needs to offer a way to clear them.
            "has_saved_configuration": bool(binding),
            "template_id": saved.id if saved in templates else False,
            "templates": [{"id": item.id, "name": item.display_name, "style": item.layout_style} for item in templates],
        }

    def rds_apply_design(self, record_id=False, template_id=False):
        record, company = self._rds_sidebar_scope(record_id)
        if not self._rds_can_manage_design():
            raise AccessError(_("Tasarım seçimini kaydetmek için DocuCraft yöneticisi olmalısınız."))
        template_id = self._rds_positive_id(template_id, allow_empty=True)
        template = self.env["rds.template"]
        if template_id:
            template = self.env["rds.template"].search(
                self._rds_design_domain(company) + [("id", "=", template_id)], limit=1
            )
            if not template:
                raise ValidationError(_("Şablon etkin değil veya bu şirket/belge modeli için kullanılamaz."))
        # Serialize concurrent choices for the same report; no manual commits.
        self.env.cr.execute("SELECT id FROM ir_act_report_xml WHERE id = %s FOR UPDATE", [self.id])
        bindings = self.env["rds.report.design"].search([
            ("report_id", "=", self.id), ("company_id", "=", company.id),
        ])
        if not template:
            bindings.unlink()
        elif bindings:
            bindings.write({"template_id": template.id})
        else:
            self.env["rds.report.design"].create({
                "report_id": self.id, "company_id": company.id, "template_id": template.id,
            })
        return self.rds_get_design_options(record.id or False)

    def _rds_is_proforma(self):
        self.ensure_one()
        name = (self.report_name or "").lower()
        return bool(self.env.context.get("proforma") or "pro_forma" in name or "proforma" in name)

    def _rds_default_export_template(self, record):
        self.ensure_one()
        template = self._rds_saved_design(record.company_id)
        provider = self.env.get("report.%s" % self.report_name)
        fixed_xmlid = getattr(provider, "_rds_fixed_template_xmlid", False) if provider is not None else False
        if not template and fixed_xmlid:
            template = self.env.ref(fixed_xmlid, raise_if_not_found=False)
        if not template:
            template = self.env["rds.template"].get_default_for(
                record._name,
                company=record.company_id,
                record=record,
            )
        if not template or template not in self.env["rds.template"].search(self._rds_design_domain(record.company_id)):
            raise UserError(_("Bu belge için kullanılabilir şablon bulunamadı."))
        return template

    def rds_export_design(self, record_id, output_format="pdf"):
        record, company = self._rds_sidebar_scope(record_id)
        if not record:
            raise UserError(_("İndirmek için sağ üstten gerçek bir belge kaydı seçin."))
        if output_format not in ("pdf", "docx", "docx_editable", "png", "zip"):
            raise ValidationError(_("Desteklenmeyen çıktı biçimi."))
        template = self._rds_default_export_template(record)
        language_mode = "company"
        Wizard = self.env["rds.export.wizard"].with_company(company)
        language = Wizard._automatic_language(record, language_mode)
        wizard = Wizard.create({
            "res_model": record._name,
            "res_ids_json": json.dumps(record.ids),
            "template_id": template.id,
            "output_format": output_format,
            "language_mode": language_mode,
            "language_id": language.id or False,
            "rds_source_report_id": self.id,
        })
        return wizard.action_export()

    def _rds_render_plan(self, report_ref, docids, data=None):
        """Return choices per record; an explicit wizard template has priority."""
        data = self._rds_report_data(data)
        report = self._get_report(report_ref)
        if report.model not in RDS_STUDIO_MODELS or not docids or self.env.context.get("rds_sidebar_rendering"):
            return report, []
        # Keep the existing public/portal rendering path unchanged. The new
        # selector is an authenticated back-office feature; it grants no
        # template or business-model access to portal/public visitors.
        if self.env.user.share and not self.env.is_superuser():
            return report, []
        if self.env.context.get("rds_disable_standard_sale_report"):
            return report, []
        ids = [docids] if isinstance(docids, int) else list(docids)
        records = self.env[report.model].browse(ids).exists()
        check_record_access(records)
        explicit_id = data.get("rds_template_id")
        explicit = self.env["rds.template"]
        if explicit_id:
            explicit_id = self._rds_positive_id(explicit_id)
            explicit = self.env["rds.template"].browse(explicit_id).exists()
            check_record_access(explicit)
            if not explicit:
                raise ValidationError(_("Seçilen şablon bulunamadı."))
        plan = []
        for record in records:
            selected = explicit or report._rds_saved_design(record.company_id)
            if selected:
                check_record_access(selected)
                if not selected.active or (selected.company_id and selected.company_id != record.company_id):
                    raise ValidationError(_("Şablon etkin değil veya belgenin şirketiyle uyumsuz."))
                if selected.target_model_id and selected.target_model_id.model != report.model:
                    raise ValidationError(_("Seçilen şablon bu belge modeli için uygun değil."))
            plan.append((record, selected))
        return report, plan

    @api.model
    def _rds_merge_rendered_html(self, documents):
        """Combine per-company renderings into one valid report HTML document."""
        if not documents:
            raise UserError(_("Birleştirilecek rapor içeriği bulunamadı."))
        if len(documents) == 1:
            return documents[0]

        try:
            root = html.document_fromstring(documents[0])
        except (etree.ParserError, TypeError, ValueError) as exc:
            raise UserError(_("Rapor HTML içeriği okunamadı.")) from exc
        destination = root.xpath("//main")
        destination = destination[0] if destination else root.find("body")
        if destination is None:
            raise UserError(_("Rapor HTML içeriğinde belge gövdesi bulunamadı."))

        for content in documents[1:]:
            try:
                other = html.document_fromstring(content)
            except (etree.ParserError, TypeError, ValueError) as exc:
                raise UserError(_("Rapor HTML içeriği okunamadı.")) from exc
            source = other.xpath("//main")
            source = source[0] if source else other.find("body")
            if source is None:
                raise UserError(_("Rapor HTML içeriğinde belge gövdesi bulunamadı."))
            for node in list(source):
                destination.append(node)
        return etree.tostring(root, encoding="utf-8", method="html")

    @api.model
    def _rds_target_report(self, report, record, template):
        """Resolve and validate a connector's themed report action.

        Connector methods return XML IDs, but a missing or accidentally
        repointed XML ID must not be treated as an arbitrary report database
        ID.  Validate both the model and QWeb report type before rendering.
        """
        target_xmlid = record._rds_report_action_xmlid(template)
        target = self.env.ref(target_xmlid, raise_if_not_found=False)
        if (
            not target
            or target._name != "ir.actions.report"
            or target.model != report.model
            or target.report_type not in ("qweb-pdf", "qweb-html")
        ):
            raise UserError(_("Seçilen tasarımın belge raporu bulunamadı veya geçersiz."))
        return target

    @api.model
    def _render_qweb_html(self, report_ref, docids, data=None):
        data = self._rds_report_data(data)
        report, plan = self._rds_render_plan(report_ref, docids, data)
        if not any(template for _record, template in plan):
            return super()._render_qweb_html(report_ref, docids, data=data)
        documents = []
        for record, template in plan:
            lang = data.get("lang") or record.partner_id.lang or self.env.user.lang
            render_context = {"rds_sidebar_rendering": True, "lang": lang}
            if report.model == "sale.order":
                render_context["proforma"] = report._rds_is_proforma()
            renderer = self.with_context(**render_context)
            values = dict(data)
            values["lang"] = lang
            target_ref = report.id
            if template:
                target = self._rds_target_report(report, record, template)
                target_ref = target.id
                values["rds_template_id"] = template.id
            content, _kind = super(IrActionsReport, renderer)._render_qweb_html(target_ref, [record.id], data=values)
            documents.append(content)
        return self._rds_merge_rendered_html(documents), "html"

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        data = self._rds_report_data(data)
        _report, plan = self._rds_render_plan(report_ref, res_ids, data)
        if any(template for _record, template in plan):
            # Neither read old PDFs nor overwrite the original report's cached
            # attachment with a different design. No stored files are deleted.
            renderer = self.with_context(report_pdf_no_attachment=True, rds_sidebar_ignore_attachment=True)
            return super(IrActionsReport, renderer)._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        return super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)

    def retrieve_attachment(self, record):
        if self.env.context.get("rds_sidebar_ignore_attachment"):
            return self.env["ir.attachment"]
        return super().retrieve_attachment(record)
