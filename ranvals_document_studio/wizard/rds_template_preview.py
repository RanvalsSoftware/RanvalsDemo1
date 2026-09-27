import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.common import check_record_access

PREVIEW_MODELS = {
    "sale.order": "sale_order_id",
    "account.move": "account_move_id",
    "purchase.order": "purchase_order_id",
}


class RdsTemplatePreviewWizard(models.TransientModel):
    _name = "rds.template.preview.wizard"
    _description = "DocuCraft Gerçek Kayıt Önizlemesi"

    template_id = fields.Many2one("rds.template", required=True, readonly=True)
    target_model = fields.Selection(
        [("sale.order", "Satış Teklifi / Siparişi"), ("account.move", "Fatura"), ("purchase.order", "Satın Alma Siparişi")],
        required=True,
    )
    sale_order_id = fields.Many2one("sale.order", string="Satış Belgesi")
    account_move_id = fields.Many2one(
        "account.move",
        string="Fatura",
        domain=[
            ("move_type", "in", ("out_invoice", "out_refund", "in_invoice", "in_refund")),
        ],
    )
    purchase_order_id = fields.Many2one("purchase.order", string="Satın Alma Belgesi")
    language_id = fields.Many2one("res.lang", required=True, domain="[('active', '=', True)]")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        template_id = values.get("template_id") or self.env.context.get("default_template_id")
        template = self.env["rds.template"].browse(template_id).exists()
        target = template.target_model_id.model if template else False
        if target in PREVIEW_MODELS:
            values["target_model"] = target
        values.setdefault("language_id", self.env["res.lang"].search([
            ("code", "=", self.env.user.lang), ("active", "=", True)
        ], limit=1).id)
        return values

    @api.onchange("target_model")
    def _onchange_target_model(self):
        self.sale_order_id = False
        self.account_move_id = False
        self.purchase_order_id = False

    def _selected_record(self):
        self.ensure_one()
        field_name = PREVIEW_MODELS.get(self.target_model)
        record = self[field_name] if field_name else False
        if not record:
            raise UserError(_("Önizleme için bir belge kaydı seçin."))
        record = record.exists()
        if not record:
            raise AccessError(_("Kaynak belgeye erişim izniniz bulunmuyor."))
        check_record_access(record)
        if "company_id" in record._fields and record.company_id not in self.env.companies:
            raise AccessError(_("Belgenin şirketine erişim izniniz bulunmuyor."))
        if record._name == "account.move" and record.move_type not in {
            "out_invoice", "out_refund", "in_invoice", "in_refund"
        }:
            raise ValidationError(_("Önizleme yalnız müşteri/tedarikçi faturaları ve iadeler için kullanılabilir."))
        return record

    def action_preview(self):
        self.ensure_one()
        record = self._selected_record()
        check_record_access(self.template_id)
        if self.template_id.target_model_id and self.template_id.target_model_id.model != record._name:
            raise ValidationError(_("Şablon hedef modeli seçilen belgeyle eşleşmiyor."))
        if self.template_id.company_id and self.template_id.company_id != record.company_id:
            raise ValidationError(_("Şablon şirketi seçilen belgenin şirketiyle eşleşmiyor."))
        company = (
            record.company_id
            if "company_id" in record._fields and record.company_id
            else self.env.company
        )
        export = self.env["rds.export.wizard"].with_company(company).create({
            "res_model": record._name,
            "res_ids_json": json.dumps(record.ids),
            "template_id": self.template_id.id,
            "output_format": "pdf",
            "language_id": self.language_id.id,
            "attach_to_record": False,
        })
        return export.action_preview()


class RdsTemplatePreviewActions(models.Model):
    _inherit = "rds.template"

    def action_open_real_record_preview(self):
        self.ensure_one()
        check_record_access(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("Gerçek Kayıtla Test Et"),
            "res_model": "rds.template.preview.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }
