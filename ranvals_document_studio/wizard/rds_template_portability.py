import base64
import binascii
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..models.rds_template_version import MAX_TEMPLATE_JSON_BYTES


class RdsTemplatePortabilityWizard(models.TransientModel):
    _name = "rds.template.portability.wizard"
    _description = "DocuCraft Template Import/Export"

    mode = fields.Selection([("export", "Export"), ("import", "Import")], required=True, default="export")
    template_id = fields.Many2one("rds.template", readonly=True)
    upload_data = fields.Binary(attachment=False)
    upload_name = fields.Char()
    export_data = fields.Binary(readonly=True, attachment=False)
    export_name = fields.Char(readonly=True)
    preview_summary = fields.Text(readonly=True)
    state = fields.Selection([("draft", "Draft"), ("ready", "Ready")], default="draft", required=True)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        active_id = self.env.context.get("active_id")
        if active_id and self.env.context.get("active_model") == "rds.template":
            values["template_id"] = active_id
            values["mode"] = "export"
        return values

    def _check_manager(self):
        if not self.env.user.has_group("ranvals_document_studio.group_rds_manager"):
            raise AccessError(_("You do not have permission to transfer templates."))

    def _decode_upload(self):
        self.ensure_one()
        self._check_manager()
        if not self.upload_data:
            raise UserError(_("Select the JSON file to import."))
        if self.upload_name and not self.upload_name.lower().endswith(".json"):
            raise ValidationError(_("Only DocuCraft templates with a .json extension are accepted."))
        encoded = self.upload_data
        if not isinstance(encoded, (bytes, bytearray, str)):
            raise ValidationError(_("The uploaded file is invalid."))
        max_encoded = ((MAX_TEMPLATE_JSON_BYTES + 2) // 3) * 4 + 4
        if len(encoded) > max_encoded:
            raise ValidationError(_("The template file exceeds the 512 KB limit."))
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValidationError(_("The uploaded file is not valid Base64 data.")) from error
        return self.env["rds.template"]._rds_decode_payload(raw)

    def action_prepare_export(self):
        self.ensure_one()
        self._check_manager()
        if self.mode != "export" or not self.template_id:
            raise UserError(_("No template is available for export."))
        self.template_id.check_access("read")
        payload = self.template_id._rds_portable_payload()
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        safe_code = self.template_id.code[:72]
        self.write({
            "export_data": base64.b64encode(raw),
            "export_name": f"DocuCraft_{safe_code}.json",
            "preview_summary": _("%(name)s — %(count)s dynamic fields; portable inactive non-default copy.") % {
                "name": self.template_id.display_name, "count": len(payload["fields"]),
            },
            "state": "ready",
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/?model=rds.template.portability.wizard&id={self.id}&field=export_data&filename_field=export_name&download=true",
            "target": "self",
        }

    def action_preview_import(self):
        self.ensure_one()
        payload = self._decode_upload()
        template = payload["template"]
        self.write({
            "preview_summary": _("%(name)s — target: %(model)s — %(count)s dynamic fields. An inactive copy will be created; company and default selection will not be imported.") % {
                "name": template["name"],
                "model": template.get("target_model") or _("General"),
                "count": len(payload["fields"]),
            },
            "state": "ready",
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_create_copy(self):
        self.ensure_one()
        payload = self._decode_upload()
        values = self.env["rds.template"]._rds_values_from_payload(payload, for_import=True)
        suffix = _("(Imported)")
        base_name = values["name"][: max(1, 200 - len(suffix) - 1)].rstrip()
        values["name"] = "%s %s" % (base_name, suffix)
        template = self.env["rds.template"].create(values)
        return {
            "type": "ir.actions.act_window",
            "res_model": "rds.template",
            "res_id": template.id,
            "view_mode": "form",
            "target": "current",
        }


class RdsTemplatePortabilityActions(models.Model):
    _inherit = "rds.template"

    def action_open_portability(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Export Template"),
            "res_model": "rds.template.portability.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_mode": "export", "default_template_id": self.id},
        }
