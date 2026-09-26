/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const HEX_RE = /^#[0-9A-F]{6}$/;

const FONT_OPTIONS = [
    ["serif", "Kurumsal Serif"],
    ["sans", "Modern Sans Serif"],
    ["technical", "Teknik Sans Serif"],
    ["editorial", "Editoryal"],
];
const FONT_VALUES = new Set(FONT_OPTIONS.map(([value]) => value));

const FONT_CSS = {
    serif: "Georgia, 'Times New Roman', serif",
    sans: "Arial, Helvetica, sans-serif",
    technical: "'Trebuchet MS', Arial, sans-serif",
    editorial: "Georgia, 'Times New Roman', serif",
};

function normalizeHex(value) {
    let normalized = String(value || "").trim().toUpperCase();
    if (normalized && !normalized.startsWith("#")) {
        normalized = `#${normalized}`;
    }
    return HEX_RE.test(normalized) ? normalized : "#000000";
}

export class RdsColorPicker extends Component {
    static template = "ranvals_document_studio.RdsColorPicker";
    static props = { ...standardFieldProps };

    get value() {
        return normalizeHex(this.props.record.data[this.props.name]);
    }

    async onColorChange(event) {
        if (this.props.readonly) {
            return;
        }
        const value = normalizeHex(event.target.value);
        if (value === this.value) {
            return;
        }
        await this.props.record.update({ [this.props.name]: value });
    }

    async onTextChange(event) {
        if (this.props.readonly) {
            return;
        }
        let value = String(event.target.value || "").trim().toUpperCase();
        if (value && !value.startsWith("#")) {
            value = `#${value}`;
        }
        if (!HEX_RE.test(value)) {
            event.target.value = this.value;
            return;
        }
        if (value === this.value) {
            return;
        }
        await this.props.record.update({ [this.props.name]: value });
    }
}

export class RdsFontPicker extends Component {
    static template = "ranvals_document_studio.RdsFontPicker";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name] || "sans";
    }

    get options() {
        return FONT_OPTIONS;
    }

    get sampleStyle() {
        return `font-family:${FONT_CSS[this.value] || FONT_CSS.sans};`;
    }

    get currentLabel() {
        const option = FONT_OPTIONS.find(([value]) => value === this.value);
        return option ? option[1] : this.value;
    }

    async onFontChange(event) {
        if (this.props.readonly) {
            return;
        }
        const value = event.target.value;
        if (!FONT_VALUES.has(value) || value === this.value) {
            event.target.value = this.value;
            return;
        }
        await this.props.record.update({ [this.props.name]: value });
    }
}

registry.category("fields").add("rds_color_picker", {
    component: RdsColorPicker,
    supportedTypes: ["char"],
});

registry.category("fields").add("rds_font_picker", {
    component: RdsFontPicker,
    supportedTypes: ["selection"],
});
