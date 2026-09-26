/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { RdsLanguageButton } from "@ranvals_document_studio/js/rds_language_motion";
import { RdsReportDesignSelector } from "./report_design_selector";

RdsReportDesignSelector.components = { ...RdsReportDesignSelector.components, RdsLanguageButton };
patch(RdsReportDesignSelector.prototype, {
    setup() {
        super.setup(...arguments);
        Object.assign(this.state, { languages: [], languageCode: "", savedLanguageCode: "" });
        // Wrap only this component's ORM facade; never mutate Odoo's shared
        // ORM service. The old tested save/staleness/download flow is unchanged.
        const orm = this.orm;
        this.orm = { call: (model, method, args, kwargs) => {
            if (model === "ir.actions.report" && method === "rds_apply_design") {
                args = [...args, this.state.languageCode || false];
            }
            return orm.call(model, method, args, kwargs);
        } };
    },
    acceptOptions(result) {
        super.acceptOptions(result);
        this.state.languages = result.languages || [];
        this.state.languageCode = result.language_code || "";
        this.state.savedLanguageCode = this.state.languageCode;
    },
    get changed() {
        return super.changed || this.state.languageCode !== this.state.savedLanguageCode;
    },
    async prepareLanguageChange() {
        if (this.state.busy) return false;
        if (this.changed) {
            this.notification.add(_t("Önce bekleyen tasarım ve belge dili seçimini uygulayın."), {type:"warning"});
            return false;
        }
        const key = this.key;
        await this.props.save();
        return this.alive && key === this.key && !this.props.reportModel.isDirty;
    },
});
