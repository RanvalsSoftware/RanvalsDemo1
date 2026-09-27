import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


MAX_FIELD_PATH_DEPTH = 4
UNSUPPORTED_TERMINAL_FIELD_TYPES = {"binary"}


class RdsTemplateField(models.Model):
    _name = "rds.template.field"
    _description = "Document Template Field"
    _order = "section, sequence, id"

    template_id = fields.Many2one("rds.template", required=True, ondelete="cascade", index=True)
    active = fields.Boolean(default=True)
    section = fields.Selection(
        [("metadata", "Document Detail Card"), ("line", "Line / Table Column")],
        required=True,
        default="metadata",
    )
    source_model_id = fields.Many2one(
        "ir.model",
        string="Source Model",
        required=True,
        domain="[('transient', '=', False)]",
        ondelete="cascade",
    )
    source_model_name = fields.Char(related="source_model_id.model", store=True, readonly=True)
    field_id = fields.Many2one(
        "ir.model.fields",
        string="Odoo Field",
        domain="[('model_id', '=', source_model_id)]",
        ondelete="set null",
    )
    field_path = fields.Char(
        required=True,
        help="For a direct field, use e.g. name; for a related field, use e.g. partner_id.vat. Up to 4 levels are supported, and intermediate relations must be Many2one.",
    )
    label = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    icon_class = fields.Char(default="fa-circle-o")
    value_type = fields.Selection(
        [
            ("auto", "Automatic"),
            ("text", "Text"),
            ("date", "Date"),
            ("monetary", "Monetary"),
            ("percentage", "Percentage"),
            ("integer", "Integer"),
            ("float", "Decimal Number"),
        ],
        default="auto",
        required=True,
    )
    alignment = fields.Selection(
        [("left", "Left"), ("center", "Center"), ("right", "Right")],
        default="left",
        required=True,
    )
    width_percent = fields.Integer(default=16)
    hide_if_empty = fields.Boolean(default=True)
    bold = fields.Boolean()
    highlight = fields.Boolean()

    @api.onchange("field_id")
    def _onchange_field_id(self):
        for record in self:
            if record.field_id:
                record.field_path = record.field_id.name
                if not record.label:
                    record.label = record.field_id.field_description
                field_type = record.field_id.ttype
                if field_type in ("date", "datetime"):
                    record.value_type = "date"
                elif field_type in ("monetary",):
                    record.value_type = "monetary"
                elif field_type in ("integer",):
                    record.value_type = "integer"
                elif field_type in ("float",):
                    record.value_type = "float"

    @api.constrains("field_path", "source_model_id", "field_id")
    def _check_field_path(self):
        for record in self:
            raw_path = record.field_path or ""
            parts = raw_path.split(".")
            if (
                not raw_path
                or len(parts) > MAX_FIELD_PATH_DEPTH
                or any(not part for part in parts)
            ):
                raise ValidationError(_("The field path must contain between 1 and 4 field levels."))
            model_name = record.source_model_id.model
            if not model_name or model_name not in self.env.registry.models:
                raise ValidationError(_("The source Odoo model is no longer available."))
            if record.field_id and record.field_id.model_id != record.source_model_id:
                raise ValidationError(_("The selected Odoo field does not belong to the source model."))
            model = self.env[model_name]
            current_model = model
            for index, part in enumerate(parts):
                if part.startswith("_") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", part):
                    raise ValidationError(_("Invalid field path: %s") % record.field_path)
                if index == 0 and record.field_id and record.field_id.name != part:
                    raise ValidationError(_("The selected Odoo field must match the first part of the field path."))
                if part not in current_model._fields:
                    raise ValidationError(_("Field %s was not found on model %s.") % (part, current_model._name))
                field = current_model._fields[part]
                if index < len(parts) - 1:
                    # Intermediate traversal is deliberately restricted to many2one.
                    # x2many values are supported only as the terminal field and are
                    # rendered as a comma-separated display-name list.
                    if field.type != "many2one" or not field.comodel_name:
                        raise ValidationError(
                            _("%s is not a Many2one field that can be used in the middle of a related path.")
                            % part
                        )
                    if field.comodel_name not in self.env.registry.models:
                        raise ValidationError(
                            _("The related model of field %s is no longer available.") % part
                        )
                    current_model = self.env[field.comodel_name]
                else:
                    if field.type in UNSUPPORTED_TERMINAL_FIELD_TYPES:
                        raise ValidationError(
                            _("Fields of type %s cannot be used as document text.") % field.type
                        )
                    if getattr(field, "exportable", True) is False:
                        raise ValidationError(
                            _("Field %s is marked by Odoo as non-exportable.") % part
                        )

    @api.constrains("template_id", "section", "source_model_id")
    def _check_source_scope(self):
        for record in self:
            target = record.template_id.target_model_id
            if record.section == "metadata" and target and record.source_model_id != target:
                raise ValidationError(
                    _("The detail-card field's source model must match the template's target model.")
                )

    @api.constrains("label", "icon_class")
    def _check_display_properties(self):
        for record in self:
            if len(record.label or "") > 200:
                raise ValidationError(_("The field label can contain at most 200 characters."))
            if record.icon_class and not re.fullmatch(r"fa-[a-z0-9-]+", record.icon_class):
                raise ValidationError(
                    _("The icon class must start with fa- and contain only lowercase letters, digits, and hyphens.")
                )

    @api.constrains("width_percent")
    def _check_width(self):
        for record in self:
            if record.width_percent < 5 or record.width_percent > 100:
                raise ValidationError(_("Column width must be between 5 and 100 percent."))


    @api.constrains(
        "template_id",
        "section",
        "source_model_id",
        "active",
        "width_percent",
    )
    def _check_layout_capacity(self):
        """Keep the one-row PDF grids readable and prevent invalid table widths."""
        for record in self:
            siblings = record.template_id.field_ids.filtered(
                lambda item, current=record: item.active
                and item.section == current.section
                and item.source_model_id == current.source_model_id
            )
            if record.section == "metadata" and len(siblings) > 8:
                raise ValidationError(_("A model can use at most 8 active detail cards."))
            if record.section == "line":
                total_width = sum(siblings.mapped("width_percent"))
                if total_width > 100:
                    raise ValidationError(
                        _("The total width of active table columns cannot exceed 100 percent. Current total: %s")
                        % total_width
                    )
