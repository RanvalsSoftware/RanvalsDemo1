/** @odoo-module **/

// Pure orchestration so failed saves and stale report selections are testable
// without mounting the Enterprise editor. No ORM call happens before save.
export async function runSelectorOperation({ save, isDirty, isCurrent, perform, reload, isFailed = () => false }) {
    await save();
    if (!isCurrent()) {
        return { ok: false, reason: "stale" };
    }
    if (isDirty()) {
        return { ok: false, reason: "unsaved" };
    }
    const value = await perform();
    if (!isCurrent()) {
        return { ok: false, reason: "stale" };
    }
    if (reload) {
        await reload();
        if (!isCurrent()) {
            return { ok: false, reason: "stale" };
        }
        if (isFailed()) {
            return { ok: false, reason: "preview" };
        }
    }
    return { ok: true, value };
}
