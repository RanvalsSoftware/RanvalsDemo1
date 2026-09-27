import hashlib
import json
import re

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL
from psycopg2.extras import Json

from ..tools.common import check_record_access
from .rds_template import FONT_CSS

TEMPLATE_SCHEMA = "docucraft.template"
TEMPLATE_SCHEMA_VERSION = 1
MAX_TEMPLATE_FIELDS = 100
MAX_TEMPLATE_JSON_BYTES = 512 * 1024
MAX_INTERNAL_TEMPLATE_FIELDS = 1000
MAX_INTERNAL_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_TRANSLATION_LANGUAGES = 128
_VERSIONING_TOKEN = object()
SUPPORTED_FONT_KEYS = frozenset(FONT_CSS)

TEMPLATE_VALUE_FIELDS = (
    "name",
    "sequence",
    "active",
    "is_default",
    "layout_style",
    "primary_color",
    "secondary_color",
    "accent_color",
    "text_color",
    "heading_font",
    "body_font",
    "logo_height_mm",
    "tagline",
    "footer_text",
    "notes_text",
    "show_company",
    "show_partner",
    "show_metadata",
    "show_notes",
    "show_bank",
    "show_footer",
)

FIELD_VALUE_FIELDS = (
    "active",
    "section",
    "field_path",
    "label",
    "sequence",
    "icon_class",
    "value_type",
    "alignment",
    "width_percent",
    "hide_if_empty",
    "bold",
    "highlight",
)

STRING_LIMITS = {
    "name": 200,
    "tagline": 500,
    "footer_text": 2000,
    "notes_text": 50000,
    "field_path": 255,
    "label": 200,
    "icon_class": 80,
    "source_model": 128,
    "target_model": 128,
}

INTERNAL_STRING_LIMITS = {
    **STRING_LIMITS,
    "name": 1000,
    "tagline": 10000,
    "footer_text": 50000,
    "notes_text": 1024 * 1024,
    "label": 4000,
}

TRANSLATED_TEMPLATE_FIELDS = ("name", "tagline", "footer_text", "notes_text")


