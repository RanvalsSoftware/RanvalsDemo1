from odoo import models
from odoo.tools.misc import formatLang

from odoo.addons.ranvals_document_studio.tools.common import lang_code, plain_text, tr_label


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def action_open_rds_export(self):
        return self.env["rds.export.wizard"].open_for_records(self)

    def _rds_report_action_xmlid(self, template=None):
        self.ensure_one()
        return "ranvals_document_studio.action_report_rds_purchase_document"

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
        """Return Odoo 19 tax groups and cash rounding without losing totals."""
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
        totals.append(
            {
                "label": tr_label("grand_total", code),
                "value": template.format_value(
                    total_amount, "monetary", self.currency_id, lang
                ),
                "is_total": True,
            }
        )
        return totals

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
        order = self.with_context(lang=resolved_lang)
        template = template.with_context(lang=resolved_lang)
        code = lang_code(resolved_lang)
        context = template.base_context(order, lang=resolved_lang)
        title_key = "purchase_quote" if order.state in ("draft", "sent", "to approve") else "purchase_order"
        title = tr_label(title_key, code)
        if order.state == "cancel":
            state_label = dict(
                order._fields["state"]._description_selection(order.env)
            ).get(order.state)
            if state_label:
                title = "%s %s" % (state_label, title)
        tax_country = (
            order.tax_country_id
            or order.company_id.account_fiscal_country_id
            or order.company_id.country_id
        )
        context.update(
            {
                "title": title,
                "number": order.name or "-",
                "tax_label": tax_country.vat_label or tr_label("tax_no", code),
            }
        )
        context["labels"]["partner_info"] = tr_label("vendor_info", code)
        context["labels"]["document_info"] = tr_label("document_info", code)
        context["bank_labels"] = {
            key: tr_label(key, code)
            for key in ("bank", "branch", "account_name", "iban", "swift")
        }

        order_date_field = (
            "date_approve"
            if order.state == "purchase" and order.date_approve
            else "date_order"
        )
        metadata_specs = [
            {"label_key": "document_no", "path": "name", "icon": "fa-file-text-o"},
            {"label_key": "date", "path": order_date_field, "value_type": "date", "icon": "fa-calendar"},
            {"label_key": "delivery_date", "path": "date_planned", "value_type": "date", "icon": "fa-truck"},
            {"label_key": "buyer", "path": "user_id", "icon": "fa-user"},
            {"label_key": "reference", "path": "partner_ref", "icon": "fa-bookmark-o"},
            {"label_key": "payment_terms", "path": "payment_term_id", "icon": "fa-credit-card"},
        ]
        context["metadata"] = template.get_metadata_context(
            order, metadata_specs, lang=resolved_lang
        )

        shipping_partner = order.dest_address_id
        context["shipping_partner_info"] = template.partner_info(shipping_partner)
        if shipping_partner:
            shipping_label = order._fields["dest_address_id"].get_description(
                order.env, attributes={"string"}
            ).get("string", "Shipping Address")
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
                    }
                )

        report_lines = order.order_line.filtered(
            lambda line: line.display_type or line.product_qty != 0
        )
        custom_columns, custom_lines = template.get_custom_line_context(
            report_lines,
            currency=order.currency_id,
            lang=resolved_lang,
        )
        if custom_columns:
            columns, lines = custom_columns, custom_lines
        else:
            columns = [
                {"label": tr_label("description", code), "align": "left", "width": 34},
                {"label": tr_label("quantity", code), "align": "right", "width": 12},
                {"label": tr_label("unit_price", code), "align": "right", "width": 14},
                {"label": tr_label("discount", code), "align": "right", "width": 10},
                {"label": tr_label("tax", code), "align": "right", "width": 12},
                {"label": tr_label("amount", code), "align": "right", "width": 18},
            ]
            lines = []
            for line in report_lines:
                display_type = getattr(line, "display_type", False)
                if display_type in ("line_section", "line_subsection", "section", "subsection"):
                    lines.append({"is_section": True, "description": plain_text(line.name)})
                    continue
                if display_type in ("line_note", "note"):
                    lines.append({"is_note": True, "description": plain_text(line.name)})
                    continue
                uom_field = "product_uom_id" if "product_uom_id" in line._fields else "product_uom"
                uom = line[uom_field]
                tax_field = "tax_ids" if "tax_ids" in line._fields else "taxes_id"
                taxes = line[tax_field] if tax_field in line._fields else self.env["account.tax"]
                tax_text = ", ".join(
                    tax.tax_label or tax.name for tax in taxes if tax.tax_label or tax.name
                )
                quantity_text = "%s %s" % (
                    order._rds_format_quantity(line.product_qty, lang=resolved_lang),
                    uom.display_name if uom else "",
                )
                discount = getattr(line, "discount", 0.0)
                lines.append(
                    {
                        "values": [
                            {"text": plain_text(line.name), "align": "left"},
                            {"text": quantity_text.strip(), "align": "right"},
                            {
                                "text": order._rds_format_unit_price(
                                    line.price_unit,
                                    order.currency_id,
                                    lang=resolved_lang,
                                ),
                                "align": "right",
                            },
                            {
                                "text": template.format_value(
                                    discount,
                                    "percentage",
                                    lang=resolved_lang,
                                ),
                                "align": "right",
                            },
                            {"text": tax_text or "-", "align": "right"},
                            {
                                "text": template.format_value(
                                    line.price_subtotal,
                                    "monetary",
                                    order.currency_id,
                                    resolved_lang,
                                ),
                                "align": "right",
                                "bold": True,
                                "highlight": template.layout_style == "industrial",
                            },
                        ]
                    }
                )
        context["columns"] = columns
        context["lines"] = lines
        context["totals"] = order._rds_totals_context(
            template, code, lang=resolved_lang
        )
        notes = []
        if template.notes_text:
            notes.extend([line.strip(" •-") for line in plain_text(template.notes_text).splitlines() if line.strip()])
        if getattr(order, "note", False):
            notes.extend([line.strip(" •-") for line in plain_text(order.note).splitlines() if line.strip()])
        if order.payment_term_id and order.payment_term_id.note:
            notes.extend(
                line.strip(" •-")
                for line in plain_text(order.payment_term_id.note).splitlines()
                if line.strip()
            )
        context["notes"] = notes
        # A purchase order is sent to the vendor.  Publishing an arbitrary
        # buyer-company bank account here is neither part of Odoo's standard
        # purchase report nor a reliable payment destination for this order.
        context["bank"] = {}
        return context
