import base64
import binascii
import re
from datetime import date, datetime

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.tools.misc import formatLang

from ..tools.common import format_date_value, lang_code, normalize_hex, plain_text, tr_label


FONT_SELECTION = [
    ("serif", "Kurumsal Serif"),
    ("sans", "Modern Sans Serif"),
    ("technical", "Teknik / Endüstriyel"),
    ("editorial", "Editoryal"),
]

FONT_CSS = {
    "serif": "Georgia, 'Times New Roman', serif",
    "sans": "Arial, Helvetica, sans-serif",
    "technical": "'Trebuchet MS', Arial, sans-serif",
    "editorial": "Georgia, 'Times New Roman', serif",
}

MAX_FIELD_PATH_DEPTH = 4
MAX_RELATIONAL_VALUES = 100
MAX_FIELD_TEXT_LENGTH = 10000
SAFE_PREVIEW_PATH = re.compile(
    r"^/[A-Za-z0-9_]+/static/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:jpe?g|png|webp)$",
    re.IGNORECASE,
)


class RdsTemplate(models.Model):
    _name = "rds.template"
    _description = "Ranvals Belge Şablonu"
    _order = "sequence, name, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True, copy=False)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Şirket",
        help="Boş bırakılırsa şablon tüm izinli şirketlerde kullanılabilir.",
        ondelete="cascade",
    )
    target_model_id = fields.Many2one(
        "ir.model",
        string="Hedef Model",
        domain="[('transient', '=', False)]",
        ondelete="cascade",
        help="Boş bırakılırsa şablon, Belge Studio bağlayıcısı olan tüm modellerde kullanılabilir.",
    )
    is_default = fields.Boolean(string="Varsayılan")
    layout_style = fields.Selection(
        [
            ("beauty", "Premium Bordo / Güzellik"),
            ("construction", "Kurumsal Lacivert / İnşaat"),
            ("technology", "Modern Mavi / Teknoloji"),
            ("industrial", "Endüstriyel / Üretim"),
            ("eco", "Doğal Yeşil / Mimarlık"),
            ("furniture", "Terakota / Mobilya"),
        ],
        required=True,
        default="technology",
    )
    primary_color = fields.Char(default="#17345F", required=True)
    secondary_color = fields.Char(default="#F4F7FA", required=True)
    accent_color = fields.Char(default="#D33A35", required=True)
    text_color = fields.Char(default="#27313A", required=True)
    heading_font = fields.Selection(FONT_SELECTION, default="sans", required=True)
    body_font = fields.Selection(FONT_SELECTION, default="sans", required=True)
    heading_font_css = fields.Char(compute="_compute_font_css")
    body_font_css = fields.Char(compute="_compute_font_css")
    logo_height_mm = fields.Integer(default=18)
    tagline = fields.Char(translate=True)
    footer_text = fields.Char(translate=True)
    notes_text = fields.Html(translate=True)
    show_company = fields.Boolean(default=True)
    show_partner = fields.Boolean(default=True)
    show_metadata = fields.Boolean(default=True)
    show_notes = fields.Boolean(default=True)
    show_bank = fields.Boolean(default=True)
    show_footer = fields.Boolean(default=True)
    preview_path = fields.Char(readonly=True)
    preview_html = fields.Html(compute="_compute_preview_html", sanitize=False)
    field_ids = fields.One2many("rds.template.field", "template_id", string="Alanlar", copy=True)

    if hasattr(models, "Constraint"):
        _code_company_unique = models.Constraint(
            "UNIQUE(code, company_id)",
            "Şablon kodu şirket bazında benzersiz olmalıdır.",
        )
    else:  # Odoo 17/18
        _sql_constraints = [
            (
                "rds_template_code_company_unique",
                "UNIQUE(code, company_id)",
                "Şablon kodu şirket bazında benzersiz olmalıdır.",
            )
        ]

    # PostgreSQL considers NULL values distinct in a regular UNIQUE
    # constraint.  Odoo 19's partial unique indexes close that race for global
    # templates and for every nullable default-template scope.  The Python
    # constraints below remain as a cross-version, user-friendly fallback.
    if hasattr(models, "UniqueIndex"):
        _code_company_value_unique = models.UniqueIndex(
            "(code, company_id) WHERE company_id IS NOT NULL",
            "Şablon kodu şirket bazında benzersiz olmalıdır.",
        )
        _code_global_unique = models.UniqueIndex(
            "(code) WHERE company_id IS NULL",
            "Şablon kodu şirket bazında benzersiz olmalıdır.",
        )
        _default_company_model_unique = models.UniqueIndex(
            "(company_id, target_model_id)"
            " WHERE is_default IS TRUE"
            " AND company_id IS NOT NULL AND target_model_id IS NOT NULL",
            "Aynı şirket ve hedef model için yalnızca bir varsayılan şablon olabilir.",
        )
        _default_global_model_unique = models.UniqueIndex(
            "(target_model_id)"
            " WHERE is_default IS TRUE"
            " AND company_id IS NULL AND target_model_id IS NOT NULL",
            "Aynı şirket ve hedef model için yalnızca bir varsayılan şablon olabilir.",
        )
        _default_company_generic_unique = models.UniqueIndex(
            "(company_id)"
            " WHERE is_default IS TRUE"
            " AND company_id IS NOT NULL AND target_model_id IS NULL",
            "Aynı şirket ve hedef model için yalnızca bir varsayılan şablon olabilir.",
        )
        _default_global_generic_unique = models.UniqueIndex(
            "(is_default)"
            " WHERE is_default IS TRUE"
            " AND company_id IS NULL AND target_model_id IS NULL",
            "Aynı şirket ve hedef model için yalnızca bir varsayılan şablon olabilir.",
        )

    @api.model
    def _generate_unique_code(self, name, company_id=False):
        base = re.sub(r"[^A-Za-z0-9]+", "_", (name or "TEMPLATE").upper()).strip("_") or "TEMPLATE"
        candidate = base[:72]
        index = 1
        domain_company = company_id or False
        while self.search_count([("code", "=", candidate), ("company_id", "=", domain_company)]):
            index += 1
            suffix = "_%s" % index
            candidate = "%s%s" % (base[: max(1, 72 - len(suffix))], suffix)
        return candidate

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if not values.get("code"):
                values["code"] = self._generate_unique_code(values.get("name"), values.get("company_id"))
        return super().create(vals_list)

    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {})
        default.setdefault("name", _("%s (Kopya)") % self.name)
        default.setdefault("code", self._generate_unique_code("%s_COPY" % self.code, self.company_id.id))
        default.setdefault("is_default", False)
        return super().copy(default)

    @api.depends("heading_font", "body_font")
    def _compute_font_css(self):
        for record in self:
            record.heading_font_css = FONT_CSS.get(record.heading_font, FONT_CSS["sans"])
            record.body_font_css = FONT_CSS.get(record.body_font, FONT_CSS["sans"])

    @api.depends("preview_path", "name")
    def _compute_preview_html(self):
        for record in self:
            if record.preview_path and record._is_safe_preview_path(record.preview_path):
                record.preview_html = Markup(
                    '<div style="text-align:center;padding:8px;">'
                    '<img src="%s" alt="%s" style="max-width:100%%;max-height:640px;border:1px solid #dde3e8;border-radius:8px;box-shadow:0 8px 24px rgba(15,23,42,.08);"/>'
                    "</div>"
                ) % (escape(record.preview_path), escape(record.name or "Template"))
            else:
                record.preview_html = Markup(
                    '<div class="text-muted" style="padding:24px;text-align:center;">Önizleme bulunmuyor.</div>'
                )

    @api.model
    def _is_safe_preview_path(self, value):
        """Only allow local addon static images in the unsanitized preview."""
        if not isinstance(value, str) or not SAFE_PREVIEW_PATH.fullmatch(value):
            return False
        return all(part not in ("", ".", "..") for part in value.split("/")[1:])

    @api.constrains("preview_path")
    def _check_preview_path(self):
        for record in self.filtered("preview_path"):
            if not record._is_safe_preview_path(record.preview_path):
                raise ValidationError(
                    _("Önizleme görseli yalnızca bir Odoo eklentisinin /static/ dizininden seçilebilir.")
                )

    @api.constrains("code")
    def _check_code_format(self):
        for record in self:
            if not re.fullmatch(r"[A-Za-z0-9_]{1,72}", record.code or ""):
                raise ValidationError(
                    _("Şablon kodu 1-72 karakter olmalı; yalnızca harf, rakam ve alt çizgi içermelidir.")
                )

    @api.constrains("code", "company_id")
    def _check_code_uniqueness(self):
        for record in self:
            if not record.code:
                continue
            duplicate = self.search_count([
                ("id", "!=", record.id),
                ("code", "=", record.code),
                ("company_id", "=", record.company_id.id or False),
            ])
            if duplicate:
                raise ValidationError(_("Şablon kodu şirket bazında benzersiz olmalıdır."))

    @api.constrains("primary_color", "secondary_color", "accent_color", "text_color")
    def _check_colors(self):
        for record in self:
            for field_name in ("primary_color", "secondary_color", "accent_color", "text_color"):
                value = record[field_name]
                if value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    raise ValidationError(_("%s alanı #RRGGBB biçiminde olmalıdır.") % record._fields[field_name].string)

    @api.constrains("logo_height_mm")
    def _check_logo_height(self):
        for record in self:
            if record.logo_height_mm < 8 or record.logo_height_mm > 40:
                raise ValidationError(_("Logo yüksekliği 8 ile 40 mm arasında olmalıdır."))

    @api.constrains("is_default", "target_model_id", "company_id")
    def _check_default_uniqueness(self):
        for record in self.filtered("is_default"):
            domain = [
                ("id", "!=", record.id),
                ("is_default", "=", True),
                ("company_id", "=", record.company_id.id or False),
                ("target_model_id", "=", record.target_model_id.id or False),
            ]
            if self.search_count(domain):
                raise ValidationError(_("Aynı şirket ve hedef model için yalnızca bir varsayılan şablon olabilir."))

    @api.constrains("target_model_id")
    def _check_metadata_field_scope(self):
        for record in self.filtered("target_model_id"):
            invalid = record.field_ids.filtered(
                lambda item, current=record: item.section == "metadata"
                and item.source_model_id != current.target_model_id
            )
            if invalid:
                raise ValidationError(
                    _("Bilgi kartı alanlarının kaynak modeli şablonun hedef modeliyle aynı olmalıdır.")
                )

    @api.model
    def get_default_for(self, model_name, company=None):
        if not isinstance(model_name, str) or model_name not in self.env.registry.models:
            return self.browse()
        company = company or self.env.company
        company.ensure_one()
        model = self.env[model_name]
        ir_model = self.env["ir.model"]._get(model._name)
        if not ir_model:
            return self.browse()
        domains = [
            [
                ("active", "=", True),
                ("company_id", "=", company.id),
                ("target_model_id", "=", ir_model.id),
                ("is_default", "=", True),
            ],
            [
                ("active", "=", True),
                ("company_id", "=", company.id),
                ("target_model_id", "=", False),
                ("is_default", "=", True),
            ],
            [
                ("active", "=", True),
                ("company_id", "=", False),
                ("target_model_id", "=", ir_model.id),
                ("is_default", "=", True),
            ],
            [
                ("active", "=", True),
                ("company_id", "=", False),
                ("target_model_id", "=", False),
                ("is_default", "=", True),
            ],
            [
                ("active", "=", True),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company.id),
                "|",
                ("target_model_id", "=", False),
                ("target_model_id", "=", ir_model.id),
            ],
        ]
        for domain in domains:
            template = self.search(domain, limit=1)
            if template:
                return template
        return self.browse()

    def action_open_add_field(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Alan Ekle"),
            "res_model": "rds.field.add.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }

    def get_label(self, key, lang=None):
        self.ensure_one()
        return tr_label(key, lang)

    def get_theme(self):
        self.ensure_one()
        return {
            "primary": normalize_hex(self.primary_color, "#17345F"),
            "secondary": normalize_hex(self.secondary_color, "#F4F7FA"),
            "accent": normalize_hex(self.accent_color, "#D33A35"),
            "text": normalize_hex(self.text_color, "#27313A"),
            "heading_font": self.heading_font_css,
            "body_font": self.body_font_css,
        }

    def get_custom_fields(self, source_model_name, section):
        self.ensure_one()
        return self.field_ids.filtered(
            lambda item: item.active
            and item.section == section
            and item.source_model_id.model == source_model_name
        ).sorted(key=lambda item: (item.sequence, item.id))

    def _resolve_path_info(self, record, path):
        """Resolve a configured field path and retain its terminal metadata.

        Related values are always read in the caller's environment.  A field
        protected by field groups or a related record rule is treated as empty
        instead of aborting the complete document or being read with sudo.
        """
        if not record or not path:
            return False, None, None
        current = record
        parts = path.split(".")
        if (
            not parts
            or len(parts) > MAX_FIELD_PATH_DEPTH
            or any(not part for part in parts)
        ):
            return False, None, None
        terminal_field = None
        owner = None
        for index, part in enumerate(parts):
            if part.startswith("_") or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", part):
                return False, None, None
            if not hasattr(current, "_fields") or part not in current._fields:
                return False, None, None
            if len(current) > 1:
                # Config constraints disallow x2many values in the middle of a
                # path.  Keep corrupt/legacy rows fail-closed as well.
                return False, terminal_field, owner
            owner = current
            terminal_field = current._fields[part]
            if index < len(parts) - 1 and terminal_field.type != "many2one":
                return False, terminal_field, owner
            try:
                current = current[part]
            except (AccessError, MissingError):
                # Do not retain boolean/type metadata for an unreadable value:
                # otherwise ``False`` would be rendered as a genuine "No" and
                # bypass hide-if-empty instead of failing closed.
                return False, None, None
            if index < len(parts) - 1 and not current:
                return False, terminal_field, owner
        return current, terminal_field, owner

    def _resolve_path(self, record, path):
        value, _field, _owner = self._resolve_path_info(record, path)
        return value

    @api.model
    def _truncate_display_text(self, value):
        text = plain_text(value)
        if len(text) <= MAX_FIELD_TEXT_LENGTH:
            return text
        return "%s…" % text[: MAX_FIELD_TEXT_LENGTH - 1]

    def _format_recordset(self, value):
        if not value:
            return ""
        visible = value[:MAX_RELATIONAL_VALUES]
        try:
            display_names = visible.mapped("display_name")
        except (AccessError, MissingError):
            return ""
        names = [self._truncate_display_text(name) for name in display_names]
        result = ", ".join(filter(None, names))
        remaining = len(value) - len(visible)
        if remaining > 0:
            result = _("%(values)s … (+%(count)s)", values=result, count=remaining)
        return self._truncate_display_text(result)

    def _field_currency(self, owner, field, fallback=None):
        currency_field = getattr(field, "currency_field", None) if field else None
        get_currency_field = getattr(field, "get_currency_field", None) if field else None
        if owner and get_currency_field:
            try:
                currency_field = get_currency_field(owner)
            except (AttributeError, KeyError, TypeError):
                currency_field = None
        if owner and currency_field and currency_field in owner._fields:
            try:
                resolved = owner[currency_field]
            except (AccessError, MissingError):
                resolved = False
            if resolved and len(resolved) == 1:
                return resolved
        return fallback

    def _field_digits(self, owner, field, fallback=2):
        if not owner or not field or not hasattr(field, "get_digits"):
            return fallback
        try:
            digits = field.get_digits(owner.env)
        except (AttributeError, TypeError, ValueError):
            return fallback
        if (
            isinstance(digits, (tuple, list))
            and len(digits) == 2
            and isinstance(digits[1], int)
        ):
            return digits[1]
        return fallback

    def format_value(
        self,
        value,
        value_type="auto",
        currency=None,
        lang=None,
        field=None,
        owner=None,
    ):
        self.ensure_one()
        # ``0 == False`` in Python; identity checks are required so valid zero
        # quantities, discounts and totals do not disappear from documents.
        if value is None or (isinstance(value, str) and value == ""):
            return ""
        if field and getattr(field, "type", None) == "boolean":
            return _("Evet") if value else _("Hayır")
        if value is False:
            return ""
        if hasattr(value, "_name"):
            return self._format_recordset(value)

        format_env = self.with_context(lang=lang).env if lang else self.env
        if field and getattr(field, "type", None) == "selection":
            selection_env = (
                owner.with_context(lang=lang).env
                if owner and lang
                else owner.env if owner else format_env
            )
            selection = dict(field._description_selection(selection_env))
            return self._truncate_display_text(selection.get(value, value))

        if value_type == "auto" and field:
            value_type = {
                "date": "date",
                "datetime": "date",
                "monetary": "monetary",
                "integer": "integer",
                "float": "float",
            }.get(field.type, "auto")

        if value_type == "date" or isinstance(value, (date, datetime)):
            if isinstance(value, datetime):
                value = fields.Datetime.context_timestamp(owner or self, value).date()
            return format_date_value(value, lang, env=format_env)

        digits = self._field_digits(owner, field)
        if value_type == "monetary":
            try:
                currency = self._field_currency(owner, field, fallback=currency)
                return formatLang(format_env, float(value), currency_obj=currency)
            except (TypeError, ValueError):
                return self._truncate_display_text(value)
        if value_type == "percentage":
            try:
                return "%s%%" % formatLang(format_env, float(value), digits=digits)
            except (TypeError, ValueError):
                return self._truncate_display_text(value)
        if value_type == "integer":
            try:
                return str(int(value))
            except (TypeError, ValueError):
                return self._truncate_display_text(value)
        if value_type == "float":
            try:
                return formatLang(format_env, float(value), digits=digits)
            except (TypeError, ValueError):
                return self._truncate_display_text(value)
        if isinstance(value, str):
            return self._truncate_display_text(value)
        return self._truncate_display_text(value)

    def get_field_display_value(self, record, field_record, currency=None, lang=None):
        self.ensure_one()
        value, field, owner = self._resolve_path_info(record, field_record.field_path)
        return self.format_value(
            value,
            field_record.value_type,
            currency=currency,
            lang=lang,
            field=field,
            owner=owner,
        )

    def get_metadata_context(self, record, default_specs, lang=None):
        self.ensure_one()
        custom = self.get_custom_fields(record._name, "metadata")
        result = []
        if custom:
            for field_record in custom:
                value = self.get_field_display_value(record, field_record, currency=getattr(record, "currency_id", None), lang=lang)
                if field_record.hide_if_empty and not value:
                    continue
                result.append(
                    {
                        "label": field_record.label or field_record.field_path,
                        "value": value or "-",
                        "icon": field_record.icon_class or "fa-circle-o",
                        "align": field_record.alignment,
                    }
                )
            return result
        for spec in default_specs:
            value = self._resolve_path(record, spec.get("path")) if spec.get("path") else spec.get("value")
            formatted = self.format_value(
                value,
                spec.get("value_type", "auto"),
                currency=getattr(record, "currency_id", None),
                lang=lang,
            )
            if spec.get("hide_if_empty") and not formatted:
                continue
            result.append(
                {
                    "label": tr_label(spec.get("label_key", "document_no"), lang),
                    "value": formatted or "-",
                    "icon": spec.get("icon", "fa-circle-o"),
                    "align": spec.get("align", "center"),
                }
            )
        return result

    def get_custom_line_context(self, line_records, currency=None, lang=None):
        self.ensure_one()
        if not line_records:
            return [], []
        source_model = line_records._name
        custom = self.get_custom_fields(source_model, "line")
        if not custom:
            return [], []
        columns = [
            {
                "label": field_record.label or field_record.field_path,
                "align": field_record.alignment,
                "width": field_record.width_percent,
            }
            for field_record in custom
        ]
        lines = []
        for line in line_records:
            display_type = getattr(line, "display_type", False)
            if display_type in ("line_section", "line_subsection", "section", "subsection"):
                lines.append({"is_section": True, "description": plain_text(getattr(line, "name", ""))})
                continue
            if display_type in ("line_note", "note"):
                lines.append({"is_note": True, "description": plain_text(getattr(line, "name", ""))})
                continue
            values = []
            for field_record in custom:
                text = self.get_field_display_value(line, field_record, currency=currency, lang=lang)
                values.append(
                    {
                        "text": text or "-",
                        "align": field_record.alignment,
                        "bold": field_record.bold,
                        "highlight": field_record.highlight,
                    }
                )
            lines.append({"values": values})
        return columns, lines

    def partner_info(self, partner):
        self.ensure_one()
        if not partner:
            return {"name": "", "lines": [], "phone": "", "email": "", "website": "", "vat": ""}
        commercial = partner.commercial_partner_id
        address_partner = partner if any(
            getattr(partner, field_name, False)
            for field_name in ("street", "street2", "zip", "city", "state_id", "country_id")
        ) else commercial
        lines = []
        if address_partner.street:
            lines.append(address_partner.street)
        if address_partner.street2:
            lines.append(address_partner.street2)
        city_parts = [part for part in (address_partner.zip, address_partner.city) if part]
        if address_partner.state_id:
            city_parts.append(address_partner.state_id.name)
        if city_parts:
            lines.append(" ".join(city_parts))
        if address_partner.country_id:
            lines.append(address_partner.country_id.name)
        return {
            "name": partner.name or commercial.name or "",
            "lines": lines,
            "phone": partner.phone or commercial.phone or "",
            "email": partner.email or commercial.email or "",
            "website": partner.website or commercial.website or "",
            "vat": partner.vat or commercial.vat or "",
            "ref": partner.ref or commercial.ref or "",
        }

    def company_info(self, company):
        self.ensure_one()
        info = self.partner_info(company.partner_id)
        info["name"] = company.name or info.get("name")
        info["phone"] = company.phone or info.get("phone")
        info["email"] = company.email or info.get("email")
        info["website"] = company.website or info.get("website")
        info["vat"] = company.vat or info.get("vat")
        return info

    def bank_info(self, company, currency=None):
        self.ensure_one()
        if not company:
            return {}
        company.ensure_one()
        if not self.env.su and company.id not in self.env.companies.ids:
            raise AccessError(_("Bu şirketin banka bilgilerine erişim izniniz bulunmuyor."))
        partner = company.partner_id
        # Company payment coordinates are intentionally printable on its own
        # commercial documents.  Keep the elevation limited to that company's
        # bank relation after the source company boundary has been checked.
        banks = partner.sudo().bank_ids
        if currency and "currency_id" in banks._fields:
            exact = banks.filtered(lambda bank: bank.currency_id == currency)
            generic = banks.filtered(lambda bank: not bank.currency_id)
            banks = exact or generic or banks
        bank = banks[:1]
        if not bank:
            return {}
        bank_record = bank.bank_id
        return {
            "bank_name": bank_record.name or "",
            "branch": getattr(bank_record, "branch", False) or "",
            "account_name": partner.name or company.name,
            "iban": bank.acc_number or "",
            "swift": getattr(bank_record, "bic", False) or getattr(bank_record, "bank_bic", False) or "",
        }

    def logo_bytes(self, company):
        self.ensure_one()
        # ``logo_web`` and resized image fields are intentionally avoided:
        # Word needs the original partner image to remain sharp at 300 DPI.
        candidates = (
            getattr(company.partner_id, "image_1920", False),
            company.logo,
        )
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return base64.b64decode(candidate, validate=True)
            except (binascii.Error, TypeError, ValueError):
                continue
        return b""

    def base_context(self, record, lang=None):
        self.ensure_one()
        # Keep the shared QWeb contract complete on its own.  Normally a
        # connector enriches this dictionary, but Odoo Studio can render a
        # primary/inherited report view before every connector-specific value
        # has been provided.  Defaults here make that preview renderable and
        # let connector implementations replace only the values they own.
        company = getattr(record, "company_id", False) or self.env.company
        partner = getattr(record, "partner_id", False)
        resolved_lang = (
            lang
            or record.env.context.get("lang")
            or getattr(partner, "lang", False)
            or self.env.user.lang
        )
        code = lang_code(resolved_lang)
        labels = {
            "company_info": tr_label("company_info", code),
            "partner_info": tr_label("customer_info", code),
            "document_info": tr_label("document_info", code),
            "notes": tr_label("notes", code),
            "bank_info": tr_label("bank_info", code),
        }
        return {
            "record": record,
            "company": company,
            "partner": partner,
            "company_info": self.company_info(company),
            "partner_info": self.partner_info(partner),
            "logo_bytes": self.logo_bytes(company),
            "logo_height_mm": self.logo_height_mm,
            "layout_style": self.layout_style,
            "tagline": self.tagline or getattr(company, "report_header", False) or "",
            "footer_text": self.footer_text or tr_label("thank_you", code),
            "labels": labels,
            "theme": self.get_theme(),
            "direction": "rtl" if code == "ar" else "ltr",
            "lang_code": code,
            "title": "",
            "number": "-",
            "tax_label": company.country_id.vat_label or tr_label("tax_no", code),
            "metadata": [],
            "columns": [],
            "lines": [],
            "totals": [],
            "notes": [],
            "bank": {},
            "bank_labels": {
                key: tr_label(key, code)
                for key in ("bank", "branch", "account_name", "iban", "swift")
            },
            "show_company": self.show_company,
            "show_partner": self.show_partner,
            "show_metadata": self.show_metadata,
            "show_notes": self.show_notes,
            "show_bank": self.show_bank,
            "show_footer": self.show_footer,
        }
