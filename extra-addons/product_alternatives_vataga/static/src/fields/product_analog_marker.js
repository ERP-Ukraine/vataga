/** @odoo-module **/

import { Component, onMounted, onPatched, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class ProductAnalogMarkerField extends Component {
    setup() {
        this.orm = useService("orm");
        this.root = useRef("root");
        this.state = useState({ open: false, analogRows: [], menuStyle: "" });
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
        return this.state.analogRows;
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
        if (this.fallbackAnalogRows.length) {
            this.state.analogRows = this.fallbackAnalogRows;
            return;
        }
        if (!this.recordModel || !this.recordId) {
            this.state.analogRows = [];
            return;
        }
        try {
            const lines = await this.orm.call(
                this.recordModel,
                "get_analog_product_names",
                [[this.recordId]]
            );
            this.state.analogRows = lines.map((line) => {
                const [component, ...analogParts] = line.split("\t");
                return {
                    component: component || "",
                    analog: analogParts.join("\t") || component || "",
                };
            });
        } catch {
            this.state.analogRows = [];
        }
    }

    async toggleDropdown(ev) {
        ev.stopPropagation();
        if (this.state.open) {
            this.state.open = false;
            return;
        }
        const rect = ev.currentTarget.getBoundingClientRect();
        const menuWidth = Math.min(760, window.innerWidth - 24);
        const left = Math.max(
            menuWidth / 2 + 12,
            Math.min(rect.left + rect.width / 2, window.innerWidth - menuWidth / 2 - 12)
        );
        this.state.menuStyle = [
            `top: ${rect.bottom + 4}px`,
            `left: ${left}px`,
            `width: ${menuWidth}px`,
        ].join("; ");
        this.state.open = true;
        if (!this.state.analogRows.length) {
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
    supportedTypes: ["char", "text"],
};

registry.category("fields").add("product_analog_marker", productAnalogMarkerField);
