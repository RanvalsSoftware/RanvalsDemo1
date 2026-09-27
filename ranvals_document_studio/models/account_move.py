from odoo import models, _
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

from odoo.addons.ranvals_document_studio.tools.common import lang_code, plain_text, tr_label


class AccountMove(models.Model):
    _inherit = "account.move"

    _RDS_EXPORT_METADATA_FIELDS = (
        {"key": "builtin:metadata:name", "field_path": "name", "label_key": "document_no", "alignment": "center"},
        {"key": "builtin:metadata:invoice_date", "field_path": "invoice_date", "label_key": "date", "alignment": "center"},
        {"key": "builtin:metadata:invoice_date_due", "field_path": "invoice_date_due", "label_key": "due_date", "alignment": "center"},
        {"key": "builtin:metadata:invoice_user_id", "field_path": "invoice_user_id", "label_key": "salesperson", "alignment": "center", "fallback_paths": ("user_id",)},
        {"key": "builtin:metadata:ref", "field_path": "ref", "label_key": "reference", "alignment": "center"},
        {"key": "builtin:metadata:invoice_payment_term_id", "field_path": "invoice_payment_term_id", "label_key": "payment_terms", "alignment": "center"},
        {"key": "builtin:metadata:partner_shipping_id", "field_path": "partner_shipping_id", "alignment": "center", "context_only": True},
    )
    _RDS_EXPORT_LINE_FIELDS = (
        {"key": "builtin:line:description", "field_path": "name", "label_key": "description", "alignment": "left"},
        {"key": "builtin:line:quantity", "field_path": "quantity", "label_key": "quantity", "alignment": "right"},
        {"key": "builtin:line:unit_price", "field_path": "price_unit", "label_key": "unit_price", "alignment": "right"},
        {"key": "builtin:line:discount", "field_path": "discount", "label_key": "discount", "alignment": "right"},
        {"key": "builtin:line:tax", "field_path": "tax_ids", "label_key": "tax", "alignment": "right"},
        {"key": "builtin:line:amount", "field_path": "price_subtotal", "label_key": "amount", "alignment": "right"},
    )

    def action_open_rds_export(self):
        supported_types = ("out_invoice", "out_refund", "in_invoice", "in_refund")
        unsupported = self.filtered(lambda move: move.move_type not in supported_types)
        if unsupported:
            raise UserError(_("DocuCraft yalnız fatura ve iade faturalarında kullanılabilir."))
        return self.env["rds.export.wizard"].open_for_records(self)

    def _rds_export_field_config(self):
        return {
            "metadata": {
                "model": "account.move",
                "relation_field": False,
                "view_id": self.env.ref("account.view_move_form"),
                "builtin_fields": self._RDS_EXPORT_METADATA_FIELDS,
            },
            "line": {
                "model": "account.move.line",
                "relation_field": "invoice_line_ids",
                "view_id": self.env.ref("account.view_move_form"),
                "builtin_fields": self._RDS_EXPORT_LINE_FIELDS,
            },
        }

    def _rds_export_line_records(self):
        self.ensure_one()
        return self._get_move_lines_to_report()

    def _rds_report_action_xmlid(self, template=None):
        self.ensure_one()
        return "ranvals_document_studio.action_report_rds_invoice_document"

    def _rds_format_quantity(self, value, lang=None):
        """Format quantities with Odoo's configured Product Unit precision."""
        self.ensure_one()
        localized = self.with_context(lang=lang) if lang else self
        return formatLang(
            localized.env,
            value,
            dp="Product Unit",
        )

    def _rds_format_unit_price(self, value, currency, lang=None):
        """Keep Product Price precision while retaining the document currency."""
        self.ensure_one()
        localized = self.with_context(lang=lang) if lang else self
        number = formatLang(
            localized.env,
            value,
            dp="Product Price",
        )
        if not currency or not currency.symbol:
            return number
        separator = "\N{NO-BREAK SPACE}"
        if currency.position == "before":
            return "%s%s%s" % (currency.symbol, separator, number)
        return "%s%s%s" % (number, separator, currency.symbol)

    def _rds_totals_context(self, template, code, lang=None):
        """Return Odoo 19 tax groups, rounding, total and remaining balance."""
        self.ensure_one()
        tax_totals = self.tax_totals or {}
        totals = []
        has_tax_groups = False

        for subtotal in tax_totals.get("subtotals", []):
            amount = subtotal.get("base_amount_currency")
            if amount is None:
                amount = self.amount_untaxed
            totals.append(
                {
                    "label": subtotal.get("name") or tr_label("subtotal", code),
                    "value": template.format_value(
                        amount, "monetary", self.currency_id, lang
                    ),
                }
            )
            for tax_group in subtotal.get("tax_groups", []):
                has_tax_groups = True
                tax_amount = tax_group.get("tax_amount_currency")
                if tax_amount is None:
                    tax_amount = 0.0
                totals.append(
                    {
                        "label": tax_group.get("group_name")
                        or tr_label("tax_total", code),
                        "value": template.format_value(
                            tax_amount, "monetary", self.currency_id, lang
                        ),
                    }
                )

        if not totals:
            totals.append(
                {
                    "label": tr_label("subtotal", code),
                    "value": template.format_value(
                        self.amount_untaxed, "monetary", self.currency_id, lang
                    ),
                }
            )
        if not has_tax_groups:
            totals.append(
                {
                    "label": tr_label("tax_total", code),
                    "value": template.format_value(
                        self.amount_tax, "monetary", self.currency_id, lang
                    ),
                }
            )
        if "cash_rounding_base_amount_currency" in tax_totals:
            totals.append(
                {
                    "label": self.env._("Rounding"),
                    "value": template.format_value(
                        tax_totals["cash_rounding_base_amount_currency"],
                        "monetary",
                        self.currency_id,
                        lang,
                    ),
                }
            )

        total_amount = tax_totals.get("total_amount_currency")
        if total_amount is None:
            total_amount = self.amount_total

        show_amount_due = (
            self.state == "posted"
            and not self.currency_id.is_zero(self.amount_residual - total_amount)
        )
        totals.append(
            {
                "label": tr_label("grand_total", code),
                "value": template.format_value(
                    total_amount, "monetary", self.currency_id, lang
                ),
                "is_total": not show_amount_due,
            }
        )

        if show_amount_due:
            due_label = self._fields["amount_residual"].get_description(
                self.env, attributes={"string"}
            ).get("string", "Amount Due")
            totals.append(
                {
                    "label": due_label,
                    "value": template.format_value(
                        self.amount_residual, "monetary", self.currency_id, lang
                    ),
                    "is_total": True,
                }
            )
        return totals

    def _rds_bank_context(self, template):
        """Use the bank selected on the invoice and respect payment direction."""
        self.ensure_one()
        if not template.show_bank:
            return {}
        bank = self.partner_bank_id
        if bank:
            bank_record = bank.bank_id
            account_name = (
                getattr(bank, "acc_holder_name", False)
                or bank.partner_id.name
                or self.company_id.name
            )
            return {
                "bank_name": bank_record.name or "",
                "branch": getattr(bank_record, "branch", False) or "",
                "account_name": account_name,
                "iban": bank.acc_number or "",
                "swift": getattr(bank_record, "bic", False)
                or getattr(bank_record, "bank_bic", False)
                or "",
            }
        if self.move_type in ("out_invoice", "in_refund"):
            return template.bank_info(self.company_id, self.currency_id)
        return {}

    @staticmethod
    def _rds_partner_info_text(info):
        return ", ".join(
            part
            for part in [info.get("name"), *(info.get("lines") or [])]
            if part
        )

    def _rds_document_context(self, template, lang=None):
        self.ensure_one()
        resolved_lang = lang or self.partner_id.lang or self.env.user.lang
        move = self.with_context(lang=resolved_lang)
        template = template.with_context(lang=resolved_lang)
        code = lang_code(resolved_lang)
        context = template.base_context(move, lang=resolved_lang)
        is_vendor = move.move_type in ("in_invoice", "in_refund")
        title_key = "refund" if move.move_type in ("out_refund", "in_refund") else "invoice"
        move_type_label = dict(
            move._fields["move_type"]._description_selection(move.env)
        ).get(move.move_type)
        title = move_type_label or tr_label(title_key, code)
        if move.state in ("draft", "cancel"):
            state_label = dict(
                move._fields["state"]._description_selection(move.env)
            ).get(move.state)
            if state_label:
                title = "%s %s" % (state_label, title)
        tax_country = (
            move.tax_country_id
            or move.company_id.account_fiscal_country_id
            or move.company_id.country_id
        )
        context.update(
            {
                "title": title.upper(),
                "number": move.name if move.name and move.name != "/" else (move.ref or "-"),
                "tax_label": tax_country.vat_label or tr_label("tax_no", code),
            }
        )
        context["labels"]["partner_info"] = tr_label("vendor_info" if is_vendor else "customer_info", code)
        context["labels"]["document_info"] = tr_label("document_info", code)
        context["bank_labels"] = {
            key: tr_label(key, code)
            for key in ("bank", "branch", "account_name", "iban", "swift")
        }

        user_field = "invoice_user_id" if "invoice_user_id" in move._fields else "user_id"
        metadata_specs = [
            {
                "label_key": "document_no",
                "value": context["number"],
                "field_path": "name",
                "icon": "fa-file-text-o",
            },
            {"label_key": "date", "path": "invoice_date", "value_type": "date", "icon": "fa-calendar"},
            {"label_key": "due_date", "path": "invoice_date_due", "value_type": "date", "icon": "fa-calendar-check-o"},
            {"label_key": "salesperson", "path": user_field, "icon": "fa-user"},
            {"label_key": "reference", "path": "ref", "icon": "fa-bookmark-o"},
            {"label_key": "payment_terms", "path": "invoice_payment_term_id", "icon": "fa-credit-card"},
        ]
        context["metadata"] = template.get_metadata_context(
            move, metadata_specs, lang=resolved_lang
        )

        shipping_partner = move.partner_shipping_id
        context["shipping_partner_info"] = template.partner_info(shipping_partner)
        custom_metadata = template.has_configured_fields(
            "account.move", "metadata"
        )
        if not custom_metadata and shipping_partner and shipping_partner != move.partner_id:
            shipping_label = move._fields["partner_shipping_id"].get_description(
                move.env, attributes={"string"}
            ).get("string", "Delivery Address")
            shipping_text = self._rds_partner_info_text(
                context["shipping_partner_info"]
            )
            if shipping_text:
                context["metadata"].append(
                    {
                        "label": shipping_label,
                        "value": shipping_text,
                        "icon": "fa-truck",
                        "align": "center",
                        "key": "builtin:metadata:partner_shipping_id",
                        "field_path": "partner_shipping_id",
                    }
                )

        invoice_lines = move._get_move_lines_to_report()
        custom_columns, custom_lines = template.get_custom_line_context(
            invoice_lines,
            currency=move.currency_id,
            lang=resolved_lang,
        )
        custom_line_configured = template.has_configured_fields(
            invoice_lines._name, "line"
        )
        if custom_line_configured:
            columns, lines = custom_columns, custom_lines
        else:
            price_field = (
                "price_total"
                if move.company_price_include == "tax_included"
                else "price_subtotal"
            )
            columns = [
                {"label": tr_label("description", code), "align": "left", "width": 34, "key": "builtin:line:description", "field_path": "name"},
                {"label": tr_label("quantity", code), "align": "right", "width": 12, "key": "builtin:line:quantity", "field_path": "quantity"},
                {"label": tr_label("unit_price", code), "align": "right", "width": 14, "key": "builtin:line:unit_price", "field_path": "price_unit"},
                {"label": tr_label("discount", code), "align": "right", "width": 10, "key": "builtin:line:discount", "field_path": "discount"},
                {"label": tr_label("tax", code), "align": "right", "width": 12, "key": "builtin:line:tax", "field_path": "tax_ids"},
                {"label": tr_label("amount", code), "align": "right", "width": 18, "key": "builtin:line:amount", "field_path": price_field},
            ]
            lines = []
            for line in invoice_lines:
                source_reference = template._export_line_reference(line)
                display_type = getattr(line, "display_type", False)
                if display_type in ("line_section", "line_subsection", "section", "subsection"):
                    lines.append(
                        {
                            "is_section": True,
                            "description": plain_text(line.name),
                            **source_reference,
                        }
                    )
                    continue
                if display_type in ("line_note", "note"):
                    lines.append(
                        {
                            "is_note": True,
                            "description": plain_text(line.name),
                            **source_reference,
                        }
                    )
                    continue
                if display_type not in (False, "product"):
                    continue
                uom = getattr(line, "product_uom_id", False)
                taxes = getattr(line, "tax_ids", self.env["account.tax"])
                tax_text = ", ".join(
                    tax.tax_label or tax.name for tax in taxes if tax.tax_label or tax.name
                )
                quantity_text = "%s %s" % (
                    move._rds_format_quantity(line.quantity, lang=resolved_lang),
                    uom.display_name if uom else "",
                )
                lines.append(
                    {
                        "values": [
                            {"text": plain_text(line.name), "align": "left"},
                            {"text": quantity_text.strip(), "align": "right"},
                            {
                                "text": move._rds_format_unit_price(
                                    line.price_unit,
                                    move.currency_id,
                                    lang=resolved_lang,
                                ),
                                "align": "right",
                            },
                            {
                                "text": template.format_value(
                                    getattr(line, "discount", 0.0),
                                    "percentage",
                                    lang=resolved_lang,
                                ),
                                "align": "right",
                            },
                            {"text": tax_text or "-", "align": "right"},
                            {
                                "text": template.format_value(
                                    line[price_field],
                                    "monetary",
                                    move.currency_id,
                                    resolved_lang,
                                ),
                                "align": "right",
                                "bold": True,
                                "highlight": template.layout_style == "industrial",
                            },
                        ],
                        **source_reference,
                    }
                )
        context["columns"] = columns
        context["lines"] = lines
        context["show_lines"] = bool(columns)
        context["_line_records"] = invoice_lines
        context["totals"] = move._rds_totals_context(
            template, code, lang=resolved_lang
        )
        notes = []
        if template.notes_text:
            notes.extend([line.strip(" •-") for line in plain_text(template.notes_text).splitlines() if line.strip()])
        if getattr(move, "narration", False):
            notes.extend([line.strip(" •-") for line in plain_text(move.narration).splitlines() if line.strip()])
        if move.invoice_payment_term_id and move.invoice_payment_term_id.note:
            notes.extend(
                line.strip(" •-")
                for line in plain_text(
                    move.invoice_payment_term_id.note
                ).splitlines()
                if line.strip()
            )
        context["notes"] = notes
        context["bank"] = move._rds_bank_context(template)
        field_specs = move.env.context.get("rds_export_field_specs")
        if field_specs is not None:
            template.apply_export_field_specs(
                context,
                move,
                field_specs,
                lang=resolved_lang,
            )
        return context
