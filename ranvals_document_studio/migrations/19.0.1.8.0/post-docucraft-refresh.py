from odoo import SUPERUSER_ID, api


ICON_PATH = "/ranvals_document_studio/static/description/docucraft_app_icon_v8_20260914.png"
MENU_ICON = "ranvals_document_studio,static/description/docucraft_app_icon_v8_20260914.png"
MODULE_NAMES = {
    "ranvals_document_studio": "DocuCraft – PDF, Word & Document Designer for Odoo",
}


def migrate(cr, version):
    """Refresh all DocuCraft application icons and visible module names."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    Module = env["ir.module.module"].sudo()

    for technical_name, display_name in MODULE_NAMES.items():
        module = Module.search([("name", "=", technical_name)], limit=1)
        if module:
            module.write({
                "icon": ICON_PATH,
                "shortdesc": display_name,
            })

    menu = env.ref("ranvals_document_studio.menu_rds_root", raise_if_not_found=False)
    if menu:
        menu.sudo().write({
            "name": "DocuCraft",
            "web_icon": MENU_ICON,
        })

    env.invalidate_all()
