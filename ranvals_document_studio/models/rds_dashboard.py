from odoo import _, api, models
from odoo.exceptions import AccessError


CONNECTOR_SPECS = (
    {
        "technical_name": "account",
        "name": "Muhasebe (Accounting)",
        "description": (
            "All-in-One paketle otomatik kurulan fatura ve iade belgesi "
            "tasarım entegrasyonu."
        ),
        "icon": "05_accounting_document_calculator.svg",
        "tone": "green",
    },
    {
        "technical_name": "sale_management",
        "name": "Satış (Sales)",
        "description": (
            "All-in-One paketle otomatik kurulan teklif ve satış siparişi "
            "tasarım entegrasyonu."
        ),
        "icon": "06_sales_cart.svg",
        "tone": "purple",
    },
    {
        "technical_name": "purchase",
        "name": "Satınalma (Purchase)",
        "description": (
            "All-in-One paketle otomatik kurulan satın alma teklifi ve "
            "siparişi tasarım entegrasyonu."
        ),
        "icon": "07_purchase_bag.svg",
        "tone": "blue",
    },
    {
        "technical_name": "web_studio",
        "name": "Studio Tasarım Seçici",
        "description": (
            "All-in-One paketle kurulan Enterprise Studio rapor paneli "
            "şablon, belge dili ve PDF / Word / PNG / ZIP seçicisi."
        ),
        "icon": "13_pdf_file.svg",
        "tone": "purple",
    },
)

OUTPUT_FORMAT_LABELS = {
    "pdf": "PDF",
    "docx_editable": "Word – Düzenlenebilir",
    "docx": "Word – PDF Görünümü",
    "png": "PNG",
    "zip": "ZIP",
}


class RdsDashboard(models.AbstractModel):
    _name = "rds.dashboard"
    _description = "Belge Studio Gösterge Ekranı"

    @api.model
    def _get_connector_specs(self):
        """Return fixed capability dependencies for the All-in-One package.

        Sales, Accounting, Purchase and Enterprise Studio are all hard
        dependencies of the single-module distribution.
        """
        return list(CONNECTOR_SPECS)

    @api.model
    def get_connector_dashboard(self):
        """Return the fixed connector allow-list without bypassing module ACLs."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Bağlayıcıları yalnız sistem yöneticileri görüntüleyebilir."))

        specs = self._get_connector_specs()
        technical_names = [item["technical_name"] for item in specs]
        modules = self.env["ir.module.module"].search(
            [("name", "in", technical_names)]
        )
        modules_by_name = {module.name: module for module in modules}
        connector_values = []

        for spec in specs:
            module = modules_by_name.get(spec["technical_name"])
            state = module.state if module else "missing"
            is_installed = state in ("installed", "to upgrade")
            # Odoo's historical field names are inverted: installed_version is
            # the code version on disk and latest_version is the DB version.
            has_update = bool(
                module
                and (
                    state == "to upgrade"
                    or (
                        state == "installed"
                        and module.installed_version
                        and module.latest_version
                        and module.installed_version != module.latest_version
                    )
                )
            )
            if has_update:
                status_key = "update"
                status_label = _("Güncelleme Bekliyor")
            elif is_installed:
                status_key = "installed"
                status_label = _("Kurulu")
            else:
                status_key = "missing"
                status_label = _("Kurulu Değil")

            connector_values.append(
                {
                    "id": module.id if module else False,
                    "technical_name": spec["technical_name"],
                    "name": _(spec["name"]),
                    "description": _(spec["description"]),
                    "icon": spec["icon"],
                    "tone": spec["tone"],
                    "status_key": status_key,
                    "status_label": status_label,
                    "installed_version": module.latest_version if module else False,
                    "available_version": module.installed_version if module else False,
                }
            )

        return {
            "connectors": connector_values,
            "stats": {
                "total": len(specs),
                "installed": sum(
                    item["status_key"] in ("installed", "update")
                    for item in connector_values
                ),
                "updates": sum(
                    item["status_key"] == "update" for item in connector_values
                ),
            },
        }

    @api.model
    def get_export_dashboard(
        self,
        query="",
        output_format=False,
        offset=0,
        limit=20,
        sort_direction="desc",
    ):
        """Return a paginated history payload under the caller's record rules."""
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(_("Dışa aktarım geçmişini görüntüleme yetkiniz yok."))

        Log = self.env["rds.export.log"]
        output_format = output_format if output_format in OUTPUT_FORMAT_LABELS else False
        try:
            offset = max(int(offset), 0)
            limit = min(max(int(limit), 1), 100)
        except (TypeError, ValueError):
            offset, limit = 0, 20
        sort_direction = "asc" if sort_direction == "asc" else "desc"

        domain = []
        if output_format:
            domain.append(("output_format", "=", output_format))
        query = (query or "").strip()[:120]
        if query:
            domain.extend(
                [
                    "|",
                    "|",
                    "|",
                    ("file_name", "ilike", query),
                    ("record_name", "ilike", query),
                    ("template_id", "ilike", query),
                    ("user_id", "ilike", query),
                ]
            )

        records = Log.search(
            domain,
            offset=offset,
            limit=limit,
            order="create_date %s, id %s" % (sort_direction, sort_direction),
        )
        stats = {"total": Log.search_count([])}
        for format_key in OUTPUT_FORMAT_LABELS:
            stats[format_key] = Log.search_count(
                [("output_format", "=", format_key)]
            )

        return {
            "stats": stats,
            "filtered_total": Log.search_count(domain),
            "records": [self._serialize_export_log(record) for record in records],
        }

    @api.model
    def _serialize_export_log(self, record):
        user_name = record.user_id.name or "-"
        name_parts = [part for part in user_name.split() if part]
        initials = "".join(part[0] for part in name_parts[:2]).upper() or "?"
        local_date = self.env["ir.qweb.field.datetime"].value_to_html(
            record.create_date,
            {"format": "d MMM yyyy HH:mm"},
        )
        has_file = bool(record.has_stored_file)
        has_source_access = record._has_source_access()
        if not has_source_access:
            status_key, status_label = "denied", _("Erişim Yok")
        elif record.attached_to_record:
            status_key, status_label = "attached", _("Ekli")
        elif has_file:
            status_key, status_label = "ready", _("Hazır")
        else:
            status_key, status_label = "missing", _("Dosya Yok")

        return {
            "id": record.id,
            "created_at": local_date,
            "user_name": user_name,
            "user_initials": initials,
            "user_tone": record.user_id.id % 4,
            "record_name": record.record_name or record.name,
            "res_model": record.res_model,
            "res_id": record.res_id,
            "template_name": record.template_id.display_name,
            "output_format": record.output_format,
            "format_label": OUTPUT_FORMAT_LABELS.get(record.output_format, "-"),
            "file_name": record.file_name,
            "file_size": record.file_size_display,
            "status_key": status_key,
            "status_label": status_label,
            "can_open_record": has_source_access,
            "can_download": has_file and has_source_access,
        }
