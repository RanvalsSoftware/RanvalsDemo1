// Isolated tests for the language patch layered on the Studio selector.
// Run: node --experimental-vm-modules --test tests/test_report_language_component.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const componentSource = await readFile(
    new URL('../static/src/studio/js/report_design_selector.js', import.meta.url),
    'utf8',
);
const flowSource = await readFile(
    new URL('../static/src/studio/js/selector_flow.js', import.meta.url),
    'utf8',
);
const languageSource = await readFile(
    new URL('../static/src/studio/js/report_language.js', import.meta.url),
    'utf8',
);

async function fixture({ initialLanguage = '' } = {}) {
    const calls = [];
    const notifications = [];
    const effects = [];
    const unmount = [];
    let languageCode = initialLanguage;
    let templateId = 1;
    const context = vm.createContext({ console });
    const languageButton = class RdsLanguageButton {};
    const nativeEditor = { components: {} };
    const options = () => ({
        supported: true,
        can_manage: true,
        company_name: 'Test Company',
        template_id: templateId,
        templates: [{ id: 1, name: 'Template 1', style: 'beauty' }],
        languages: [
            { code: 'en_US', name: 'English', active: true },
            { code: 'fr_FR', name: 'French', active: true },
        ],
        language_code: languageCode,
    });
    const services = {
        orm: {
            call: async (name, method, args, kwargs) => {
                calls.push({ name, method, args, kwargs });
                if (method === 'rds_apply_design') {
                    templateId = args[2];
                    languageCode = args[3] || '';
                }
                return options();
            },
        },
        action: { doAction: async action => calls.push({ method: 'action', action }) },
        notification: {
            add: (message, config) => notifications.push({ message, config }),
        },
    };
    function synthetic(name, values) {
        return new vm.SyntheticModule(Object.keys(values), function () {
            for (const [key, value] of Object.entries(values)) {
                this.setExport(key, value);
            }
        }, { context, identifier: name });
    }
    // This is the relevant part of Odoo 19's patch helper: the extension is
    // placed over descriptors copied from the patched prototype, allowing its
    // native ``super`` calls and getters to resolve correctly.
    function patch(target, extension) {
        const previous = Object.create(Object.getPrototypeOf(target));
        for (const key of Reflect.ownKeys(extension)) {
            const descriptor = Object.getOwnPropertyDescriptor(target, key);
            if (descriptor) {
                Object.defineProperty(previous, key, descriptor);
            }
        }
        Object.setPrototypeOf(extension, previous);
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
    }
    const modules = {
        '@odoo/owl': synthetic('owl', {
            Component: class {},
            useState: value => value,
            useEffect: (effect, deps) => effects.push({ effect, deps }),
            onWillUnmount: callback => unmount.push(callback),
        }),
        '@web/core/utils/hooks': synthetic('hooks', { useService: name => services[name] }),
        '@web/core/l10n/translation': synthetic('translation', { _t: text => text }),
        '@web/core/user': synthetic('user', {
            user: { context: { allowed_company_ids: [1] } },
        }),
        '@web/core/utils/patch': synthetic('patch', { patch }),
        '@web_studio/client_action/report_editor/report_editor_wysiwyg/report_editor_wysiwyg':
            synthetic('native', { ReportEditorWysiwyg: nativeEditor }),
        '@ranvals_document_studio/js/rds_language_motion': synthetic('language-button', {
            RdsLanguageButton: languageButton,
        }),
        './selector_flow': new vm.SourceTextModule(flowSource, { context }),
    };
    const componentModule = new vm.SourceTextModule(componentSource, { context });
    await componentModule.link(name => {
        assert.ok(modules[name], `Unknown component import ${name}`);
        return modules[name];
    });
    await componentModule.evaluate();
    modules['./report_design_selector'] = componentModule;

    const languageModule = new vm.SourceTextModule(languageSource, { context });
    await languageModule.link(name => {
        assert.ok(modules[name], `Unknown language import ${name}`);
        return modules[name];
    });
    await languageModule.evaluate();

    const model = {
        editedReportId: 101,
        recordToDisplay: 36,
        reportData: { model: 'sale.order' },
        routesContext: { allowed_company_ids: [1] },
        isDirty: false,
        _errorMessage: false,
        loadReportHtml: async payload => calls.push({ method: 'reload', payload }),
        inPreview: false,
    };
    const Selector = componentModule.namespace.RdsReportDesignSelector;
    const instance = new Selector();
    instance.props = {
        reportModel: model,
        save: async () => calls.push({ method: 'save' }),
    };
    instance.setup();
    await instance.load();
    return {
        calls,
        instance,
        languageButton,
        model,
        notifications,
        options,
    };
}

test('language button is registered without replacing selector components', async () => {
    const { instance, languageButton } = await fixture();
    assert.equal(instance.constructor.components.RdsLanguageButton, languageButton);
});

test('language options and saved value are loaded with design options', async () => {
    const { instance } = await fixture({ initialLanguage: 'en_US' });
    assert.equal(instance.state.languages.length, 2);
    assert.equal(instance.state.languageCode, 'en_US');
    assert.equal(instance.state.savedLanguageCode, 'en_US');
    assert.equal(instance.changed, false);
});

test('applying a language forwards one explicit RPC argument', async () => {
    const { calls, instance } = await fixture();
    calls.length = 0;
    instance.state.languageCode = 'fr_FR';
    await instance.apply();
    const apply = calls.find(call => call.method === 'rds_apply_design');
    assert.equal(apply.args.length, 4);
    assert.equal(apply.args[3], 'fr_FR');
    assert.equal(instance.state.savedLanguageCode, 'fr_FR');
    assert.equal(instance.changed, false);
});

test('clearing a saved language forwards false', async () => {
    const { calls, instance } = await fixture({ initialLanguage: 'en_US' });
    calls.length = 0;
    instance.state.languageCode = '';
    await instance.apply();
    const apply = calls.find(call => call.method === 'rds_apply_design');
    assert.equal(apply.args[3], false);
    assert.equal(instance.state.savedLanguageCode, '');
});

test('language dialog is blocked while selector changes are pending', async () => {
    const { calls, instance, notifications } = await fixture();
    calls.length = 0;
    instance.state.languageCode = 'fr_FR';
    assert.equal(await instance.prepareLanguageChange(), false);
    assert.equal(calls.length, 0);
    assert.equal(notifications.at(-1).config.type, 'warning');
});

test('language dialog preparation saves Studio and rejects a dirty result', async () => {
    const { calls, instance, model } = await fixture();
    calls.length = 0;
    assert.equal(await instance.prepareLanguageChange(), true);
    assert.deepEqual(calls.map(call => call.method), ['save']);

    calls.length = 0;
    instance.props.save = async () => {
        calls.push({ method: 'save' });
        model.isDirty = true;
    };
    assert.equal(await instance.prepareLanguageChange(), false);
    assert.deepEqual(calls.map(call => call.method), ['save']);
});
