from odoo import models


class RdsTemplate(models.Model):
    _inherit = "rds.template"
    _description = "DocuCraft Document Template"


class RdsDashboard(models.AbstractModel):
    _inherit = "rds.dashboard"
    _description = "DocuCraft Dashboard"
