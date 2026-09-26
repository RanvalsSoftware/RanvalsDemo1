from odoo import SUPERUSER_ID, api


PRODUCT_NAME = "DocuCraft – PDF, Word & Document Designer for Odoo"
CORE_ICON = "/ranvals_document_studio/static/description/icon.png"

MODULE_TITLES = {
    "ranvals_document_studio": PRODUCT_NAME,
}

MODULE_SUMMARIES = {
    "ranvals_document_studio": "Odoo teklif, fatura ve satın alma belgeleri için profesyonel PDF, DOCX ve PNG tasarım stüdyosu",
}

REPLACEMENTS = (
    ("Ranvals Document Studio", PRODUCT_NAME),
    ("Ranvals Belge Şablonu", "DocuCraft Belge Şablonu"),
    ("Belge Studio", "DocuCraft"),
    ("Document Studio", "DocuCraft"),
)


def _translated_languages(env):
    languages = env["res.lang"].search([("active", "=", True)]).mapped("code")
    return list(dict.fromkeys([env.user.lang or "en_US", "en_US", *languages]))


def _replace_text(value):
    if not isinstance(value, str) or not value:
        return value
    result = value
    for old, new in REPLACEMENTS:
        result = result.replace(old, new)
    return result


def _rewrite_translated_fields(env, model_name, field_names, domain):
    if model_name not in env.registry:
        return
    model = env[model_name].sudo()
    records = model.search(domain)
    if not records:
        return
    languages = _translated_languages(env)
    for lang in languages:
        localized = records.with_context(lang=lang)
        for record in localized:
            values = {}
            for field_name in field_names:
                if field_name not in record._fields:
                    continue
                old_value = record[field_name]
                new_value = _replace_text(old_value)
                if new_value != old_value:
                    values[field_name] = new_value
            if values:
                record.write(values)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    languages = _translated_languages(env)

    modules = env["ir.module.module"].sudo().search([
        ("name", "in", list(MODULE_TITLES)),
    ])
    modules_by_name = {module.name: module for module in modules}
    for technical_name, title in MODULE_TITLES.items():
        module = modules_by_name.get(technical_name)
        if not module:
            continue
        module.write({"icon": CORE_ICON})
        for lang in languages:
            module.with_context(lang=lang).write({
                "shortdesc": title,
                "summary": MODULE_SUMMARIES[technical_name],
            })

    searchable_models = (
        ("ir.ui.menu", ("name",)),
        ("ir.actions.act_window", ("name", "help")),
        ("ir.actions.client", ("name",)),
        ("ir.actions.server", ("name",)),
        ("ir.actions.report", ("name",)),
        ("report.paperformat", ("name",)),
        ("ir.module.category", ("name", "description")),
        ("res.groups.privilege", ("name", "description")),
        ("ir.model", ("name",)),
    )
    brand_domain = [
        "|", "|", "|",
        ("name", "ilike", "Ranvals Document Studio"),
        ("name", "ilike", "Ranvals Belge Şablonu"),
        ("name", "ilike", "Belge Studio"),
        ("name", "ilike", "Document Studio"),
    ]
    for model_name, field_names in searchable_models:
        _rewrite_translated_fields(env, model_name, field_names, brand_domain)

    env.invalidate_all()
