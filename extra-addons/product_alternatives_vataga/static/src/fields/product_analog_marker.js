/** @odoo-module **/

import { Component, onMounted, onPatched, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class ProductAnalogMarkerField extends Component {
    setup() {
        this.orm = useService("orm");
        this.root = useRef("root");
        this.state = useState({ open: false, analogNames: [], menuStyle: "" });
        onMounted(() => this.clearCellTitle());
        onPatched(() => this.clearCellTitle());
    }

    get marker() {
        return this.markerPayload.split("\n")[0] || "";
    }

    get markerPayload() {
        return this.props.record.data[this.props.name] || "";
    }

    get analogNames() {
        return this.state.analogNames;
    }

    get markerAnalogNames() {
        return this.markerPayload.split("\n").slice(1).filter(Boolean);
    }

    get fallbackAnalogNames() {
        const names = this.props.record.data.analog_product_names || "";
        return this.markerAnalogNames.length
            ? this.markerAnalogNames
            : names.split("\n").filter(Boolean);
    }

    get recordModel() {
        return (
            this.props.record.resModel ||
            this.props.record.model?.root?.resModel ||
            this.props.record.model?.config?.resModel
        );
    }

    get recordId() {
        return this.props.record.resId || this.props.record.data.id || this.props.record.evalContext?.id;
    }

    async loadAnalogNames() {
        if (this.fallbackAnalogNames.length) {
            this.state.analogNames = this.fallbackAnalogNames;
            return;
        }
        if (!this.recordModel || !this.recordId) {
            this.state.analogNames = [];
            return;
        }
        try {
            this.state.analogNames = await this.orm.call(
                this.recordModel,
                "get_analog_product_names",
                [[this.recordId]]
            );
        } catch {
            this.state.analogNames = [];
        }
    }

    async toggleDropdown(ev) {
        ev.stopPropagation();
        if (this.state.open) {
            this.state.open = false;
            return;
        }
        const rect = ev.currentTarget.getBoundingClientRect();
        this.state.menuStyle = [
            `top: ${rect.bottom + 4}px`,
            `left: ${rect.left + rect.width / 2}px`,
        ].join("; ");
        this.state.open = true;
        if (!this.state.analogNames.length) {
            await this.loadAnalogNames();
        }
    }

    clearCellTitle() {
        const cell = this.root.el?.closest("td");
        if (cell) {
            cell.removeAttribute("title");
        }
    }
}

ProductAnalogMarkerField.template =
    "product_alternatives_vataga.ProductAnalogMarkerField";
ProductAnalogMarkerField.props = {
    ...standardFieldProps,
};

export const productAnalogMarkerField = {
    component: ProductAnalogMarkerField,
    supportedTypes: ["char"],
};

registry.category("fields").add("product_analog_marker", productAnalogMarkerField);
