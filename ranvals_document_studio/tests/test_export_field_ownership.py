import json

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestRdsExportFieldOwnership(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(
            cls.env,
            login="rds_export_field_owner",
            groups="base.group_user",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="rds_export_field_other",
            groups="base.group_user",
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "DocuCraft ownership test customer"}
        )
        cls.order = cls.env["sale.order"].create(
            {"partner_id": cls.partner.id}
        )
        cls.template = cls.env.ref(
            "ranvals_document_studio.template_graphite_copper"
        )

    def _wizard_for(self, user):
        return self.env["rds.export.wizard"].with_user(user).create(
            {
                "res_model": self.order._name,
                "res_ids_json": json.dumps(self.order.ids),
                "template_id": self.template.id,
                "output_format": "pdf",
                "field_selection_mode": "custom",
            }
        )

    def test_owner_cannot_move_field_line_to_another_users_wizard(self):
        owner_wizard = self._wizard_for(self.owner)
        other_wizard = self._wizard_for(self.other_user)
        self.assertEqual(owner_wizard.create_uid, self.owner)
        self.assertEqual(other_wizard.create_uid, self.other_user)

        line = self.env["rds.export.field.line"].with_user(self.owner).create(
            {
                "wizard_id": owner_wizard.id,
                "section": "metadata",
                "enabled": True,
                "sequence": 10,
                "source_model": self.order._name,
                "field_key": "builtin:metadata:name",
                "field_path": "name",
                "label": "Document Number",
                "auto_label": "Document Number",
                "sample_value": self.order.name,
                "origin": "builtin",
            }
        )

        # Mirror a crafted model RPC: changing the relational owner directly
        # must be rejected before the destination wizard can be adopted.
        rpc_line = self.env["rds.export.field.line"].with_user(self.owner).browse(
            line.id
        )
        with self.assertRaisesRegex(ValidationError, "başka bir işleme taşınamaz"):
            rpc_line.write({"wizard_id": other_wizard.id})

        self.assertEqual(line.wizard_id, owner_wizard)
