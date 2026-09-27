from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..tools.common import check_record_access

SUPPORTED_RULE_MODELS = {"sale.order", "account.move", "purchase.order"}


class RdsTemplateRule(models.Model):
    _name = "rds.template.rule"
    _description = "DocuCraft Conditional Template Rule"
    _order = "priority desc, sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    priority = fields.Integer(default=10, help="Higher values are evaluated first.")
    sequence = fields.Integer(default=10)
    template_id = fields.Many2one("rds.template", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(
        "res.company",
        help="If empty, the rule can be evaluated in every company where the template is available.",
        ondelete="cascade",
    )
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        domain="[('model', 'in', ['sale.order', 'account.move', 'purchase.order'])]",
        ondelete="cascade",
    )
    partner_id = fields.Many2one("res.partner", ondelete="cascade")
    country_id = fields.Many2one("res.country", ondelete="cascade")
    currency_id = fields.Many2one("res.currency", ondelete="cascade")
    min_total = fields.Monetary(currency_field="currency_id")
    max_total = fields.Monetary(currency_field="currency_id")

    @api.constrains("model_id", "template_id", "company_id", "min_total", "max_total", "currency_id")
    def _check_safe_scope(self):
        for rule in self:
            if rule.model_id.model not in SUPPORTED_RULE_MODELS:
                raise ValidationError(_("Conditional template rules are available only for sales, invoice and purchase documents."))
            target = rule.template_id.target_model_id.model
            if target and target != rule.model_id.model:
                raise ValidationError(_("The rule model must match the template target model."))
            if rule.company_id and rule.template_id.company_id and rule.company_id != rule.template_id.company_id:
                raise ValidationError(_("The rule company must match the template company."))
            if (rule.min_total or rule.max_total) and not rule.currency_id:
                raise ValidationError(_("A currency is required for amount conditions."))
            if rule.min_total < 0 or rule.max_total < 0:
                raise ValidationError(_("Amount limits cannot be negative."))
            if rule.min_total and rule.max_total and rule.min_total > rule.max_total:
                raise ValidationError(_("The minimum amount cannot exceed the maximum amount."))

    def _matches_record(self, record, company):
        self.ensure_one()
        if self.company_id and self.company_id != company:
            return False
        record_company = record.company_id if "company_id" in record._fields else company
        if record_company != company:
            return False
        partner = record.partner_id if "partner_id" in record._fields else self.env["res.partner"]
        if self.partner_id and partner.commercial_partner_id != self.partner_id.commercial_partner_id:
            return False
        if self.country_id and partner.country_id != self.country_id:
            return False
        currency = record.currency_id if "currency_id" in record._fields else self.env["res.currency"]
        if self.currency_id and currency != self.currency_id:
            return False
        total = record.amount_total if "amount_total" in record._fields else 0.0
        if self.min_total and total < self.min_total:
            return False
        return not (self.max_total and total > self.max_total)


class RdsTemplateRuleResolver(models.Model):
    _inherit = "rds.template"

    rule_ids = fields.One2many("rds.template.rule", "template_id", string="Automatic Selection Rules")

    @api.model
    def get_default_for(self, model_name, company=None, record=None):
        company = company or self.env.company
        company.ensure_one()
        if record is not None:
            if not record or len(record) != 1 or record._name != model_name or model_name not in SUPPORTED_RULE_MODELS:
                raise ValidationError(_("Conditional template selection requires exactly one compatible document record."))
            record = record.exists()
            if not record:
                raise AccessError(_("You do not have access to the source document."))
            check_record_access(record)
            if company not in self.env.companies:
                raise AccessError(_("You do not have access to the company."))
            if "company_id" in record._fields and record.company_id != company:
                raise ValidationError(_("The source document company does not match the template-selection company."))
            model_record = self.env["ir.model"]._get(model_name)
            rules = self.env["rds.template.rule"].search([
                ("active", "=", True),
                ("model_id", "=", model_record.id),
                "|", ("company_id", "=", False), ("company_id", "=", company.id),
                ("template_id.active", "=", True),
                ("template_id.target_model_id", "in", [False, model_record.id]),
                "|", ("template_id.company_id", "=", False), ("template_id.company_id", "=", company.id),
            ], order="priority desc, sequence, id")
            for rule in rules:
                if rule._matches_record(record, company):
                    return rule.template_id
        return super().get_default_for(model_name, company=company)
