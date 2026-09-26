from odoo import SUPERUSER_ID, api


ICON_PATH = "/ranvals_document_studio/static/description/docucraft_app_icon_20260911.png"
MENU_ICON = "ranvals_document_studio,static/description/docucraft_app_icon_20260911.png"
MODULES = (
    "ranvals_document_studio",
)


def migrate(cr, version):
    """Refresh every DocuCraft icon reference with a cache-busting path."""
    env = api.Environment(cr, SUPERUSER_ID, {})

    modules = env["ir.module.module"].sudo().search([("name", "in", MODULES)])
    if modules:
        modules.write({"icon": ICON_PATH})

    menu = env.ref("ranvals_document_studio.menu_rds_root", raise_if_not_found=False)
    if menu:
        menu.sudo().write({"web_icon": MENU_ICON})

    env.invalidate_all()
