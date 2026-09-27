import base64
import binascii
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.common import check_record_access


_logger = logging.getLogger(__name__)

MAX_JOB_BYTES = 100 * 1024 * 1024
MAX_PENDING_JOBS_PER_USER = 20
MAX_JOB_ATTEMPTS = 3
MAX_ERROR_MESSAGE = 1000
MAX_FIELD_SELECTION_JSON_CHARS = 32_768
ALLOWED_OUTPUT_FORMATS = {
    "pdf",
    "docx",
    "docx_editable",
    "png",
    "zip",
}


class RdsExportJob(models.Model):
    _name = "rds.export.job"
    _description = "DocuCraft Arka Plan Dışa Aktarım İşi"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default=lambda self: _("Yeni"))
    state = fields.Selection(
        [
            ("queued", "Kuyrukta"),
            ("running", "Hazırlanıyor"),
            ("done", "Tamamlandı"),
            ("failed", "Hata"),
            ("cancelled", "İptal"),
        ],
        required=True,
        readonly=True,
        default="queued",
        index=True,
    )
    progress = fields.Integer(readonly=True, default=0)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
    )
    template_id = fields.Many2one(
        "rds.template",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    rds_source_report_id = fields.Many2one(
        "ir.actions.report",
        string="Kaynak Studio Raporu",
        readonly=True,
        copy=False,
        ondelete="set null",
        help=(
            "Studio'dan başlatılan dışa aktarımda kullanılan özgün rapor "
            "aksiyonu. Arka plan çalışanı aynı rapor kimliği ve bağlamıyla "
            "çıktı üretir."
        ),
    )
    source_report_expected = fields.Boolean(
        string="Studio Kaynak Raporu Bekleniyor",
        readonly=True,
        copy=False,
        help=(
            "İşin bir Studio raporundan başlatıldığını kalıcı olarak işaretler. "
            "Kaynak rapor sonradan silinirse başka bir rapora sessizce dönmek "
            "yerine iş güvenli biçimde başarısız olur."
        ),
    )
    res_model = fields.Char(required=True, readonly=True, index=True)
    res_ids_json = fields.Text(required=True, readonly=True)
    record_count = fields.Integer(required=True, readonly=True)
    record_names = fields.Char(readonly=True)
    output_format = fields.Selection(
        [
            ("pdf", "PDF"),
            ("docx_editable", "Word / DOCX – Düzenlenebilir"),
            ("docx", "Word / DOCX – Tasarımı Birebir Korur"),
            ("png", "PNG"),
            ("zip", "Tüm Formatlar / ZIP"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    dpi = fields.Selection(
        [("96", "96 DPI"), ("150", "150 DPI"), ("300", "300 DPI")],
        required=True,
        readonly=True,
        default="150",
    )
    language_id = fields.Many2one("res.lang", readonly=True, ondelete="restrict")
    attach_to_record = fields.Boolean(readonly=True)
    file_name_prefix = fields.Char(readonly=True)
    field_selection_json = fields.Text(
        string="Belge Alanı Seçimi",
        readonly=True,
        copy=False,
        help=(
            "İş oluşturulurken doğrulanan alan seçiminin değiştirilemez kopyası. "
            "Boş değer eski işler için şablon varsayılanlarını kullanır."
        ),
    )
    attempts = fields.Integer(readonly=True, default=0)
    requested_at = fields.Datetime(readonly=True, default=fields.Datetime.now, index=True)
    started_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    payload_purged_at = fields.Datetime(readonly=True, index=True)
    error_message = fields.Text(readonly=True)
    file_name = fields.Char(readonly=True)
    file_mimetype = fields.Char(readonly=True)
    file_size = fields.Integer(readonly=True)
    file_size_display = fields.Char(compute="_compute_file_size_display")
    file_data = fields.Binary(
        readonly=True,
        attachment=True,
        copy=False,
        groups="base.group_system",
    )
    has_file = fields.Boolean(compute="_compute_has_file", compute_sudo=True)

    @api.depends("file_size")
    def _compute_file_size_display(self):
        for job in self:
            size = max(job.file_size or 0, 0)
            if size < 1024:
                job.file_size_display = "%s B" % size
            elif size < 1024 * 1024:
                job.file_size_display = "%.1f KB" % (size / 1024)
            else:
                job.file_size_display = "%.1f MB" % (size / (1024 * 1024))

    @api.depends("file_data")
    def _compute_has_file(self):
        stored_ids = set()
        if self.ids:
            stored_ids = set(
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
        for job in self:
            job.has_file = job.id in stored_ids

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Arka plan işleri yalnız güvenli dışa aktarım akışından oluşturulabilir."))
        for values in vals_list:
            # ``ondelete=set null`` lets administrators remove obsolete report
            # actions without jobs becoming permanent blockers.  This durable
            # marker still lets the worker distinguish that case from a normal
            # connector export which never had a Studio source report.
            if values.get("rds_source_report_id"):
                values["source_report_expected"] = True
            if not values.get("name") or values.get("name") == _("Yeni"):
                values["name"] = (
                    self.env["ir.sequence"].next_by_code("rds.export.job")
                    or _("DocuCraft İşi")
                )
        return super().create(vals_list)

    @api.model
    def _enqueue_from_wizard(self, wizard):
        """Create one immutable job after validating the caller and source now."""
        wizard.ensure_one()
        # The wizard recordset carries the real RPC caller's environment.  Use
        # it consistently even when this private helper is invoked through a
        # differently-environmented model recordset in tests/server code.
        caller_env = wizard.env
        Job = caller_env[self._name]
        check_record_access(wizard, "write")
        records = wizard._get_records()
        check_record_access(wizard.template_id)
        source_report = wizard.rds_source_report_id
        if source_report:
            # Validate the report identity, model, source-record ACL and report
            # groups as the real caller before an immutable job is accepted.
            for record in records:
                wizard._rds_source_report(record)
        if wizard.output_format not in ALLOWED_OUTPUT_FORMATS:
            raise ValidationError(_("Desteklenmeyen arka plan çıktı formatı."))
        if wizard.template_id not in wizard.available_template_ids:
            raise ValidationError(_("Seçilen şablon bu kayıtlar için kullanılamaz."))
        companies = (
            records.mapped("company_id")
            if "company_id" in records._fields
            else caller_env["res.company"]
        )
        if len(companies) > 1:
            raise ValidationError(
                _("Bir arka plan işi yalnız aynı şirkete ait kayıtları içerebilir.")
            )
        company = companies or caller_env.company
        if not caller_env.su and company not in caller_env.companies:
            raise AccessError(_("Kaynak belgelerin şirketine erişim izniniz bulunmuyor."))
        pending_count = Job.sudo().search_count(
            [
                ("user_id", "=", caller_env.user.id),
                ("state", "in", ("queued", "running")),
            ]
        )
        if pending_count >= MAX_PENDING_JOBS_PER_USER:
            raise UserError(
                _("Aynı anda en fazla %s bekleyen dışa aktarım işi olabilir.")
                % MAX_PENDING_JOBS_PER_USER
            )
        names = ", ".join(records.mapped("display_name"))
        language_code = (
            wizard.language_id.code
            if wizard.language_id
            else caller_env.user.lang
        )
        field_specs = wizard._selected_field_specs(
            record=records[:1],
            language_code=language_code,
        )
        field_selection_json = (
            json.dumps(field_specs, ensure_ascii=False, separators=(",", ":"))
            if field_specs is not None
            else False
        )
        if (
            field_selection_json
            and len(field_selection_json) > MAX_FIELD_SELECTION_JSON_CHARS
        ):
            raise ValidationError(_("Belge alanı seçimi izin verilen sınırı aşıyor."))
        job = Job.sudo().create(
            {
                "user_id": caller_env.user.id,
                "company_id": company.id,
                "template_id": wizard.template_id.id,
                "rds_source_report_id": source_report.id or False,
                "source_report_expected": bool(source_report),
                "res_model": wizard.res_model,
                "res_ids_json": json.dumps(records.ids),
                "record_count": len(records),
                "record_names": names[:500],
                "output_format": wizard.output_format,
                "dpi": wizard.dpi,
                "language_id": wizard.language_id.id or False,
                "attach_to_record": wizard.attach_to_record,
                "file_name_prefix": wizard.file_name_prefix,
                "field_selection_json": field_selection_json,
            }
        )
        return job.with_user(caller_env.user)

    def _source_records(self):
        self.ensure_one()
        wizard_model = self.env["rds.export.wizard"]
        record_ids = wizard_model._parse_record_ids(self.res_ids_json)
        source_model = wizard_model._source_model(self.res_model)
        records = source_model.browse(record_ids).exists()
        if len(records) != len(record_ids):
            raise AccessError(_("Kaynak belgelerden biri artık kullanılamıyor."))
        check_record_access(records)
        if "company_id" in records._fields:
            # Company-neutral records are valid sources (the synchronous
            # exporter supports them too).  Only an explicit *different*
            # company is a cross-company violation.
            foreign = records.filtered(
                lambda record: record.company_id
                and record.company_id != self.company_id
            )
            if foreign:
                raise AccessError(_("Kaynak belge artık bu işin şirketine ait değil."))
        return records

    def _as_requesting_user(self):
        self.ensure_one()
        user = self.user_id.exists()
        if not user or not user.active or user.share:
            raise AccessError(_("İşi isteyen kullanıcı artık etkin bir iç kullanıcı değil."))
        if self.company_id not in user.company_ids:
            raise AccessError(_("İşi isteyen kullanıcının şirket erişimi artık bulunmuyor."))
        return self.with_user(user).with_company(self.company_id).with_context(
            allowed_company_ids=[self.company_id.id]
        )

    def _render_as_requesting_user(self):
        self.ensure_one()
        job = self._as_requesting_user()
        records = job._source_records()
        source_report = job.rds_source_report_id
        if job.source_report_expected and not source_report:
            raise AccessError(
                _(
                    "Bu işin kaynak Studio raporu artık mevcut değil; "
                    "güvenli bir çıktı üretilemedi."
                )
            )
        if source_report and not job.source_report_expected:
            # Defensive fail-closed handling for an inconsistent legacy or
            # manually altered row.  Legitimate jobs always set both values in
            # ``create``/``_enqueue_from_wizard``.
            raise AccessError(_("Arka plan işinin kaynak rapor bilgisi tutarsız."))
        field_specs = None
        if job.field_selection_json:
            if len(job.field_selection_json) > MAX_FIELD_SELECTION_JSON_CHARS:
                raise ValidationError(_("Belge alanı seçimi izin verilen sınırı aşıyor."))
            try:
                field_specs = json.loads(job.field_selection_json)
            except (TypeError, ValueError) as error:
                raise ValidationError(_("Belge alanı seçimi okunamadı.")) from error
            field_specs = job.template_id.normalize_export_field_specs(
                records[:1],
                field_specs,
                lang=job.language_id.code if job.language_id else job.env.user.lang,
            )
        field_line_commands = [
            (
                0,
                0,
                {
                    "section": item["section"],
                    "enabled": True,
                    "sequence": index * 10 + 10,
                    "source_model": item["source_model"],
                    "field_key": item["key"],
                    "field_path": item["field_path"],
                    "label": item["label"],
                    "auto_label": item["label"],
                    "sample_value": "-",
                    "origin": "screen",
                    "is_custom": item["field_path"].startswith("x_"),
                },
            )
            for index, item in enumerate(field_specs or [])
        ]
        wizard = job.env["rds.export.wizard"].create(
            {
                "res_model": job.res_model,
                "res_ids_json": job.res_ids_json,
                "template_id": job.template_id.id,
                "rds_source_report_id": source_report.id or False,
                "output_format": job.output_format,
                "dpi": job.dpi,
                "language_id": job.language_id.id or False,
                "attach_to_record": job.attach_to_record,
                "file_name_prefix": job.file_name_prefix,
                "field_selection_mode": (
                    "custom" if field_specs is not None else "inherit"
                ),
                "field_line_ids": field_line_commands,
                # The queue worker must always execute the payload now.  If
                # ``auto`` were left as the default, a ZIP or a job with more
                # than three records would enqueue itself again indefinitely.
                "processing_mode": "immediate",
            }
        )
        # Report ACLs and optional group restrictions may have changed while
        # the job waited in the queue.  Re-check every source record as the
        # requester before starting PDF/DOCX/PNG rendering.  The same helper
        # also validates the exact report/model pairing and company scope.
        if source_report:
            for record in records:
                wizard._rds_source_report(record)
        wizard.action_export()
        encoded = wizard.sudo().file_data
        try:
            content = base64.b64decode(encoded or b"", validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise UserError(_("Arka plan çıktısı geçerli dosya verisi üretmedi.")) from error
        if not content or len(content) > MAX_JOB_BYTES:
            raise UserError(_("Arka plan çıktısı boş veya 100 MB sınırını aşıyor."))
        values = {
            "file_name": wizard.file_name,
            "file_mimetype": wizard.file_mimetype,
            "file_size": len(content),
            "file_data": base64.b64encode(content),
        }
        wizard.sudo().unlink()
        return values

    @staticmethod
    def _safe_error_message(error):
        if isinstance(error, (AccessError, UserError, ValidationError)):
            message = str(error)
        else:
            message = _("Beklenmeyen bir sunucu hatası oluştu. Sistem yöneticisi logları incelemelidir.")
        return " ".join(message.split())[:MAX_ERROR_MESSAGE]

    def _notify_requester(self, *, success):
        self.ensure_one()
        if not self.user_id.active:
            return
        try:
            self.user_id.sudo()._bus_send(
                "simple_notification",
                {
                    "title": _("DocuCraft dışa aktarımı"),
                    "message": (
                        _("%s hazır. Arka Plan İşlerim ekranından indirebilirsiniz.")
                        % self.name
                        if success
                        else _("%s tamamlanamadı: %s")
                        % (self.name, self.error_message)
                    ),
                    "type": "success" if success else "danger",
                    "sticky": not success,
                },
            )
        except Exception:
            # A transient websocket/bus problem must never roll back a fully
            # rendered document or stop the cron from processing later jobs.
            _logger.exception(
                "Could not notify user %s for DocuCraft export job %s",
                self.user_id.id,
                self.id,
            )

    def _process_one(self):
        self.ensure_one()
        # Multiple cron workers/triggers may overlap.  SKIP LOCKED gives the
        # job to exactly one worker, and the SQL-domain recheck observes the
        # latest committed state instead of a stale ORM cache value.
        job = self.sudo().try_lock_for_update().filtered_domain(
            [("state", "=", "queued")]
        )
        if not job:
            return False
        if job.attempts >= MAX_JOB_ATTEMPTS:
            job.write(
                {
                    "state": "failed",
                    "progress": 100,
                    "finished_at": fields.Datetime.now(),
                    "error_message": _("Bu iş izin verilen yeniden deneme sayısını aştı."),
                }
            )
            return False
        job.write(
            {
                "state": "running",
                "progress": 10,
                "started_at": fields.Datetime.now(),
                "finished_at": False,
                "attempts": job.attempts + 1,
                "error_message": False,
            }
        )
        try:
            with self.env.cr.savepoint():
                values = job._render_as_requesting_user()
        except Exception as error:  # the cron must continue with the next job
            if not isinstance(error, (AccessError, UserError, ValidationError)):
                _logger.exception("Unexpected DocuCraft export job failure for job %s", job.id)
            job.write(
                {
                    "state": "failed",
                    "progress": 100,
                    "finished_at": fields.Datetime.now(),
                    "error_message": self._safe_error_message(error),
                }
            )
            job._notify_requester(success=False)
            return False
        job.write(
            {
                **values,
                "state": "done",
                "progress": 100,
                "finished_at": fields.Datetime.now(),
                "error_message": False,
            }
        )
        job._notify_requester(success=True)
        return True

    @api.model
    def _cron_process_pending_jobs(self, limit=3):
        limit = max(1, min(int(limit or 3), 10))
        domain = [("state", "=", "queued")]
        remaining = self.sudo().search_count(domain)
        jobs = self.sudo().search(domain, order="requested_at, id", limit=limit)
        cron = self.env["ir.cron"]
        if self.env.context.get("ir_cron_progress_id"):
            cron._commit_progress(remaining=remaining)
        for job in jobs:
            job._process_one()
            remaining = max(remaining - 1, 0)
            if self.env.context.get("ir_cron_progress_id"):
                time_left = cron._commit_progress(1, remaining=remaining)
                if not time_left:
                    break
        return len(jobs)

    def action_cancel(self):
        locked_jobs = self.browse()
        for requested_job in self:
            check_record_access(requested_job)
            if requested_job.user_id != self.env.user and not self.env.user.has_group(
                "ranvals_document_studio.group_rds_manager"
            ):
                raise AccessError(_("Yalnız kendi işinizi iptal edebilirsiniz."))
            job = requested_job.try_lock_for_update().filtered_domain(
                [("state", "=", "queued")]
            )
            if not job:
                raise UserError(_("Yalnız kuyruktaki işler iptal edilebilir."))
            locked_jobs |= job
        locked_jobs.sudo().write(
            {
                "state": "cancelled",
                "progress": 100,
                "finished_at": fields.Datetime.now(),
            }
        )
        return True

    def action_retry(self):
        self.ensure_one()
        check_record_access(self)
        if self.user_id != self.env.user and not self.env.user.has_group(
            "ranvals_document_studio.group_rds_manager"
        ):
            raise AccessError(_("Yalnız kendi işinizi yeniden deneyebilirsiniz."))
        job = self.try_lock_for_update().filtered_domain(
            [("state", "in", ("failed", "cancelled"))]
        )
        if not job:
            raise UserError(_("Yalnız başarısız veya iptal edilmiş işler yeniden denenebilir."))
        if job.attempts >= MAX_JOB_ATTEMPTS:
            raise UserError(_("Bu iş izin verilen yeniden deneme sayısını aştı."))
        # Validate both the actor requesting the retry and the original
        # requester whose privileges the worker will actually use.
        job._source_records()
        job.sudo()._as_requesting_user()._source_records()
        job.sudo().write(
            {
                "state": "queued",
                "progress": 0,
                "started_at": False,
                "finished_at": False,
                "error_message": False,
                "file_data": False,
                "file_name": False,
                "file_mimetype": False,
                "file_size": 0,
                "payload_purged_at": False,
            }
        )
        return True

    def _get_stored_file(self):
        self.ensure_one()
        check_record_access(self)
        self._source_records()
        if self.state != "done":
            raise UserError(_("Bu arka plan işi henüz indirilmeye hazır değil."))
        encoded = self.sudo().file_data
        try:
            content = base64.b64decode(encoded or b"", validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise UserError(_("Arka plan işi dosyası bozuk.")) from error
        if not content or len(content) > MAX_JOB_BYTES:
            raise UserError(_("Arka plan işi dosyası bulunamadı veya boyut sınırını aşıyor."))
        return (
            self.file_name,
            content,
            self.file_mimetype or "application/octet-stream",
        )

    def action_download(self):
        self.ensure_one()
        self._get_stored_file()
        return {
            "type": "ir.actions.client",
            "tag": "ranvals_document_studio.download_export",
            "params": {
                "url": "/ranvals_document_studio/export_jobs/%s" % self.id,
            },
        }

    def unlink(self):
        if self.filtered(lambda job: job.state == "running"):
            raise UserError(_("Çalışan bir dışa aktarım işi silinemez."))
        return super().unlink()
