from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRdsTemplateEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.template = self.env["rds.template"].create(
            {
                "name": "Test Template",
                "code": "TEST_TEMPLATE_ENGINE",
                "layout_style": "technology",
            }
        )

    def test_safe_field_resolution(self):
        partner = self.env["res.partner"].create({"name": "Test Partner", "vat": "1234567890"})
        self.assertEqual(self.template._resolve_path(partner, "name"), "Test Partner")
        self.assertEqual(self.template._resolve_path(partner, "vat"), "1234567890")
        self.assertFalse(self.template._resolve_path(partner, "__class__"))
        self.assertFalse(self.template._resolve_path(partner, "missing_field"))
        self.assertFalse(self.template._resolve_path(partner, "child_ids.name"))

    def test_monetary_currency_uses_odoo_field_resolver(self):
        class MonetaryFieldStub:
            currency_field = None

            @staticmethod
            def get_currency_field(_owner):
                return "currency_id"

        self.assertEqual(
            self.template._field_currency(self.env.company, MonetaryFieldStub()),
            self.env.company.currency_id,
        )

    def test_restricted_boolean_field_fails_closed_instead_of_rendering_no(self):
        boolean_field = self.env.user._fields["active"]

        class RestrictedRecord:
            _fields = {"active": boolean_field}

            def __bool__(self):
                return True

            def __len__(self):
                return 1

            def __getitem__(self, _field_name):
                raise AccessError("restricted")

        value, field, owner = self.template._resolve_path_info(
            RestrictedRecord(),
            "active",
        )

        self.assertFalse(value)
        self.assertIsNone(field)
        self.assertIsNone(owner)
        self.assertEqual(self.template.format_value(value, field=field, owner=owner), "")

    def test_zero_values_are_not_treated_as_empty(self):
        self.assertEqual(self.template.format_value(0), "0")
        self.assertEqual(self.template.format_value(0, "text"), "0")
        self.assertEqual(self.template.format_value(0, "integer"), "0")
        self.assertTrue(self.template.format_value(0.0, "monetary", self.env.company.currency_id))
        self.assertTrue(self.template.format_value(0.0, "percentage"))

    def test_selection_field_uses_its_display_label(self):
        model = self.env["ir.model"]._get("res.partner")
        field = self.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "type")], limit=1
        )
        configured = self.env["rds.template.field"].create(
            {
                "template_id": self.template.id,
                "source_model_id": model.id,
                "field_id": field.id,
                "field_path": "type",
                "label": "Address Type",
            }
        )
        partner = self.env["res.partner"].create(
            {"name": "Selection Test", "type": "invoice"}
        )

        value = self.template.get_field_display_value(partner, configured)

        self.assertTrue(value)
        self.assertNotEqual(value, "invoice")

    def test_field_configuration_rejects_ambiguous_or_unsafe_paths(self):
        partner_model = self.env["ir.model"]._get("res.partner")
        partner_name = self.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "name")], limit=1
        )
        company_name = self.env["ir.model.fields"].search(
            [("model", "=", "res.company"), ("name", "=", "name")], limit=1
        )
        image = self.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "image_1920")], limit=1
        )
        base_values = {
            "template_id": self.template.id,
            "source_model_id": partner_model.id,
            "label": "Unsafe",
        }

        with self.assertRaises(ValidationError):
            self.env["rds.template.field"].create(
                dict(base_values, field_id=partner_name.id, field_path="name..display_name")
            )
        with self.assertRaises(ValidationError):
            self.env["rds.template.field"].create(
                dict(base_values, field_id=company_name.id, field_path="name")
            )
        with self.assertRaises(ValidationError):
            self.env["rds.template.field"].create(
                dict(base_values, field_id=image.id, field_path="image_1920")
            )
        with self.assertRaises(ValidationError):
            self.env["rds.template.field"].create(
                dict(
                    base_values,
                    field_id=partner_name.id,
                    field_path="name",
                    icon_class='fa-user\" onclick=\"alert(1)',
                )
            )

    def test_preview_path_rejects_remote_and_parent_paths(self):
        with self.assertRaises(ValidationError):
            self.template.preview_path = "https://example.com/tracker.png"
        with self.assertRaises(ValidationError):
            self.template.preview_path = "/ranvals_document_studio/static/../secret.png"

    def test_default_template_lookup(self):
        default_template = self.env.ref("ranvals_document_studio.template_technology_blue")
        found = self.env["rds.template"].get_default_for("res.partner", company=self.env.company)
        self.assertEqual(found, default_template)

    def test_base_context_satisfies_shared_qweb_contract(self):
        context = self.template.base_context(self.env.user)

        self.assertTrue(
            {
                "record",
                "company",
                "partner",
                "company_info",
                "partner_info",
                "labels",
                "theme",
                "title",
                "number",
                "tax_label",
                "metadata",
                "columns",
                "lines",
                "totals",
                "notes",
                "bank",
                "bank_labels",
            }.issubset(context)
        )
        self.assertTrue(
            {"company_info", "partner_info", "document_info", "notes", "bank_info"}.issubset(
                context["labels"]
            )
        )
        self.assertTrue(
            {"bank", "branch", "account_name", "iban", "swift"}.issubset(
                context["bank_labels"]
            )
        )

    def test_report_page_fills_the_a4_printable_body(self):
        styles = self.env.ref("ranvals_document_studio.rds_base_styles").arch_db
        paperformat = self.env.ref("ranvals_document_studio.paperformat_rds_a4")

        self.assertIn("min-height: 280mm", styles)
        self.assertTrue(paperformat.disable_shrinking)

    def test_footer_text_is_only_rendered_by_the_page_footer(self):
        external_layout = self.env.ref("ranvals_document_studio.rds_external_layout").arch_db
        totals_and_notes = self.env.ref("ranvals_document_studio.rds_totals_notes").arch_db

        self.assertIn("footer_text", external_layout)
        self.assertNotIn("footer_text", totals_and_notes)
