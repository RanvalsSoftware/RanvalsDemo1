"""Explicit user-language settings. Never switch another user or overwrite terms."""
from markupsafe import Markup, escape
from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from ..tools.common import check_record_access

# Keep both standard Odoo English (US) and English (UK).  Fresh Odoo
# databases use en_US, while existing DocuCraft deployments may already have
# saved en_GB report-language bindings.
RDS_LANGUAGES = [
    ("tr_TR", "Türkçe"), ("en_US", "English (US)"),
    ("en_GB", "English (UK)"),
    ("ru_RU", "Русский"), ("fr_FR", "Français"),
    ("it_IT", "Italiano"), ("ar_001", "العربية"),
]
RDS_LANGUAGE_CODES = tuple(code for code, _label in RDS_LANGUAGES)


class RdsLanguageWizard(models.TransientModel):
    _name = "rds.language.wizard"
    _description = "Document Studio Language"

    language_code = fields.Selection(RDS_LANGUAGES, string="Arayüz dili", required=True,
        default=lambda self: self.env.user.lang if self.env.user.lang in RDS_LANGUAGE_CODES else "en_GB")
    language_active = fields.Boolean(compute="_compute_language_active")
    missing_language_count = fields.Integer(compute="_compute_language_active")

    def _check_internal_user(self):
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(_("Bu işlem yalnız iç kullanıcılar içindir."))

    def _selected_language(self):
        self.ensure_one()
        self._check_internal_user()
        check_record_access(self)
        if self.language_code not in RDS_LANGUAGE_CODES:
            raise UserError(_("Desteklenmeyen dil seçimi."))
        lang = self.env["res.lang"].with_context(active_test=False).search(
            [("code", "=", self.language_code)], limit=1)
        if not lang:
            raise UserError(_("Dil kaydı bulunamadı. Sistem yöneticinize başvurun."))
        return lang

    @api.depends("language_code")
    def _compute_language_active(self):
        for wizard in self:
            wizard.missing_language_count = len(RDS_LANGUAGE_CODES) - self.env["res.lang"].search_count(
                [("code", "in", RDS_LANGUAGE_CODES), ("active", "=", True)])
            wizard.language_active = bool(self.env["res.lang"].search_count(
                [("code", "=", wizard.language_code), ("active", "=", True)]))

    def action_activate_language(self):
        self.ensure_one()
        self._check_internal_user()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Dilleri yalnız sistem yöneticisi etkinleştirebilir."))
        lang = self._selected_language()
        # Native language installer, no sudo and no overwrite of customised terms.
        self.env["base.language.install"].create({
            "lang_ids": [(6, 0, lang.ids)], "overwrite": False,
        }).lang_install()
        self.invalidate_recordset(["language_active", "missing_language_count"])
        return {"type": "ir.actions.act_window", "res_model": self._name,
                "res_id": self.id, "views": [(False, "form")], "target": "new",
                "name": _("Dil ayarları")}

    def action_activate_supported_languages(self):
        self.ensure_one()
        self._check_internal_user()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Dilleri yalnız sistem yöneticisi etkinleştirebilir."))
        languages = self.env["res.lang"].with_context(active_test=False).search(
            [("code", "in", RDS_LANGUAGE_CODES)])
        if len(languages) != len(RDS_LANGUAGE_CODES):
            raise UserError(_("Dil kaydı bulunamadı. Sistem yöneticinize başvurun."))
        self.env["base.language.install"].create({
            "lang_ids": [(6, 0, languages.ids)], "overwrite": False,
        }).lang_install()
        self.invalidate_recordset(["language_active", "missing_language_count"])
        return {"type": "ir.actions.act_window", "res_model": self._name,
                "res_id": self.id, "views": [(False, "form")], "target": "new",
                "name": _("Dil ayarları")}

    def action_apply(self):
        lang = self._selected_language()
        if not lang.active:
            raise UserError(_("Bu dil henüz etkin değil. Önce sistem yöneticisi dili etkinleştirmelidir."))
        # SELF_WRITEABLE_FIELDS: only the authenticated user's own preference.
        self.env.user.write({"lang": lang.code})
        return {"type": "ir.actions.client", "tag": "reload_context"}


class RdsTemplateLocalization(models.Model):
    _inherit = "rds.template"

    @api.depends("preview_path", "name")
    @api.depends_context("lang")
    def _compute_preview_html(self):
        super()._compute_preview_html()
        for record in self:
            if not record.preview_path or not record._is_safe_preview_path(record.preview_path):
                record.preview_html = Markup('<div class="text-muted p-4 text-center">%s</div>') % escape(_("Önizleme bulunmuyor."))

    def base_context(self, record, lang=None):
        localized = self.with_context(lang=lang) if lang else self
        record = record.with_context(lang=lang) if lang else record
        return super(RdsTemplateLocalization, localized).base_context(record, lang=lang)

    def get_metadata_context(self, record, default_specs, lang=None):
        localized = self.with_context(lang=lang) if lang else self
        record = record.with_context(lang=lang) if lang else record
        return super(RdsTemplateLocalization, localized).get_metadata_context(record, default_specs, lang=lang)

    def get_custom_line_context(self, line_records, currency=None, lang=None):
        localized = self.with_context(lang=lang) if lang else self
        lines = line_records.with_context(lang=lang) if lang else line_records
        return super(RdsTemplateLocalization, localized).get_custom_line_context(lines, currency=currency, lang=lang)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _get_translation_frontend_modules_name(cls):
        modules = super()._get_translation_frontend_modules_name()
        return list(dict.fromkeys(modules + ["ranvals_document_studio"]))
