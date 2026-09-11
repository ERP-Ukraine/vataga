/** @odoo-module **/

import { Domain } from "@web/core/domain";
import { registry } from "@web/core/registry";
import { StatusBarField, statusBarField } from "@web/views/fields/statusbar/statusbar_field";

export class QualityBoardStatusBar extends StatusBarField {
    async selectItem() {
        // Display only: never update the client record before a business action.
    }

    getAllItems() {
        // Standard statusbar ORs the current value into its search domain.
        // Do not offer that legacy stage as an extra step on historical cards.
        const domain = new Domain(this.props.domain);
        return super.getAllItems().filter((item) => domain.contains({ id: item.value }));
    }
}

registry.category("fields").add("quality_board_statusbar", {
    ...statusBarField,
    component: QualityBoardStatusBar,
    extractProps: (...args) => ({
        ...statusBarField.extractProps(...args),
        isDisabled: true,
    }),
});
