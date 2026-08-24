from odoo import api, fields, models

from ..tools import normalize_email, normalize_phone


class CrmLead(models.Model):
    _inherit = "crm.lead"

    meta_connection_id = fields.Many2one(
        "meta.lead.connection", string="Meta Connection", copy=False, index=True, readonly=True
    )
    meta_leadgen_id = fields.Char(
        string="Meta Lead ID", copy=False, index=True, readonly=True
    )
    meta_page_id = fields.Char(string="Meta Page ID", copy=False, index=True, readonly=True)
    meta_page_name = fields.Char(string="Meta Page", copy=False, readonly=True)
    meta_form_id = fields.Char(string="Meta Form ID", copy=False, index=True, readonly=True)
    meta_form_name = fields.Char(string="Meta Form", copy=False, readonly=True)
    meta_campaign_id = fields.Char(
        string="Meta Campaign ID", copy=False, index=True, readonly=True
    )
    meta_campaign_name = fields.Char(string="Meta Campaign", copy=False, readonly=True)
    meta_adset_id = fields.Char(string="Meta Ad Set ID", copy=False, index=True, readonly=True)
    meta_adset_name = fields.Char(string="Meta Ad Set", copy=False, readonly=True)
    meta_ad_id = fields.Char(string="Meta Ad ID", copy=False, index=True, readonly=True)
    meta_ad_name = fields.Char(string="Meta Ad", copy=False, readonly=True)
    meta_platform = fields.Char(string="Meta Platform", copy=False, index=True, readonly=True)
    meta_created_time = fields.Datetime(string="Submitted on Meta", copy=False, readonly=True)
    meta_is_organic = fields.Boolean(string="Organic Meta Lead", copy=False, readonly=True)

    meta_normalized_email = fields.Char(
        compute="_compute_meta_normalized_contacts", store=True, index=True, copy=False
    )
    meta_normalized_phone = fields.Char(
        compute="_compute_meta_normalized_contacts", store=True, index=True, copy=False
    )
    meta_event_ids = fields.One2many("meta.lead.event", "crm_lead_id", string="Meta Events")
    meta_event_count = fields.Integer(compute="_compute_meta_event_count")

    @api.depends("email_from", "phone")
    def _compute_meta_normalized_contacts(self):
        for lead in self:
            lead.meta_normalized_email = normalize_email(lead.email_from)
            lead.meta_normalized_phone = normalize_phone(lead.phone)

    def _compute_meta_event_count(self):
        grouped = self.env["meta.lead.event"].read_group(
            [("crm_lead_id", "in", self.ids)], ["crm_lead_id"], ["crm_lead_id"]
        )
        counts = {item["crm_lead_id"][0]: item["crm_lead_id_count"] for item in grouped}
        for lead in self:
            lead.meta_event_count = counts.get(lead.id, 0)

    def action_open_meta_events(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "meta_lead_ads_crm.action_meta_lead_event"
        )
        action["domain"] = [("crm_lead_id", "=", self.id)]
        action["context"] = {"create": False}
        return action
