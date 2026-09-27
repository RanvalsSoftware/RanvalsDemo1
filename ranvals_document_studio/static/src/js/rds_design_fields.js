/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const HEX_RE = /^#[0-9A-F]{6}$/;
const FALLBACK_FONT_CSS = "Arial, sans-serif";

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
        const selection = this.props.record.fields[this.props.name]?.selection;
        return Array.isArray(selection) ? selection.filter((option) => option[1] !== "") : [];
    }

    get fontCss() {
        const cssFieldName = `${this.props.name}_css`;
        const cssValue = this.props.record.data[cssFieldName];
        return typeof cssValue === "string" && cssValue.trim()
            ? cssValue
            : FALLBACK_FONT_CSS;
    }

    get sampleStyle() {
        return `font-family:${this.fontCss};`;
    }

    get currentLabel() {
        const option = this.options.find(([value]) => value === this.value);
        return option ? option[1] : this.value;
    }

    async onFontChange(event) {
        if (this.props.readonly) {
            return;
        }
        const value = event.target.value;
        const isSupported = this.options.some(([optionValue]) => optionValue === value);
        if (!isSupported || value === this.value) {
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
