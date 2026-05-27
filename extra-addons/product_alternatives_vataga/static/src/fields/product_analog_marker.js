/** @odoo-module **/

import { Component, onMounted, onPatched, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class ProductAnalogMarkerField extends Component {
    setup() {
        this.root = useRef("root");
        onMounted(() => this.clearCellTitle());
        onPatched(() => this.clearCellTitle());
    }

    get marker() {
        return this.markerPayload.split("\n")[0] || "";
    }

    get markerPayload() {
        return this.props.record.data[this.props.name] || "";
    }

    get analogRows() {
        return this.fallbackAnalogRows;
    }

    get markerAnalogNames() {
        return this.markerPayload.split("\n").slice(1).filter(Boolean);
    }

    get fallbackAnalogRows() {
        const names = this.props.record.data.analog_product_names || "";
        const lines = this.markerAnalogNames.length
            ? this.markerAnalogNames
            : names.split("\n").filter(Boolean);
        return lines.map((line) => {
            const [component, ...analogParts] = line.split("\t");
            return {
                component: component || "",
                analog: analogParts.join("\t") || component || "",
            };
        });
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
    supportedTypes: ["char", "text"],
};

registry.category("fields").add("product_analog_marker", productAnalogMarkerField);
