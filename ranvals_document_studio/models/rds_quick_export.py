"""One-click, access-controlled document exports for business documents."""

import json
from typing import ClassVar

from odoo import _, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.common import check_record_access

MAX_QUICK_EXPORT_RECORDS = 50
MAX_DATABASE_ID = 2_147_483_647
QUICK_EXPORT_FORMATS = frozenset(("pdf", "docx_editable"))


class RdsQuickExportMixin(models.AbstractModel):
    """Shared implementation for the three supported business models.

    Every public action below still runs on the caller's original recordset
    and never elevates access to business data.
    """

    _name = "rds.quick.export.mixin"
    _description = "DocuCraft Quick Export Mixin"

    _rds_quick_export_group = False

    def _rds_check_quick_export_scope(self):
        if not self:
            raise UserError(_("Select at least one document to download."))
        if len(self) > MAX_QUICK_EXPORT_RECORDS:
            raise UserError(
                _("You can download at most %s documents in one operation.")
                % MAX_QUICK_EXPORT_RECORDS
            )
        if any(
            isinstance(record_id, bool)
            or not isinstance(record_id, int)
            or record_id <= 0
            or record_id > MAX_DATABASE_ID
            for record_id in self.ids
        ):
            raise UserError(_("One of the selected documents has an invalid ID."))
        if self._rds_quick_export_group and not (
            self.env.su or self.env.user.has_group(self._rds_quick_export_group)
        ):
            raise AccessError(_("You do not have permission to export these documents."))

        records = self.exists()
        if len(records) != len(self):
            raise UserError(_("One of the selected documents no longer exists."))
        check_record_access(records)

        companies = records.mapped("company_id")
        if len(companies) != 1:
            raise UserError(
                _(
                    "Select documents from the same company for a bulk download. Download documents from different companies in separate operations."
                )
            )
        if not self.env.su and companies not in self.env.companies:
            raise AccessError(_("You do not have access to documents from this company."))
        return records, companies

    def _rds_validate_quick_export_records(self):
        """Model-specific eligibility hook."""

    def _rds_quick_export_template(self, records, company):
        """Return one common effective template, or an empty recordset.

        Conditional rules are evaluated per business record.  A batch is only
        exported immediately when every item resolves to the same template;
        otherwise the normal chooser is opened so no document silently uses a
        template selected for another customer or condition.
        """
        Template = self.env["rds.template"]
        selected = Template.browse()
        for record in records:
            template = Template.get_default_for(
                records._name,
                company=company,
                record=record,
            )
            if not template or len(template) != 1:
                return Template.browse()
            check_record_access(template)
            if (
                not template.active
                or (template.company_id and template.company_id != company)
                or (
                    template.target_model_id
                    and template.target_model_id.model != records._name
                )
            ):
                return Template.browse()
            selected |= template
            if len(selected) > 1:
                return Template.browse()
        return selected

    def _rds_quick_export_language(self, records, mode="company"):
        """Use the export wizard's single source of truth for automatic languages."""
        company = getattr(records[:1], "company_id", False) or self.env.company
        return (
            self.env["rds.export.wizard"]
            .with_company(company)
            ._automatic_language(records, mode)
        )

    def _rds_open_quick_export_selector(self, records, output_format):
        action = self.env["rds.export.wizard"].open_for_records(records)
        action["context"] = dict(
            action.get("context", {}),
            default_output_format=output_format,
        )
        return action

    def _rds_quick_export(self, output_format):
        if output_format not in QUICK_EXPORT_FORMATS:
            raise ValidationError(_("Unsupported quick-download format."))
        records, company = self._rds_check_quick_export_scope()
        records._rds_validate_quick_export_records()
        template = self._rds_quick_export_template(records, company)
        if not template:
            return self._rds_open_quick_export_selector(records, output_format)

        language_mode = "company"
        language = self._rds_quick_export_language(records, language_mode)
        wizard = self.env["rds.export.wizard"].with_company(company).create(
            {
                "res_model": records._name,
                "res_ids_json": json.dumps(records.ids),
                "template_id": template.id,
                "output_format": output_format,
                "language_mode": language_mode,
                "language_id": language.id or False,
            }
        )
        return wizard.action_export()

    def action_rds_download_pdf(self):
        """Download the effective DocuCraft design as PDF."""
        return self._rds_quick_export("pdf")

    def action_rds_download_editable_word(self):
        """Download the effective design as a genuinely editable Word file."""
        return self._rds_quick_export("docx_editable")


class SaleOrderQuickExport(models.Model):
    _name = "sale.order"
    _inherit: ClassVar[list[str]] = ["sale.order", "rds.quick.export.mixin"]

    _rds_quick_export_group = "sales_team.group_sale_salesman"


class AccountMoveQuickExport(models.Model):
    _name = "account.move"
    _inherit: ClassVar[list[str]] = ["account.move", "rds.quick.export.mixin"]

    _rds_quick_export_group = "account.group_account_invoice"

    def _rds_validate_quick_export_records(self):
        supported_types = frozenset(
            ("out_invoice", "out_refund", "in_invoice", "in_refund")
        )
        if any(move.move_type not in supported_types for move in self):
            raise UserError(
                _(
                    "Quick download is available only for customer/vendor invoices and refunds."
                )
            )


class PurchaseOrderQuickExport(models.Model):
    _name = "purchase.order"
    _inherit: ClassVar[list[str]] = ["purchase.order", "rds.quick.export.mixin"]

    _rds_quick_export_group = "purchase.group_purchase_user"
