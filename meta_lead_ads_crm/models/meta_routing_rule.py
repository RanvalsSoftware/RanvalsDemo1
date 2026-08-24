from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class MetaLeadRoutingRule(models.Model):
    _name = "meta.lead.routing.rule"
    _description = "Meta Lead Ads Routing Rule"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    connection_id = fields.Many2one(
        "meta.lead.connection", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="connection_id.company_id", store=True, readonly=True)
    match_field = fields.Selection(
        [
            ("any", "Any Lead"),
            ("form_id", "Form ID"),
            ("campaign_id", "Campaign ID"),
            ("adset_id", "Ad Set ID"),
            ("ad_id", "Ad ID"),
            ("platform", "Platform"),
        ],
        default="form_id",
        required=True,
    )
    match_value = fields.Char(
        help="Exact Meta ID/value. Platform matching is case-insensitive."
    )
    team_id = fields.Many2one(
        "crm.team",
        string="Sales Team",
        check_company=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Salesperson",
        domain="[('share', '=', False), ('active', '=', True)]",
    )

    @api.constrains("match_field", "match_value")
    def _check_match_value(self):
        for rule in self:
            if rule.match_field != "any" and not (rule.match_value or "").strip():
                raise ValidationError(_("A match value is required for this routing rule."))

    @api.constrains("team_id", "user_id", "company_id")
    def _check_assignment_company(self):
        for rule in self:
            if rule.team_id.company_id and rule.team_id.company_id != rule.company_id:
                raise ValidationError(_("The routing sales team belongs to another company."))
            if rule.user_id and (not rule.user_id.active or rule.company_id not in rule.user_id.company_ids):
                raise ValidationError(
                    _("The routing salesperson must be active and have access to the company.")
                )

    def _matches_payload(self, payload):
        self.ensure_one()
        if self.match_field == "any":
            return True
        actual = str(payload.get(self.match_field) or "").strip()
        expected = str(self.match_value or "").strip()
        if self.match_field == "platform":
            return actual.casefold() == expected.casefold()
        return actual == expected


class MetaLeadConnectionRouting(models.Model):
    _inherit = "meta.lead.connection"

    def _routing_assignment(self, payload):
        self.ensure_one()
        team = self.team_id if self.team_id and self.team_id.active else self.env["crm.team"]
        user = self.user_id if self.user_id and self.user_id.active else self.env["res.users"]
        for rule in self.routing_rule_ids.filtered("active").sorted("sequence"):
            if rule._matches_payload(payload):
                if rule.team_id and rule.team_id.active:
                    team = rule.team_id
                if rule.user_id and rule.user_id.active:
                    user = rule.user_id
                break
        if user and self.company_id not in user.company_ids:
            user = self.env["res.users"]
        if team and team.company_id and team.company_id != self.company_id:
            team = self.env["crm.team"]
        return team, user

