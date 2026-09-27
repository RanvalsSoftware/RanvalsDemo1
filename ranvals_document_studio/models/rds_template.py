import base64
import binascii
import logging
import re
from datetime import date, datetime

from lxml import etree
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.tools.image import image_data_uri
from odoo.tools.misc import formatLang

from ..tools.common import format_date_value, lang_code, normalize_hex, plain_text, tr_label


_logger = logging.getLogger(__name__)


FONT_SELECTION = [
    ("serif", "Corporate Serif"),
    ("sans", "Modern Sans Serif"),
    ("technical", "Technical Sans Serif"),
    ("editorial", "Editorial"),
    ("lato", "Lato"),
    ("roboto", "Roboto"),
    ("open_sans", "Open Sans"),
    ("montserrat", "Montserrat"),
    ("raleway", "Raleway"),
    ("oswald", "Oswald"),
    ("tajawal", "Tajawal"),
    ("fira_mono", "Fira Mono"),
    ("humanist", "Humanist Sans"),
    ("helvetica", "Helvetica / Arial"),
    ("verdana", "Verdana"),
    ("tahoma", "Tahoma"),
    ("lucida", "Lucida Sans"),
    ("times", "Times New Roman"),
    ("garamond", "Garamond"),
    ("palatino", "Palatino"),
    ("cambria", "Cambria"),
    ("bookman", "Bookman"),
    ("monospace", "Corporate Monospace"),
]

FONT_CSS = {
    "serif": "Georgia, 'DejaVu Serif', 'Times New Roman', serif",
    "sans": "Arial, 'Liberation Sans', Helvetica, sans-serif",
    "technical": "'Trebuchet MS', 'DejaVu Sans', Arial, sans-serif",
    "editorial": "Georgia, 'DejaVu Serif', 'Times New Roman', serif",
    "lato": "Lato, 'Odoo Unicode Support Noto', Arial, sans-serif",
    "roboto": "Roboto, 'Odoo Unicode Support Noto', Arial, sans-serif",
    "open_sans": "Open_Sans, 'Open Sans', 'Odoo Unicode Support Noto', Arial, sans-serif",
    "montserrat": "Montserrat, 'Odoo Unicode Support Noto', Arial, sans-serif",
    "raleway": "Raleway, 'Odoo Unicode Support Noto', Arial, sans-serif",
    "oswald": "Oswald, 'Arial Narrow', Arial, sans-serif",
    "tajawal": "Tajawal, 'Odoo Unicode Support Noto', Arial, sans-serif",
    "fira_mono": "Fira_Mono, 'Fira Mono', Consolas, 'Courier New', monospace",
    "humanist": "Calibri, Carlito, 'Segoe UI', Arial, sans-serif",
    "helvetica": "Helvetica, Arial, 'Liberation Sans', sans-serif",
    "verdana": "Verdana, 'DejaVu Sans', Arial, sans-serif",
    "tahoma": "Tahoma, 'DejaVu Sans', Arial, sans-serif",
    "lucida": "'Lucida Sans', 'Lucida Grande', 'DejaVu Sans', Arial, sans-serif",
    "times": "'Times New Roman', 'Liberation Serif', 'DejaVu Serif', serif",
    "garamond": "Garamond, 'EB Garamond', 'Liberation Serif', 'DejaVu Serif', serif",
    "palatino": "'Palatino Linotype', 'Book Antiqua', Palatino, 'Liberation Serif', serif",
    "cambria": "Cambria, Caladea, 'Liberation Serif', 'DejaVu Serif', serif",
    "bookman": "'Bookman Old Style', 'URW Bookman', 'DejaVu Serif', serif",
    "monospace": "'Courier New', 'Liberation Mono', 'DejaVu Sans Mono', monospace",
}

