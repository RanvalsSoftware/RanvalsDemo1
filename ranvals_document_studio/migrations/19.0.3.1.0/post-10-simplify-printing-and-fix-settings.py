"""Repair the Odoo 19 user-rights arch and simplify legacy print bindings."""

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL
from psycopg2.extras import Json


SAFE_CATEGORY_NAME = "DocuCraft"
SAFE_PRIVILEGE_NAME = "DocuCraft Yönetimi"

TEMPLATE_NAMES = {
    "template_beauty_premium": "Signature Burgundy",
    "template_construction_navy": "Executive Navy",
    "template_technology_blue": "Horizon Blue",
    "template_industrial_red": "Atlas Steel",
    "template_eco_green": "Sage Reserve",
    "template_furniture_terracotta": "Copper Atelier",
}

REPORT_NAMES = {
    "action_report_rds_sale_document": "DocuCraft Belgesi – Varsayılan",
    "action_report_rds_sale_beauty_document": "DocuCraft – Signature Burgundy",
    "action_report_rds_sale_construction_document": "DocuCraft – Executive Navy",
    "action_report_rds_sale_technology_document": "DocuCraft – Horizon Blue",
    "action_report_rds_sale_industrial_document": "DocuCraft – Atlas Steel",
    "action_report_rds_sale_eco_document": "DocuCraft – Sage Reserve",
    "action_report_rds_sale_furniture_document": "DocuCraft – Copper Atelier",
}

REPORT_FILE_NAMES = {
    "action_report_rds_sale_beauty_document": "(object.name or 'Quotation') + '_Signature_Burgundy'",
    "action_report_rds_sale_construction_document": "(object.name or 'Quotation') + '_Executive_Navy'",
    "action_report_rds_sale_technology_document": "(object.name or 'Quotation') + '_Horizon_Blue'",
    "action_report_rds_sale_industrial_document": "(object.name or 'Quotation') + '_Atlas_Steel'",
    "action_report_rds_sale_eco_document": "(object.name or 'Quotation') + '_Sage_Reserve'",
    "action_report_rds_sale_furniture_document": "(object.name or 'Quotation') + '_Copper_Atelier'",
}

UNBOUND_SERVER_ACTIONS = {
    "server_action_sale_rds_export": "DocuCraft Uyumluluk – Seçici",
    "server_action_sale_rds_quick_pdf": "DocuCraft Uyumluluk – PDF",
    "server_action_sale_rds_quick_word": "DocuCraft Uyumluluk – Word",
    "server_action_account_rds_quick_pdf": "DocuCraft Uyumluluk – PDF",
    "server_action_account_rds_quick_word": "DocuCraft Uyumluluk – Word",
    "server_action_purchase_rds_quick_pdf": "DocuCraft Uyumluluk – PDF",
    "server_action_purchase_rds_quick_word": "DocuCraft Uyumluluk – Word",
}


def _write_translated(record, values):
    """Set every stored language without activating inactive languages.

    Odoo 19 stores translated fields as JSONB.  ``with_context(lang=...)``
    rejects inactive languages, so normal ORM writes can leave a historical
    unsafe translation behind.  Preserve the existing language keys but
    normalize every value, and always retain the required en_US fallback.
    """
    if not record:
        return
    translated = {
        name: value
        for name, value in values.items()
        if name in record._fields and record._fields[name].translate
    }
    plain = {name: value for name, value in values.items() if name not in translated}
    if plain:
        record.sudo().write(plain)
    for name, value in translated.items():
        field = record._fields[name]
        stored = field._get_stored_translations(record) or {}
        normalized = {language: value for language in stored}
        normalized["en_US"] = value
        record.flush_recordset([name])
        record.env.cr.execute(SQL(
            "UPDATE %s SET %s = %s WHERE id = %s",
            SQL.identifier(record._table),
            SQL.identifier(name),
            Json(normalized),
            record.id,
        ))
        record.invalidate_recordset([name])
        record.modified([name])


def _ref(env, xmlid):
    return env.ref(
        "ranvals_document_studio.%s" % xmlid,
        raise_if_not_found=False,
    )


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Odoo 19's group widget interpolates category names into an XML string
    # without escaping them.  Rewrite every stored translation so an old
    # ampersand can never keep the Users form broken after the upgrade.
    _write_translated(
        _ref(env, "module_category_rds"),
        {"name": SAFE_CATEGORY_NAME},
    )
    _write_translated(
        _ref(env, "privilege_rds"),
        {"name": SAFE_PRIVILEGE_NAME},
    )

    # Seed templates are noupdate records.  Preserve their technical keys and
    # layout settings while replacing the old sector-specific names and
    # previews.  The form now renders a live, theme-aware preview instead.
    for xmlid, name in TEMPLATE_NAMES.items():
        _write_translated(
            _ref(env, xmlid),
            {"name": name, "preview_path": False},
        )

    sale_model = env["ir.model"]._get("sale.order")
    account_model = env["ir.model"]._get("account.move")
    purchase_model = env["ir.model"]._get("purchase.order")

    for xmlid, name in UNBOUND_SERVER_ACTIONS.items():
        action = _ref(env, xmlid)
        if action:
            action.sudo().write({"binding_model_id": False})
            _write_translated(action, {"name": name})

    single_print_actions = {
        "server_action_sale_rds_print": sale_model,
        "server_action_account_rds_export": account_model,
        "server_action_purchase_rds_export": purchase_model,
    }
    for xmlid, model in single_print_actions.items():
        action = _ref(env, xmlid)
        if not action:
            continue
        action.sudo().write({
            "binding_model_id": model.id,
            "binding_type": "report",
            "binding_view_types": "list,form",
        })
        _write_translated(action, {"name": "DocuCraft Yazdır"})

    for xmlid, name in REPORT_NAMES.items():
        report = _ref(env, xmlid)
        if not report:
            continue
        values = {"binding_model_id": False}
        if xmlid in REPORT_FILE_NAMES:
            values["print_report_name"] = REPORT_FILE_NAMES[xmlid]
        report.sudo().write(values)
        _write_translated(report, {"name": name})

    env.registry.clear_cache("groups")
    env.invalidate_all()
