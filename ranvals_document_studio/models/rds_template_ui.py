from odoo import api, fields, models, _


STYLE_PRESETS = {
    "beauty": {
        "primary_color": "#7A1020",
        "secondary_color": "#FFF9F5",
        "accent_color": "#B97865",
        "text_color": "#332B2B",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "construction": {
        "primary_color": "#0B2A50",
        "secondary_color": "#F5F7F9",
        "accent_color": "#F36B21",
        "text_color": "#24313D",
        "heading_font": "technical",
        "body_font": "sans",
    },
    "technology": {
        "primary_color": "#155BD7",
        "secondary_color": "#F4F8FF",
        "accent_color": "#10B9E8",
        "text_color": "#17263B",
        "heading_font": "sans",
        "body_font": "sans",
    },
    "industrial": {
        "primary_color": "#123865",
        "secondary_color": "#F3F6F9",
        "accent_color": "#6C8FB8",
        "text_color": "#1E2E3E",
        "heading_font": "technical",
        "body_font": "sans",
    },
    "eco": {
        "primary_color": "#245D46",
        "secondary_color": "#F2F8F4",
        "accent_color": "#8FAE77",
        "text_color": "#25352E",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "furniture": {
        "primary_color": "#6B4226",
        "secondary_color": "#FBF7F2",
        "accent_color": "#B9845A",
        "text_color": "#352A24",
        "heading_font": "editorial",
        "body_font": "sans",
    },
    "noir_executive": {
        "primary_color": "#111827",
        "secondary_color": "#F8FAFC",
        "accent_color": "#C8A96B",
        "text_color": "#1F2937",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "royal_ledger": {
        "primary_color": "#14213D",
        "secondary_color": "#F8F5EE",
        "accent_color": "#C49A44",
        "text_color": "#202838",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "swiss_grid": {
        "primary_color": "#111111",
        "secondary_color": "#F7F7F5",
        "accent_color": "#E53935",
        "text_color": "#222222",
        "heading_font": "sans",
        "body_font": "sans",
    },
    "arctic_minimal": {
        "primary_color": "#234E70",
        "secondary_color": "#F5FAFD",
        "accent_color": "#38BDF8",
        "text_color": "#1E293B",
        "heading_font": "sans",
        "body_font": "sans",
    },
    "indigo_flow": {
        "primary_color": "#312E81",
        "secondary_color": "#F5F3FF",
        "accent_color": "#8B5CF6",
        "text_color": "#1F2937",
        "heading_font": "sans",
        "body_font": "sans",
    },
    "emerald_ledger": {
        "primary_color": "#064E3B",
        "secondary_color": "#F2F8F5",
        "accent_color": "#C6A15B",
        "text_color": "#26352E",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "sandstone_classic": {
        "primary_color": "#4A4038",
        "secondary_color": "#FBF7EF",
        "accent_color": "#B77945",
        "text_color": "#302B27",
        "heading_font": "serif",
        "body_font": "serif",
    },
    "graphite_copper": {
        "primary_color": "#252A2E",
        "secondary_color": "#F5F2EE",
        "accent_color": "#B56E4A",
        "text_color": "#252A2E",
        "heading_font": "technical",
        "body_font": "sans",
    },
}


class RdsTemplate(models.Model):
    _inherit = "rds.template"

    design_preview_html = fields.Html(
        compute="_compute_design_preview_html",
        sanitize=False,
        string="Live Design Preview",
    )

    @api.onchange("layout_style")
    def _onchange_layout_style_design(self):
        """Apply the selected sector preset immediately in the form editor."""
        for record in self:
            preset = STYLE_PRESETS.get(record.layout_style)
            if preset:
                record.update(dict(preset))

    def action_apply_style_preset(self):
        """Re-apply the current style's official palette and typography."""
        self.ensure_one()
        preset = STYLE_PRESETS.get(self.layout_style)
        if preset:
            self.write(dict(preset))
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_apply_design(self):
        """The form controller saves dirty values before object buttons run."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Design saved"),
                "message": _("Your color, font, and copy settings will be used in document exports."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    @api.depends(
        "name",
        "layout_style",
        "primary_color",
        "secondary_color",
        "accent_color",
        "text_color",
        "heading_font",
        "body_font",
        "logo_height_mm",
        "tagline",
        "footer_text",
        "show_company",
        "show_partner",
        "show_metadata",
        "show_notes",
        "show_bank",
        "show_footer",
        "company_id",
        "company_id.partner_id.lang",
        "target_model_id",
    )
    @api.depends_context("lang", "company")
    def _compute_design_preview_html(self):
        for record in self:
            # Use the exact same localized QWeb router as the template preview
            # and exported documents.  This keeps every layout, block switch,
            # company language and target-model title in one rendering path.
            record.design_preview_html = record._rds_dynamic_preview_html()
