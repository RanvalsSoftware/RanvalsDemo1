/** @odoo-module **/

import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";

export class RdsPdfPreviewDialog extends Component {
    static template = "ranvals_document_studio.PdfPreviewDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        url: String,
        title: { type: String, optional: true },
    };
    static defaultProps = {
        title: _t("PDF Önizleme"),
    };
}

/**
 * Download a generated document without opening a browser window.
 *
 * Odoo 19 handles ``ir.actions.act_url`` with ``target: \"download\"`` through
 * ``window.open``.  Export rendering happens after an RPC round-trip, so the
 * browser's user-activation window has usually expired by then and the popup
 * is blocked.  The core download helper uses an XHR + Blob instead and keeps
 * the current action (and its modal) intact until the transfer has started.
 */
export async function downloadExport(env, action) {
    const url = action.params?.url;
    if (!url) {
        throw new Error("Missing Document Studio download URL");
    }

    env.services.ui.block();
    try {
        await download({ url, data: action.params?.data || {} });
    } finally {
        env.services.ui.unblock();
    }

    // Close the transient export wizard only after the browser accepted the
    // binary response.  When the request fails the exception is propagated and
    // the wizard stays open so the user can retry.
    return { type: "ir.actions.act_window_close" };
}

/** Display a same-origin generated PDF without requesting a popup window. */
export function previewExport(env, action) {
    const url = action.params?.url;
    if (!url) {
        throw new Error("Missing Document Studio preview URL");
    }
    env.services.dialog.add(RdsPdfPreviewDialog, {
        url,
        title: action.params?.title || _t("PDF Önizleme"),
    });
}

registry
    .category("actions")
    .add("ranvals_document_studio.download_export", downloadExport);
registry
    .category("actions")
    .add("ranvals_document_studio.preview_export", previewExport);
