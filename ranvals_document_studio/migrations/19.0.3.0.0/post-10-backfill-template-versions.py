"""Create a restorable baseline for templates that predate version tracking."""

import logging

from odoo import SUPERUSER_ID, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        UPDATE rds_template_version
           SET version_user_id = COALESCE(create_uid, %s)
         WHERE version_user_id IS NULL
        """,
        [SUPERUSER_ID],
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    templates = env["rds.template"].search([])
    versioned_ids = set(env["rds.template.version"].search([]).mapped("template_id").ids)
    for template in templates.filtered(lambda item: item.id not in versioned_ids):
        try:
            with cr.savepoint():
                template._rds_create_version(
                    template._rds_snapshot_payload(),
                    # Migration scripts do not run in a request language;
                    # translating here would emit one traceback-style warning
                    # per legacy template during every upgrade.
                    note="Başlangıç sürümü",
                    diff_summary="19.0.3 yükseltme başlangıç durumu",
                )
        except ValidationError as error:
            # A legacy database can contain a template larger than even the
            # deliberately generous internal 1000-field / 4 MB snapshot
            # envelope.  Never truncate or rewrite that business data and do
            # not block the whole module upgrade; leave it unversioned and
            # make the exceptional record visible in the server log.
            _logger.warning(
                "DocuCraft skipped baseline snapshot for legacy template id=%s: %s",
                template.id,
                error,
            )
