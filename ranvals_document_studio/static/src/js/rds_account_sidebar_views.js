/** @odoo-module **/

import {
    AccountMoveFormController,
    AccountMoveFormView,
} from "@account/components/account_move_form/account_move_form";
import { registry } from "@web/core/registry";
import { RdsSidebar } from "@ranvals_document_studio/js/rds_sidebar";
import { getRdsActiveMenuKey } from "@ranvals_document_studio/js/rds_sidebar_views";

export class RdsAccountMoveFormController extends AccountMoveFormController {
    static template = "ranvals_document_studio.RdsAccountMoveFormView";
    static components = {
        ...AccountMoveFormController.components,
        RdsSidebar,
    };

    get rdsActiveMenuKey() {
        return getRdsActiveMenuKey(this.props.resModel);
    }
}

export const rdsAccountMoveFormView = {
    ...AccountMoveFormView,
    Controller: RdsAccountMoveFormController,
};

registry
    .category("views")
    .add("rds_account_move_sidebar_form", rdsAccountMoveFormView);
