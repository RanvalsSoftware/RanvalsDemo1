import importlib
import importlib.metadata
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from odoo import _, api, fields, models, release
from odoo.tools import config, file_path


_logger = logging.getLogger(__name__)

DEPENDENCIES = (
    ("python-docx", "docx"),
    ("pypdfium2", "pypdfium2"),
    ("Pillow", "PIL"),
)
REPORT_XMLIDS = (
    "ranvals_document_studio.action_report_rds_sale_document",
    "ranvals_document_studio.action_report_rds_sale_beauty_document",
    "ranvals_document_studio.action_report_rds_sale_construction_document",
    "ranvals_document_studio.action_report_rds_sale_technology_document",
    "ranvals_document_studio.action_report_rds_sale_industrial_document",
    "ranvals_document_studio.action_report_rds_sale_eco_document",
    "ranvals_document_studio.action_report_rds_sale_furniture_document",
    "ranvals_document_studio.action_report_rds_invoice_document",
    "ranvals_document_studio.action_report_rds_purchase_document",
)


class RdsSystemHealth(models.TransientModel):
    _name = "rds.system.health"
    _description = "DocuCraft Sistem Sağlığı"

    checked_at = fields.Datetime(readonly=True)
    line_ids = fields.One2many("rds.system.health.line", "health_id", readonly=True)
    ok_count = fields.Integer(compute="_compute_counts")
    warning_count = fields.Integer(compute="_compute_counts")
    error_count = fields.Integer(compute="_compute_counts")

    @api.depends("line_ids.status")
    def _compute_counts(self):
        for health in self:
            health.ok_count = len(health.line_ids.filtered(lambda line: line.status == "ok"))
            health.warning_count = len(health.line_ids.filtered(lambda line: line.status == "warning"))
            health.error_count = len(health.line_ids.filtered(lambda line: line.status == "error"))

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        if "checked_at" in field_names:
            values["checked_at"] = fields.Datetime.now()
        if "line_ids" in field_names:
            values["line_ids"] = [(0, 0, item) for item in self._collect_checks()]
        return values

    @api.model
    def _check(self, category, name, status, detail):
        return {"category": category, "name": name, "status": status, "detail": detail}

    @api.model
    def _find_wkhtmltopdf(self):
        candidates = []
        bin_path = config.get("bin_path")
        if isinstance(bin_path, str) and bin_path:
            candidates.append(Path(bin_path) / ("wkhtmltopdf.exe" if os.name == "nt" else "wkhtmltopdf"))
        discovered = shutil.which("wkhtmltopdf")
        if discovered:
            candidates.append(Path(discovered))
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved.name.lower() not in ("wkhtmltopdf", "wkhtmltopdf.exe") or not resolved.is_file():
                continue
            return resolved
        return False

    @api.model
    def _collect_checks(self):
        checks = [
            self._check("runtime", _("Odoo"), "ok", release.version),
            self._check("runtime", _("Python"), "ok", platform.python_version()),
        ]
        for distribution, module_name in DEPENDENCIES:
            try:
                importlib.import_module(module_name)
                try:
                    version = importlib.metadata.version(distribution)
                except importlib.metadata.PackageNotFoundError:
                    version = _("sürüm bilgisi yok")
                checks.append(self._check("dependency", distribution, "ok", str(version)))
            except Exception as error:
                # A broken native wheel may raise RuntimeError/ValueError (not
                # only ImportError).  Health diagnostics must report it rather
                # than make the entire screen unavailable.
                _logger.warning(
                    "DocuCraft dependency health check failed for %s",
                    distribution,
                    exc_info=True,
                )
                checks.append(self._check("dependency", distribution, "error", _("Yüklenemedi: %s") % type(error).__name__))

        executable = self._find_wkhtmltopdf()
        if executable:
            detail = str(executable)
            try:
                process_env = os.environ.copy()
                process_env["PATH"] = str(executable.parent) + os.pathsep + process_env.get("PATH", "")
                result = subprocess.run(
                    [str(executable), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    env=process_env,
                )
                version_line = (result.stdout or result.stderr or "").strip().splitlines()
                if version_line:
                    detail = version_line[0][:300]
                status = "ok" if result.returncode == 0 else "warning"
            except (OSError, subprocess.SubprocessError):
                status = "warning"
            checks.append(self._check("render", "wkhtmltopdf", status, detail))
        else:
            checks.append(self._check("render", "wkhtmltopdf", "error", _("Çalıştırılabilir dosya bulunamadı.")))

        font_roots = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
        available_roots = [path for path in font_roots if path.is_dir()]
        font_count = 0
        for root in available_roots:
            try:
                font_count += sum(1 for path in root.rglob("*") if path.suffix.lower() in (".ttf", ".otf"))
            except OSError:
                continue
        checks.append(self._check(
            "render", _("Sistem fontları"), "ok" if font_count else "warning",
            _("%s adet TTF/OTF font bulundu.") % font_count if font_count else _("TTF/OTF font dizini bulunamadı."),
        ))

        paper = self.env.ref("ranvals_document_studio.paperformat_rds_a4", raise_if_not_found=False)
        checks.append(self._check(
            "configuration", _("A4 kâğıt biçimi"),
            "ok" if paper and paper._name == "report.paperformat" else "error",
            paper.display_name if paper else _("XML kimliği bulunamadı."),
        ))
        missing_reports = [xmlid for xmlid in REPORT_XMLIDS if not self.env.ref(xmlid, raise_if_not_found=False)]
        checks.append(self._check(
            "configuration", _("Rapor bağlantıları"), "error" if missing_reports else "ok",
            _("Eksik: %s") % ", ".join(missing_reports) if missing_reports else _("%s rapor aksiyonu doğrulandı.") % len(REPORT_XMLIDS),
        ))

        studio = self.env["ir.module.module"].sudo().search([("name", "=", "web_studio")], limit=1)
        try:
            studio_asset = file_path(
                "ranvals_document_studio/static/src/studio/js/report_design_selector.js"
            )
        except FileNotFoundError:
            studio_asset = False
        studio_ok = studio.state == "installed" and bool(studio_asset and Path(studio_asset).is_file())
        checks.append(self._check(
            "configuration", _("Enterprise Studio varlıkları"), "ok" if studio_ok else "error",
            _("web_studio kurulu ve DocuCraft Studio dosyaları mevcut.") if studio_ok else _("web_studio veya Studio varlık dosyası eksik."),
        ))

        cron = self.env.ref("ranvals_document_studio.ir_cron_rds_export_retention", raise_if_not_found=False)
        checks.append(self._check(
            "maintenance", _("Saklama politikası görevi"),
            "ok" if cron and cron.active else "warning",
            _("Etkin") if cron and cron.active else _("Bulunamadı veya devre dışı."),
        ))
        export_cron = self.env.ref(
            "ranvals_document_studio.ir_cron_rds_export_jobs",
            raise_if_not_found=False,
        )
        checks.append(self._check(
            "maintenance", _("Arka plan dışa aktarım görevi"),
            "ok" if export_cron and export_cron.active else "error",
            _("Etkin") if export_cron and export_cron.active else _("Bulunamadı veya devre dışı."),
        ))
        return checks

    def action_refresh(self):
        self.ensure_one()
        self.write({
            "checked_at": fields.Datetime.now(),
            "line_ids": [(5, 0, 0)] + [(0, 0, item) for item in self._collect_checks()],
        })
        return {"type": "ir.actions.client", "tag": "reload"}


class RdsSystemHealthLine(models.TransientModel):
    _name = "rds.system.health.line"
    _description = "DocuCraft Sistem Sağlığı Satırı"
    _order = "category, id"

    health_id = fields.Many2one("rds.system.health", required=True, ondelete="cascade")
    category = fields.Selection(
        [("runtime", "Çalışma Ortamı"), ("dependency", "Bağımlılık"), ("render", "Render"),
         ("configuration", "Yapılandırma"), ("maintenance", "Bakım")], required=True
    )
    name = fields.Char(required=True)
    status = fields.Selection([("ok", "Hazır"), ("warning", "Uyarı"), ("error", "Hata")], required=True)
    detail = fields.Char(readonly=True)
