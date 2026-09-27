from odoo import _, api, fields, models
from odoo.addons.ranvals_document_studio.tools.common import check_record_access
from odoo.exceptions import ValidationError


RDS_STUDIO_MODELS = frozenset(("sale.order", "account.move", "purchase.order"))


class RdsReportDesign(models.Model):
    _name = "rds.report.design"
    _description = "Document Design by Report and Company"
    _rec_name = "report_id"

    report_id = fields.Many2one("ir.actions.report", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", index=True)
    template_id = fields.Many2one("rds.template", required=True, ondelete="cascade")

    if hasattr(models, "Constraint"):
        _report_company_unique = models.Constraint(
            "UNIQUE(report_id, company_id)",
            "Only one design can be selected per report and company.",
        )
    else:  # Odoo 17/18
        _sql_constraints = [
            (
                "rds_report_design_company_unique",
                "UNIQUE(report_id, company_id)",
                "Only one design can be selected per report and company.",
            )
        ]

    @api.constrains("report_id", "company_id", "template_id")
    def _check_design_scope(self):
        for row in self:
            # ``ir.actions.report`` read ACLs do not enforce its optional
            # ``group_ids`` restriction.  Use the same check as the Studio
            # endpoint so a manager cannot bypass a report's groups by
            # creating a binding directly over RPC.
            row.report_id._rds_check_report_access()
            check_record_access(row.template_id)
            if row.report_id.model not in RDS_STUDIO_MODELS or row.report_id.report_type not in ("qweb-pdf", "qweb-html"):
                raise ValidationError(_("This connector supports only sales, invoice, and purchase QWeb reports."))
            if not row.template_id.active:
                raise ValidationError(_("An archived template cannot be selected."))
            if row.template_id.company_id and row.template_id.company_id != row.company_id:
                raise ValidationError(_("The template and report selection must belong to the same company."))
            target = row.template_id.target_model_id.model
            if target and target != row.report_id.model:
                raise ValidationError(_("The selected template is not suitable for this document model."))

    def unlink(self):
        # Constraints are not run on unlink.  Keep direct ORM calls aligned
        # with ``rds_apply_design`` and prevent deletion of bindings for
        # reports the caller is not allowed to use.
        for row in self:
            row.report_id._rds_check_report_access()
        return super().unlink()
