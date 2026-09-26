from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

RETENTION_DAYS_KEY = "ranvals_document_studio.retention_days"
DELETE_ATTACHMENTS_KEY = "ranvals_document_studio.retention_delete_owned_attachments"


class RdsExportLogRetention(models.Model):
    _inherit = "rds.export.log"

    payload_purged_at = fields.Datetime(readonly=True, index=True)

    @api.model
    def _retention_parameters(self):
        params = self.env["ir.config_parameter"].sudo()
        try:
            days = int(params.get_param(RETENTION_DAYS_KEY, "90"))
        except (TypeError, ValueError):
            days = 90
        days = min(max(days, 1), 3650)
        delete_attachments = params.get_param(DELETE_ATTACHMENTS_KEY, "False").lower() in ("1", "true", "yes")
        return days, delete_attachments

    @api.model
    def _cron_purge_expired_payloads(self, limit=500):
        days, delete_attachments = self._retention_parameters()
        cutoff = fields.Datetime.now() - timedelta(days=days)
        domain = [("create_date", "<", cutoff)]
        if delete_attachments:
            domain.extend(["|", ("payload_purged_at", "=", False), ("attachment_id", "!=", False)])
        else:
            domain.append(("payload_purged_at", "=", False))
        logs = self.sudo().search(domain, order="create_date, id", limit=min(max(int(limit), 1), 2000))
        purged_payloads = 0
        deleted_attachments = 0
        for log in logs:
            # Clear only the attachment-backed binary owned by this log field.
            history_blobs = self.env["ir.attachment"].sudo().search([
                ("res_model", "=", self._name),
                ("res_field", "=", "file_data"),
                ("res_id", "=", log.id),
            ])
            if history_blobs or log.file_data:
                log.write({"file_data": False})
                history_blobs.exists().unlink()
                purged_payloads += 1

            if delete_attachments and log.attachment_id:
                attachment = log.attachment_id.sudo().exists()
                owned = bool(
                    attachment
                    and attachment.res_model == log.res_model
                    and attachment.res_id == log.res_id
                    and attachment.create_uid == log.user_id
                    and attachment.name == log.file_name
                    and (not log.file_mimetype or attachment.mimetype == log.file_mimetype)
                )
                log.write({"attachment_id": False})
                if owned:
                    attachment.unlink()
                    deleted_attachments += 1
            if not log.payload_purged_at:
                log.write({"payload_purged_at": fields.Datetime.now()})
        purged_jobs = 0
        # The background-job feature is optional during upgrades.  Reuse the
        # exact same retention window when its model is present, and never
        # touch queued/running jobs.
        if "rds.export.job" in self.env.registry.models:
            jobs = self.env["rds.export.job"].sudo().search([
                ("state", "in", ("done", "failed", "cancelled")),
                ("finished_at", "!=", False),
                ("finished_at", "<", cutoff),
                ("payload_purged_at", "=", False),
            ], order="finished_at, id", limit=min(max(int(limit), 1), 2000))
            job_blobs = self.env["ir.attachment"].sudo().search([
                ("res_model", "=", "rds.export.job"),
                ("res_field", "=", "file_data"),
                ("res_id", "in", jobs.ids),
            ])
            for job in jobs:
                owned_blobs = job_blobs.filtered(lambda attachment, current=job: attachment.res_id == current.id)
                had_payload = bool(owned_blobs or job.file_data)
                job.write({
                    "file_data": False,
                    "payload_purged_at": fields.Datetime.now(),
                })
                if had_payload:
                    purged_jobs += 1
            job_blobs.exists().filtered(
                lambda attachment: attachment.res_id in set(jobs.ids)
            ).unlink()
        return {
            "logs": len(logs),
            "payloads": purged_payloads,
            "attachments": deleted_attachments,
            "jobs": purged_jobs,
        }


class RdsRetentionSettings(models.TransientModel):
    _name = "rds.retention.settings"
    _description = "DocuCraft Saklama Politikası"

    retention_days = fields.Integer(required=True, default=90)
    delete_owned_attachments = fields.Boolean(default=False)

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        days, delete_attachments = self.env["rds.export.log"]._retention_parameters()
        values.update({"retention_days": days, "delete_owned_attachments": delete_attachments})
        return values

    @api.constrains("retention_days")
    def _check_retention_days(self):
        for settings in self:
            if settings.retention_days < 1 or settings.retention_days > 3650:
                raise ValidationError(_("Saklama süresi 1 ile 3650 gün arasında olmalıdır."))

    def _check_system_admin(self):
        # This policy is database-wide and the cron deliberately processes all
        # companies, so only system administrators may change or run it.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Genel saklama politikasını yalnız sistem yöneticileri yönetebilir."))

    def action_save(self):
        self.ensure_one()
        self._check_system_admin()
        self._check_retention_days()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(RETENTION_DAYS_KEY, str(self.retention_days))
        params.set_param(DELETE_ATTACHMENTS_KEY, "True" if self.delete_owned_attachments else "False")
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("DocuCraft"), "message": _("Saklama politikası kaydedildi."), "type": "success", "sticky": False,
        }}

    def action_purge_now(self):
        self.ensure_one()
        self.action_save()
        result = self.env["rds.export.log"]._cron_purge_expired_payloads(limit=2000)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Bakım tamamlandı"),
            "message": _("%(logs)s kayıt tarandı; %(payloads)s arşiv kopyası, %(attachments)s sahipli ek ve %(jobs)s tamamlanmış iş dosyası temizlendi.") % result,
            "type": "success", "sticky": True,
        }}
