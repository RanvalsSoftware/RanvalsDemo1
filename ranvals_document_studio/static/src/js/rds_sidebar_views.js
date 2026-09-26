/** @odoo-module **/

import { onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { RdsSidebar } from "@ranvals_document_studio/js/rds_sidebar";

const RDS_ACTIVE_MENU_KEY_BY_MODEL = Object.freeze({
    "rds.template": "templates",
    "account.move": "invoices",
    "rds.export.log": "history",
});

const RDS_LIST_HERO_BY_MODEL = Object.freeze({
    "rds.template": Object.freeze({
        title: "PDF Şablonları",
        subtitle: "PDF belgeleri için şablonları yönetin ve yapılandırın.",
        icon: "13_pdf_file.svg",
    }),
    "account.move": Object.freeze({
        title: "Faturalar",
        subtitle: "Tüm fatura ve belge kayıtlarını görüntüleyin ve yönetin.",
        icon: "05_accounting_document_calculator.svg",
    }),
    "rds.export.log": Object.freeze({
        title: "Dışa Aktarım Geçmişi",
        subtitle: "Oluşturulan belgeleri görüntüleyin ve tekrar indirin.",
        icon: "19_activity_clock.svg",
    }),
});

export function getRdsActiveMenuKey(resModel) {
    return RDS_ACTIVE_MENU_KEY_BY_MODEL[resModel];
}

export class RdsListController extends ListController {
    static template = "ranvals_document_studio.RdsListView";
    static components = {
        ...ListController.components,
        RdsSidebar,
    };

    get rdsActiveMenuKey() {
        return getRdsActiveMenuKey(this.props.resModel);
    }

    get rdsListHero() {
        return RDS_LIST_HERO_BY_MODEL[this.props.resModel] || false;
    }

    rdsIcon(fileName) {
        return `/ranvals_document_studio/static/src/img/icons/${fileName}`;
    }
}

export class RdsFormController extends FormController {
    static template = "ranvals_document_studio.RdsFormView";
    static components = {
        ...FormController.components,
        RdsSidebar,
    };

    setup() {
        super.setup();
        onMounted(async () => {
            if (
                this.props.resModel === "rds.template" &&
                this.canEdit &&
                !this.model.root.isInEdition
            ) {
                await this.model.root.switchMode("edit");
            }
        });
    }

    get modelParams() {
        const params = super.modelParams;
        if (this.props.resModel === "rds.template" && this.canEdit) {
            params.config.mode = "edit";
        }
        return params;
    }

    get rdsActiveMenuKey() {
        return getRdsActiveMenuKey(this.props.resModel);
    }
}

export const rdsListView = {
    ...listView,
    Controller: RdsListController,
};

export const rdsFormView = {
    ...formView,
    Controller: RdsFormController,
};

registry.category("views").add("rds_sidebar_list", rdsListView);
registry.category("views").add("rds_sidebar_form", rdsFormView);
