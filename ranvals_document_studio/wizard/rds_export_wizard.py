import base64
import binascii
import json
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.archive import build_zip_archive
from ..tools.common import check_record_access, safe_filename
from ..tools.docx_editable_renderer import EditableDocxError, render_editable_docx
from ..tools.docx_renderer import DocxDependencyError, render_docx
from ..tools.png_renderer import PngDependencyError, render_png_pages


MAX_EXPORT_RECORDS = 50
MAX_PNG_PAGES = 50
MAX_EXPORT_BYTES = 100 * 1024 * 1024
MAX_RES_IDS_JSON_CHARS = 4096
MAX_DATABASE_ID = 2_147_483_647
DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXPORT_MIMETYPES = {
    "application/pdf",
    DOCX_MIMETYPE,
    "application/zip",
    "image/png",
}


class RdsExportWizard(models.TransientModel):
    _name = "rds.export.wizard"
    _description = "DocuCraft Dışa Aktarım"

    res_model = fields.Char(required=True, readonly=True)
    res_ids_json = fields.Text(required=True, readonly=True, default="[]")
    record_count = fields.Integer(compute="_compute_record_count")
    available_template_ids = fields.Many2many("rds.template", compute="_compute_available_templates")
    template_id = fields.Many2one(
        "rds.template",
        required=True,
        domain="[('id', 'in', available_template_ids)]",
    )
    template_preview_html = fields.Html(compute="_compute_template_preview", sanitize=False)
    output_format = fields.Selection(
        [
            ("pdf", "PDF"),
            ("docx", "Word / DOCX (PDF görünümü)"),
            ("docx_editable", "Word / DOCX (düzenlenebilir)"),
            ("png", "PNG"),
            ("zip", "ZIP – PDF + 2 Word türü + PNG"),
        ],
        required=True,
        default="pdf",
    )
    dpi = fields.Selection(
        [("96", "96 DPI – Ekran"), ("150", "150 DPI – Standart"), ("300", "300 DPI – Baskı")],
        default="150",
        required=True,
    )
    language_id = fields.Many2one("res.lang", string="Belge Dili", domain="[('active', '=', True)]")
    attach_to_record = fields.Boolean(string="Kayda Ekle", default=False)
    file_name_prefix = fields.Char(string="Dosya Adı Öneki")
    file_data = fields.Binary(
        readonly=True,
        attachment=False,
        groups="base.group_system",
    )
    file_name = fields.Char(readonly=True)
    file_mimetype = fields.Char(readonly=True)

    @api.model
    def _parse_record_ids(self, value, silent=False):
        def invalid(message):
            if silent:
                return []
            raise UserError(message)

        if not isinstance(value, str) or len(value) > MAX_RES_IDS_JSON_CHARS:
            return invalid(_("Kayıt listesi okunamadı."))
        try:
            payload = json.loads(value or "[]")
        except (TypeError, ValueError):
            return invalid(_("Kayıt listesi okunamadı."))
        if not isinstance(payload, list):
            return invalid(_("Kayıt listesi bir JSON listesi olmalıdır."))
        if len(payload) > MAX_EXPORT_RECORDS:
            return invalid(
                _("Tek işlemde en fazla %s kayıt dışa aktarılabilir.")
                % MAX_EXPORT_RECORDS
            )
        ids = []
        seen = set()
        for record_id in payload:
            if (
                isinstance(record_id, bool)
                or not isinstance(record_id, int)
                or record_id <= 0
                or record_id > MAX_DATABASE_ID
            ):
                return invalid(_("Kayıt listesinde geçersiz bir kayıt kimliği var."))
            if record_id not in seen:
                ids.append(record_id)
                seen.add(record_id)
        return ids

    @api.model
    def _source_model(self, model_name, silent=False):
        if not isinstance(model_name, str) or model_name not in self.env.registry.models:
            if silent:
                return False
            raise UserError(_("Geçersiz kaynak model."))
        source_model = self.env[model_name]
        if (
            getattr(source_model, "_abstract", False)
            or getattr(source_model, "_transient", False)
            or not getattr(source_model, "_auto", False)
        ):
            if silent:
                return False
            raise UserError(_("Geçersiz kaynak model."))
        return source_model

    @api.constrains("res_model", "res_ids_json")
    def _check_source_reference(self):
        for wizard in self:
            if wizard._source_model(wizard.res_model, silent=True) is False:
                raise ValidationError(_("Geçersiz kaynak model."))
            if wizard._parse_record_ids(wizard.res_ids_json, silent=True) == []:
                try:
                    payload = json.loads(wizard.res_ids_json or "[]")
                except (TypeError, ValueError):
                    payload = None
                if payload != []:
                    raise ValidationError(_("Kayıt listesi geçersiz veya izin verilen sınırı aşıyor."))

    @api.constrains("file_data", "file_name", "file_mimetype")
    def _check_download_payload(self):
        max_encoded_size = ((MAX_EXPORT_BYTES + 2) // 3) * 4 + 4
        for wizard in self.filtered("file_data"):
            encoded = wizard.file_data
            if not isinstance(encoded, (bytes, bytearray, str)) or len(encoded) > max_encoded_size:
                raise ValidationError(_("Dışa aktarım dosyası 100 MB sınırını aşıyor."))
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, TypeError, ValueError) as error:
                raise ValidationError(_("Dışa aktarım dosyası geçerli Base64 verisi değil.")) from error
            if len(decoded) > MAX_EXPORT_BYTES:
                raise ValidationError(_("Dışa aktarım dosyası 100 MB sınırını aşıyor."))
            if not wizard.file_name or len(wizard.file_name) > 255:
                raise ValidationError(_("Dışa aktarım dosya adı geçersiz."))
            if wizard.file_mimetype not in EXPORT_MIMETYPES:
                raise ValidationError(_("Dışa aktarım dosya türü geçersiz."))

    def write(self, vals):
        """Keep a rendered blob bound to the source records it came from."""
        protected_source_fields = {"res_model", "res_ids_json"}
        if protected_source_fields.intersection(vals):
            check_record_access(self, "write")
            if self.sudo().filtered("file_data"):
                raise ValidationError(
                    _(
                        "Çıktısı oluşturulmuş bir işlemde kaynak kayıtlar değiştirilemez. "
                        "Yeni bir dışa aktarım penceresi açın."
                    )
                )
        return super().write(vals)

    @api.model
    def open_for_records(self, records):
        if not records:
            raise UserError(_("Belge oluşturmak için en az bir kayıt seçin."))
        if len(records) > MAX_EXPORT_RECORDS:
            raise UserError(
                _("Tek işlemde en fazla %s kayıt dışa aktarılabilir.")
                % MAX_EXPORT_RECORDS
            )
        check_record_access(records)
        return {
            "type": "ir.actions.act_window",
            "name": _("DocuCraft"),
            "res_model": "rds.export.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": records._name,
                "default_res_ids_json": json.dumps(records.ids),
                "active_model": records._name,
                "active_ids": records.ids,
            },
        }

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        model_name = values.get("res_model") or self.env.context.get("active_model")
        context_ids = self.env.context.get("active_ids") or (
            [self.env.context.get("active_id")] if self.env.context.get("active_id") else []
        )
        if model_name and not values.get("res_model"):
            values["res_model"] = model_name
        if context_ids and not values.get("res_ids_json"):
            values["res_ids_json"] = json.dumps(context_ids)
        ids = self._parse_record_ids(values.get("res_ids_json") or "[]", silent=True)
        source_model = self._source_model(model_name, silent=True) if model_name else False
        if source_model is not False and ids:
            records = source_model.browse(ids).exists()
            check_record_access(records)
            company = getattr(records[:1], "company_id", False) or self.env.company
            template = self.env["rds.template"].get_default_for(
                model_name,
                company=company,
                # For a batch, choosing a rule from only the first document
                # could silently apply the wrong customer/amount rule to the
                # remaining records.  Single-record flows are unambiguous.
                record=records if len(records) == 1 else None,
            )
            if template:
                values.setdefault("template_id", template.id)
            partner = getattr(records[:1], "partner_id", False)
            lang_code = (partner.lang if partner else self.env.user.lang) or self.env.user.lang
            language = self.env["res.lang"].search([("code", "=", lang_code), ("active", "=", True)], limit=1)
            if language:
                values.setdefault("language_id", language.id)
        return values

    @api.depends("res_ids_json")
    def _compute_record_count(self):
        for wizard in self:
            wizard.record_count = len(
                wizard._parse_record_ids(wizard.res_ids_json or "[]", silent=True)
            )

    @api.depends("res_model", "res_ids_json")
    def _compute_available_templates(self):
        Template = self.env["rds.template"]
        for wizard in self:
            if not wizard.res_model or wizard.res_model not in self.env.registry.models:
                wizard.available_template_ids = Template.browse()
                continue
            model_record = self.env["ir.model"]._get(wizard.res_model)
            records = wizard._get_records(silent=True)
            company = (
                getattr(records[:1], "company_id", False) or self.env.company
                if records
                else self.env.company
            )
            domain = [
                ("active", "=", True),
                "|",
                ("target_model_id", "=", False),
                ("target_model_id", "=", model_record.id),
                "|",
                ("company_id", "=", False),
                ("company_id", "=", company.id),
            ]
            wizard.available_template_ids = Template.search(domain)

    @api.depends("template_id", "template_id.preview_html")
    def _compute_template_preview(self):
        for wizard in self:
            wizard.template_preview_html = wizard.template_id.preview_html if wizard.template_id else False

    @api.onchange("available_template_ids")
    def _onchange_available_templates(self):
        for wizard in self:
            if wizard.template_id not in wizard.available_template_ids:
                wizard.template_id = wizard.available_template_ids[:1]

    def _get_records(self, silent=False):
        self.ensure_one()
        source_model = self._source_model(self.res_model, silent=silent)
        if source_model is False:
            return self.env["res.users"].browse()
        ids = self._parse_record_ids(self.res_ids_json or "[]", silent=silent)
        records = source_model.browse(ids).exists()
        if len(records) != len(ids):
            if silent:
                return source_model.browse()
            raise UserError(
                _("Seçilen belgelerden biri artık mevcut değil; listeyi yenileyip tekrar deneyin.")
            )
        if not records and not silent:
            raise UserError(_("Dışa aktarılacak kayıt bulunamadı."))
        check_record_access(records)
        return records

    def _render_pdf(self, record, language_code):
        check_record_access(record)
        if not hasattr(record, "_rds_report_action_xmlid"):
            raise UserError(_("%s modeli için DocuCraft bağlayıcısı kurulmamış.") % record._name)
        # Sales provides a separate QWeb root for every studio-editable
        # design.  Passing the selected template lets PDF and PNG use the
        # same report users edit in Odoo Studio; other connectors retain
        # their generic report action.
        report_xmlid = record._rds_report_action_xmlid(self.template_id)
        if not isinstance(report_xmlid, str) or not re.fullmatch(
            r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", report_xmlid
        ):
            raise UserError(_("Belge rapor aksiyonu geçersiz."))
        report = self.env.ref(report_xmlid, raise_if_not_found=False)
        if not report or report._name != "ir.actions.report":
            raise UserError(_("Belge rapor aksiyonu bulunamadı: %s") % report_xmlid)
        # Odoo intentionally resolves report actions with sudo: standard
        # internal users do not have direct read ACLs on ir.actions.report.
        # The trusted connector supplies an XMLID; validate all security-
        # relevant metadata on that exact technical action before rendering.
        report = report.sudo()
        if (
            not self.env.su
            and report.group_ids
            and set(report.group_ids.ids).isdisjoint(self.env.user.all_group_ids.ids)
        ):
            raise AccessError(_("Bu belge raporunu oluşturma yetkiniz bulunmuyor."))
        if report.model != record._name or report.report_type != "qweb-pdf":
            raise UserError(_("Belge rapor aksiyonu kaynak model veya PDF türüyle eşleşmiyor."))
        data = {
            "rds_template_id": self.template_id.id,
            "model_name": record._name,
            "lang": language_code,
        }
        report_service = self.env["ir.actions.report"].with_context(lang=language_code)
        pdf_content, output_type = report_service._render_qweb_pdf(
            # Pass the validated action itself.  Resolving by ``report_name``
            # would select the first matching action with sudo in Odoo core,
            # which could differ when duplicate technical names exist.
            report,
            res_ids=[record.id],
            data=data,
        )
        if (
            output_type != "pdf"
            or not isinstance(pdf_content, (bytes, bytearray))
            or not pdf_content.startswith(b"%PDF")
        ):
            raise UserError(_("PDF motoru geçerli bir PDF çıktısı döndürmedi."))
        pdf_content = bytes(pdf_content)
        self._ensure_output_size(pdf_content)
        return pdf_content

    def _record_context(self, record, language_code):
        if not hasattr(record, "_rds_document_context"):
            raise UserError(_("%s modeli için DocuCraft bağlayıcısı kurulmamış.") % record._name)
        return record.with_context(lang=language_code)._rds_document_context(self.template_id, language_code)

    def _ensure_output_size(self, content, current_size=0):
        if isinstance(content, int):
            content_size = content
        elif isinstance(content, (bytes, bytearray)):
            content_size = len(content)
        else:
            raise UserError(_("Belge motoru geçersiz dosya verisi döndürdü."))
        if content_size < 0 or current_size + content_size > MAX_EXPORT_BYTES:
            raise UserError(
                _(
                    "Oluşturulan çıktı 100 MB sınırını aşıyor. Daha az kayıt, "
                    "daha düşük PNG çözünürlüğü veya daha küçük görseller kullanın."
                )
            )

    def _append_output(self, outputs, file_name, content, mimetype):
        current_size = sum(len(item[1]) for item in outputs)
        self._ensure_output_size(content, current_size=current_size)
        outputs.append((file_name, bytes(content), mimetype))

    def _export_record(self, record, language_code):
        prefix = safe_filename(self.file_name_prefix) if self.file_name_prefix else ""
        record_name = safe_filename(getattr(record, "display_name", False) or getattr(record, "name", False) or str(record.id))
        template_code = safe_filename(self.template_id.code)
        # Preserve the record ID in the suffix.  Truncating the fully joined
        # name could otherwise remove the only unique part when a prefix or
        # display name is long, making multi-record ZIP members collide.
        identity_suffix = safe_filename("ID%s_%s" % (record.id, template_code))
        descriptive = safe_filename(
            "_".join(part for part in (prefix, record_name) if part)
        )
        descriptive_limit = max(1, 120 - len(identity_suffix) - 1)
        descriptive = descriptive[:descriptive_limit].rstrip("._-")
        base_name = "%s_%s" % (descriptive, identity_suffix) if descriptive else identity_suffix

        requested_formats = (
            ("pdf", "docx", "docx_editable", "png")
            if self.output_format == "zip"
            else (self.output_format,)
        )
        outputs = []
        pdf_content = None

        for output_format in requested_formats:
            if output_format == "pdf":
                pdf_content = pdf_content or self._render_pdf(record, language_code)
                self._append_output(
                    outputs,
                    "%s.pdf" % base_name,
                    pdf_content,
                    "application/pdf",
                )
                continue

            if output_format == "docx":
                pdf_content = pdf_content or self._render_pdf(record, language_code)
                try:
                    content = render_docx(pdf_content, max_pages=MAX_PNG_PAGES)
                except DocxDependencyError as exc:
                    raise UserError(str(exc)) from exc
                self._append_output(
                    outputs,
                    "%s_Word_PDF_Gorunumu.docx" % base_name,
                    content,
                    DOCX_MIMETYPE,
                )
                continue

            if output_format == "docx_editable":
                # The native Word path intentionally consumes the already
                # access-controlled connector context and never reads through
                # sudo or rasterizes a PDF page.
                document_context = self._record_context(record, language_code)
                try:
                    content = render_editable_docx(
                        document_context,
                        language_code=language_code,
                    )
                except EditableDocxError as exc:
                    raise UserError(str(exc)) from exc
                self._append_output(
                    outputs,
                    "%s_Word_Duzenlenebilir.docx" % base_name,
                    content,
                    DOCX_MIMETYPE,
                )
                continue

            if output_format == "png":
                pdf_content = pdf_content or self._render_pdf(record, language_code)
                try:
                    pages = render_png_pages(
                        pdf_content,
                        dpi=int(self.dpi),
                        max_pages=MAX_PNG_PAGES,
                        max_output_bytes=MAX_EXPORT_BYTES,
                    )
                except PngDependencyError as exc:
                    raise UserError(str(exc)) from exc
                if not pages:
                    raise UserError(_("PDF içinde PNG'ye dönüştürülebilecek sayfa bulunamadı."))
                for index, page in enumerate(pages):
                    self._append_output(
                        outputs,
                        "%s_page_%02d.png" % (base_name, index + 1),
                        page,
                        "image/png",
                    )
                continue

            raise UserError(_("Desteklenmeyen çıktı formatı."))

        return outputs

    def _build_output(self):
        self.ensure_one()
        records = self._get_records()
        record_companies = (
            records.mapped("company_id")
            if "company_id" in records._fields
            else self.env["res.company"]
        )
        if len(record_companies) > 1:
            raise ValidationError(
                _(
                    "Tek dışa aktarımda aynı şirkete ait belgeleri seçin. "
                    "Farklı şirketleri ayrı işlemlerde dışa aktarabilirsiniz."
                )
            )
        render_company = record_companies or self.env.company
        if not self.env.su and render_company not in self.env.companies:
            raise AccessError(_("Kaynak belgelerin şirketine erişim izniniz bulunmuyor."))
        if render_company != self.env.company:
            return self.with_company(render_company)._build_output()
        check_record_access(self.template_id)
        if self.template_id not in self.available_template_ids:
            raise ValidationError(_("Seçilen şablon bu model veya şirket için kullanılamaz."))
        if self.template_id.target_model_id and self.template_id.target_model_id.model != self.res_model:
            raise ValidationError(_("Seçilen şablon bu kayıt modeli için tanımlı değildir."))
        if self.template_id.company_id and "company_id" not in records._fields:
            raise ValidationError(
                _("Şirkete özel şablon şirket alanı olmayan bir modelde kullanılamaz.")
            )
        if self.template_id.company_id:
            foreign_records = records.filtered(lambda item: item.company_id != self.template_id.company_id)
            if foreign_records:
                raise ValidationError(_("Şirkete özel şablon yalnız kendi şirketinin kayıtlarında kullanılabilir."))
        if self.language_id and not self.language_id.active:
            raise ValidationError(_("Seçilen belge dili etkin değil."))
        language_code = self.language_id.code if self.language_id else self.env.user.lang or "tr_TR"
        outputs = []
        record_map = []
        total_output_size = 0
        for record in records:
            record_outputs = self._export_record(record, language_code)
            record_size = sum(len(content) for _name, content, _mimetype in record_outputs)
            self._ensure_output_size(record_size, current_size=total_output_size)
            total_output_size += record_size
            outputs.extend(record_outputs)
            record_map.append((record, record_outputs))

        if len(outputs) == 1:
            file_name, content, mimetype = outputs[0]
            return file_name, content, mimetype, record_map

        zip_content = build_zip_archive(
            (file_name, content) for file_name, content, _mimetype in outputs
        )
        self._ensure_output_size(zip_content)
        if self.file_name_prefix:
            prefix = safe_filename(self.file_name_prefix)
        elif self.output_format == "zip" and len(records) == 1:
            prefix = safe_filename(records.display_name or str(records.id))
        else:
            prefix = "DocuCraft"
        return "%s.zip" % prefix, zip_content, "application/zip", record_map

    def _save_result(self, download=True):
        self.ensure_one()
        # Authenticate ownership/write access before the narrow elevation used
        # solely for the system-only raw blob field below.
        check_record_access(self, "write")
        file_name, content, mimetype, record_map = self._build_output()
        if len(content) > MAX_EXPORT_BYTES:
            raise UserError(
                _(
                    "Oluşturulan çıktı 100 MB sınırını aşıyor. Daha az kayıt, "
                    "daha düşük PNG çözünürlüğü veya daha küçük görseller kullanın."
                )
            )
        # The raw transient blob is hidden from generic ORM and /web/content
        # reads.  Controlled HTTP routes first check this wizard and its live
        # source records, then read this one field with a narrow elevation.
        self.sudo().write(
            {
                "file_data": base64.b64encode(content),
                "file_name": file_name,
                "file_mimetype": mimetype,
            }
        )
        # A browser preview is temporary and must not create a permanent
        # history blob every time the user refreshes it.
        if not download:
            record_map = []
        for record, record_outputs in record_map:
            record_file_name, record_content, record_mimetype = record_outputs[0]
            if len(record_outputs) > 1:
                record_content = build_zip_archive(
                    (output_name, output_content)
                    for output_name, output_content, _output_mimetype in record_outputs
                )
                record_file_name = "%s.zip" % safe_filename(record.display_name or str(record.id))
                record_mimetype = "application/zip"

            attachment = self.env["ir.attachment"]
            if self.attach_to_record:
                attachment = self.env["ir.attachment"].create(
                    {
                        "name": record_file_name,
                        "datas": base64.b64encode(record_content),
                        "mimetype": record_mimetype,
                        "res_model": record._name,
                        "res_id": record.id,
                    }
                )

            # Internal users cannot manufacture arbitrary history blobs over
            # RPC.  This narrow sudo is fed only by the renderer above and the
            # real caller/company are always written explicitly.
            self.env["rds.export.log"].sudo().create(
                {
                    "name": _("%s dışa aktarımı") % record.display_name,
                    "user_id": self.env.user.id,
                    "template_id": self.template_id.id,
                    "res_model": record._name,
                    "res_id": record.id,
                    "record_name": record.display_name,
                    "output_format": self.output_format,
                    "company_id": (record.company_id.id if "company_id" in record._fields and record.company_id else self.env.company.id),
                    "file_name": record_file_name,
                    # Keep one access-controlled copy for the history screen,
                    # even when the optional business-record attachment is off.
                    "file_size": len(record_content),
                    "file_mimetype": record_mimetype,
                    # The optional business-record attachment is already an
                    # access-controlled durable copy.  Store a history copy
                    # only when that attachment was not requested.
                    "file_data": (
                        False
                        if attachment
                        else base64.b64encode(record_content)
                    ),
                    "attachment_id": attachment.id if attachment else False,
                }
            )
        preview_url = "/ranvals_document_studio/export_wizard/%s/preview" % self.id
        if download:
            # Odoo 19 implements ``ir.actions.act_url`` target ``download``
            # with ``window.open``.  Since report rendering completes after an
            # RPC round-trip, browsers commonly reject that call as a popup.
            # Our client action uses Odoo's XHR/Blob download helper instead.
            return {
                "type": "ir.actions.client",
                "tag": "ranvals_document_studio.download_export",
                "params": {
                    "url": "/ranvals_document_studio/export_wizard/%s" % self.id,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "ranvals_document_studio.preview_export",
            "params": {
                "url": preview_url,
                "title": _("%s Önizleme") % file_name,
            },
        }

    def action_export(self):
        return self._save_result(download=True)

    def action_preview(self):
        self.ensure_one()
        if self.output_format != "pdf":
            raise UserError(_("Tarayıcı önizlemesi yalnız PDF formatında kullanılabilir."))
        if self.record_count != 1:
            raise UserError(_("Önizleme için tek bir kayıt seçin."))
        return self._save_result(download=False)
