from odoo import SUPERUSER_ID, api


ICON_PATH = "/ranvals_document_studio/static/description/docucraft_app_icon_v9_20260914.png"
MENU_ICON = "ranvals_document_studio,static/description/docucraft_app_icon_v9_20260914.png"
MODULES = (
    "ranvals_document_studio",
)


def migrate(cr, version):
    """Refresh the installed app/menu icon after upgrading an existing database."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    modules = env["ir.module.module"].sudo().search([("name", "in", MODULES)])
    if modules:
        modules.write({"icon": ICON_PATH})

    menu = env.ref("ranvals_document_studio.menu_rds_root", raise_if_not_found=False)
    if menu:
        menu.sudo().write({"name": "DocuCraft", "web_icon": MENU_ICON})

    env.registry.clear_cache()
    env.invalidate_all()
