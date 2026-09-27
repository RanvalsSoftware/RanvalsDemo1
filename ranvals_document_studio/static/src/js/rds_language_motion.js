/** @odoo-module **/
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { clearUncommittedChanges } from "@web/webclient/actions/action_service";
import { patch } from "@web/core/utils/patch";
import { RdsSidebar } from "./rds_sidebar";
import { RdsListController } from "./rds_sidebar_views";
import { animateRdsContent } from "./rds_motion";

export class RdsLanguageButton extends Component {
    static template = "ranvals_document_studio.LanguageButton";
    static props = { beforeOpen: { type: Function, optional: true } };
    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ busy: false });
        this.alive = true;
        onWillUnmount(() => { this.alive = false; });
    }
    get code() { return (user.context.lang || "en_US").split("_")[0].toUpperCase(); }
    async open() {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            if (this.props.beforeOpen && await this.props.beforeOpen() === false) return;
            // Let native forms save/discard/cancel before a locale reload is possible.
            if (!this.alive || !await clearUncommittedChanges(this.env)) return;
            if (!this.alive) return;
            await this.action.doAction("ranvals_document_studio.action_rds_language_wizard");
        } catch (error) {
            if (this.alive) this.notification.add(error.data?.message || error.message || _t("Could not open language settings."), { type: "danger" });
        } finally { if (this.alive) this.state.busy = false; }
    }
}
RdsSidebar.components = { ...RdsSidebar.components, RdsLanguageButton };
patch(RdsSidebar.prototype, {
    get rdsCollapseTitle() { return this.isCollapsed ? _t("Expand menu") : _t("Collapse menu"); },
    setup() {
        super.setup(...arguments);
        let stopAnimation = () => {};
        onMounted(() => {
            const shell = this.panelRef.el?.closest(".o_rds_sidebar_layout, .o_rds_sidebar_view");
            stopAnimation = animateRdsContent(shell);
        });
        onWillUnmount(() => stopAnimation());
    },
});

patch(RdsListController.prototype, {
    get rdsListHero() {
        const hero = super.rdsListHero;
        return hero ? { ...hero, title: _t(hero.title), subtitle: _t(hero.subtitle) } : false;
    },
});
