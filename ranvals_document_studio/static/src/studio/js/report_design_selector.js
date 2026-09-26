/** @odoo-module **/
import { Component, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { ReportEditorWysiwyg } from "@web_studio/client_action/report_editor/report_editor_wysiwyg/report_editor_wysiwyg";
import { runSelectorOperation } from "./selector_flow";

const SUPPORTED_REPORT_MODELS = new Set(["sale.order", "account.move", "purchase.order"]);
const MAX_ODOO_ID = 2147483647;

function positiveInteger(value) {
    if (typeof value === "string" && !/^\d+$/.test(value)) {
        return false;
    }
    const parsed = typeof value === "string" ? Number(value) : value;
    return Number.isSafeInteger(parsed) && parsed > 0 && parsed <= MAX_ODOO_ID ? parsed : false;
}

export class RdsReportDesignSelector extends Component {
    static template = "ranvals_document_studio.ReportDesignSelector";
    static props = { reportModel: Object, save: Function };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ ready: false, supported: false, canManage: false, busy: false,
            templateId: "", savedId: "", templates: [], company: "", error: "",
            hasSavedConfiguration: false });
        this.generation = 0;
        this.alive = true;
        onWillUnmount(() => { this.alive = false; this.generation++; });
        useEffect(() => {
            this.load();
        }, () => [this.key]);
    }

    get key() {
        const companies = JSON.stringify(this.callContext.allowed_company_ids || []);
        return `${this.reportId || 0}:${this.recordId || 0}:${companies}`;
    }

    // Do not silently hide the integration while options are loading or a
    // request fails. Unsupported report models keep the native sidebar only.
    get visible() {
        return SUPPORTED_REPORT_MODELS.has(this.props.reportModel.reportData?.model) || this.state.supported;
    }

    get hasTemplates() {
        return this.state.templates.length > 0;
    }

    get canApply() {
        return this.state.ready && this.state.supported && this.state.canManage &&
            !this.state.busy && this.hasRecord &&
            (this.hasTemplates || !!this.state.savedId || this.state.hasSavedConfiguration);
    }

    get selectedName() {
        const item = this.state.templates.find((template) => String(template.id) === this.state.templateId);
        return item?.name || _t("Mevcut / varsayılan tasarım");
    }

    get changed() {
        return this.state.templateId !== this.state.savedId;
    }

    get hasRecord() {
        return !!this.recordId;
    }

    get reportId() {
        return positiveInteger(this.props.reportModel.editedReportId);
    }

    get recordId() {
        return positiveInteger(this.props.reportModel.recordToDisplay);
    }

    get callContext() {
        return { ...user.context, ...this.props.reportModel.routesContext };
    }

    acceptOptions(result) {
        this.state.supported = !!result.supported;
        this.state.canManage = !!result.can_manage;
        this.state.templates = result.templates || [];
        this.state.company = result.company_name || "";
        this.state.hasSavedConfiguration = !!result.has_saved_configuration;
        this.state.templateId = result.template_id ? String(result.template_id) : "";
        this.state.savedId = this.state.templateId;
        this.state.error = "";
    }

    async load() {
        const generation = ++this.generation;
        const key = this.key;
        this.state.ready = false;
        this.state.error = "";
        if (!this.reportId) {
            this.acceptOptions({ supported: false });
            this.state.ready = true;
            return;
        }
        try {
            const result = await this.orm.call("ir.actions.report", "rds_get_design_options",
                [[this.reportId], this.recordId],
                { context: this.callContext });
            if (this.alive && generation === this.generation && key === this.key) {
                this.acceptOptions(result);
                this.state.ready = true;
            }
        } catch (error) {
            if (this.alive && generation === this.generation && key === this.key) {
                this.state.error = error.data?.message || error.message || _t("Tasarım seçenekleri yüklenemedi.");
                this.state.ready = true;
            }
        }
    }

    async apply() {
        if (!this.canApply) return;
        const model = this.props.reportModel;
        const key = this.key;
        const recordId = this.recordId;
        const reportId = this.reportId;
        const templateId = this.state.templateId ? Number(this.state.templateId) : false;
        const context = this.callContext;
        if (!reportId || !recordId || !this.state.canManage) return;
        this.state.busy = true;
        try {
            const result = await runSelectorOperation({
                save: () => this.props.save(),
                isDirty: () => !!model.isDirty,
                isCurrent: () => this.alive && key === this.key,
                perform: async () => {
                    const saved = await this.orm.call("ir.actions.report", "rds_apply_design",
                        [[reportId], recordId, templateId], { context });
                    if (this.alive && key === this.key) this.acceptOptions(saved);
                },
                reload: async () => {
                    // Supplying resId bypasses Studio's cached HTML. The native
                    // editor/save/reset source remains untouched.
                    await model.loadReportHtml({ resId: recordId || 0 });
                    model.inPreview = true;
                },
                isFailed: () => !!model._errorMessage,
            });
            if (!result.ok) {
                if (result.reason === "unsaved") {
                    this.notification.add(_t("Rapor kaydedilemedi. Önce Studio hatasını düzeltin; tasarım değiştirilmedi."), { type: "warning" });
                } else if (result.reason === "preview") {
                    this.notification.add(_t("Tasarım kaydedildi ancak önizleme oluşturulamadı. Studio hata ayrıntılarını kontrol edin."), { type: "warning" });
                }
                return;
            }
            this.notification.add(_t("Bu rapor ve şirket için tasarım kaydedildi."), { type: "success" });
        } catch (error) {
            if (this.alive) this.notification.add(error.data?.message || error.message || _t("İşlem tamamlanamadı."), { type: "danger" });
        } finally {
            if (this.alive) this.state.busy = false;
        }
    }
}

ReportEditorWysiwyg.components = { ...ReportEditorWysiwyg.components, RdsReportDesignSelector };
