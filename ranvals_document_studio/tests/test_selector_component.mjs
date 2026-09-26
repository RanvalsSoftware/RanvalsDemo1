// Isolated component tests, NOT a running Odoo/Enterprise integration test.
// Run: node --experimental-vm-modules --test tests/test_selector_component.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const componentSource = await readFile(new URL('../static/src/studio/js/report_design_selector.js', import.meta.url), 'utf8');
const flowSource = await readFile(new URL('../static/src/studio/js/selector_flow.js', import.meta.url), 'utf8');
const templates = ['beauty', 'construction', 'technology', 'industrial', 'eco', 'furniture']
    .map((style, i) => ({id: i + 1, name: `Template ${i + 1}`, style}));

async function fixture({ recordId = 36, reportModel = 'sale.order', canManage = true } = {}) {
    const calls = [], notifications = [], effects = [], unmount = [];
    let chosen = false;
    const options = () => ({supported: true, can_manage: canManage, company_name: 'Test Company',
        template_id: chosen, templates: templates.map(t => ({...t}))});
    const context = vm.createContext({ console });
    const nativeField = function NativeField() {};
    const nativeEditor = { components: { CharField: nativeField, Many2OneField: nativeField } };
    const services = {
        orm: {call: async (name, method, args, kwargs) => {
            calls.push({name, method, args, kwargs});
            if (method === 'rds_apply_design') chosen = args[2];
            return method === 'rds_export_design' ? {type: 'ir.actions.client', tag: 'download'} : options();
        }},
        action: {doAction: async action => calls.push({method: 'action', action})},
        notification: {add: (message, config) => notifications.push({message, config})},
    };
    function synthetic(name, values) {
        return new vm.SyntheticModule(Object.keys(values), function () {
            for (const [key, value] of Object.entries(values)) this.setExport(key, value);
        }, { context, identifier: name });
    }
    const modules = {
        '@odoo/owl': synthetic('owl', {
            Component: class {}, useState: value => value,
            useEffect: (effect, deps) => effects.push({effect, deps}),
            onWillUnmount: cb => unmount.push(cb),
        }),
        '@web/core/utils/hooks': synthetic('hooks', {useService: name => services[name]}),
        '@web/core/l10n/translation': synthetic('translation', {_t: text => text}),
        '@web/core/user': synthetic('user', {user: {context: {allowed_company_ids: [1]}}}),
        '@web_studio/client_action/report_editor/report_editor_wysiwyg/report_editor_wysiwyg': synthetic('native', {ReportEditorWysiwyg: nativeEditor}),
        './selector_flow': new vm.SourceTextModule(flowSource, {context}),
    };
    const module = new vm.SourceTextModule(componentSource, {context});
    await module.link(name => { assert.ok(modules[name], `Unknown import ${name}`); return modules[name]; });
    await module.evaluate();
    const model = { editedReportId: 101, recordToDisplay: recordId, reportData: {model: reportModel},
        routesContext: {allowed_company_ids: [1]}, isDirty: false, _errorMessage: false,
        loadReportHtml: async payload => calls.push({method: 'reload', payload}), inPreview: false };
    const instance = new module.namespace.RdsReportDesignSelector();
    instance.props = {reportModel: model, save: async () => calls.push({method: 'save'})};
    instance.setup();
    return {instance, model, calls, services, notifications, nativeEditor, nativeField, effects, unmount, options};
}

