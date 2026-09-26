"""Move records owned by the former connector addons into the single addon.

Before upgrading an existing five-addon database, the four legacy addon
directories must be removed from the addons path.  Reassigning their external
IDs before this module's XML files load lets those files update the original
records instead of creating duplicate reports, actions, views and bindings.
"""

LEGACY_MODULES = (
    "ranvals_document_studio_sale",
    "ranvals_document_studio_account",
    "ranvals_document_studio_purchase",
    "ranvals_document_studio_studio",
)
TARGET_MODULE = "ranvals_document_studio"


def migrate(cr, version):
    for legacy_module in LEGACY_MODULES:
        # Remove an already duplicated alias only when both XML IDs point to
        # the same record.  Divergent conflicts abort below so an upgrade can
        # never silently attach the canonical XML ID to the wrong record.
        cr.execute(
            """
            DELETE FROM ir_model_data AS legacy
             USING ir_model_data AS target
             WHERE legacy.module = %s
               AND target.module = %s
               AND target.name = legacy.name
               AND target.model = legacy.model
               AND target.res_id = legacy.res_id
            """,
            [legacy_module, TARGET_MODULE],
        )
        cr.execute(
            """
            SELECT legacy.name,
                   legacy.model,
                   legacy.res_id,
                   target.model,
                   target.res_id
              FROM ir_model_data AS legacy
              JOIN ir_model_data AS target
                ON target.module = %s
               AND target.name = legacy.name
             WHERE legacy.module = %s
            """,
            [TARGET_MODULE, legacy_module],
        )
        conflicts = cr.fetchall()
        if conflicts:
            details = ", ".join(
                "%s (%s,%s != %s,%s)" % row for row in conflicts[:10]
            )
            raise RuntimeError(
                "DocuCraft All-in-One XML-ID conflict while merging %s: %s"
                % (legacy_module, details)
            )
        cr.execute(
            "UPDATE ir_model_data SET module = %s WHERE module = %s",
            [TARGET_MODULE, legacy_module],
        )
