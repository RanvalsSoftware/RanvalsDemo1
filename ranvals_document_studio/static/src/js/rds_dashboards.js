/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { RdsSidebar } from "@ranvals_document_studio/js/rds_sidebar";

const ICON_ROOT = "/ranvals_document_studio/static/src/img/icons";
const HISTORY_PAGE_SIZE = 4;
const FORMAT_ICONS = {
    pdf: "13_pdf_file.svg",
    docx_editable: "14_word_file.svg",
    docx: "14_word_file.svg",
    png: "15_png_image.svg",
    zip: "24_zip_archive.svg",
};

class RdsDashboardComponent extends Component {
    static props = { ...standardActionServiceProps };
    static components = { RdsSidebar };

    setupServices() {
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this.ui = useService("ui");
    }

    icon(fileName) {
        return `${ICON_ROOT}/${fileName}`;
    }
}

export class RdsConnectorsDashboard extends RdsDashboardComponent {
    static template = "ranvals_document_studio.ConnectorsDashboard";

    setup() {
        this.setupServices();
        this.state = useState({
            connectors: [],
            stats: { total: 0, installed: 0, updates: 0 },
            query: "",
            filter: "all",
            filterOpen: false,
            loading: true,
            error: false,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            const data = await this.orm.call(
                "rds.dashboard",
                "get_connector_dashboard",
                []
            );
            this.state.connectors = data.connectors;
            this.state.stats = data.stats;
        } catch (error) {
            this.state.error = true;
            this.notification.add(_t("Bağlayıcı bilgileri yüklenemedi."), {
                type: "danger",
            });
        } finally {
            this.state.loading = false;
        }
    }

    get visibleConnectors() {
        const query = this.state.query.trim().toLocaleLowerCase();
        return this.state.connectors.filter((connector) => {
            const matchesFilter =
                this.state.filter === "all" || connector.status_key === this.state.filter;
            const haystack = `${connector.name} ${connector.technical_name}`.toLocaleLowerCase();
            return matchesFilter && (!query || haystack.includes(query));
        });
    }

    onSearchInput(event) {
        this.state.query = event.target.value;
    }

    toggleFilter() {
        this.state.filterOpen = !this.state.filterOpen;
    }

    selectFilter(filter) {
        this.state.filter = filter;
        this.state.filterOpen = false;
    }

    onFilterKeydown(event) {
        if (event.key === "Escape") {
            this.state.filterOpen = false;
        }
    }

    async openConnector(connector) {
        if (!connector.id) {
            this.notification.add(_t("Bağlayıcı modül uygulama listesinde bulunamadı."), {
                type: "warning",
            });
            return;
        }
        await this.actionService.doAction({
            type: "ir.actions.act_window",
            name: connector.name,
            res_model: "ir.module.module",
            res_id: connector.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openConnectorList() {
        return this.actionService.doAction(
            "ranvals_document_studio.action_rds_connectors"
        );
    }
}

export class RdsExportDashboard extends RdsDashboardComponent {
    static template = "ranvals_document_studio.ExportDashboard";

    setup() {
        this.setupServices();
        this.searchTimer = null;
        this.loadSequence = 0;
        this.state = useState({
            records: [],
            stats: { total: 0, pdf: 0, docx_editable: 0, docx: 0, png: 0, zip: 0 },
            filteredTotal: 0,
            query: "",
            format: "all",
            formatOpen: false,
            page: 0,
            sortDirection: "desc",
            loading: true,
            error: false,
        });
        onWillStart(() => this.load());
        onWillUnmount(() => clearTimeout(this.searchTimer));
    }

    async load() {
        const loadSequence = ++this.loadSequence;
        this.state.loading = true;
        this.state.error = false;
        const query = this.state.query;
        const format = this.state.format;
        const page = this.state.page;
        const sortDirection = this.state.sortDirection;
        try {
            const data = await this.orm.call(
                "rds.dashboard",
                "get_export_dashboard",
                [
                    query,
                    format === "all" ? false : format,
                    page * HISTORY_PAGE_SIZE,
                    HISTORY_PAGE_SIZE,
                    sortDirection,
                ]
            );
            if (loadSequence !== this.loadSequence) {
                return;
            }
            this.state.records = data.records;
            this.state.stats = data.stats;
            this.state.filteredTotal = data.filtered_total;
        } catch (error) {
            if (loadSequence !== this.loadSequence) {
                return;
            }
            this.state.error = true;
            this.notification.add(_t("Dışa aktarım geçmişi yüklenemedi."), {
                type: "danger",
            });
        } finally {
            if (loadSequence === this.loadSequence) {
                this.state.loading = false;
            }
        }
    }

    get pageCount() {
        return Math.max(Math.ceil(this.state.filteredTotal / HISTORY_PAGE_SIZE), 1);
    }

    get visibleDownloadIds() {
        return this.state.records.filter((record) => record.can_download).map((record) => record.id);
    }

    onSearchInput(event) {
        this.state.query = event.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => {
            this.state.page = 0;
            this.load();
        }, 250);
    }

    toggleFormatMenu() {
        this.state.formatOpen = !this.state.formatOpen;
    }

    selectFormat(format) {
        this.state.format = format;
        this.state.formatOpen = false;
        this.state.page = 0;
        return this.load();
    }

    onFormatKeydown(event) {
        if (event.key === "Escape") {
            this.state.formatOpen = false;
        }
    }

    toggleSort() {
        this.state.sortDirection = this.state.sortDirection === "desc" ? "asc" : "desc";
        this.state.page = 0;
        return this.load();
    }

    previousPage() {
        if (this.state.page > 0) {
            this.state.page -= 1;
            return this.load();
        }
    }

    nextPage() {
        if (this.state.page + 1 < this.pageCount) {
            this.state.page += 1;
            return this.load();
        }
    }

    downloadRecord(record) {
        if (!record.can_download) {
            this.notification.add(_t("Bu kayıt için indirilebilir dosya bulunamadı."), {
                type: "warning",
            });
            return;
        }
        return this.downloadIds([record.id]);
    }

    downloadVisible() {
        if (!this.visibleDownloadIds.length) {
            this.notification.add(_t("Görünen kayıtlarda indirilebilir dosya yok."), {
                type: "warning",
            });
            return;
        }
        return this.downloadIds(this.visibleDownloadIds);
    }

    async downloadIds(ids) {
        this.ui.block();
        try {
            await download({
                url: `/ranvals_document_studio/export_logs/${ids.join(",")}`,
                data: {},
            });
        } catch (error) {
            this.notification.add(_t("Dosya indirilemedi. Lütfen tekrar deneyin."), {
                type: "danger",
            });
            throw error;
        } finally {
            this.ui.unblock();
        }
    }

    openSource(record) {
        if (!record.res_model || !record.res_id) {
            return;
        }
        return this.actionService.doAction({
            type: "ir.actions.act_window",
            name: record.record_name,
            res_model: record.res_model,
            res_id: record.res_id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openHistoryList() {
        return this.actionService.doAction(
            "ranvals_document_studio.action_rds_export_log"
        );
    }

    formatIcon(outputFormat) {
        return this.icon(FORMAT_ICONS[outputFormat] || "01_app_document.svg");
    }
}

registry
    .category("actions")
    .add("ranvals_document_studio.connectors_dashboard", RdsConnectorsDashboard);
registry
    .category("actions")
    .add("ranvals_document_studio.export_dashboard", RdsExportDashboard);
