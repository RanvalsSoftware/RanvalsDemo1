from odoo import models
from odoo.tools.misc import formatLang

from odoo.addons.ranvals_document_studio.tools.common import lang_code, plain_text, tr_label


class SaleOrder(models.Model):
    _inherit = "sale.order"

    _RDS_EXPORT_METADATA_FIELDS = (
        {"key": "builtin:metadata:name", "field_path": "name", "label_key": "document_no", "alignment": "center"},
        {"key": "builtin:metadata:date_order", "field_path": "date_order", "label_key": "date", "alignment": "center"},
        {"key": "builtin:metadata:validity_date", "field_path": "validity_date", "label_key": "validity", "alignment": "center"},
        {"key": "builtin:metadata:user_id", "field_path": "user_id", "label_key": "salesperson", "alignment": "center"},
        {"key": "builtin:metadata:client_order_ref", "field_path": "client_order_ref", "label_key": "reference", "alignment": "center"},
        {"key": "builtin:metadata:commitment_date", "field_path": "commitment_date", "label_key": "delivery_date", "alignment": "center"},
        {"key": "builtin:metadata:partner_invoice_id", "field_path": "partner_invoice_id", "alignment": "center", "context_only": True},
        {"key": "builtin:metadata:partner_shipping_id", "field_path": "partner_shipping_id", "alignment": "center", "context_only": True},
    )
    _RDS_EXPORT_LINE_FIELDS = (
        {"key": "builtin:line:description", "field_path": "name", "label_key": "description", "alignment": "left"},
        {"key": "builtin:line:quantity", "field_path": "product_uom_qty", "label_key": "quantity", "alignment": "right"},
        {"key": "builtin:line:unit_price", "field_path": "price_unit", "label_key": "unit_price", "alignment": "right"},
        {"key": "builtin:line:discount", "field_path": "discount", "label_key": "discount", "alignment": "right"},
        {"key": "builtin:line:tax", "field_path": "tax_ids", "label_key": "tax", "alignment": "right"},
        {"key": "builtin:line:amount", "field_path": "price_subtotal", "label_key": "amount", "alignment": "right"},
    )

    _RDS_STUDIO_REPORT_ACTIONS = {
        "beauty": "ranvals_document_studio.action_report_rds_sale_beauty_document",
        "construction": "ranvals_document_studio.action_report_rds_sale_construction_document",
        "technology": "ranvals_document_studio.action_report_rds_sale_technology_document",
        "industrial": "ranvals_document_studio.action_report_rds_sale_industrial_document",
        "eco": "ranvals_document_studio.action_report_rds_sale_eco_document",
        "furniture": "ranvals_document_studio.action_report_rds_sale_furniture_document",
    }

    def action_open_rds_export(self):
        return self.env["rds.export.wizard"].open_for_records(self)

    def _rds_export_field_config(self):
        return {
            "metadata": {
                "model": "sale.order",
                "relation_field": False,
                "view_id": self.env.ref("sale.view_order_form"),
                "builtin_fields": self._RDS_EXPORT_METADATA_FIELDS,
            },
            "line": {
                "model": "sale.order.line",
                "relation_field": "order_line",
                "view_id": self.env.ref("sale.view_order_form"),
                "builtin_fields": self._RDS_EXPORT_LINE_FIELDS,
            },
        }

    def _rds_export_line_records(self):
        self.ensure_one()
        return self._get_order_lines_to_report()

    def _rds_standard_report_template(self):
        """Return the design used by Odoo's native quotation/order report.

        The standard report is also rendered from portal and email routes,
        where the visitor does not have direct access to ``rds.template``.
        Only template selection is elevated; sale.order access remains under
        the current user and the report controller's normal checks.
        """
        self.ensure_one()
        if self.env.context.get("rds_disable_standard_sale_report"):
            return self.env["rds.template"].browse()
        return self.env["rds.template"].sudo().get_default_for(
            "sale.order",
            company=self.company_id,
        )

    def _rds_report_action_xmlid(self, template=None):
        self.ensure_one()
        if template and template.layout_style in self._RDS_STUDIO_REPORT_ACTIONS:
            return self._RDS_STUDIO_REPORT_ACTIONS[template.layout_style]
        return "ranvals_document_studio.action_report_rds_sale_document"

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
            rounding = tax_totals["cash_rounding_base_amount_currency"]
            totals.append(
                {
                    "label": self.env._("Rounding"),
                    "value": template.format_value(
                        rounding, "monetary", self.currency_id, lang
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
    def _rds_line_prices_hidden(line):
        parent = getattr(line, "parent_id", False)
        grandparent = parent and getattr(parent, "parent_id", False)
        return bool(
            (parent and getattr(parent, "collapse_prices", False))
            or (grandparent and getattr(grandparent, "collapse_prices", False))
        )

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
        if order.env.context.get("proforma"):
            title_key = "proforma"
        else:
            title_key = "quote" if order.state in ("draft", "sent") else "order"
        title = tr_label(title_key, code)
        if order.state == "cancel" and title_key != "proforma":
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
        customer_partner = order.partner_id
        invoice_partner = order.partner_invoice_id or customer_partner
        shipping_partner = order.partner_shipping_id
        context["partner"] = customer_partner
        context["partner_info"] = template.partner_info(customer_partner)
        context["invoice_partner_info"] = template.partner_info(invoice_partner)
        context["shipping_partner_info"] = template.partner_info(shipping_partner)
        context["labels"]["partner_info"] = tr_label("customer_info", code)
        context["labels"]["document_info"] = tr_label("document_info", code)
        context["bank_labels"] = {
            key: tr_label(key, code)
            for key in ("bank", "branch", "account_name", "iban", "swift")
        }

        metadata_specs = [
            {"label_key": "document_no", "path": "name", "icon": "fa-file-text-o"},
            {"label_key": "date", "path": "date_order", "value_type": "date", "icon": "fa-calendar"},
            {"label_key": "validity", "path": "validity_date", "value_type": "date", "icon": "fa-hourglass-half"},
            {"label_key": "salesperson", "path": "user_id", "icon": "fa-user"},
            {"label_key": "reference", "path": "client_order_ref", "icon": "fa-bookmark-o"},
            {"label_key": "delivery_date", "path": "commitment_date", "value_type": "date", "icon": "fa-truck"},
        ]
        context["metadata"] = template.get_metadata_context(
            order, metadata_specs, lang=resolved_lang
        )
        custom_metadata = template.has_configured_fields(
            "sale.order", "metadata"
        )
        if not custom_metadata and invoice_partner and invoice_partner != customer_partner:
            invoice_label = order._fields["partner_invoice_id"].get_description(
                order.env, attributes={"string"}
            ).get("string", "Invoice Address")
            invoice_text = self._rds_partner_info_text(
                context["invoice_partner_info"]
            )
            if invoice_text:
                context["metadata"].append(
                    {
                        "label": invoice_label,
                        "value": invoice_text,
                        "icon": "fa-file-text-o",
                        "align": "center",
                        "key": "builtin:metadata:partner_invoice_id",
                        "field_path": "partner_invoice_id",
                    }
                )
        if (
            not custom_metadata
            and shipping_partner
            and shipping_partner != customer_partner
            and shipping_partner != invoice_partner
        ):
            shipping_label = order._fields["partner_shipping_id"].get_description(
                order.env, attributes={"string"}
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

        report_lines = order._get_order_lines_to_report()
        custom_line_records = report_lines.filtered(
            lambda line: line.display_type or not self._rds_line_prices_hidden(line)
        )
        custom_columns, custom_lines = template.get_custom_line_context(
            custom_line_records,
            currency=order.currency_id,
            lang=resolved_lang,
        )
        custom_line_configured = template.has_configured_fields(
            custom_line_records._name, "line"
        )
        if custom_line_configured:
            columns, lines = custom_columns, custom_lines
            rendered_line_records = custom_line_records
        else:
            tax_field = "tax_ids" if "tax_ids" in report_lines._fields else "tax_id"
            price_field = (
                "price_total"
                if order.company_price_include == "tax_included"
                else "price_subtotal"
            )
            columns = [
                {"label": tr_label("description", code), "align": "left", "width": 34, "key": "builtin:line:description", "field_path": "name"},
                {"label": tr_label("quantity", code), "align": "right", "width": 12, "key": "builtin:line:quantity", "field_path": "product_uom_qty"},
                {"label": tr_label("unit_price", code), "align": "right", "width": 14, "key": "builtin:line:unit_price", "field_path": "price_unit"},
                {"label": tr_label("discount", code), "align": "right", "width": 10, "key": "builtin:line:discount", "field_path": "discount"},
                {"label": tr_label("tax", code), "align": "right", "width": 12, "key": "builtin:line:tax", "field_path": tax_field},
                {"label": tr_label("amount", code), "align": "right", "width": 18, "key": "builtin:line:amount", "field_path": price_field},
            ]
            lines = []
            for line in report_lines:
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
                uom_field = "product_uom_id" if "product_uom_id" in line._fields else "product_uom"
                uom = line[uom_field]
                taxes = line[tax_field]
                tax_text = ", ".join(
                    tax.tax_label or tax.name for tax in taxes if tax.tax_label or tax.name
                )
                quantity_text = "%s %s" % (
                    order._rds_format_quantity(
                        line.product_uom_qty, lang=resolved_lang
                    ),
                    uom.display_name if uom else "",
                )
                hide_prices = self._rds_line_prices_hidden(line)
                lines.append(
                    {
                        "values": [
                            {"text": plain_text(line.name), "align": "left"},
                            {"text": quantity_text.strip(), "align": "right"},
                            {
                                "text": ""
                                if hide_prices
                                else order._rds_format_unit_price(
                                    line.price_unit,
                                    order.currency_id,
                                    lang=resolved_lang,
                                ),
                                "align": "right",
                            },
                            {
                                "text": ""
                                if hide_prices
                                else template.format_value(
                                    getattr(line, "discount", 0.0),
                                    "percentage",
                                    lang=resolved_lang,
                                ),
                                "align": "right",
                            },
                            {"text": "" if hide_prices else (tax_text or "-"), "align": "right"},
                            {
                                "text": ""
                                if hide_prices
                                else template.format_value(
                                    line[price_field],
                                    "monetary",
                                    order.currency_id,
                                    resolved_lang,
                                ),
                                "align": "right",
                                "bold": True,
                                "highlight": not hide_prices
                                and template.layout_style == "industrial",
                            },
                        ],
                        **source_reference,
                    }
                )
            rendered_line_records = report_lines
        context["columns"] = columns
        context["lines"] = lines
        context["show_lines"] = bool(columns)
        context["_line_records"] = rendered_line_records
        context["totals"] = order._rds_totals_context(
            template, code, lang=resolved_lang
        )
        notes = []
        if template.notes_text:
            notes.extend([line.strip(" •-") for line in plain_text(template.notes_text).splitlines() if line.strip()])
        if order.note:
            notes.extend([line.strip(" •-") for line in plain_text(order.note).splitlines() if line.strip()])
        if order.payment_term_id and order.payment_term_id.note:
            notes.extend(
                line.strip(" •-")
                for line in plain_text(order.payment_term_id.note).splitlines()
                if line.strip()
            )
        context["notes"] = notes
        context["bank"] = template.bank_info(order.company_id, order.currency_id) if template.show_bank else {}
        field_specs = order.env.context.get("rds_export_field_specs")
        if field_specs is not None:
            template.apply_export_field_specs(
                context,
                order,
                field_specs,
                lang=resolved_lang,
            )
        return context
