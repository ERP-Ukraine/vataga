/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class ProductAnalogMarkerField extends Component {
    setup() {
        this.state = useState({ open: false });
    }

    get marker() {
        return this.props.record.data[this.props.name] || "";
    }

    get analogNames() {
        const names = this.props.record.data.analog_product_names || "";
        return names.split("\n").filter(Boolean);
    }

    toggleDropdown(ev) {
        ev.stopPropagation();
        this.state.open = !this.state.open;
    }
}

ProductAnalogMarkerField.template =
    "product_alternatives_vataga.ProductAnalogMarkerField";
ProductAnalogMarkerField.props = {
    ...standardFieldProps,
};
ProductAnalogMarkerField.supportedTypes = ["char"];

registry.category("fields").add("product_analog_marker", ProductAnalogMarkerField);