MAX_FIELD_PATH_DEPTH = 4
MAX_RELATIONAL_VALUES = 100
MAX_FIELD_TEXT_LENGTH = 10000
MAX_EXPORT_FIELD_CANDIDATES = 160
MAX_EXPORT_METADATA_FIELDS = 24
MAX_EXPORT_LINE_FIELDS = 12
EXPORT_FIELD_TYPES = {
    "boolean",
    "char",
    "date",
    "datetime",
    "float",
    "html",
    "integer",
    "many2many",
    "many2one",
    "monetary",
    "selection",
    "text",
}
EXPORT_FIELD_EXCLUDED_NAMES = {
    "access_token",
    "access_url",
    "create_date",
    "create_uid",
    "display_name",
    "id",
    "message_attachment_count",
    "message_follower_ids",
    "message_has_error",
    "message_has_error_counter",
    "message_has_sms_error",
    "message_ids",
    "website_message_ids",
    "write_date",
    "write_uid",
}
EXPORT_FIELD_EXCLUDED_PREFIXES = (
    "activity_",
    "message_",
    "website_message_",
)
EXPORT_FIELD_UTILITY_WIDGETS = {
    "account-tax-totals-field",
    "handle",
    "statinfo",
    "stock_rescheduling_popover",
    "x2many_buttons",
}
EXPORT_FIELD_SECRET_PARTS = (
    "api_key",
    "credential",
    "password",
    "private_key",
    "secret",
    "session",
    "token",
)
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
        help="Boş bırakılırsa şablon, DocuCraft bağlayıcısı olan tüm modellerde kullanılabilir.",
    )
    is_default = fields.Boolean(string="Varsayılan")
    layout_style = fields.Selection(
        [
            ("beauty", "Signature Burgundy"),
            ("construction", "Executive Navy"),
            ("technology", "Horizon Blue"),
            ("industrial", "Atlas Steel"),
            ("eco", "Sage Reserve"),
            ("furniture", "Copper Atelier"),
            ("noir_executive", "Noir Executive"),
            ("royal_ledger", "Royal Ledger"),
            ("swiss_grid", "Swiss Grid"),
            ("arctic_minimal", "Arctic Minimal"),
            ("indigo_flow", "Indigo Flow"),
            ("emerald_ledger", "Emerald Ledger"),
            ("sandstone_classic", "Sandstone Classic"),
            ("graphite_copper", "Graphite Copper"),
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

    @api.depends(
        "preview_path",
        "name",
        "layout_style",
        "primary_color",
        "secondary_color",
        "accent_color",
        "text_color",
        "heading_font",
        "body_font",
        "tagline",
        "footer_text",
        "logo_height_mm",
        "show_company",
        "show_partner",
        "show_metadata",
        "show_notes",
        "show_bank",
        "show_footer",
        "company_id",
        "target_model_id",
    )
    def _compute_preview_html(self):
        for record in self:
            if record.preview_path and record._is_safe_preview_path(record.preview_path):
                record.preview_html = Markup(
                    '<div style="text-align:center;padding:8px;">'
                    '<img src="%s" alt="%s" style="max-width:100%%;max-height:640px;border:1px solid #dde3e8;border-radius:8px;box-shadow:0 8px 24px rgba(15,23,42,.08);"/>'
                    "</div>"
                ) % (escape(record.preview_path), escape(record.name or "Template"))
            else:
                record.preview_html = record._rds_dynamic_preview_html()

    def _rds_dynamic_preview_html(self):
        """Render the real QWeb layout with safe, localized representative data."""
        self.ensure_one()
        company = self.company_id or self.env.company
        preferred_lang = company.partner_id.lang or self.env.user.lang or "en_US"
        language = self.env["res.lang"].search(
            [("code", "=", preferred_lang), ("active", "=", True)], limit=1
        )
        resolved_lang = language.code if language else self.env.user.lang or "en_US"
        localized = self.with_context(lang=resolved_lang)
        code = lang_code(resolved_lang)
        currency = company.currency_id
        subtotal = formatLang(
            localized.env,
            1250.0,
            currency_obj=currency,
        )
        taxes = formatLang(
            localized.env,
            250.0,
            currency_obj=currency,
        )
        total = formatLang(
            localized.env,
            1500.0,
            currency_obj=currency,
        )
        today = fields.Date.context_today(localized)
        target_model = self.target_model_id.model if self.target_model_id else False
        title_key = "invoice" if target_model == "account.move" else (
            "purchase_order" if target_model == "purchase.order" else "quote"
        )
        context = {
            "record": False,
            "company": company,
            "partner": False,
            "company_info": localized.company_info(company),
            "partner_info": {
                "name": tr_label("customer_info", code).title(),
                "lines": ["DocuCraft", "Istanbul"],
                "phone": "+90 212 000 00 00",
                "email": "customer@example.com",
                "website": "",
                "vat": "TR1234567890",
            },
            "logo_bytes": localized.logo_bytes(company),
            "logo_height_mm": self.logo_height_mm,
            "layout_style": self.layout_style,
            "tagline": self.tagline or tr_label("thank_you", code),
            "footer_text": self.footer_text or tr_label("thank_you", code),
            "labels": {
                "company_info": tr_label("company_info", code),
                "partner_info": tr_label("customer_info", code),
                "document_info": tr_label("document_info", code),
                "document_no": tr_label("document_no", code),
                "notes": tr_label("notes", code),
                "bank_info": tr_label("bank_info", code),
            },
            "theme": localized.get_theme(),
            "direction": "rtl" if code == "ar" else "ltr",
            "lang_code": code,
            "title": tr_label(title_key, code),
            "number": "DOC-2026-001",
            "tax_label": company.country_id.vat_label or tr_label("tax_no", code),
            "metadata": [
                {
                    "label": tr_label("document_no", code),
                    "value": "DOC-2026-001",
                    "icon": "fa-file-text-o",
                },
                {
                    "label": tr_label("date", code),
                    "value": format_date_value(today, resolved_lang, env=localized.env),
                    "icon": "fa-calendar",
                },
                {
                    "label": tr_label("reference", code),
                    "value": "REF-1001",
                    "icon": "fa-bookmark-o",
                },
                {
                    "label": tr_label("payment_terms", code),
                    "value": "30",
                    "icon": "fa-credit-card",
                },
            ],
            "columns": [
                {"label": tr_label("description", code), "align": "left", "width": 46},
                {"label": tr_label("quantity", code), "align": "right", "width": 14},
                {"label": tr_label("unit_price", code), "align": "right", "width": 20},
                {"label": tr_label("amount", code), "align": "right", "width": 20},
            ],
            "lines": [
                {
                    "values": [
                        {"text": tr_label("description", code).title(), "align": "left"},
                        {"text": "1", "align": "right"},
                        {"text": subtotal, "align": "right"},
                        {"text": subtotal, "align": "right", "bold": True},
                    ]
                }
            ],
            "totals": [
                {"label": tr_label("subtotal", code), "value": subtotal},
                {"label": tr_label("tax_total", code), "value": taxes},
                {"label": tr_label("grand_total", code), "value": total, "is_total": True},
            ],
            "notes": [tr_label("thank_you", code)],
            "bank": {
                "bank_name": "DocuCraft Bank",
                "branch": "Istanbul",
                "account_name": company.name or "DocuCraft",
                "iban": "TR00 0000 0000 0000 0000 0000 00",
                "swift": "DEMOXX",
            },
            "bank_labels": {
                key: tr_label(key, code)
                for key in ("bank", "branch", "account_name", "iban", "swift")
            },
            "show_company": self.show_company,
            "show_partner": self.show_partner,
            "show_metadata": self.show_metadata,
            "show_lines": True,
            "show_notes": self.show_notes,
            "show_bank": self.show_bank,
            "show_footer": self.show_footer,
        }
        try:
            rendered = self.env["ir.ui.view"].with_context(
                inherit_branding=False,
                lang=resolved_lang,
            )._render_template(
                "ranvals_document_studio.rds_layout_router",
                {
                    "ctx": context,
                    "rds_template": localized,
                    "image_data_uri": image_data_uri,
                    "report_type": "html",
                },
            )
            return Markup(
                '<div class="rds-template-layout-preview">'
                '<div class="rds-template-layout-preview__label">{}</div>{}'
                "</div>"
            ).format(escape(self.name or "DocuCraft"), Markup(rendered))
        except Exception:
            _logger.exception("Could not render the DocuCraft template preview")
            return Markup(
                '<div class="rds-template-preview-fallback"><strong>{}</strong><span>{}</span></div>'
            ).format(
                escape(self.name or "DocuCraft"),
                escape(self.tagline or tr_label("thank_you", code)),
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

    def has_configured_fields(self, source_model_name, section):
        """Return whether a section was configured, including all-off rows."""
        self.ensure_one()
        return bool(
            self.with_context(active_test=False).field_ids.filtered(
                lambda item: item.section == section
                and item.source_model_id.model == source_model_name
            )
        )

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
        configured = self.with_context(active_test=False).field_ids.filtered(
            lambda item: item.section == "metadata"
            and item.source_model_id.model == record._name
        )
        custom = configured.filtered("active").sorted(
            key=lambda item: (item.sequence, item.id)
        )
        result = []
        # ``configured`` and ``custom`` must remain distinct: a template whose
        # metadata rows were deliberately all disabled must render no metadata,
        # not silently fall back to the connector defaults.
        if configured:
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
                        "key": "template:%s" % field_record.id,
                        "field_path": field_record.field_path,
                        "hide_if_empty": bool(field_record.hide_if_empty),
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
                    "key": spec.get("key") or "builtin:metadata:%s" % (
                        spec.get("field_path") or spec.get("path") or spec.get("label_key", "document_no")
                    ),
                    "field_path": spec.get("field_path") or spec.get("path"),
                    "hide_if_empty": bool(spec.get("hide_if_empty")),
                }
            )
        return result

    def get_custom_line_context(self, line_records, currency=None, lang=None):
        self.ensure_one()
        if not line_records:
            return [], []
        source_model = line_records._name
        configured = self.with_context(active_test=False).field_ids.filtered(
            lambda item: item.section == "line"
            and item.source_model_id.model == source_model
        )
        custom = configured.filtered("active").sorted(
            key=lambda item: (item.sequence, item.id)
        )
        if not configured:
            return [], []
        columns = [
            {
                "label": field_record.label or field_record.field_path,
                "align": field_record.alignment,
                "width": field_record.width_percent,
                "key": "template:%s" % field_record.id,
                "field_path": field_record.field_path,
            }
            for field_record in custom
        ]
        lines = []
        for line in line_records:
            source_reference = self._export_line_reference(line)
            display_type = getattr(line, "display_type", False)
            if display_type in ("line_section", "line_subsection", "section", "subsection"):
                lines.append(
                    {
                        "is_section": True,
                        "description": plain_text(getattr(line, "name", "")),
                        **source_reference,
                    }
                )
                continue
            if display_type in ("line_note", "note"):
                lines.append(
                    {
                        "is_note": True,
                        "description": plain_text(getattr(line, "name", "")),
                        **source_reference,
                    }
                )
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
            lines.append({"values": values, **source_reference})
        return columns, lines

    @api.model
    def _export_line_reference(self, line):
        """Keep a stable source pointer beside every rendered business line.

        Some Odoo report helpers omit technical/accounting rows from the
        rendered output.  Matching the resulting dictionaries back to the
        source record by list index would then read values from a neighbour.
        The record reference also covers unsaved preview rows whose ``id`` is
        a ``NewId`` rather than a database integer.
        """
        if not line or not hasattr(line, "_name") or len(line) != 1:
            return {}
        return {
            "_source_model": line._name,
            "_source_id": line.id,
            "_source_record": line,
        }

    @api.model
    def _is_export_field_name_safe(self, field_name):
        lowered = (field_name or "").lower()
        return bool(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field_name or "")
            and field_name not in EXPORT_FIELD_EXCLUDED_NAMES
            and not field_name.startswith(EXPORT_FIELD_EXCLUDED_PREFIXES)
            and not any(part in lowered for part in EXPORT_FIELD_SECRET_PARTS)
        )

    @api.model
    def _node_is_statically_hidden(self, node):
        for candidate in (node, *node.iterancestors()):
            for attribute in ("invisible", "column_invisible"):
                invisible = (candidate.get(attribute) or "").strip().lower()
                if invisible in {"1", "true"}:
                    return True
        return False

    @api.model
    def _node_is_export_utility(self, node):
        """Reject view helpers that display controls rather than printable data."""
        widget = (node.get("widget") or "").strip()
        if widget in EXPORT_FIELD_UTILITY_WIDGETS:
            return True
        explicit_label = node.get("string")
        if explicit_label is not None and not explicit_label.strip():
            return True
        for ancestor in node.iterancestors():
            if ancestor.tag == "button":
                return True
            classes = set((ancestor.get("class") or "").split())
            if "alert" in classes or "oe_button_box" in classes or "button_box" in classes:
                return True
        return False

    @api.model
    def _view_field_nodes(self, document_model, section, config):
        """Return access-filtered form nodes for a document or its line view."""
        view_id = config.get("view_id")
        view = document_model.get_view(
            view_id=view_id.id if getattr(view_id, "id", False) else view_id,
            view_type="form",
        )
        try:
            arch = etree.fromstring(view["arch"].encode())
        except (KeyError, TypeError, ValueError, etree.XMLSyntaxError):
            return []
        relation_field = config.get("relation_field")
        if section == "metadata" or not relation_field:
            nodes = []
            for node in arch.xpath(".//field[@name]"):
                # Embedded list/form fields belong to the line model.  A root
                # document field never has another field node as an ancestor.
                if node.xpath("ancestor::field"):
                    continue
                nodes.append(node)
            return nodes
        containers = arch.xpath(
            ".//field[@name=$field_name]", field_name=relation_field
        )
        nodes = []
        custom_nodes = []
        for container in containers:
            for node in container.xpath(".//field[@name]"):
                ancestors = node.xpath("ancestor::field")
                if not ancestors or ancestors[-1] is not container:
                    continue
                field_name = node.get("name") or ""
                if field_name.startswith(("x_", "x_studio_")):
                    custom_nodes.append(node)
                    continue
                if node.xpath("ancestor::list"):
                    nodes.append(node)
        nodes.extend(custom_nodes)
        if nodes:
            return nodes
        # Some installations keep the x2many list in a separate inherited
        # view.  Falling back to the real user's default list still preserves
        # field-level groups and gives Studio fields a chance to appear.
        line_model = document_model.env[config["model"]]
        try:
            line_arch = etree.fromstring(
                line_model.get_view(view_type="list")["arch"].encode()
            )
        except (KeyError, TypeError, ValueError, etree.XMLSyntaxError):
            return []
        return line_arch.xpath(".//field[@name]")

    @api.model
    def _export_field_alignment(self, field_type):
        if field_type in {"float", "integer", "monetary"}:
            return "right"
        if field_type in {"date", "datetime", "boolean"}:
            return "center"
        return "left"

    @api.model
    def _export_field_path_visible(self, user_env, model_name, path):
        """Validate every segment with the exporting user's ``fields_get``.

        The template record can legitimately be selected with ``sudo`` for a
        portal/native report.  That elevation must never become the source of
        truth for the field chooser, especially for Studio fields protected
        by a group on either the document or a related model.
        """
        if model_name not in user_env.registry.models or not path:
            return False
        current_model = user_env[model_name]
        parts = path.split(".")
        for index, part in enumerate(parts):
            try:
                description = current_model.fields_get(
                    [part], attributes=["type", "relation"]
                ).get(part)
            except AccessError:
                return False
            if not description:
                return False
            if index < len(parts) - 1:
                relation = description.get("relation")
                if (
                    description.get("type") != "many2one"
                    or relation not in user_env.registry.models
                ):
                    return False
                current_model = user_env[relation]
        return True

    def _export_field_config(self, record):
        self.ensure_one()
        if hasattr(record, "_rds_export_field_config"):
            config = record._rds_export_field_config()
            if isinstance(config, dict):
                return config
        return {
            "metadata": {"model": record._name, "relation_field": False},
        }

    def _export_base_context(self, record, lang=None):
        self.ensure_one()
        if not hasattr(record, "_rds_document_context"):
            return {}
        # ``None`` explicitly means inherit the connector/template defaults;
        # an empty list means the user deliberately disabled every field.
        clean_record = record.with_context(
            lang=lang or record.env.context.get("lang"),
            rds_export_field_specs=None,
        )
        return clean_record._rds_document_context(self, lang)

    def get_export_field_catalog(self, record, lang=None, base_context=None):
        """Discover printable fields from the access-filtered live form.

        The returned technical key/path catalog is also the allow-list used at
        render time.  It is intentionally produced without sudo so Studio
        fields restricted by groups cannot leak into the chooser or output.
        """
        self.ensure_one()
        record.ensure_one()
        config = self._export_field_config(record)
        base_context = (
            base_context
            if isinstance(base_context, dict)
            else self._export_base_context(record, lang=lang)
        )
        catalog = []
        by_key = {}
        by_section_path = {}
        visible_path_cache = {}

        def path_is_visible(definition):
            source_model = definition.get("source_model")
            paths = [
                definition.get("field_path"),
                *(definition.get("fallback_paths") or []),
            ]
            for path in filter(None, paths):
                cache_key = (source_model, path)
                if cache_key not in visible_path_cache:
                    visible_path_cache[cache_key] = self._export_field_path_visible(
                        record.env, source_model, path
                    )
                if visible_path_cache[cache_key]:
                    return True
            return False

        def add(definition):
            access_checked = definition.pop("_access_checked", False)
            key = definition.get("key")
            path = definition.get("field_path")
            section = definition.get("section")
            if key in by_key:
                # Runtime context owns the current label/enabled state, while
                # connector definitions carry value-independent semantics
                # such as conditional rich formatting and hide-if-empty.
                existing = by_key[key]
                for option in (
                    "context_only",
                    "fallback_paths",
                    "hide_if_empty",
                ):
                    if option in definition:
                        existing[option] = definition[option]
                return
            if (
                not key
                or not path
                or (not access_checked and not path_is_visible(definition))
                or (section, path) in by_section_path
                or len(catalog) >= MAX_EXPORT_FIELD_CANDIDATES
            ):
                return
            definition["sequence"] = len(catalog) * 10 + 10
            catalog.append(definition)
            by_key[key] = definition
            by_section_path[(section, path)] = definition

        for section, context_key in (("metadata", "metadata"), ("line", "columns")):
            for item in base_context.get(context_key) or []:
                if not isinstance(item, dict) or not item.get("field_path"):
                    continue
                add(
                    {
                        "key": item.get("key") or "builtin:%s:%s" % (
                            section,
                            item["field_path"],
                        ),
                        "section": section,
                        "source_model": config.get(section, {}).get("model", record._name),
                        "field_path": item["field_path"],
                        "label": item.get("label") or item["field_path"],
                        "enabled": True,
                        "origin": "template" if str(item.get("key", "")).startswith("template:") else "builtin",
                        "alignment": item.get("align") or "left",
                        "hide_if_empty": bool(item.get("hide_if_empty")),
                    }
                )

        # Include disabled persistent template rows.  They remain grey in the
        # per-export chooser instead of making the defaults reappear.
        for item in self.with_context(active_test=False).field_ids.sorted(
            key=lambda field: (field.section, field.sequence, field.id)
        ):
            if item.section not in config:
                continue
            if item.source_model_id.model != config[item.section].get("model"):
                continue
            add(
                {
                    "key": "template:%s" % item.id,
                    "section": item.section,
                    "source_model": item.source_model_id.model,
                    "field_path": item.field_path,
                    "label": item.label or item.field_path,
                    "enabled": bool(item.active),
                    "origin": "template",
                    "alignment": item.alignment,
                    "hide_if_empty": bool(item.hide_if_empty),
                }
            )

        # A connector's built-in fields must remain in the allow-list even
        # when the current sample record has no value for a conditional card.
        # Otherwise a selection made from record A can fail validation while
        # rendering record B in the same batch.  Template rows are registered
        # first so a configured field keeps ownership of its path even when
        # hide-if-empty removed it from this record's runtime context.
        for section, section_config in config.items():
            source_model_name = section_config.get("model")
            if source_model_name not in record.env.registry.models:
                continue
            source_model = record.env[source_model_name].with_context(lang=lang)
            descriptions = source_model.fields_get(attributes=["string"])
            for item in section_config.get("builtin_fields") or []:
                field_path = item.get("field_path")
                first_field = (field_path or "").split(".", 1)[0]
                label = item.get("label")
                if not label and item.get("label_key"):
                    label = tr_label(item["label_key"], lang)
                if not label:
                    label = (
                        descriptions.get(first_field, {}).get("string")
                        or field_path
                    )
                add(
                    {
                        "key": item.get("key"),
                        "section": section,
                        "source_model": source_model_name,
                        "field_path": field_path,
                        "label": label,
                        "enabled": False,
                        "origin": "builtin",
                        "alignment": item.get("alignment") or "left",
                        "context_only": bool(item.get("context_only")),
                        "fallback_paths": list(item.get("fallback_paths") or []),
                        "hide_if_empty": bool(item.get("hide_if_empty")),
                    }
                )

        document_model = record.env[record._name].with_context(lang=lang)
        for section, section_config in config.items():
            source_model_name = section_config.get("model")
            if source_model_name not in record.env.registry.models:
                continue
            source_model = record.env[source_model_name].with_context(lang=lang)
            descriptions = source_model.fields_get(
                attributes=["string", "type", "relation"]
            )
            for node in self._view_field_nodes(document_model, section, section_config):
                field_name = node.get("name")
                description = descriptions.get(field_name)
                field = source_model._fields.get(field_name)
                if (
                    not description
                    or not field
                    or self._node_is_statically_hidden(node)
                    or self._node_is_export_utility(node)
                    or not self._is_export_field_name_safe(field_name)
                    or description.get("type") not in EXPORT_FIELD_TYPES
                    or getattr(field, "exportable", True) is False
                ):
                    continue
                label = re.sub(
                    r"\s+",
                    " ",
                    (node.get("string") or description.get("string") or field_name).strip(),
                )
                if not label:
                    continue
                add(
                    {
                        "key": "screen:%s:%s" % (section, field_name),
                        "section": section,
                        "source_model": source_model_name,
                        "field_path": field_name,
                        "label": label,
                        "enabled": False,
                        "origin": "screen",
                        "alignment": self._export_field_alignment(description.get("type")),
                        "field_type": description.get("type"),
                        "is_custom": field_name.startswith(("x_", "x_studio_")),
                        "_access_checked": True,
                    }
                )
        return catalog

    def normalize_export_field_specs(self, record, specs, lang=None):
        """Validate an untrusted per-export selection against the live view."""
        self.ensure_one()
        record.ensure_one()
        if not isinstance(specs, (list, tuple)):
            raise UserError(_("Belge alanı seçimi geçerli bir liste olmalıdır."))
        if len(specs) > MAX_EXPORT_METADATA_FIELDS + MAX_EXPORT_LINE_FIELDS:
            raise UserError(_("Tek belgede çok fazla alan seçildi."))
        catalog = {
            item["key"]: item
            for item in self.get_export_field_catalog(record, lang=lang)
        }
        normalized = []
        seen = set()
        section_counts = {"metadata": 0, "line": 0}
        for raw in specs:
            if not isinstance(raw, dict):
                raise UserError(_("Belge alanı seçimi geçersiz."))
            key = raw.get("key")
            definition = catalog.get(key)
            if not definition or key in seen:
                raise UserError(_("Seçilen belge alanı artık kullanılamıyor."))
            label = raw.get("label")
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 200:
                raise UserError(_("Belge alanı başlığı 1 ile 200 karakter arasında olmalıdır."))
            section = definition["section"]
            section_counts[section] += 1
            limit = (
                MAX_EXPORT_METADATA_FIELDS
                if section == "metadata"
                else MAX_EXPORT_LINE_FIELDS
            )
            if section_counts[section] > limit:
                raise UserError(
                    _("%(section)s bölümünde en fazla %(limit)s alan seçilebilir.")
                    % {
                        "section": _("Belge Bilgileri") if section == "metadata" else _("Satır Sütunları"),
                        "limit": limit,
                    }
                )
            normalized.append(
                {
                    "key": key,
                    "section": section,
                    "source_model": definition["source_model"],
                    "field_path": definition["field_path"],
                    "label": label.strip(),
                    "alignment": definition.get("alignment") or "left",
                    "context_only": bool(definition.get("context_only")),
                    "fallback_paths": list(definition.get("fallback_paths") or []),
                    "hide_if_empty": bool(definition.get("hide_if_empty")),
                }
            )
            seen.add(key)
        return normalized

    def _selected_field_value(self, record, spec, lang=None):
        self.ensure_one()
        if not record:
            return ""
        for path in (spec["field_path"], *(spec.get("fallback_paths") or [])):
            value, field, owner = self._resolve_path_info(record, path)
            formatted = self.format_value(
                value,
                "auto",
                currency=getattr(record, "currency_id", None),
                lang=lang,
                field=field,
                owner=owner,
            )
            if formatted:
                return formatted
        return ""

    def apply_export_field_specs(self, context, record, specs, lang=None):
        """Filter/relabel rich connector context and append safe screen fields."""
        self.ensure_one()
        normalized = self.normalize_export_field_specs(record, specs, lang=lang)
        metadata_specs = [item for item in normalized if item["section"] == "metadata"]
        line_specs = [item for item in normalized if item["section"] == "line"]

        metadata_by_key = {
            item.get("key"): item
            for item in context.get("metadata") or []
            if isinstance(item, dict) and item.get("key")
        }
        metadata = []
        for spec in metadata_specs:
            item = dict(metadata_by_key.get(spec["key"]) or {})
            if not item:
                if spec.get("hide_if_empty"):
                    continue
                item = {
                    "value": (
                        "-"
                        if spec.get("context_only")
                        else self._selected_field_value(record, spec, lang=lang)
                        or "-"
                    ),
                    "icon": "fa-circle-o",
                    "align": spec.get("alignment") or "left",
                    "field_path": spec["field_path"],
                    "key": spec["key"],
                }
            item["label"] = spec["label"]
            item["value"] = item.get("value") or "-"
            metadata.append(item)
        context["metadata"] = metadata
        context["show_metadata"] = bool(metadata)

        columns = [item for item in context.get("columns") or [] if isinstance(item, dict)]
        column_index = {
            item.get("key"): index
            for index, item in enumerate(columns)
            if item.get("key")
        }
        selected_columns = []
        for spec in line_specs:
            column = dict(columns[column_index[spec["key"]]]) if spec["key"] in column_index else {
                "key": spec["key"],
                "field_path": spec["field_path"],
                "align": spec.get("alignment") or "left",
            }
            column["label"] = spec["label"]
            selected_columns.append(column)
        if selected_columns:
            base_width, remainder = divmod(100, len(selected_columns))
            for index, column in enumerate(selected_columns):
                column["width"] = base_width + (1 if index < remainder else 0)

        line_records = context.get("_line_records")
        if line_records is None:
            line_records = (
                record._rds_export_line_records()
                if hasattr(record, "_rds_export_line_records")
                else self.env["res.users"].browse()
            )
        line_records_by_reference = {
            (line._name, line.id): line
            for line in line_records
            if line.id
        }
        original_lines = context.get("lines") or []
        selected_lines = []
        for index, original in enumerate(original_lines):
            if original.get("is_section") or original.get("is_note"):
                selected_lines.append(dict(original))
                continue
            source_line = original.get("_source_record")
            if (
                not source_line
                or not hasattr(source_line, "_name")
                or len(source_line) != 1
            ):
                source_line = line_records_by_reference.get(
                    (
                        original.get("_source_model"),
                        original.get("_source_id"),
                    )
                )
            if not source_line and index < len(line_records):
                # Compatibility fallback for third-party connectors that have
                # not adopted the stable line reference contract yet.
                source_line = line_records[index]
            source_values = original.get("values") or []
            values = []
            for spec in line_specs:
                existing_index = column_index.get(spec["key"])
                if existing_index is not None and existing_index < len(source_values):
                    values.append(dict(source_values[existing_index]))
                    continue
                text = self._selected_field_value(source_line, spec, lang=lang) if source_line else ""
                values.append(
                    {
                        "text": text or "-",
                        "align": spec.get("alignment") or "left",
                    }
                )
            selected_lines.append(
                {
                    "values": values,
                    **{
                        key: original[key]
                        for key in (
                            "_source_model",
                            "_source_id",
                            "_source_record",
                        )
                        if key in original
                    },
                }
            )
        context["columns"] = selected_columns
        context["lines"] = selected_lines if selected_columns else []
        context["show_lines"] = bool(selected_columns)
        return context

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
            "document_no": tr_label("document_no", code),
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
            "show_lines": True,
            "show_notes": self.show_notes,
            "show_bank": self.show_bank,
            "show_footer": self.show_footer,
        }
