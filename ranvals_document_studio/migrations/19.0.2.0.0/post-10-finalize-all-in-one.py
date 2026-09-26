"""Finalize report bindings and retire the former split module rows."""

from odoo import SUPERUSER_ID, api


LEGACY_MODULES = (
    "ranvals_document_studio_sale",
    "ranvals_document_studio_account",
    "ranvals_document_studio_purchase",
    "ranvals_document_studio_studio",
)
SALE_REPORT_ACTIONS = (
    "action_report_rds_sale_beauty_document",
    "action_report_rds_sale_construction_document",
    "action_report_rds_sale_technology_document",
    "action_report_rds_sale_industrial_document",
    "action_report_rds_sale_eco_document",
    "action_report_rds_sale_furniture_document",
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sale_model = env["ir.model"]._get("sale.order")
    expected = {
        "binding_model_id": sale_model.id,
        "binding_type": "report",
        "binding_view_types": "list,form",
        "report_type": "qweb-pdf",
    }
    for xmlid in SALE_REPORT_ACTIONS:
        action = env.ref(
            "ranvals_document_studio.%s" % xmlid,
            raise_if_not_found=False,
        )
        if not action:
            continue
        values = {
            field_name: value
            for field_name, value in expected.items()
            if (
                action[field_name].id
                if field_name == "binding_model_id"
                else action[field_name]
            )
            != value
        }
        if values:
            action.write(values)

    # The data records were claimed by the target module in the pre-migration.
    # Avoid running the normal uninstall cleanup: it would delete those records.
    cr.execute(
        """
        UPDATE ir_module_module
           SET state = 'uninstalled',
               auto_install = FALSE,
               application = FALSE
         WHERE name = ANY(%s)
        """,
        [list(LEGACY_MODULES)],
    )
    env.invalidate_all()
