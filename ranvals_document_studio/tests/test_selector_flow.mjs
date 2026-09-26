import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
const source = await readFile(new URL("../static/src/studio/js/selector_flow.js", import.meta.url), "utf8");
const { runSelectorOperation: run } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const base = () => ({ save: async () => {}, isDirty: () => false, isCurrent: () => true, perform: async () => "done" });

test("save -> apply -> reload order", async () => {
    const calls = [];
    const result = await run({ ...base(), save: async () => calls.push("save"), perform: async () => calls.push("apply"), reload: async () => calls.push("reload") });
    assert.deepEqual(calls, ["save", "apply", "reload"]); assert.equal(result.ok, true);
});
test("unsaved Studio changes prevent apply", async () => {
    let called = false;
    const result = await run({ ...base(), isDirty: () => true, perform: async () => { called = true; } });
    assert.equal(result.reason, "unsaved"); assert.equal(called, false);
});
test("save rejection prevents apply", async () => {
    let called = false;
    await assert.rejects(run({ ...base(), save: async () => { throw new Error("save failed"); }, perform: async () => { called = true; } }));
    assert.equal(called, false);
});
test("report navigation during save cancels apply", async () => {
    let current = true; let called = false;
    const result = await run({ ...base(), save: async () => { current = false; }, isCurrent: () => current, perform: async () => { called = true; } });
    assert.equal(result.reason, "stale"); assert.equal(called, false);
});
test("record navigation during apply prevents reload", async () => {
    let current = true; let reloaded = false;
    const result = await run({ ...base(), isCurrent: () => current, perform: async () => { current = false; }, reload: async () => { reloaded = true; } });
    assert.equal(result.reason, "stale"); assert.equal(reloaded, false);
});
test("navigation during reload is not reported as success", async () => {
    let current = true;
    const result = await run({ ...base(), isCurrent: () => current, reload: async () => { current = false; } });
    assert.equal(result.reason, "stale");
});
test("backend rejection propagates and stops reload", async () => {
    let reloaded = false;
    await assert.rejects(run({ ...base(), perform: async () => { throw new Error("ACL"); }, reload: async () => { reloaded = true; } }));
    assert.equal(reloaded, false);
});
test("preview errors cannot be shown as success", async () => {
    const result = await run({ ...base(), reload: async () => {}, isFailed: () => true });
    assert.equal(result.reason, "preview");
});
test("operation without reload preserves return value", async () => {
    const result = await run(base()); assert.deepEqual(result, { ok: true, value: "done" });
});
test("native no-op save returning false is valid when clean", async () => {
    const result = await run({ ...base(), save: async () => false }); assert.equal(result.ok, true);
});
