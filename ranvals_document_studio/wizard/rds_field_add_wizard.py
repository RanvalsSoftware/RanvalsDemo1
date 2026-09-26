from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RdsFieldAddWizard(models.TransientModel):
    _name = "rds.field.add.wizard"
    _description = "Belge Şablonuna Alan Ekle"

    template_id = fields.Many2one("rds.template", required=True)
    section = fields.Selection(
        [("metadata", "Belge Bilgi Kartı"), ("line", "Satır / Tablo Kolonu")],
        default="metadata",
        required=True,
    )
    source_model_id = fields.Many2one(
        "ir.model",
        required=True,
        domain="[('transient', '=', False)]",
    )
    field_id = fields.Many2one(
        "ir.model.fields",
        required=True,
        domain="[('model_id', '=', source_model_id)]",
    )
    field_path = fields.Char(
        string="Alan Yolu",
        required=True,
        help="Doğrudan alan için name; ilişkili alan için partner_id.vat. En fazla 4 seviye.",
    )
    label = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    icon_class = fields.Char(default="fa-circle-o")
    value_type = fields.Selection(
        [
            ("auto", "Otomatik"), ("text", "Metin"), ("date", "Tarih"),
            ("monetary", "Para"), ("percentage", "Yüzde"),
            ("integer", "Tam Sayı"), ("float", "Ondalık Sayı"),
        ],
        default="auto",
        required=True,
    )
    alignment = fields.Selection(
        [("left", "Sol"), ("center", "Orta"), ("right", "Sağ")],
        default="left",
        required=True,
    )
    width_percent = fields.Integer(default=16)
    hide_if_empty = fields.Boolean(default=True)
    bold = fields.Boolean()
    highlight = fields.Boolean()

    @api.onchange("template_id", "section")
    def _onchange_template_section(self):
        for wizard in self:
            if wizard.section == "metadata" and wizard.template_id.target_model_id:
                wizard.source_model_id = wizard.template_id.target_model_id

    @api.onchange("field_id")
    def _onchange_field_id(self):
        for wizard in self:
            if wizard.field_id:
                wizard.field_path = wizard.field_id.name
                wizard.label = wizard.field_id.field_description
                type_map = {
                    "date": "date", "datetime": "date", "monetary": "monetary",
                    "integer": "integer", "float": "float",
                }
                wizard.value_type = type_map.get(wizard.field_id.ttype, "auto")

    def action_add(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Şablon seçilmedi."))
        self.env["rds.template.field"].create(
            {
                "template_id": self.template_id.id,
                "section": self.section,
                "source_model_id": self.source_model_id.id,
                "field_id": self.field_id.id,
                "field_path": self.field_path,
                "label": self.label,
                "sequence": self.sequence,
                "icon_class": self.icon_class,
                "value_type": self.value_type,
                "alignment": self.alignment,
                "width_percent": self.width_percent,
                "hide_if_empty": self.hide_if_empty,
                "bold": self.bold,
                "highlight": self.highlight,
            }
        )
        return {"type": "ir.actions.act_window_close"}
