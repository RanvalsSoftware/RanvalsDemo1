import base64
import copy
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestPlatformFeatures(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sale_model = cls.env["ir.model"]._get("sale.order")
        cls.sale_line_model = cls.env["ir.model"]._get("sale.order.line")
        cls.partner = cls.env["res.partner"].create({
            "name": "DocuCraft Platform Test",
            "country_id": cls.env.ref("base.us").id,
        })
        cls.template = cls.env["rds.template"].create({
            "name": "Platform Test Template",
            "code": "PLATFORM_TEST_TEMPLATE",
            "target_model_id": cls.sale_model.id,
            "layout_style": "technology",
        })
        cls.order = cls.env["sale.order"].create({
            "partner_id": cls.partner.id,
            "company_id": cls.env.company.id,
        })

    def test_01_meaningful_changes_create_versions_and_restore(self):
        versions_before = self.env["rds.template.version"].search_count([
            ("template_id", "=", self.template.id)
        ])
        original = self.template.primary_color
        self.template.write({"primary_color": "#112233"})
        self.assertGreater(
            self.env["rds.template.version"].search_count([("template_id", "=", self.template.id)]),
            versions_before,
        )
        initial = self.env["rds.template.version"].search(
            [("template_id", "=", self.template.id)], order="id", limit=1
        )
        initial.action_restore()
        self.assertEqual(self.template.primary_color, original)

    def test_02_dynamic_fields_are_versioned_and_have_insight(self):
        field_record = self.env["rds.template.field"].create({
            "template_id": self.template.id,
            "section": "metadata",
            "source_model_id": self.sale_model.id,
            "field_path": "partner_id.country_id",
            "label": "Country",
        })
        self.assertIn(field_record.technical_risk, ("related", "restricted"))
        self.assertTrue(field_record.technical_risk_help)
        payload = json.loads(self.env["rds.template.version"].search(
            [("template_id", "=", self.template.id)], order="id desc", limit=1
        ).snapshot_json)
        self.assertEqual(payload["fields"][0]["field_path"], "partner_id.country_id")

    def test_03_conditional_rule_resolution_is_deterministic(self):
        alternate = self.env["rds.template"].create({
            "name": "Platform High Priority",
            "code": "PLATFORM_HIGH_PRIORITY",
            "target_model_id": self.sale_model.id,
            "layout_style": "eco",
        })
        Rule = self.env["rds.template.rule"]
        Rule.create({
            "name": "Generic fallback",
            "priority": 10,
            "template_id": self.template.id,
            "model_id": self.sale_model.id,
        })
        Rule.create({
            "name": "US orders",
            "priority": 50,
            "template_id": alternate.id,
            "model_id": self.sale_model.id,
            "country_id": self.env.ref("base.us").id,
            "currency_id": self.order.currency_id.id,
            "min_total": 0,
        })
        selected = self.env["rds.template"].get_default_for(
            "sale.order", self.env.company, record=self.order
        )
        self.assertEqual(selected, alternate)
        self.partner.country_id = self.env.ref("base.be")
        selected = self.env["rds.template"].get_default_for(
            "sale.order", self.env.company, record=self.order
        )
        self.assertEqual(selected, self.template)
        self.partner.country_id = self.env.ref("base.us")
        alternate.target_model_id = self.env["ir.model"]._get("purchase.order")
        selected = self.env["rds.template"].get_default_for(
            "sale.order", self.env.company, record=self.order
        )
        self.assertEqual(selected, self.template)

    def test_04_json_round_trip_creates_safe_inactive_copy(self):
        self.env["rds.template.field"].create({
            "template_id": self.template.id,
            "section": "metadata",
            "source_model_id": self.sale_model.id,
            "field_path": "client_order_ref",
            "label": "Customer Reference",
        })
        payload = self.template._rds_portable_payload()

        def assert_no_ids(value):
            if isinstance(value, dict):
                self.assertFalse(any(key == "id" or key.endswith("_id") for key in value))
                for child in value.values():
                    assert_no_ids(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_ids(child)

        assert_no_ids(payload)
        values = self.env["rds.template"]._rds_values_from_payload(payload, for_import=True)
        imported = self.env["rds.template"].create(values)
        self.assertFalse(imported.active)
        self.assertFalse(imported.is_default)
        self.assertEqual(imported.company_id, self.env.company)
        self.assertNotEqual(imported.code, self.template.code)
        self.assertEqual(imported.field_ids.field_path, "client_order_ref")

    def test_04b_import_name_stays_within_schema_limit(self):
        payload = self.template._rds_portable_payload()
        payload["template"]["name"] = "N" * 200
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        manager = new_test_user(
            self.env,
            login="rds_portability_name_manager",
            groups="base.group_user,ranvals_document_studio.group_rds_manager",
        )
        wizard = self.env["rds.template.portability.wizard"].with_user(manager).create({
            "mode": "import",
            "upload_name": "long-name.json",
            "upload_data": base64.b64encode(raw),
        })
        action = wizard.action_create_copy()
        imported = self.env["rds.template"].browse(action["res_id"])
        self.assertLessEqual(len(imported.name), 200)
        self.assertIn("İçe Aktarıldı", imported.name)

    def test_05_json_rejects_non_whitelisted_keys(self):
        payload = self.template._rds_portable_payload()
        payload["template"]["preview_path"] = "../../unsafe-preview.png"
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_decode_payload(json.dumps(payload))

    def test_05b_json_rejects_invalid_types_and_values_during_validation(self):
        mutations = [
            ("sequence", []),
            ("active", "yes"),
            ("layout_style", "unknown"),
            ("primary_color", "red"),
            ("heading_font", "comic"),
            ("logo_height_mm", 999),
        ]
        for field_name, invalid_value in mutations:
            with self.subTest(field_name=field_name):
                payload = self.template._rds_portable_payload()
                payload["template"][field_name] = invalid_value
                with self.assertRaises(ValidationError):
                    self.env["rds.template"]._rds_decode_payload(json.dumps(payload))

        payload = self.template._rds_portable_payload()
        payload["fields"] = [{
            "active": "yes",
            "section": "metadata",
            "field_path": "name",
            "label": "Name",
            "sequence": 10,
            "icon_class": "fa-circle-o",
            "value_type": "auto",
            "alignment": "left",
            "width_percent": 16,
            "hide_if_empty": True,
            "bold": False,
            "highlight": False,
            "source_model": "sale.order",
        }]
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_decode_payload(json.dumps(payload))

    def test_06_real_record_preview_delegates_without_audit_blob(self):
        language = self.env["res.lang"].search([("active", "=", True)], limit=1)
        wizard = self.env["rds.template.preview.wizard"].create({
            "template_id": self.template.id,
            "target_model": "sale.order",
            "sale_order_id": self.order.id,
            "language_id": language.id,
        })
        export_model = type(self.env["rds.export.wizard"])
        with patch.object(export_model, "action_preview", autospec=True, return_value={"type": "test"}):
            self.assertEqual(wizard.action_preview(), {"type": "test"})

    def test_06b_preview_uses_record_company_and_rejects_journal_entries(self):
        foreign_company = self.env["res.company"].create({"name": "DocuCraft Preview Company"})
        self.env.user.write({"company_ids": [Command.link(foreign_company.id)]})
        foreign_order = self.env["sale.order"].with_company(foreign_company).create({
            "partner_id": self.partner.id,
            "company_id": foreign_company.id,
        })
        language = self.env["res.lang"].search([("active", "=", True)], limit=1)
        preview = self.env["rds.template.preview.wizard"].create({
            "template_id": self.template.id,
            "target_model": "sale.order",
            "sale_order_id": foreign_order.id,
            "language_id": language.id,
        })
        export_model = type(self.env["rds.export.wizard"])
        with patch.object(
            export_model,
            "action_preview",
            autospec=True,
            return_value={"type": "test"},
        ) as action_preview:
            preview.action_preview()
        self.assertEqual(action_preview.call_args.args[0].env.company, foreign_company)

        journal = self.env["account.journal"].search([
            ("company_id", "=", self.env.company.id),
            ("type", "=", "general"),
        ], limit=1)
        entry = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
        })
        invalid_preview = self.env["rds.template.preview.wizard"].create({
            "template_id": self.template.id,
            "target_model": "account.move",
            "account_move_id": entry.id,
            "language_id": language.id,
        })
        with self.assertRaises(ValidationError):
            invalid_preview.action_preview()

    def test_07_retention_keeps_audit_metadata(self):
        log = self.env["rds.export.log"].sudo().create({
            "name": "Old export",
            "user_id": self.env.user.id,
            "company_id": self.env.company.id,
            "template_id": self.template.id,
            "res_model": self.order._name,
            "res_id": self.order.id,
            "record_name": self.order.display_name,
            "output_format": "pdf",
            "file_name": "old.pdf",
            "file_mimetype": "application/pdf",
            "file_size": 8,
            "file_data": base64.b64encode(b"%PDF-old"),
        })
        old_date = fields.Datetime.now() - timedelta(days=120)
        self.env.cr.execute("UPDATE rds_export_log SET create_date = %s WHERE id = %s", (old_date, log.id))
        self.env["ir.config_parameter"].sudo().set_param(
            "ranvals_document_studio.retention_days", "90"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "ranvals_document_studio.retention_delete_owned_attachments", "False"
        )
        result = self.env["rds.export.log"]._cron_purge_expired_payloads(limit=1)
        self.assertEqual(result["logs"], 1)
        self.assertTrue(log.exists())
        self.assertTrue(log.payload_purged_at)
        self.assertFalse(log.sudo().file_data)
        self.assertEqual(log.file_name, "old.pdf")

    def test_08_health_service_returns_structured_checks(self):
        checks = self.env["rds.system.health"]._collect_checks()
        self.assertTrue(checks)
        self.assertTrue(all(set(item) == {"category", "name", "status", "detail"} for item in checks))
        self.assertIn("ok", {item["status"] for item in checks})

    def test_09_retention_progresses_beyond_batch_limit(self):
        logs = self.env["rds.export.log"].sudo()
        for index in range(3):
            logs |= self.env["rds.export.log"].sudo().create({
                "name": f"Old export {index}",
                "user_id": self.env.user.id,
                "company_id": self.env.company.id,
                "template_id": self.template.id,
                "res_model": self.order._name,
                "res_id": self.order.id,
                "record_name": self.order.display_name,
                "output_format": "pdf",
                "file_name": f"old-{index}.pdf",
                "file_mimetype": "application/pdf",
                "file_size": 8,
                "file_data": base64.b64encode(b"%PDF-old"),
            })
        old_date = fields.Datetime.now() - timedelta(days=120)
        self.env.cr.execute("UPDATE rds_export_log SET create_date = %s WHERE id IN %s", (old_date, tuple(logs.ids)))
        self.env["ir.config_parameter"].sudo().set_param("ranvals_document_studio.retention_days", "90")
        self.env["ir.config_parameter"].sudo().set_param(
            "ranvals_document_studio.retention_delete_owned_attachments", "False"
        )
        for _index in range(3):
            self.env["rds.export.log"]._cron_purge_expired_payloads(limit=1)
        self.assertTrue(all(logs.mapped("payload_purged_at")))
        self.assertFalse(any(logs.sudo().mapped("file_data")))

    def test_10_retention_only_clears_terminal_job_payloads(self):
        if "rds.export.job" not in self.env.registry.models:
            self.skipTest("Background jobs are not installed")
        common = {
            "user_id": self.env.user.id,
            "company_id": self.env.company.id,
            "template_id": self.template.id,
            "res_model": self.order._name,
            "res_ids_json": json.dumps(self.order.ids),
            "record_count": 1,
            "record_names": self.order.display_name,
            "output_format": "pdf",
            "dpi": "150",
            "file_name": "job.pdf",
            "file_mimetype": "application/pdf",
            "file_size": 8,
            "file_data": base64.b64encode(b"%PDF-job"),
        }
        old_date = fields.Datetime.now() - timedelta(days=120)
        queued = self.env["rds.export.job"].sudo().create({
            **common, "name": "QUEUED-JOB", "state": "queued", "finished_at": False,
        })
        done = self.env["rds.export.job"].sudo().create({
            **common, "name": "DONE-JOB", "state": "done", "finished_at": old_date,
        })
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "rds.export.job"),
            ("res_field", "=", "file_data"),
            ("res_id", "in", (done | queued).ids),
        ])
        self.env.cr.execute("UPDATE ir_attachment SET create_date = %s WHERE id IN %s", (old_date, tuple(attachments.ids)))
        self.env["ir.config_parameter"].sudo().set_param("ranvals_document_studio.retention_days", "90")
        result = self.env["rds.export.log"]._cron_purge_expired_payloads(limit=1)
        self.assertEqual(result["jobs"], 1)
        self.assertFalse(done.sudo().file_data)
        self.assertTrue(done.payload_purged_at)
        self.assertTrue(queued.sudo().file_data)

    def test_11_template_versions_are_company_isolated(self):
        foreign_company = self.env["res.company"].create({"name": "DocuCraft Foreign Company"})
        foreign_manager = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "DocuCraft Foreign Manager",
            "login": "docucraft-foreign-manager",
            "company_id": foreign_company.id,
            "company_ids": [Command.set(foreign_company.ids)],
            "group_ids": [Command.set([
                self.env.ref("ranvals_document_studio.group_rds_manager").id,
            ])],
        })
        company_template = self.env["rds.template"].create({
            "name": "Company-isolated template",
            "code": "COMPANY_ISOLATED_TEMPLATE",
            "company_id": self.env.company.id,
            "target_model_id": self.sale_model.id,
            "layout_style": "technology",
        })
        version = self.env["rds.template.version"].search([
            ("template_id", "=", company_template.id)
        ], limit=1)
        company_template.sudo().company_id = foreign_company
        self.assertEqual(
            self.env["rds.template.version"].with_user(foreign_manager).search_count([
                ("id", "=", version.id)
            ]),
            0,
        )
        with self.assertRaises(AccessError):
            version.with_user(foreign_manager).action_restore()

    def test_12_legacy_template_preserves_pre_change_baseline(self):
        versions = self.env["rds.template.version"].search([
            ("template_id", "=", self.template.id),
        ])
        versions.unlink()
        original = self.template.primary_color

        self.template.write({"primary_color": "#ABCDEF"})

        snapshots = self.env["rds.template.version"].search([
            ("template_id", "=", self.template.id),
        ], order="id")
        self.assertGreaterEqual(len(snapshots), 2)
        baseline = json.loads(snapshots[0].snapshot_json)
        latest = json.loads(snapshots[-1].snapshot_json)
        self.assertEqual(baseline["template"]["primary_color"], original)
        self.assertEqual(latest["template"]["primary_color"], "#ABCDEF")

    def test_13_global_retention_policy_requires_system_admin(self):
        manager = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "DocuCraft Retention Manager",
            "login": "docucraft-retention-manager",
            "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set([
                self.env.ref("ranvals_document_studio.group_rds_manager").id,
            ])],
        })
        settings = self.env["rds.retention.settings"].sudo().create({
            "retention_days": 90,
            "delete_owned_attachments": False,
        })
        with self.assertRaises(AccessError):
            settings.with_user(manager).action_save()

    def test_14_version_history_cannot_be_bypassed_or_forged(self):
        count_before = self.env["rds.template.version"].search_count([
            ("template_id", "=", self.template.id),
        ])
        self.template.with_context(rds_skip_versioning=True).write({
            "secondary_color": "#123456",
        })
        versions = self.env["rds.template.version"].search([
            ("template_id", "=", self.template.id),
        ], order="id")
        self.assertGreater(len(versions), count_before)
        self.assertEqual(versions[-1].version_user_id, self.env.user)

        manager = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "DocuCraft Version Manager",
            "login": "docucraft-version-manager",
            "company_id": self.env.company.id,
            "company_ids": [Command.set(self.env.company.ids)],
            "group_ids": [Command.set([
                self.env.ref("ranvals_document_studio.group_rds_manager").id,
            ])],
        })
        with self.assertRaises(AccessError):
            self.env["rds.template.version"].with_user(manager).create({
                "template_id": self.template.id,
                "company_id": self.env.company.id,
                "version_user_id": manager.id,
                "snapshot_json": "{}",
                "checksum": "0" * 64,
                "note": "forged",
            })

    def test_15_snapshot_restores_all_stored_translations(self):
        french = self.env["res.lang"].with_context(active_test=False).search([
            ("code", "=", "fr_FR")
        ], limit=1)
        if not french.active:
            french.action_unarchive()
        template = self.env["rds.template"].create({
            "name": "Translation snapshot",
            "code": "TRANSLATION_SNAPSHOT",
            "target_model_id": self.sale_model.id,
            "layout_style": "technology",
        })
        field_record = self.env["rds.template.field"].create({
            "template_id": template.id,
            "active": False,
            "section": "metadata",
            "source_model_id": self.sale_model.id,
            "field_path": "client_order_ref",
            "label": "Customer reference",
        })
        template.with_context(lang="en_US").write({
            "name": "English template",
            "tagline": "English tagline",
            "footer_text": "English footer",
            "notes_text": "<p>English notes</p>",
        })
        template.with_context(lang="fr_FR").write({
            "name": "Modèle français",
            "tagline": "Slogan français",
            "footer_text": "Pied français",
            "notes_text": "<p>Notes françaises</p>",
        })
        field_record.with_context(lang="en_US").write({"label": "Reference"})
        field_record.with_context(lang="fr_FR").write({"label": "Référence"})
        expected = {
            field_name: template._fields[field_name]._get_stored_translations(template)
            for field_name in ("name", "tagline", "footer_text", "notes_text")
        }
        expected_label = field_record._fields["label"]._get_stored_translations(
            field_record
        )

        payload = template._rds_snapshot_payload()
        self.assertEqual(payload["snapshot"]["translations"]["template"], expected)
        self.assertEqual(
            payload["snapshot"]["translations"]["field_labels"],
            [expected_label],
        )
        portable = template._rds_portable_payload()
        self.assertNotIn("snapshot", portable)
        self.assertNotIn("translations", json.dumps(portable, ensure_ascii=False))

        version = template._rds_create_version(payload, note="translation target")
        template.with_context(lang="en_US").write({
            field_name: f"Changed {field_name}" for field_name in expected
        })
        template.with_context(lang="fr_FR").write({
            field_name: f"Modifié {field_name}" for field_name in expected
        })
        field_record.with_context(lang="en_US").write({"label": "Changed"})
        field_record.with_context(lang="fr_FR").write({"label": "Modifiée"})
        version.action_restore()

        for field_name, translations in expected.items():
            self.assertEqual(
                template._fields[field_name]._get_stored_translations(template),
                translations,
            )
        restored_field = template._rds_ordered_template_fields()
        self.assertEqual(len(restored_field), 1)
        self.assertEqual(
            restored_field._fields["label"]._get_stored_translations(restored_field),
            expected_label,
        )

    def test_16_old_snapshot_without_translation_metadata_still_restores(self):
        payload = self.template._rds_snapshot_payload()
        payload["snapshot"].pop("translations")
        raw = json.dumps(payload, ensure_ascii=False)
        decoded = self.env["rds.template"]._rds_decode_payload(
            raw, allow_snapshot=True
        )
        self.assertEqual(decoded["snapshot"]["code"], self.template.code)
        self.template._rds_values_from_payload(decoded, allow_snapshot=True)

    def test_17_internal_snapshot_has_separate_bounded_limits(self):
        payload = self.template._rds_snapshot_payload()
        prototype = {
            "active": False,
            "section": "metadata",
            "field_path": "client_order_ref",
            "label": "Legacy field",
            "sequence": 10,
            "icon_class": "fa-circle-o",
            "value_type": "auto",
            "alignment": "left",
            "width_percent": 16,
            "hide_if_empty": True,
            "bold": False,
            "highlight": False,
            "source_model": "sale.order",
        }
        payload["fields"] = [
            {**prototype, "label": f"Legacy field {index}"}
            for index in range(101)
        ]
        payload["snapshot"]["translations"]["field_labels"] = [
            {"en_US": item["label"]} for item in payload["fields"]
        ]
        payload["template"]["tagline"] = "T" * 501
        payload["snapshot"]["translations"]["template"]["tagline"] = {
            "en_US": "T" * 501
        }
        self.env["rds.template"]._rds_validate_payload(
            payload, allow_snapshot=True
        )

        portable = copy.deepcopy(payload)
        portable.pop("snapshot")
        portable["template"]["active"] = False
        portable["template"]["is_default"] = False
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_validate_payload(portable)

        oversized = copy.deepcopy(payload)
        oversized["fields"] = [copy.deepcopy(prototype) for _index in range(1001)]
        oversized["snapshot"]["translations"]["field_labels"] = [
            {"en_US": "Legacy"} for _index in range(1001)
        ]
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_validate_payload(
                oversized, allow_snapshot=True
            )

    def test_18_snapshot_translation_metadata_is_strictly_validated(self):
        payload = self.template._rds_snapshot_payload()
        payload["snapshot"]["translations"]["template"]["name"]["not_a_language"] = "Unsafe"
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_validate_payload(
                payload, allow_snapshot=True
            )

        payload = self.template._rds_snapshot_payload()
        payload["snapshot"]["translations"]["template"]["name"]["en_US"] = 42
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_validate_payload(
                payload, allow_snapshot=True
            )

        payload = self.template._rds_snapshot_payload()
        large_notes = "N" * (600 * 1024)
        payload["template"]["notes_text"] = large_notes
        payload["snapshot"]["translations"]["template"]["notes_text"] = {
            "en_US": large_notes
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(raw), 512 * 1024)
        self.env["rds.template"]._rds_decode_payload(
            raw, allow_snapshot=True
        )
        with self.assertRaises(ValidationError):
            self.env["rds.template"]._rds_decode_payload(raw)
