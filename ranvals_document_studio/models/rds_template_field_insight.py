from odoo import _, api, fields, models


class RdsTemplateFieldInsight(models.Model):
    _inherit = "rds.template.field"

    technical_risk = fields.Selection(
        [("safe", "Fast"), ("related", "Related"), ("costly", "Caution"), ("restricted", "Restricted")],
        compute="_compute_technical_insight",
        string="Field Profile",
    )
    technical_risk_help = fields.Text(compute="_compute_technical_insight", string="Technical Explanation")

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
                    messages.append(_("The field path can no longer be resolved."))
                    break
                field = current_model._fields[part]
                terminal = field
                if getattr(field, "groups", None):
                    risk = "restricted"
                    messages.append(_("This field has group-based access restrictions; the document engine applies the current user permissions."))
                if index < len(parts) - 1:
                    if risk != "restricted":
                        risk = "related"
                    messages.append(_("Read through a related record; it may generate more queries than a direct field."))
                    current_model = self.env[field.comodel_name] if field.comodel_name in self.env.registry.models else False
            if terminal and terminal.type in ("one2many", "many2many"):
                if risk != "restricted":
                    risk = "costly"
                messages.append(_("Multi-value relations are limited to 100 visible values and may be expensive on large records."))
            if terminal and getattr(terminal, "compute", None) and not getattr(terminal, "store", False):
                if risk not in ("restricted", "costly"):
                    risk = "costly"
                messages.append(_("A non-stored computed field is recalculated during export."))
            if not messages:
                messages.append(_("Direct low-cost field read."))
            record.technical_risk = risk
            record.technical_risk_help = " ".join(dict.fromkeys(messages))
