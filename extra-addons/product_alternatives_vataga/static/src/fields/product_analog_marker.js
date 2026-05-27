/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class ProductAnalogMarkerField extends Component {
    setup() {
        this.orm = useService("orm");
        this.state = useState({ open: false, analogNames: [] });
    }

    get marker() {
        return this.props.record.data[this.props.name] || "";
    }

    get analogNames() {
        return this.state.analogNames;
    }

    get fallbackAnalogNames() {
        const names = this.props.record.data.analog_product_names || "";
        return names.split("\n").filter(Boolean);
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
        this.state.open = !this.state.open;
        if (this.state.open) {
            await this.loadAnalogNames();
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
