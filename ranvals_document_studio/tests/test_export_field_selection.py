import json
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, tagged

from odoo.addons.ranvals_document_studio.tools.common import (
    is_document_language_supported,
    lang_code,
)
from odoo.addons.ranvals_document_studio.wizard.rds_export_wizard import (
    RdsExportWizard,
)


@tagged("post_install", "-at_install")
class TestRdsExportFieldSelection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.env.ref(
            "ranvals_document_studio.template_technology_blue"
        )
        cls.sale_model = cls.env["ir.model"]._get("sale.order")
        cls.sale_line_model = cls.env["ir.model"]._get("sale.order.line")

        # Reproduce fields created by Studio rather than relying on Studio's
        # optional demo data.  The inherited form proves that discovery is
        # driven by the effective business view, not the whole model registry.
        field_model = cls.env["ir.model.fields"]
        cls.safe_custom_field = field_model.create(
            {
                "name": "x_rds_catalog_note",
                "field_description": "Studio Customer Note",
                "model_id": cls.sale_model.id,
                "state": "manual",
                "ttype": "char",
            }
        )
        cls.hidden_custom_field = field_model.create(
            {
                "name": "x_rds_catalog_hidden",
                "field_description": "Studio Hidden Note",
                "model_id": cls.sale_model.id,
                "state": "manual",
                "ttype": "char",
            }
        )
        cls.secret_custom_field = field_model.create(
            {
                "name": "x_rds_api_token",
                "field_description": "Studio API Token",
                "model_id": cls.sale_model.id,
                "state": "manual",
                "ttype": "char",
            }
        )
        cls.blank_widget_field = field_model.create(
            {
                "name": "x_rds_widget_payload",
                "field_description": "Studio Widget Payload",
                "model_id": cls.sale_model.id,
                "state": "manual",
                "ttype": "char",
            }
        )
        cls.alert_custom_field = field_model.create(
            {
                "name": "x_rds_alert_payload",
                "field_description": "Studio Alert Payload",
                "model_id": cls.sale_model.id,
                "state": "manual",
                "ttype": "char",
            }
        )
        cls.button_custom_field = field_model.create(
            {
                "name": "x_rds_button_count",
                "field_description": "Studio Button Count",
                "model_id": cls.sale_model.id,
                "state": "manual",
                "ttype": "integer",
            }
        )
        cls.form_only_line_field = field_model.create(
            {
                "name": "x_rds_line_form_note",
                "field_description": "Studio Line Form Note",
                "model_id": cls.sale_line_model.id,
                "state": "manual",
                "ttype": "char",
            }
        )
        cls.catalog_view = cls.env["ir.ui.view"].create(
            {
                "name": "sale.order.rds.field.catalog.test",
                "model": "sale.order",
                "inherit_id": cls.env.ref("sale.view_order_form").id,
                "arch": """
                    <data>
                    <xpath expr="//field[@name='client_order_ref']" position="after">
                        <field name="x_rds_catalog_note"/>
                        <field name="x_rds_catalog_hidden" invisible="1"/>
                        <field name="x_rds_api_token"/>
                        <field name="x_rds_widget_payload"
                               string=" "
                               widget="stock_rescheduling_popover"/>
                    </xpath>
                    <xpath expr="//sheet" position="before">
                        <div class="alert alert-warning" role="status">
                            <field name="x_rds_alert_payload"/>
                        </div>
                    </xpath>
                    <xpath expr="//div[@name='button_box']" position="inside">
                        <button name="action_preview_sale_order"
                                type="object"
                                class="oe_stat_button">
                            <field name="x_rds_button_count"
                                   widget="statinfo"
                                   string="Technical Counter"/>
                        </button>
                    </xpath>
                    <xpath expr="//field[@name='order_line']/form//field[@name='name']"
                           position="after">
                        <field name="x_rds_line_form_note"/>
                    </xpath>
                    </data>
                """,
            }
        )

        cls.partner = cls.env["res.partner"].create(
            {"name": "DocuCraft Field Selection Customer", "lang": "en_US"}
        )
        cls.export_user = new_test_user(
            cls.env,
            login="rds_field_selection_exporter",
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.partner.id,
                "user_id": cls.export_user.id,
                "client_order_ref": "FIELD-META-VALUE",
                "x_rds_catalog_note": "STUDIO-FIELD-VALUE",
                "x_rds_catalog_hidden": "HIDDEN-FIELD-VALUE",
                "x_rds_api_token": "SECRET-FIELD-VALUE",
                "x_rds_widget_payload": "WIDGET-PAYLOAD",
                "x_rds_alert_payload": "ALERT-PAYLOAD",
                "x_rds_button_count": 7,
            }
        )
        cls.order_line = cls.env["sale.order.line"].create(
            {
                "order_id": cls.order.id,
                "name": "FIELD-LINE-VALUE",
                "product_uom_qty": 2.0,
                "price_unit": 25.0,
                "x_rds_line_form_note": "STUDIO-LINE-FORM-VALUE",
            }
        )

    def _catalog(self, language_code="en_US"):
        return self.template.get_export_field_catalog(
            self.order,
            lang=language_code,
        )

    def _field_commands(self, language_code="en_US"):
        return self.env["rds.export.wizard"]._prepare_field_line_commands(
            self.order,
            self.template,
            language_code,
        )

    def _wizard(self, *, commands=None):
        return self.env["rds.export.wizard"].with_user(self.export_user).create(
            {
                "res_model": "sale.order",
                "res_ids_json": json.dumps(self.order.ids),
                "template_id": self.template.id,
                "output_format": "pdf",
                "processing_mode": "immediate",
                "field_selection_mode": "custom",
                "field_line_ids": (
                    commands if commands is not None else self._field_commands()
                ),
            }
        )

    def test_catalog_uses_visible_form_fields_and_filters_hidden_or_secret_fields(self):
        catalog = self._catalog()
        by_path = {
            (item["section"], item["field_path"]): item for item in catalog
        }

        self.assertIn(("metadata", "partner_id"), by_path)
        self.assertIn(("line", "product_id"), by_path)
        self.assertIn(("metadata", self.safe_custom_field.name), by_path)
        self.assertEqual(
            by_path[("metadata", self.safe_custom_field.name)]["origin"],
            "screen",
        )
        self.assertTrue(
            by_path[("metadata", self.safe_custom_field.name)]["is_custom"]
        )
        self.assertNotIn(("metadata", self.hidden_custom_field.name), by_path)
        self.assertNotIn(("metadata", self.secret_custom_field.name), by_path)
        self.assertFalse(
            any(item["field_path"] == "access_token" for item in catalog)
        )

    def test_catalog_filters_view_utilities_without_optional_sale_stock(self):
        """Control widgets must not become blank labels or raw payload values."""
        catalog = self._catalog()
        metadata_paths = {
            item["field_path"]
            for item in catalog
            if item["section"] == "metadata"
        }

        self.assertNotIn(self.blank_widget_field.name, metadata_paths)
        self.assertNotIn(self.alert_custom_field.name, metadata_paths)
        self.assertNotIn(self.button_custom_field.name, metadata_paths)
        self.assertNotIn("invoice_count", metadata_paths)
        self.assertTrue(all(item["label"].strip() for item in catalog))

    def test_line_catalog_uses_list_fields_and_keeps_form_only_studio_fields(self):
        catalog = self._catalog()
        line_by_path = {
            item["field_path"]: item
            for item in catalog
            if item["section"] == "line"
        }

        # product_id is a real sales-line list column.  The following standard
        # fields are form/kanban implementation details rather than printable
        # columns and must not leak into the chooser.
        self.assertIn("product_id", line_by_path)
        self.assertNotIn("collapse_composition", line_by_path)
        self.assertNotIn("collapse_prices", line_by_path)
        self.assertNotIn("invoice_lines", line_by_path)
        self.assertNotIn("display_type", line_by_path)
        self.assertNotIn("currency_id", line_by_path)
        self.assertNotIn("sequence", line_by_path)

        # A customer may add a Studio field only to the line form.  Custom
        # fields remain discoverable even though standard discovery is driven
        # by the embedded list.
        studio = line_by_path[self.form_only_line_field.name]
        self.assertEqual(studio["origin"], "screen")
        self.assertTrue(studio["is_custom"])

    def test_field_command_labels_and_samples_are_single_line_text(self):
        self.order[self.safe_custom_field.name] = "  STUDIO\tFIELD\r\nVALUE  "
        command_values = {
            values["field_key"]: values
            for command, _unused, values in self._field_commands()
            if command == 0
        }
        custom = command_values[
            "screen:metadata:%s" % self.safe_custom_field.name
        ]

        self.assertEqual(custom["sample_value"], "STUDIO FIELD VALUE")
        self.assertNotRegex(custom["label"], r"[\t\r\n]")
        self.assertNotRegex(custom["sample_value"], r"[\t\r\n]")

    def test_column_invisible_fields_are_statically_hidden(self):
        direct = etree.fromstring(
            b'<list><field name="technical" column_invisible="1"/></list>'
        )[0]
        inherited = etree.fromstring(
            b'<list column_invisible="true"><field name="technical"/></list>'
        )[0]

        self.assertTrue(self.template._node_is_statically_hidden(direct))
        self.assertTrue(self.template._node_is_statically_hidden(inherited))

    def test_catalog_selects_rich_document_defaults(self):
        enabled = {
            item["key"]
            for item in self._catalog()
            if item["enabled"]
        }

        self.assertTrue(
            {
                "builtin:metadata:name",
                "builtin:metadata:date_order",
                "builtin:metadata:client_order_ref",
                "builtin:line:description",
                "builtin:line:quantity",
                "builtin:line:unit_price",
                "builtin:line:amount",
            }.issubset(enabled)
        )

    def test_explicit_empty_selection_disables_metadata_and_line_table(self):
        context = self.order.with_context(
            lang="en_US",
            rds_export_field_specs=[],
        )._rds_document_context(self.template, "en_US")

        self.assertEqual(context["metadata"], [])
        self.assertEqual(context["columns"], [])
        self.assertEqual(context["lines"], [])
        self.assertFalse(context["show_metadata"])
        self.assertFalse(context["show_lines"])

    def test_one_metadata_and_one_line_field_filter_and_relabel_output(self):
        specs = [
            {
                "key": "builtin:metadata:client_order_ref",
                "label": "Customer PO Number",
            },
            {
                "key": "builtin:line:description",
                "label": "Requested Service",
            },
        ]
        context = self.order.with_context(
            lang="en_US",
            rds_export_field_specs=specs,
        )._rds_document_context(self.template, "en_US")

        self.assertEqual(len(context["metadata"]), 1)
        self.assertEqual(context["metadata"][0]["label"], "Customer PO Number")
        self.assertEqual(context["metadata"][0]["value"], "FIELD-META-VALUE")
        self.assertEqual(len(context["columns"]), 1)
        self.assertEqual(context["columns"][0]["label"], "Requested Service")
        self.assertEqual(context["columns"][0]["width"], 100)
        business_lines = [
            line
            for line in context["lines"]
            if not line.get("is_section") and not line.get("is_note")
        ]
        self.assertEqual(len(business_lines), 1)
        self.assertEqual(len(business_lines[0]["values"]), 1)
        self.assertEqual(
            business_lines[0]["values"][0]["text"],
            "FIELD-LINE-VALUE",
        )
        self.assertTrue(context["show_metadata"])
        self.assertTrue(context["show_lines"])

    def test_conditional_address_fields_remain_valid_across_batch_records(self):
        invoice_partner = self.env["res.partner"].create(
            {
                "name": "Dedicated Invoice Office",
                "parent_id": self.partner.id,
                "type": "invoice",
                "street": "Invoice Street 10",
            }
        )
        shipping_partner = self.env["res.partner"].create(
            {
                "name": "Dedicated Delivery Depot",
                "parent_id": self.partner.id,
                "type": "delivery",
                "street": "Delivery Street 20",
            }
        )
        addressed_order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "partner_invoice_id": invoice_partner.id,
                "partner_shipping_id": shipping_partner.id,
            }
        )
        keys = (
            "builtin:metadata:partner_invoice_id",
            "builtin:metadata:partner_shipping_id",
        )
        addressed_catalog = {
            item["key"]: item
            for item in self.template.get_export_field_catalog(
                addressed_order, lang="en_US"
            )
        }
        plain_catalog = {
            item["key"]: item
            for item in self.template.get_export_field_catalog(
                self.order, lang="en_US"
            )
        }

        self.assertTrue(all(addressed_catalog[key]["enabled"] for key in keys))
        self.assertTrue(all(key in plain_catalog for key in keys))
        specs = [
            {"key": key, "label": addressed_catalog[key]["label"]}
            for key in keys
        ]
        addressed_context = addressed_order.with_context(
            lang="en_US", rds_export_field_specs=specs
        )._rds_document_context(self.template, "en_US")
        plain_context = self.order.with_context(
            lang="en_US", rds_export_field_specs=specs
        )._rds_document_context(self.template, "en_US")

        self.assertIn("Invoice Street 10", addressed_context["metadata"][0]["value"])
        self.assertIn("Delivery Street 20", addressed_context["metadata"][1]["value"])
        self.assertEqual(
            [item["value"] for item in plain_context["metadata"]],
            ["-", "-"],
        )

    def test_purchase_order_date_key_is_stable_for_mixed_states(self):
        vendor = self.env["res.partner"].create(
            {"name": "DocuCraft Stable Date Vendor", "supplier_rank": 1}
        )
        draft_order = self.env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "date_order": fields.Datetime.from_string("2026-01-10 09:00:00"),
            }
        )
        approved_order = self.env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "state": "purchase",
                "date_order": fields.Datetime.from_string("2026-01-11 09:00:00"),
                "date_approve": fields.Datetime.from_string("2026-01-12 09:00:00"),
            }
        )
        key = "builtin:metadata:order_date"
        specs = [{"key": key, "label": "Document Date"}]

        for order in (draft_order, approved_order):
            catalog = {
                item["key"]: item
                for item in self.template.get_export_field_catalog(
                    order, lang="en_US"
                )
            }
            self.assertIn(key, catalog)
            normalized = self.template.normalize_export_field_specs(
                order, specs, lang="en_US"
            )
            self.assertEqual(normalized[0]["key"], key)
            context = order.with_context(
                lang="en_US", rds_export_field_specs=specs
            )._rds_document_context(self.template, "en_US")
            self.assertEqual(context["metadata"][0]["label"], "Document Date")
            self.assertNotEqual(context["metadata"][0]["value"], "-")

    def test_line_values_follow_source_references_not_rendered_indexes(self):
        product_a = self.env["product.product"].create(
            {"name": "Reference Product A"}
        )
        product_b = self.env["product.product"].create(
            {"name": "Reference Product B"}
        )
        self.order_line.product_id = product_a
        second_line = self.env["sale.order.line"].create(
            {
                "order_id": self.order.id,
                "name": "SECOND-REFERENCE-LINE",
                "product_id": product_b.id,
                "product_uom_qty": 1.0,
                "price_unit": 10.0,
            }
        )
        context = {
            "metadata": [],
            "columns": [
                {
                    "key": "builtin:line:description",
                    "field_path": "name",
                    "label": "Description",
                }
            ],
            "lines": [
                {
                    "values": [{"text": second_line.name, "align": "left"}],
                    **self.template._export_line_reference(second_line),
                },
                {
                    "values": [{"text": self.order_line.name, "align": "left"}],
                    **self.template._export_line_reference(self.order_line),
                },
            ],
            "_line_records": self.order.order_line,
        }
        self.template.apply_export_field_specs(
            context,
            self.order,
            [{"key": "screen:line:product_id", "label": "Product"}],
            lang="en_US",
        )

        self.assertEqual(
            [line["values"][0]["text"] for line in context["lines"]],
            [product_b.display_name, product_a.display_name],
        )

    def test_template_hide_if_empty_is_preserved_in_custom_selection(self):
        source_field = self.env["ir.model.fields"].search(
            [
                ("model", "=", "sale.order"),
                ("name", "=", "client_order_ref"),
            ],
            limit=1,
        )
        template_field = self.env["rds.template.field"].create(
            {
                "template_id": self.template.id,
                "section": "metadata",
                "source_model_id": self.sale_model.id,
                "field_id": source_field.id,
                "field_path": source_field.name,
                "label": "Optional Customer Reference",
                "hide_if_empty": True,
            }
        )
        empty_order = self.env["sale.order"].create(
            {"partner_id": self.partner.id, "client_order_ref": False}
        )
        context = empty_order.with_context(
            lang="en_US",
            rds_export_field_specs=[
                {
                    "key": "template:%s" % template_field.id,
                    "label": template_field.label,
                }
            ],
        )._rds_document_context(self.template, "en_US")

        self.assertEqual(context["metadata"], [])
        self.assertFalse(context["show_metadata"])

    def test_unlisted_or_malicious_field_key_is_rejected(self):
        with self.assertRaises(UserError):
            self.template.normalize_export_field_specs(
                self.order,
                [
                    {
                        "key": "screen:metadata:x_rds_api_token",
                        "label": "Leaked credential",
                    }
                ],
                lang="en_US",
            )
        with self.assertRaises(UserError):
            self.template.normalize_export_field_specs(
                self.order,
                [{"key": "screen:metadata:__class__", "label": "Unsafe"}],
                lang="en_US",
            )

    def test_live_preview_renders_the_real_record_and_layout(self):
        wizard = self._wizard()
        preview = str(wizard.template_preview_html)

        self.assertIn("rds-export-live-preview", preview)
        self.assertIn("rds-page", preview)
        self.assertIn("FIELD-META-VALUE", preview)
        self.assertIn("FIELD-LINE-VALUE", preview)
        self.assertNotIn("Document item", preview)

    def test_real_form_toggle_keeps_live_preview_and_saves_both_states(self):
        """Exercise the same x2many toggle/save path used by the web client."""
        wizard = self._wizard()
        target = wizard.field_line_ids.filtered(
            lambda line: line.field_key
            == "screen:metadata:%s" % self.safe_custom_field.name
        )
        self.assertEqual(len(target), 1)
        self.assertFalse(target.enabled)

        def edit_enabled(record, enabled):
            line_index = record.field_line_ids.ids.index(target.id)
            wizard_form = Form(
                record,
                view="ranvals_document_studio.view_rds_export_wizard_form",
            )
            self.assertIn(
                "rds-export-live-preview",
                str(wizard_form.template_preview_html),
            )
            with wizard_form.field_line_ids.edit(line_index) as line_form:
                line_form.enabled = enabled
            self.assertIn(
                "rds-export-live-preview",
                str(wizard_form.template_preview_html),
            )
            saved = wizard_form.save()
            self.assertIn(
                "rds-export-live-preview",
                str(saved.template_preview_html),
            )
            return saved

        wizard = edit_enabled(wizard, True)
        self.assertTrue(target.exists().enabled)
        wizard = edit_enabled(wizard, False)
        self.assertFalse(target.exists().enabled)
        self.assertTrue(wizard.exists())

    def test_company_language_is_the_automatic_wizard_default(self):
        turkish = self.env["res.lang"]._activate_lang("tr_TR")
        self.order.company_id.partner_id.lang = turkish.code
        self.assertEqual(self.partner.lang, "en_US")

        values = self.env["rds.export.wizard"].with_context(
            default_res_model="sale.order",
            default_res_ids_json=json.dumps(self.order.ids),
            active_model="sale.order",
            active_id=self.order.id,
            active_ids=self.order.ids,
        ).default_get(
            [
                "res_model",
                "res_ids_json",
                "language_mode",
                "language_id",
            ]
        )

        self.assertEqual(values["language_mode"], "company")
        self.assertEqual(values["language_id"], turkish.id)

    def test_document_label_languages_are_selectable_and_fallback_is_safe(self):
        self.assertEqual(lang_code(None), "en")
        Language = self.env["res.lang"]
        english = Language._activate_lang("en_US")
        german = Language._activate_lang("de_DE")
        spanish = Language._activate_lang("es_ES")
        portuguese = Language._activate_lang("pt_BR")
        japanese = Language._activate_lang("ja_JP")
        self.env.user.lang = japanese.code
        self.order.company_id.partner_id.lang = german.code
        self.partner.lang = german.code

        Wizard = self.env["rds.export.wizard"]
        self.assertEqual(
            Wizard._automatic_language(self.order, "company"),
            german,
        )
        self.assertEqual(
            Wizard._automatic_language(self.order, "partner"),
            german,
        )

        values = Wizard.with_context(
            default_res_model="sale.order",
            default_res_ids_json=json.dumps(self.order.ids),
            active_model="sale.order",
            active_id=self.order.id,
            active_ids=self.order.ids,
        ).default_get(
            [
                "res_model",
                "res_ids_json",
                "language_mode",
                "language_id",
            ]
        )
        self.assertEqual(values["language_mode"], "company")
        self.assertEqual(values["language_id"], german.id)

        language_domain = Wizard._fields["language_id"].domain
        selectable_languages = Language.search(language_domain)
        for supported in (english, german, spanish, portuguese):
            self.assertIn(supported, selectable_languages)
        self.assertNotIn(japanese, selectable_languages)
        self.assertTrue(
            all(
                is_document_language_supported(code)
                for code in selectable_languages.mapped("code")
            )
        )

        self.order.company_id.partner_id.lang = japanese.code
        self.partner.lang = japanese.code
        self.assertEqual(
            Wizard._automatic_language(self.order, "company"),
            english,
        )
        self.assertEqual(
            Wizard._automatic_language(self.order, "partner"),
            english,
        )

        fallback_values = Wizard.with_context(
            default_res_model="sale.order",
            default_res_ids_json=json.dumps(self.order.ids),
            active_model="sale.order",
            active_id=self.order.id,
            active_ids=self.order.ids,
        ).default_get(
            [
                "res_model",
                "res_ids_json",
                "language_mode",
                "language_id",
            ]
        )
        self.assertEqual(fallback_values["language_id"], english.id)

    def test_export_wizard_notebook_opens_preview_before_fields(self):
        view = self.env.ref(
            "ranvals_document_studio.view_rds_export_wizard_form"
        )
        arch = etree.fromstring(view.arch_db.encode())
        pages = arch.xpath(".//notebook/page")

        self.assertEqual([page.get("name") for page in pages], ["preview", "fields"])
        self.assertEqual(
            pages[0].xpath("count(.//field[@name='template_preview_html'])"),
            1.0,
        )
        self.assertEqual(
            pages[1].xpath("count(.//field[@name='field_line_ids'])"),
            1.0,
        )
        self.assertFalse(pages[0].xpath(".//field[@name='field_line_ids']"))
        self.assertFalse(
            pages[1].xpath(".//field[@name='template_preview_html']")
        )

    def test_background_job_round_trip_preserves_custom_and_empty_selections(self):
        def round_trip(wizard):
            job = self.env["rds.export.job"]._enqueue_from_wizard(wizard)
            seen = {}

            def build_output(worker_wizard):
                seen["mode"] = worker_wizard.field_selection_mode
                seen["specs"] = worker_wizard._selected_field_specs(
                    record=self.order.with_env(worker_wizard.env),
                    language_code="en_US",
                )
                return (
                    "field-selection.pdf",
                    b"%PDF-field-selection",
                    "application/pdf",
                    [],
                )

            with patch.object(
                RdsExportWizard,
                "_build_output",
                autospec=True,
                side_effect=build_output,
            ):
                values = job.sudo()._render_as_requesting_user()
            self.assertEqual(values["file_name"], "field-selection.pdf")
            return job, seen

        custom = self._wizard()
        custom.field_line_ids.write({"enabled": False})
        custom_line = custom.field_line_ids.filtered(
            lambda line: line.field_key == "builtin:metadata:client_order_ref"
        )
        self.assertEqual(len(custom_line), 1)
        custom_line.write(
            {"enabled": True, "label": "Background Customer Reference"}
        )

        custom_job, custom_seen = round_trip(custom)
        stored = json.loads(custom_job.field_selection_json)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["key"], custom_line.field_key)
        self.assertEqual(stored[0]["label"], "Background Customer Reference")
        self.assertEqual(custom_seen["mode"], "custom")
        self.assertEqual(custom_seen["specs"], stored)

        empty = self._wizard()
        empty.field_line_ids.write({"enabled": False})
        empty_job, empty_seen = round_trip(empty)
        self.assertEqual(empty_job.field_selection_json, "[]")
        self.assertEqual(empty_seen["mode"], "custom")
        self.assertEqual(empty_seen["specs"], [])
