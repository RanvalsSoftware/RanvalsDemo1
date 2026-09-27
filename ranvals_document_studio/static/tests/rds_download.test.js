/** @odoo-module **/

import { describe, expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";

import { download } from "@web/core/network/download";
import {
    downloadExport,
    previewExport,
    RdsPdfPreviewDialog,
} from "@ranvals_document_studio/js/rds_download";

describe.current.tags("headless");

test("forwards the request to the core downloader and closes after success", async () => {
    const url = "/web/content/rds.export.wizard/42/file_data/export.pdf?download=true";
    const data = { token: "export-token" };
    const ui = {
        block() {
            expect.step("block");
        },
        unblock() {
            expect.step("unblock");
        },
    };

    patchWithCleanup(download, {
        async _download(options) {
            expect.step("download");
            expect(options).toEqual({ url, data });
        },
    });

    const nextAction = await downloadExport(
        { services: { ui } },
        { params: { url, data } }
    );

    expect.verifySteps(["block", "download", "unblock"]);
    expect(nextAction).toEqual({ type: "ir.actions.act_window_close" });
});

test("unblocks and keeps the wizard open when the download fails", async () => {
    const error = new Error("download failed");
    const ui = {
        block() {
            expect.step("block");
        },
        unblock() {
            expect.step("unblock");
        },
    };

    patchWithCleanup(download, {
        async _download() {
            expect.step("download");
            throw error;
        },
    });

    await expect(
        downloadExport(
            { services: { ui } },
            { params: { url: "/failed-download" } }
        )
    ).rejects.toThrow(error);

    expect.verifySteps(["block", "download", "unblock"]);
});

test("opens the generated PDF in the in-app preview dialog", () => {
    const url = "/web/content/rds.export.wizard/42/file_data/preview.pdf?download=false";
    const title = "preview.pdf Preview";
    const dialog = {
        add(Component, props) {
            expect.step("dialog");
            expect(Component).toBe(RdsPdfPreviewDialog);
            expect(props).toEqual({ url, title });
        },
    };

    const result = previewExport(
        { services: { dialog } },
        { params: { url, title } }
    );

    expect.verifySteps(["dialog"]);
    expect(result).toBe(undefined);
});
