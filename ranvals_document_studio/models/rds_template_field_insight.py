from odoo import _, api, fields, models


class RdsTemplateFieldInsight(models.Model):
    _inherit = "rds.template.field"

    technical_risk = fields.Selection(
        [("safe", "Hızlı"), ("related", "İlişkili"), ("costly", "Dikkat"), ("restricted", "Kısıtlı")],
        compute="_compute_technical_insight",
        string="Alan Profili",
    )
    technical_risk_help = fields.Text(compute="_compute_technical_insight", string="Teknik Açıklama")

    @api.depends("field_path", "source_model_id")
    def _compute_technical_insight(self):
        for record in self:
            risk = "safe"
            messages = []
            model_name = record.source_model_id.model
            current_model = self.env[model_name] if model_name in self.env.registry.models else False
            parts = (record.field_path or "").split(".")
            terminal = False
            for index, part in enumerate(parts):
                if not current_model or part not in current_model._fields:
                    risk = "restricted"
                    messages.append(_("Alan yolu artık çözümlenemiyor."))
                    break
                field = current_model._fields[part]
                terminal = field
                if getattr(field, "groups", None):
                    risk = "restricted"
                    messages.append(_("Bu alan grup tabanlı erişim kısıtına sahiptir; belge motoru mevcut kullanıcı yetkisini uygular."))
                if index < len(parts) - 1:
                    if risk != "restricted":
                        risk = "related"
                    messages.append(_("İlişkili kayıt üzerinden okunur; doğrudan alana göre daha fazla sorgu oluşturabilir."))
                    current_model = self.env[field.comodel_name] if field.comodel_name in self.env.registry.models else False
            if terminal and terminal.type in ("one2many", "many2many"):
                if risk != "restricted":
                    risk = "costly"
                messages.append(_("Çoklu ilişki en fazla 100 görünen değerle sınırlandırılır ve büyük kayıtlarda pahalı olabilir."))
            if terminal and getattr(terminal, "compute", None) and not getattr(terminal, "store", False):
                if risk not in ("restricted", "costly"):
                    risk = "costly"
                messages.append(_("Saklanmayan hesaplanan alan çıktı sırasında yeniden hesaplanır."))
            if not messages:
                messages.append(_("Doğrudan ve düşük maliyetli alan okuması."))
            record.technical_risk = risk
            record.technical_risk_help = " ".join(dict.fromkeys(messages))
