/** @odoo-module **/

import { Component, onMounted, onPatched, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

let activeAnalogMarkerField = null;

export class ProductAnalogMarkerField extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.root = useRef("root");
        this.state = useState({ open: false, menuStyle: "" });
        this.clearTitles = this.clearTitles.bind(this);
        this.closeDropdown = this.closeDropdown.bind(this);
        this.onDocumentClick = this.onDocumentClick.bind(this);
        onMounted(() => {
            this.clearTitles();
            document.addEventListener("click", this.onDocumentClick);
            window.addEventListener("scroll", this.closeDropdown, true);
            window.addEventListener("resize", this.closeDropdown);
        });
        onPatched(() => this.clearTitles());
        onWillUnmount(() => {
            document.removeEventListener("click", this.onDocumentClick);
            window.removeEventListener("scroll", this.closeDropdown, true);
            window.removeEventListener("resize", this.closeDropdown);
            if (activeAnalogMarkerField === this) {
                activeAnalogMarkerField = null;
            }
        });
    }

    get marker() {
        return this.markerPayload.split("\n")[0] || "";
    }

    get markerPayload() {
        return this.props.record.data[this.props.name] || "";
    }

    get analogNames() {
        return this.analogItems.map((analog) => analog.display_name);
    }

    get analogItems() {
        if (this.isSelectMode) {
            return this.selectableAnalogItems;
        }
        return this.fallbackAnalogNames.map((name) => ({
            id: false,
            display_name: name,
        }));
    }

    get isSelectMode() {
        return this.props.mode === 'select';
    }

    get isSelectionAllowed() {
        const stateField = this.props.stateField || 'raw_material_production_state';
        const productionState = this.props.record.data[stateField];
        return this.isSelectMode && !['done', 'cancel'].includes(productionState);
    }

    get payloadField() {
        return this.props.payloadField || 'analog_product_data';
    }

    get recordModel() {
        return (
            this.props.record.resModel ||
            this.props.record.model?.root?.resModel ||
            this.props.record.model?.config?.resModel
        );
    }

    get recordId() {
        return this.props.record.resId || this.props.record.data.id;
    }

    get selectableAnalogItems() {
        const payload = this.props.record.data[this.payloadField] || [];
        let analogs = payload;
        if (typeof payload === 'string') {
            try {
                analogs = JSON.parse(payload);
            } catch {
                analogs = [];
            }
        }
        return Array.isArray(analogs)
            ? analogs
                  .filter((analog) => analog.id && analog.display_name)
                  .map((analog) => ({
                      id: analog.id,
                      display_name: analog.display_name,
                  }))
            : [];
    }

    get markerAnalogNames() {
        return this.markerPayload.split("\n").slice(1).filter(Boolean);
    }

    get fallbackAnalogNames() {
        const names = this.props.record.data.analog_product_names || "";
        const lines = this.markerAnalogNames.length
            ? this.markerAnalogNames
            : names.split("\n").filter(Boolean);
        return lines.map((line) => this.getAnalogName(line)).filter(Boolean);
    }

    getAnalogName(line) {
        const parts = line.split("\t");
        return parts.length > 1 ? parts.slice(1).join("\t") : line;
    }

    toggleDropdown(ev) {
        ev.stopPropagation();
        this.clearTitles();
        if (this.state.open) {
            this.closeDropdown();
            return;
        }
        if (activeAnalogMarkerField && activeAnalogMarkerField !== this) {
            activeAnalogMarkerField.closeDropdown();
        }
        activeAnalogMarkerField = this;
        this.state.menuStyle = this.getMenuStyle(ev.currentTarget);
        this.state.open = true;
    }

    async selectAnalog(ev) {
        ev.stopPropagation();
        if (!this.isSelectionAllowed) {
            return;
        }
        const analogId = Number(ev.currentTarget.dataset.analogId);
        const analog = this.analogItems.find((item) => item.id === analogId);
        if (!analogId || !analog) {
            return;
        }
        try {
            const result = await this.orm.call(
                this.recordModel,
                'action_replace_with_analog_product',
                [[this.recordId], analogId]
            );
            await this.props.record.update({
                product_id: [result.product_id, result.product_display_name],
                product_uom: [result.product_uom_id, result.product_uom_name],
                [this.props.name]: result.analog_marker || '',
                [this.payloadField]: result.analog_product_data || [],
            });
            this.closeDropdown();
        } catch (error) {
            this.notification.add(error.message || error.toString(), {
                type: 'danger',
            });
        }
    }

    getMenuStyle(target) {
        const rect = target.getBoundingClientRect();
        const width = Math.min(860, window.innerWidth - 24);
        const left = Math.max(
            width / 2 + 12,
            Math.min(rect.left + rect.width / 2, window.innerWidth - width / 2 - 12)
        );
        return [
            `top: ${rect.bottom + 4}px`,
            `left: ${left}px`,
            `width: ${width}px`,
        ].join("; ");
    }

    closeDropdown() {
        this.state.open = false;
        if (activeAnalogMarkerField === this) {
            activeAnalogMarkerField = null;
        }
    }

    onDocumentClick(ev) {
        if (this.state.open && !this.root.el?.contains(ev.target)) {
            this.closeDropdown();
        }
    }

    clearTitles() {
        const cell = this.root.el?.closest("td");
        if (cell) {
            cell.removeAttribute("title");
            cell.dataset.tooltip = "";
        }
        if (this.root.el) {
            this.root.el.removeAttribute("title");
            this.root.el.querySelectorAll("[title]").forEach((node) => {
                node.removeAttribute("title");
            });
        }
    }
}

ProductAnalogMarkerField.template =
    "product_alternatives_vataga.ProductAnalogMarkerField";
ProductAnalogMarkerField.props = {
    ...standardFieldProps,
    mode: { type: String, optional: true },
    payloadField: { type: String, optional: true },
    stateField: { type: String, optional: true },
};

export const productAnalogMarkerField = {
    component: ProductAnalogMarkerField,
    supportedTypes: ["char", "text"],
    extractProps: ({ options }) => ({
        mode: options?.mode || 'readonly',
        payloadField: options?.payloadField,
        stateField: options?.stateField,
    }),
};

registry.category("fields").add("product_analog_marker", productAnalogMarkerField);
