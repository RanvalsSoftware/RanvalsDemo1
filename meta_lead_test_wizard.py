import json
import uuid
from datetime import datetime, timezone

from odoo import _, fields, models
from odoo.exceptions import UserError


class MetaLeadTestWizard(models.TransientModel):
    _name = "meta.lead.test.wizard"
    _description = "Create Test Meta Lead"

    connection_id = fields.Many2one("meta.lead.connection", required=True)
    full_name = fields.Char(required=True, default="Test Meta Lead")
    email = fields.Char(default="meta-test@example.com")
    phone = fields.Char(default="+90 555 000 00 00")
    company_name = fields.Char(default="Test Company")
    campaign_id = fields.Char(default="TEST-CAMPAIGN")
    campaign_name = fields.Char(default="Test Campaign")
    adset_id = fields.Char(default="TEST-ADSET")
    adset_name = fields.Char(default="Test Ad Set")
    ad_id = fields.Char(default="TEST-AD")
    ad_name = fields.Char(default="Test Ad")
    form_id = fields.Char(default="TEST-FORM")
    form_name = fields.Char(default="Test Instant Form")
    platform = fields.Selection(
        [("facebook", "Facebook"), ("instagram", "Instagram")],
        required=True,
        default="instagram",
    )
    custom_answers = fields.Text(
        default="interested_product=Odoo CRM\npreferred_contact_time=Morning",
        help="One question=value pair per line. These answers are preserved in the CRM description.",
    )

    def _field_data(self):
        self.ensure_one()
        field_data = []
        for name, value in (
            ("full_name", self.full_name),
            ("email", self.email),
            ("phone_number", self.phone),
            ("company_name", self.company_name),
        ):
            if value:
                field_data.append({"name": name, "values": [value]})
        for line in (self.custom_answers or "").splitlines():
            if not line.strip() or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() and value.strip():
                field_data.append({"name": name.strip(), "values": [value.strip()]})
        return field_data

    def action_create_test_lead(self):
        self.ensure_one()
        leadgen_id = f"TEST-{uuid.uuid4().hex.upper()}"
        payload = {
            "id": leadgen_id,
            "created_time": datetime.now(timezone.utc).isoformat(),
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "adset_id": self.adset_id,
            "adset_name": self.adset_name,
            "ad_id": self.ad_id,
            "ad_name": self.ad_name,
            "form_id": self.form_id,
            "form_name": self.form_name,
            "platform": self.platform,
            "is_organic": False,
            "field_data": self._field_data(),
        }
        event = self.env["meta.lead.event"].sudo().create(
            {
                "connection_id": self.connection_id.id,
                "meta_leadgen_id": leadgen_id,
                "page_id": self.connection_id.page_id,
                "form_id": self.form_id,
                "form_name": self.form_name,
                "ad_id": self.ad_id,
                "is_test": True,
                "lead_payload": json.dumps(payload, ensure_ascii=False),
            }
        )
        lead = event._process_one()
        if not lead:
            raise UserError(event.last_error or _("The test lead could not be created."))
        return {
            "type": "ir.actions.act_window",
            "name": lead.display_name,
            "res_model": "crm.lead",
            "res_id": lead.id,
            "view_mode": "form",
            "target": "current",
        }