test('native Studio component registrations are preserved', async () => {
    const f = await fixture();
    assert.equal(f.nativeEditor.components.CharField, f.nativeField);
    assert.equal(f.nativeEditor.components.Many2OneField, f.nativeField);
    assert.equal(f.nativeEditor.components.RdsReportDesignSelector, f.instance.constructor);
});
test('sale sidebar is visible during loading, buttons disabled', async () => {
    const {instance} = await fixture();
    assert.equal(instance.visible, true); assert.equal(instance.canApply, false); assert.equal(instance.canDownload, false);
});
test('unrelated model remains hidden when unsupported', async () => {
    const {instance} = await fixture({reportModel: 'res.partner'});
    instance.acceptOptions({supported: false});
    assert.equal(instance.visible, false);
});
test('invoice and purchase sidebars stay visible while loading', async () => {
    const invoice = await fixture({reportModel: 'account.move'});
    const purchase = await fixture({reportModel: 'purchase.order'});
    assert.equal(invoice.instance.visible, true);
    assert.equal(purchase.instance.visible, true);
});
test('all six themes load and become selectable', async () => {
    const {instance, calls} = await fixture(); await instance.load();
    assert.equal(instance.state.templates.length, 6); assert.equal(instance.canApply, true);
    assert.equal(calls[0].args[0][0], 101); assert.equal(calls[0].args[1], 36);
});
test('application saves Studio first, writes choice then reloads exact record', async () => {
    const {instance, calls, model} = await fixture(); await instance.load(); calls.length = 0;
    instance.state.templateId = '1'; await instance.apply();
    assert.deepEqual(calls.map(c => c.method), ['save', 'rds_apply_design', 'reload']);
    assert.equal(calls[1].args[2], 1); assert.equal(calls[2].payload.resId, 36);
    assert.equal(instance.changed, false); assert.equal(model.inPreview, true);
});
test('default selection clears only current report binding', async () => {
    const {instance, calls} = await fixture(); await instance.load();
    instance.state.savedId = '1'; instance.state.templateId = ''; await instance.apply();
    assert.equal(calls.find(c => c.method === 'rds_apply_design').args[2], false);
});
test('failed native save never applies design', async () => {
    const {instance, model, calls} = await fixture(); await instance.load(); calls.length = 0;
    model.isDirty = true; instance.state.templateId = '1'; await instance.apply();
    assert.deepEqual(calls.map(c => c.method), ['save']); assert.equal(instance.state.busy, false);
});
test('changing record during save cancels old operation', async () => {
    const {instance, model, calls} = await fixture(); await instance.load(); calls.length = 0;
    instance.props.save = async () => {model.recordToDisplay = 99;};
    await instance.apply(); assert.equal(calls.length, 0);
});
test('company context is part of selection identity', async () => {
    const {instance, model} = await fixture(); const before = instance.key;
    model.routesContext = {allowed_company_ids: [2, 1]}; assert.notEqual(instance.key, before);
});
test('late options response cannot replace another record choices', async () => {
    const {instance, model, services, options} = await fixture(); let resolve;
    services.orm.call = () => new Promise(r => {resolve = r;});
    const loading = instance.load(); model.recordToDisplay = 99; resolve(options()); await loading;
    assert.equal(instance.state.ready, false); assert.equal(instance.state.templates.length, 0);
});
test('loading errors stay visible and retry recovers', async () => {
    const {instance, services} = await fixture(); const call = services.orm.call;
    services.orm.call = async () => {throw new Error('Connection problem');}; await instance.load();
    assert.equal(instance.visible, true); assert.equal(instance.state.error, 'Connection problem');
    services.orm.call = call; await instance.load(); assert.equal(instance.state.error, ''); assert.equal(instance.canApply, true);
});
test('no record disables preview and export without making RPC writes', async () => {
    const {instance, calls} = await fixture({recordId: false}); await instance.load(); calls.length = 0;
    await instance.apply(); await instance.exportFile(); assert.equal(calls.length, 0);
});
test('invalid transient ids never reach the ORM service', async () => {
    const {instance, model, calls} = await fixture();
    model.editedReportId = 'new-report'; model.recordToDisplay = -1;
    await instance.load();
    assert.equal(instance.hasRecord, false); assert.equal(instance.state.supported, false);
    assert.equal(calls.length, 0);
});
test('numeric route ids are normalized before RPC', async () => {
    const {instance, model, calls} = await fixture();
    model.editedReportId = '101'; model.recordToDisplay = '36';
    await instance.load();
    assert.equal(calls[0].args[0][0], 101); assert.equal(calls[0].args[1], 36);
});
test('PostgreSQL int4 overflow ids never reach the ORM service', async () => {
    const {instance, model, calls} = await fixture();
    model.editedReportId = '2147483648'; model.recordToDisplay = 36;
    await instance.load();
    assert.equal(instance.state.supported, false); assert.equal(calls.length, 0);
});
test('manager can clear a stale binding when no active template remains', async () => {
    const {instance} = await fixture();
    instance.acceptOptions({supported: true, can_manage: true, templates: [], company_name: 'Test Company',
        has_saved_configuration: true});
    instance.state.ready = true;
    assert.equal(instance.canApply, true); assert.equal(instance.canDownload, false);
});
test('empty configuration with no templates keeps apply disabled', async () => {
    const {instance} = await fixture();
    instance.acceptOptions({supported: true, can_manage: true, templates: [], company_name: 'Test Company',
        has_saved_configuration: false});
    instance.state.ready = true;
    assert.equal(instance.canApply, false); assert.equal(instance.canDownload, false);
});
test('read-only user can download but cannot change configuration', async () => {
    const {instance, calls} = await fixture({canManage: false}); await instance.load();
    assert.equal(instance.canApply, false); assert.equal(instance.canDownload, true);
    instance.state.templateId = '1'; assert.equal(instance.canDownload, false);
    calls.length = 0; await instance.apply(); assert.equal(calls.length, 0);
});
test('Word export uses selected report and record, preserving download action', async () => {
    const {instance, calls} = await fixture(); await instance.load(); calls.length = 0;
    instance.state.format = 'docx'; await instance.exportFile();
    assert.deepEqual(calls.map(c => c.method), ['save', 'reload', 'rds_export_design', 'action']);
    assert.equal(calls[2].args[0][0], 101); assert.equal(calls[2].args[1], 36); assert.equal(calls[2].args[2], 'docx');
});
test('ZIP export is forwarded as an explicit all-format request', async () => {
    const {instance, calls} = await fixture(); await instance.load(); calls.length = 0;
    instance.state.format = 'zip'; await instance.exportFile();
    assert.deepEqual(calls.map(c => c.method), ['save', 'reload', 'rds_export_design', 'action']);
    assert.equal(calls[2].args[2], 'zip');
});
test('failed preview blocks download and produces warning', async () => {
    const {instance, model, calls, notifications} = await fixture(); await instance.load(); calls.length = 0;
    model.loadReportHtml = async () => {model._errorMessage = new Error('QWeb');};
    await instance.exportFile(); assert.equal(calls.some(c => c.method === 'rds_export_design'), false);
    assert.equal(notifications.at(-1).config.type, 'warning');
});
test('busy click cannot run the operation twice', async () => {
    const {instance, calls} = await fixture(); await instance.load(); calls.length = 0; instance.state.busy = true;
    await instance.apply(); await instance.exportFile(); assert.equal(calls.length, 0);
});
test('unknown output format is rejected before export RPC', async () => {
    const {instance, calls} = await fixture(); await instance.load(); calls.length = 0;
    instance.state.format = 'exe'; await instance.exportFile();
    assert.equal(calls.length, 0);
});
test('unmount makes pending load inert', async () => {
    const {instance, services, unmount, options} = await fixture(); let resolve;
    services.orm.call = () => new Promise(r => {resolve = r;});
    const pending = instance.load(); unmount.forEach(cb => cb()); resolve(options()); await pending;
    assert.equal(instance.state.templates.length, 0);
});