class RdsTemplateVersioning(models.Model):
    _inherit = "rds.template"

    version_ids = fields.One2many(
        "rds.template.version",
        "template_id",
        string="Versions",
        readonly=True,
    )
    version_count = fields.Integer(compute="_compute_version_count")

    @api.depends("version_ids")
    def _compute_version_count(self):
        grouped = self.env["rds.template.version"]._read_group(
            [("template_id", "in", self.ids)],
            ["template_id"],
            ["__count"],
        ) if self.ids else []
        counts = {template.id: count for template, count in grouped}
        for template in self:
            template.version_count = counts.get(template.id, 0)

    def _rds_serialized_field_values(self):
        self.ensure_one()
        result = []
        for item in self._rds_ordered_template_fields():
            values = {name: item[name] for name in FIELD_VALUE_FIELDS}
            values["source_model"] = item.source_model_id.model
            result.append(values)
        return result

    def _rds_ordered_template_fields(self):
        """Read children from storage so versioning never relies on stale x2many cache."""
        self.ensure_one()
        return self.env["rds.template.field"].with_context(active_test=False).search(
            [("template_id", "=", self.id)],
            order="section, sequence, id",
        )

    @api.model
    def _rds_stored_translations(self, record, field_name):
        """Read the exact JSONB translation map without language fallback."""
        check_record_access(record)
        field = record._fields[field_name]
        if not field.translate or not field.store:
            return {}
        stored = field._get_stored_translations(record) or {}
        return {language: value for language, value in stored.items()}

    def _rds_snapshot_translations(self):
        self.ensure_one()
        ordered_fields = self._rds_ordered_template_fields()
        return {
            "template": {
                field_name: self._rds_stored_translations(self, field_name)
                for field_name in TRANSLATED_TEMPLATE_FIELDS
            },
            "field_labels": [
                self._rds_stored_translations(item, "label")
                for item in ordered_fields
            ],
        }

    def _rds_portable_payload(self):
        """Return a strictly declarative payload without database identifiers."""
        self.ensure_one()
        check_record_access(self)
        values = {name: self[name] for name in TEMPLATE_VALUE_FIELDS}
        values["target_model"] = self.target_model_id.model or False
        # A transported template must never silently become the default or
        # active in another database/company.
        values["active"] = False
        values["is_default"] = False
        payload = {
            "schema": TEMPLATE_SCHEMA,
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "template": values,
            "fields": self._rds_serialized_field_values(),
        }
        return self._rds_validate_payload(payload)

    def _rds_snapshot_payload(self):
        self.ensure_one()
        check_record_access(self)
        values = {name: self[name] for name in TEMPLATE_VALUE_FIELDS}
        values["target_model"] = self.target_model_id.model or False
        # Internal-only metadata keeps code/company changes restorable without
        # exposing database identifiers through the portable JSON format.
        payload = {
            "schema": TEMPLATE_SCHEMA,
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "template": values,
            "fields": self._rds_serialized_field_values(),
            "snapshot": {
                "code": self.code,
                "company_id": self.company_id.id or False,
                "translations": self._rds_snapshot_translations(),
            },
        }
        return self._rds_validate_payload(payload, allow_snapshot=True)

    @api.model
    def _rds_validate_translation_map(self, translations, *, value_limit):
        if not isinstance(translations, dict) or len(translations) > MAX_TRANSLATION_LANGUAGES:
            raise ValidationError(_("The template version language translations are invalid."))
        language_codes = set(translations)
        if any(
            not isinstance(code, str)
            or len(code) > 32
            or not re.fullmatch(r"[A-Za-z0-9_@-]+", code)
            for code in language_codes
        ):
            raise ValidationError(_("The template version contains an invalid language code."))
        for value in translations.values():
            if not isinstance(value, str) or len(value) > value_limit:
                raise ValidationError(_("A translation in the template version is invalid or too long."))
        return language_codes

    @api.model
    def _rds_validate_translation_languages(self, language_codes):
        Language = self.env["res.lang"].with_context(active_test=False)
        check_record_access(Language)
        accessible = set(
            Language.search([("code", "in", list(language_codes))]).mapped("code")
        )
        missing = language_codes - accessible
        if missing:
            known = set(
                Language.sudo().search([("code", "in", list(missing))]).mapped("code")
            )
            if known:
                raise AccessError(_("You do not have access to one of the languages in the template version."))
            raise ValidationError(_("The template version contains a language that is not available in this database."))

    @api.model
    def _rds_validate_snapshot_translations(self, translations, field_values):
        if not isinstance(translations, dict) or set(translations) != {
            "template", "field_labels"
        }:
            raise ValidationError(_("The template version translation structure is invalid."))
        template_translations = translations["template"]
        if not isinstance(template_translations, dict) or set(template_translations) != set(
            TRANSLATED_TEMPLATE_FIELDS
        ):
            raise ValidationError(_("The template version field translations are missing or invalid."))
        language_codes = set()
        for field_name in TRANSLATED_TEMPLATE_FIELDS:
            language_codes.update(self._rds_validate_translation_map(
                template_translations[field_name],
                value_limit=INTERNAL_STRING_LIMITS[field_name],
            ))
        field_labels = translations["field_labels"]
        if not isinstance(field_labels, list) or len(field_labels) != len(field_values):
            raise ValidationError(_("The template version dynamic-field translations do not match."))
        for label_translations in field_labels:
            language_codes.update(self._rds_validate_translation_map(
                label_translations,
                value_limit=INTERNAL_STRING_LIMITS["label"],
            ))
        self._rds_validate_translation_languages(language_codes)
        return translations

    @api.model
    def _rds_validate_payload(self, payload, *, allow_snapshot=False):
        portable_keys = {"schema", "schema_version", "template", "fields"}
        allowed_key_sets = [portable_keys]
        if allow_snapshot:
            allowed_key_sets.append(portable_keys | {"snapshot"})
        if not isinstance(payload, dict) or set(payload) not in allowed_key_sets:
            raise ValidationError(_("The top-level structure of the template file is invalid."))
        if payload.get("schema") != TEMPLATE_SCHEMA or payload.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
            raise ValidationError(_("The template file uses an unsupported schema version."))

        internal_snapshot = allow_snapshot and "snapshot" in payload
        template_values = payload.get("template")
        field_values = payload.get("fields")
        allowed_template = set(TEMPLATE_VALUE_FIELDS) | {"target_model"}
        allowed_field = set(FIELD_VALUE_FIELDS) | {"source_model"}
        if not isinstance(template_values, dict) or set(template_values) != allowed_template:
            raise ValidationError(_("The template file contains unsupported or missing fields."))
        field_limit = MAX_INTERNAL_TEMPLATE_FIELDS if internal_snapshot else MAX_TEMPLATE_FIELDS
        if not isinstance(field_values, list) or len(field_values) > field_limit:
            raise ValidationError(_("A template can contain at most %s dynamic fields.") % field_limit)
        string_limits = INTERNAL_STRING_LIMITS if internal_snapshot else STRING_LIMITS
        for name, limit in string_limits.items():
            if name in template_values:
                value = template_values[name]
                if value not in (False, None) and (not isinstance(value, str) or len(value) > limit):
                    raise ValidationError(_("Field %s in the template file is invalid.") % name)

        if not isinstance(template_values["name"], str) or not template_values["name"].strip():
            raise ValidationError(_("The template name cannot be empty."))
        boolean_template_fields = {
            "active", "is_default", "show_company", "show_partner",
            "show_metadata", "show_notes", "show_bank", "show_footer",
        }
        if any(not isinstance(template_values[name], bool) for name in boolean_template_fields):
            raise ValidationError(_("One of the template boolean options is invalid."))
        if (
            isinstance(template_values["sequence"], bool)
            or not isinstance(template_values["sequence"], int)
            or not -(2**31) <= template_values["sequence"] < 2**31
        ):
            raise ValidationError(_("The template sequence must be a valid integer."))
        if template_values["layout_style"] not in {
            "beauty",
            "construction",
            "technology",
            "industrial",
            "eco",
            "furniture",
            "noir_executive",
            "royal_ledger",
            "swiss_grid",
            "arctic_minimal",
            "indigo_flow",
            "emerald_ledger",
            "sandstone_classic",
            "graphite_copper",
        }:
            raise ValidationError(_("The template layout style is not supported."))
        for color_name in ("primary_color", "secondary_color", "accent_color", "text_color"):
            color = template_values[color_name]
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                raise ValidationError(_("One of the template colors is not in #RRGGBB format."))
        if (
            template_values["heading_font"] not in SUPPORTED_FONT_KEYS
            or template_values["body_font"] not in SUPPORTED_FONT_KEYS
        ):
            raise ValidationError(_("The template font is not supported."))
        logo_height = template_values["logo_height_mm"]
        if isinstance(logo_height, bool) or not isinstance(logo_height, int) or not 8 <= logo_height <= 40:
            raise ValidationError(_("Logo height must be an integer between 8 and 40 mm."))

        target_model = template_values.get("target_model")
        if target_model:
            model = self.env["ir.model"]._get(target_model)
            if not model or model.transient:
                raise ValidationError(_("The target model is not available in this Odoo database."))

        snapshot = payload.get("snapshot")
        if snapshot is not None:
            allowed_snapshot_keys = (
                {"code", "company_id"},
                {"code", "company_id", "translations"},
            )
            if (
                not allow_snapshot
                or not isinstance(snapshot, dict)
                or set(snapshot) not in allowed_snapshot_keys
            ):
                raise ValidationError(_("The template version information is invalid."))
            if not isinstance(snapshot.get("code"), str) or not re.fullmatch(
                r"[A-Za-z0-9_]{1,72}", snapshot["code"]
            ):
                raise ValidationError(_("The template version code is invalid."))
            company_id = snapshot.get("company_id")
            if company_id is not False and (
                isinstance(company_id, bool) or not isinstance(company_id, int) or company_id <= 0
            ):
                raise ValidationError(_("The template version company is invalid."))
            if company_id:
                company = self.env["res.company"].browse(company_id).exists()
                if not company or (not self.env.su and company not in self.env.companies):
                    raise AccessError(_("You do not have access to the template version company."))
            if "translations" in snapshot:
                self._rds_validate_snapshot_translations(
                    snapshot["translations"], field_values
                )

        active_layout_groups = {}
        for item in field_values:
            if not isinstance(item, dict) or set(item) != allowed_field:
                raise ValidationError(_("The dynamic-field configuration is invalid."))
            for name, limit in string_limits.items():
                if name in item:
                    value = item[name]
                    if value not in (False, None) and (not isinstance(value, str) or len(value) > limit):
                        raise ValidationError(_("Dynamic field value %s is invalid.") % name)
            source_model = item.get("source_model")
            model = self.env["ir.model"]._get(source_model) if isinstance(source_model, str) else False
            if not model or model.transient:
                raise ValidationError(_("The dynamic field source model is unavailable."))
            if not isinstance(item["label"], str) or not item["label"].strip():
                raise ValidationError(_("The dynamic field label cannot be empty."))
            if item["section"] not in {"metadata", "line"}:
                raise ValidationError(_("The dynamic field section is not supported."))
            if item["value_type"] not in {
                "auto", "text", "date", "monetary", "percentage", "integer", "float"
            }:
                raise ValidationError(_("The dynamic field value type is not supported."))
            if item["alignment"] not in {"left", "center", "right"}:
                raise ValidationError(_("The dynamic field alignment is not supported."))
            if any(
                not isinstance(item[name], bool)
                for name in ("active", "hide_if_empty", "bold", "highlight")
            ):
                raise ValidationError(_("One of the dynamic field boolean options is invalid."))
            for name in ("sequence", "width_percent"):
                value = item[name]
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValidationError(_("The dynamic field numeric value is invalid."))
            if not -(2**31) <= item["sequence"] < 2**31:
                raise ValidationError(_("The dynamic field sequence is outside the allowed range."))
            if not 5 <= item["width_percent"] <= 100:
                raise ValidationError(_("Dynamic field width must be between 5 and 100 percent."))
            icon = item["icon_class"]
            if icon not in (False, None) and (
                not isinstance(icon, str) or not re.fullmatch(r"fa-[a-z0-9-]+", icon)
            ):
                raise ValidationError(_("The dynamic field icon class is invalid."))

            parts = item["field_path"].split(".") if isinstance(item["field_path"], str) else []
            if not 1 <= len(parts) <= 4 or any(
                part.startswith("_") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", part)
                for part in parts
            ):
                raise ValidationError(_("The dynamic field path is invalid."))
            current_model = self.env[source_model]
            for index, part in enumerate(parts):
                field = current_model._fields.get(part)
                if not field:
                    raise ValidationError(_("The dynamic field path was not found on the source model."))
                if index < len(parts) - 1:
                    if field.type != "many2one" or not field.comodel_name:
                        raise ValidationError(_("Intermediate parts of the dynamic field path must be Many2one fields."))
                    current_model = self.env[field.comodel_name]
                elif field.type == "binary" or getattr(field, "exportable", True) is False:
                    raise ValidationError(_("The dynamic field cannot be exported as document text."))

            target_model = template_values.get("target_model")
            if item["section"] == "metadata" and target_model and source_model != target_model:
                raise ValidationError(_("The detail-card field source model must match the target model."))
            if item["active"]:
                key = (item["section"], source_model)
                group = active_layout_groups.setdefault(key, [])
                group.append(item)

        if not internal_snapshot:
            for (section, _source_model), items in active_layout_groups.items():
                if section == "metadata" and len(items) > 8:
                    raise ValidationError(_("A model can use at most 8 active detail cards."))
                if section == "line" and sum(item["width_percent"] for item in items) > 100:
                    raise ValidationError(_("The total width of active table columns cannot exceed 100 percent."))
        return payload

    @api.model
    def _rds_decode_payload(self, raw_json, *, allow_snapshot=False):
        if not isinstance(raw_json, (str, bytes, bytearray)):
            raise ValidationError(_("The template file must be JSON text."))
        raw_bytes = raw_json.encode("utf-8") if isinstance(raw_json, str) else bytes(raw_json)
        max_bytes = MAX_INTERNAL_SNAPSHOT_BYTES if allow_snapshot else MAX_TEMPLATE_JSON_BYTES
        if not raw_bytes or len(raw_bytes) > max_bytes:
            if allow_snapshot:
                raise ValidationError(_("The template version is empty or exceeds the 4 MB limit."))
            raise ValidationError(_("The template file is empty or exceeds the 512 KB limit."))
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise ValidationError(_("The template file is not valid UTF-8 JSON.")) from error
        return self._rds_validate_payload(payload, allow_snapshot=allow_snapshot)

    @api.model
    def _rds_values_from_payload(self, payload, *, for_import=False, allow_snapshot=False):
        payload = self._rds_validate_payload(payload, allow_snapshot=allow_snapshot)
        source = payload["template"]
        values = {name: source[name] for name in TEMPLATE_VALUE_FIELDS}
        target_model = source.get("target_model")
        values["target_model_id"] = self.env["ir.model"]._get(target_model).id if target_model else False
        if for_import:
            values.update({
                "active": False,
                "is_default": False,
                "company_id": self.env.company.id,
                "code": self._generate_unique_code(
                    f"{values.get('name', 'TEMPLATE')}_IMPORT", self.env.company.id
                ),
            })
        elif payload.get("snapshot"):
            values.update({
                "code": payload["snapshot"]["code"],
                "company_id": payload["snapshot"]["company_id"],
            })
        field_commands = [] if for_import else [Command.clear()]
        for item in payload["fields"]:
            source_model = self.env["ir.model"]._get(item["source_model"])
            child = {name: item[name] for name in FIELD_VALUE_FIELDS}
            child["source_model_id"] = source_model.id
            field_commands.append(Command.create(child))
        values["field_ids"] = field_commands
        return values

    @api.model
    def _rds_set_stored_translations(self, record, field_name, translations):
        """Restore one validated translated field exactly, including inactive languages."""
        record.ensure_one()
        check_record_access(record, "write")
        field = record._fields[field_name]
        record._check_field_access(field, "write")
        if not field.translate or not field.store:
            raise ValidationError(_("The template version contains a non-translatable field."))
        record.flush_recordset([field_name])
        record.env.cr.execute(SQL(
            "UPDATE %s SET %s = %s WHERE id = %s",
            SQL.identifier(record._table),
            SQL.identifier(field_name),
            Json(translations) if translations else None,
            record.id,
        ))
        record.invalidate_recordset([field_name])
        record.modified([field_name])

    def _rds_restore_snapshot_translations(self, payload):
        self.ensure_one()
        snapshot = payload.get("snapshot") or {}
        translations = snapshot.get("translations")
        if translations is None:
            # Backward compatibility: snapshots created before translated
            # metadata existed keep the historical current-language behavior.
            return
        self._rds_validate_snapshot_translations(translations, payload["fields"])
        for field_name in TRANSLATED_TEMPLATE_FIELDS:
            self._rds_set_stored_translations(
                self, field_name, translations["template"][field_name]
            )
        ordered_fields = self._rds_ordered_template_fields()
        for item, label_translations in zip(
            ordered_fields, translations["field_labels"], strict=True
        ):
            self._rds_set_stored_translations(
                item, "label", label_translations
            )

    @api.model
    def _rds_diff_summary(self, before, after):
        changed = []
        before_template = before.get("template", {})
        after_template = after.get("template", {})
        for name in TEMPLATE_VALUE_FIELDS + ("target_model",):
            if before_template.get(name) != after_template.get(name):
                changed.append(self._fields.get(name).string if name in self._fields else _("Target Model"))
        if before.get("fields") != after.get("fields"):
            changed.append(_("Dynamic Fields"))
        before_snapshot = before.get("snapshot", {})
        after_snapshot = after.get("snapshot", {})
        if before_snapshot.get("code") != after_snapshot.get("code"):
            changed.append(_("Template Code"))
        if before_snapshot.get("company_id") != after_snapshot.get("company_id"):
            changed.append(_("Company"))
        if before_snapshot.get("translations") != after_snapshot.get("translations"):
            changed.append(_("Translations"))
        return ", ".join(changed[:12]) or _("Configuration")

    def _rds_create_version(self, payload, note=None, diff_summary=None):
        self.ensure_one()
        payload = self._rds_validate_payload(
            payload,
            allow_snapshot="snapshot" in payload,
        )
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        max_bytes = (
            MAX_INTERNAL_SNAPSHOT_BYTES
            if "snapshot" in payload
            else MAX_TEMPLATE_JSON_BYTES
        )
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValidationError(_("The template version exceeds the 4 MB limit."))
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        Version = self.env["rds.template.version"].sudo()
        latest = Version.search(
            [("template_id", "=", self.id)], order="id desc", limit=1
        )
        if latest.checksum == checksum:
            return latest
        return Version.with_context(rds_versioning_token=_VERSIONING_TOKEN).create({
            "template_id": self.id,
            "company_id": self.company_id.id or False,
            "version_user_id": self.env.user.id,
            "snapshot_json": raw,
            "checksum": checksum,
            "note": note or _("Automatic Version"),
            "diff_summary": diff_summary or _("Initial Version"),
        })

    def _rds_ensure_baseline(self, payload=None):
        """Preserve the pre-change state for templates created before versioning existed."""
        self.ensure_one()
        existing = self.env["rds.template.version"].search_count(
            [("template_id", "=", self.id)],
            limit=1,
        )
        if existing:
            return existing
        return self._rds_create_version(
            payload or self._rds_snapshot_payload(),
            note=_("Baseline Version"),
            diff_summary=_("Initial state before versioning"),
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("rds_versioning_token") is not _VERSIONING_TOKEN:
            for record in records:
                record._rds_create_version(record._rds_snapshot_payload(), note=_("Initial Version"))
        return records

    def write(self, vals):
        if self.env.context.get("rds_versioning_token") is _VERSIONING_TOKEN:
            return super().write(vals)
        before = {record.id: record._rds_snapshot_payload() for record in self}
        for record in self:
            record._rds_ensure_baseline(before[record.id])
        result = super(
            RdsTemplateVersioning,
            self.with_context(rds_versioning_token=_VERSIONING_TOKEN),
        ).write(vals)
        for record in self:
            after = record._rds_snapshot_payload()
            if before[record.id] != after:
                record._rds_create_version(
                    after,
                    diff_summary=record._rds_diff_summary(before[record.id], after),
                )
        return result

    def update_field_translations(self, field_name, translations, source_lang=""):
        if (
            self.env.context.get("rds_versioning_token") is _VERSIONING_TOKEN
            or field_name not in TRANSLATED_TEMPLATE_FIELDS
        ):
            return super().update_field_translations(
                field_name, translations, source_lang=source_lang
            )
        self.ensure_one()
        before = self._rds_snapshot_payload()
        self._rds_ensure_baseline(before)
        result = super(
            RdsTemplateVersioning,
            self.with_context(rds_versioning_token=_VERSIONING_TOKEN),
        ).update_field_translations(
            field_name, translations, source_lang=source_lang
        )
        after = self._rds_snapshot_payload()
        if before != after:
            self._rds_create_version(
                after,
                diff_summary=self._rds_diff_summary(before, after),
            )
        return result

    def action_view_versions(self):
        self.ensure_one()
        check_record_access(self)
        return {
            "type": "ir.actions.act_window",
            "name": _("%s Versions") % self.display_name,
            "res_model": "rds.template.version",
            "view_mode": "list,form",
            "domain": [("template_id", "=", self.id)],
            "context": {"default_template_id": self.id},
        }


class RdsTemplateFieldVersioning(models.Model):
    _inherit = "rds.template.field"

    def _rds_ensure_template_baselines(self, templates, before):
        for template in templates.exists():
            template._rds_ensure_baseline(before[template.id])

    def _rds_snapshot_templates(self, templates, before):
        for template in templates.exists():
            after = template._rds_snapshot_payload()
            if before.get(template.id) != after:
                template._rds_create_version(
                    after,
                    diff_summary=template._rds_diff_summary(before.get(template.id, {}), after),
                )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("rds_versioning_token") is _VERSIONING_TOKEN:
            return super().create(vals_list)
        template_ids = {value.get("template_id") for value in vals_list if value.get("template_id")}
        templates = self.env["rds.template"].browse(template_ids).exists()
        before = {template.id: template._rds_snapshot_payload() for template in templates}
        self._rds_ensure_template_baselines(templates, before)
        records = super(
            RdsTemplateFieldVersioning,
            self.with_context(rds_versioning_token=_VERSIONING_TOKEN),
        ).create(vals_list)
        self._rds_snapshot_templates(templates, before)
        return records

    def write(self, vals):
        if self.env.context.get("rds_versioning_token") is _VERSIONING_TOKEN:
            return super().write(vals)
        templates = self.template_id
        before = {template.id: template._rds_snapshot_payload() for template in templates}
        self._rds_ensure_template_baselines(templates, before)
        result = super(
            RdsTemplateFieldVersioning,
            self.with_context(rds_versioning_token=_VERSIONING_TOKEN),
        ).write(vals)
        self._rds_snapshot_templates(templates | self.template_id, before)
        return result

    def unlink(self):
        if self.env.context.get("rds_versioning_token") is _VERSIONING_TOKEN:
            return super().unlink()
        templates = self.template_id
        before = {template.id: template._rds_snapshot_payload() for template in templates}
        self._rds_ensure_template_baselines(templates, before)
        result = super(
            RdsTemplateFieldVersioning,
            self.with_context(rds_versioning_token=_VERSIONING_TOKEN),
        ).unlink()
        self._rds_snapshot_templates(templates, before)
        return result

    def update_field_translations(self, field_name, translations, source_lang=""):
        if (
            self.env.context.get("rds_versioning_token") is _VERSIONING_TOKEN
            or field_name != "label"
        ):
            return super().update_field_translations(
                field_name, translations, source_lang=source_lang
            )
        self.ensure_one()
        templates = self.template_id
        before = {
            template.id: template._rds_snapshot_payload()
            for template in templates
        }
        self._rds_ensure_template_baselines(templates, before)
        result = super(
            RdsTemplateFieldVersioning,
            self.with_context(rds_versioning_token=_VERSIONING_TOKEN),
        ).update_field_translations(
            field_name, translations, source_lang=source_lang
        )
        self._rds_snapshot_templates(templates, before)
        return result


class RdsTemplateVersion(models.Model):
    _name = "rds.template.version"
    _description = "DocuCraft Template Version"
    _order = "create_date desc, id desc"

    template_id = fields.Many2one("rds.template", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", index=True, ondelete="cascade")
    version_user_id = fields.Many2one(
        "res.users",
        string="Changed By",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
        ondelete="restrict",
    )
    snapshot_json = fields.Text(required=True, readonly=True, groups="ranvals_document_studio.group_rds_manager")
    checksum = fields.Char(required=True, readonly=True, index=True)
    note = fields.Char(required=True, default=lambda self: _("Automatic Version"))
    diff_summary = fields.Char(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and self.env.context.get("rds_versioning_token") is not _VERSIONING_TOKEN:
            raise AccessError(_("Template versions can only be created through the validated change flow."))
        return super().create(vals_list)

    def action_restore(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("ranvals_document_studio.group_rds_manager"):
            raise AccessError(_("You do not have permission to restore template versions."))
        check_record_access(self)
        check_record_access(self.template_id, "write")
        payload = self.env["rds.template"]._rds_decode_payload(
            self.snapshot_json,
            allow_snapshot=True,
        )
        values = self.env["rds.template"]._rds_values_from_payload(
            payload,
            allow_snapshot=True,
        )
        # One2many command evaluation honors ``active_test``.  Remove every
        # current child explicitly so an archived field cannot survive the
        # restore and shift the position-based translation metadata.
        current_fields = self.template_id._rds_ordered_template_fields()
        if current_fields:
            current_fields.with_context(
                rds_versioning_token=_VERSIONING_TOKEN
            ).unlink()
        self.template_id.with_context(
            rds_versioning_token=_VERSIONING_TOKEN
        ).write(values)
        self.template_id._rds_restore_snapshot_translations(payload)
        restored = self.template_id._rds_snapshot_payload()
        self.template_id._rds_create_version(
            restored,
            note=_("Version restored: %s") % (self.create_date or self.id),
            diff_summary=_("Safe Restore"),
        )
        return {"type": "ir.actions.client", "tag": "reload"}
