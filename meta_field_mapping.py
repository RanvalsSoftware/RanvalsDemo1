from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..tools import normalize_meta_field_name


class MetaLeadFieldMapping(models.Model):
    _name = "meta.lead.field.mapping"
    _description = "Meta Lead Ads Field Mapping"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    connection_id = fields.Many2one(
        "meta.lead.connection", required=True, ondelete="cascade", index=True
    )
    meta_field_name = fields.Char(
        required=True,
        help="Field name delivered in Meta field_data, for example work_email.",
    )
    meta_field_name_normalized = fields.Char(
        compute="_compute_normalized_name", store=True, index=True
    )
    target_field = fields.Selection(
        [
            ("contact_name", "Contact Name"),
            ("email_from", "Email"),
            ("phone", "Phone"),
            ("partner_name", "Company Name"),
            ("function", "Job Position"),
            ("street", "Street"),
            ("city", "City"),
            ("zip", "ZIP/Postal Code"),
        ],
        required=True,
    )

    if hasattr(models, "Constraint"):
        _mapping_unique = models.Constraint(
            "UNIQUE(connection_id, meta_field_name_normalized)",
            "A Meta field can only be mapped once per connection.",
        )
    else:
        _sql_constraints = [
            (
                "connection_field_unique",
                "unique(connection_id, meta_field_name_normalized)",
                "A Meta field can only be mapped once per connection.",
            )
        ]

    @api.depends("meta_field_name")
    def _compute_normalized_name(self):
        for mapping in self:
            mapping.meta_field_name_normalized = normalize_meta_field_name(mapping.meta_field_name)

    @api.constrains("meta_field_name")
    def _check_meta_field_name(self):
        for mapping in self:
            if not mapping.meta_field_name_normalized:
                raise ValidationError(_("Meta field name cannot be empty."))

