import base64
import binascii

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools.common import check_record_access


class RdsExportLog(models.Model):
    _name = "rds.export.log"
    _description = "Belge Studio Dışa Aktarım Geçmişi"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True)
    user_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    template_id = fields.Many2one("rds.template", required=True, ondelete="restrict")
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    record_name = fields.Char()
    output_format = fields.Selection(
        [
            ("pdf", "PDF"),
            ("docx", "Word / DOCX (PDF görünümü)"),
            ("docx_editable", "Word / DOCX (düzenlenebilir)"),
            ("png", "PNG"),
            ("zip", "ZIP"),
        ],
        required=True,
        index=True,
    )
    file_name = fields.Char(required=True)
    file_size = fields.Integer()
    file_size_display = fields.Char(compute="_compute_file_size_display")
    file_mimetype = fields.Char(readonly=True)
    file_data = fields.Binary(
        readonly=True,
        attachment=True,
        copy=False,
        groups="base.group_system",
    )
    has_stored_file = fields.Boolean(
        compute="_compute_file_state",
        compute_sudo=True,
    )
    attached_to_record = fields.Boolean(
        compute="_compute_file_state",
        compute_sudo=True,
    )
    attachment_id = fields.Many2one("ir.attachment", ondelete="set null")

    @api.depends("file_size")
    def _compute_file_size_display(self):
        for record in self:
            size = max(record.file_size or 0, 0)
            if size < 1024:
                record.file_size_display = "%s B" % size
            elif size < 1024 * 1024:
                record.file_size_display = "%.1f KB" % (size / 1024)
            else:
                record.file_size_display = "%.1f MB" % (size / (1024 * 1024))

    @api.depends("file_data", "attachment_id")
    def _compute_file_state(self):
        history_attachment_ids = set()
        if self.ids:
            history_attachment_ids = set(
                self.env["ir.attachment"]
                .sudo()
                .search(
                    [
                        ("res_model", "=", self._name),
                        ("res_field", "=", "file_data"),
                        ("res_id", "in", self.ids),
                        ("file_size", ">", 0),
                    ]
                )
                .mapped("res_id")
            )
        for record in self:
            attachment = record.attachment_id.exists()
            attached = bool(
                attachment
                and attachment.res_model == record.res_model
                and attachment.res_id == record.res_id
            )
            record.has_stored_file = record.id in history_attachment_ids or attached
            record.attached_to_record = attached

    def _get_stored_file(self):
        self.ensure_one()
        check_record_access(self)
        self._check_source_access()
        # The raw blob field is system-only so it cannot be fetched through
        # generic ORM or /web/content endpoints.  The controlled route calls
        # this method after both log and live source-document ACL checks.
        file_data = self.sudo().file_data
        if file_data:
            try:
                content = base64.b64decode(file_data, validate=True)
            except (binascii.Error, TypeError, ValueError) as error:
                raise UserError(_("Bu geçmiş kaydındaki dosya verisi bozuk.")) from error
            return (
                self.file_name,
                content,
                self.file_mimetype or "application/octet-stream",
            )
        attachment = self.attachment_id.exists()
        if not attachment:
            raise UserError(_("Bu geçmiş kaydına ait indirilebilir dosya bulunamadı."))
        if attachment.res_model != self.res_model or attachment.res_id != self.res_id:
            raise AccessError(_("Geçmiş kaydındaki ek kaynak belgeyle eşleşmiyor."))
        check_record_access(attachment)
        content = attachment.raw
        if not content:
            raise UserError(_("Bu geçmiş kaydına ait indirilebilir dosya bulunamadı."))
        return (
            self.file_name or attachment.name,
            content,
            self.file_mimetype
            or attachment.mimetype
            or "application/octet-stream",
        )

    def _check_source_access(self):
        """Apply the source document's current ACLs to archived snapshots."""
        self.ensure_one()
        if self.res_model not in self.env.registry.models:
            raise AccessError(_("Kaynak belgeye erişim izniniz bulunmuyor."))
        source = self.env[self.res_model].browse(self.res_id).exists()
        if not source:
            raise AccessError(_("Kaynak belgeye erişim izniniz bulunmuyor."))
        check_record_access(source)

    def _has_source_access(self):
        """Return whether the current user may still read the source record."""
        self.ensure_one()
        try:
            self._check_source_access()
        except AccessError:
            return False
        return True

    def action_download(self):
        self.ensure_one()
        check_record_access(self)
        if not self.has_stored_file:
            raise UserError(_("Bu geçmiş kaydına ait indirilebilir dosya bulunamadı."))
        return {
            "type": "ir.actions.client",
            "tag": "ranvals_document_studio.download_export",
            "params": {
                "url": "/ranvals_document_studio/export_logs/%s" % self.id,
            },
        }
