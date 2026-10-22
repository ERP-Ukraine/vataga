/** @odoo-module **/

import { Component, status, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

export const QUALITY_CHECK_MATRIX_REFRESH = "quality_vataga:refresh_check_matrix";

// Use the public record/model APIs. Never discard dirty form values while
// refreshing a custom field, and prevent new input during the actual reload.
export async function refreshQualityCheckRecord(record, ui, reloadRoot = false) {
    ui.block();
    try {
        if (
            (await record.isDirty()) ||
            record.model.root.resId !== record.resId
        ) {
            return false;
        }
        if (reloadRoot) {
            // FormController's onWillLoadRoot also refreshes the mail thread.
            await record.model.load();
        } else {
            await record.load();
        }
        return true;
    } finally {
        ui.unblock();
    }
}

export class QualityUnitAdmissionButtons extends Component {
    static template = "quality_vataga.UnitAdmissionButtons";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.ui = useService("ui");
        this.state = useState({ opening: false });
        this.pendingCheckId = false;
        useRecordObserver((record) => {
            const dirty = record.dirty;
            if (this.pendingCheckId && !dirty) {
                const checkId = this.pendingCheckId;
                this.pendingCheckId = false;
                return this.refreshAfterDecision(checkId);
            }
        });
    }

    isCurrentCheck(checkId) {
        return (
            status(this) !== "destroyed" &&
            this.props.record.resId === checkId &&
            this.props.record.model.root.resId === checkId
        );
    }

    async open(mode) {
        const record = this.props.record;
        const checkId = record.resId;
        if (!checkId || this.state.opening) {
            return;
        }
        this.state.opening = true;
        try {
            // Match standard form object buttons: save pending edits before
            // opening the wizard, without losing them if validation fails.
            if ((await record.isDirty()) && (await record.save()) === false) {
                return;
            }
            if (!this.isCurrentCheck(checkId)) {
                return;
            }
            const method = mode === "revoke"
                ? "action_open_unit_admission_revocation_wizard"
                : "action_open_unit_admission_wizard";
            const action = await this.orm.call("quality.check", method, [[checkId]], {
                context: record.context,
            });
            if (!this.isCurrentCheck(checkId)) {
                return;
            }
            await this.action.doAction(action, {
                onClose: (infos) => {
                    // Cancel (including the dialog X) has no success marker.
                    if (infos?.quality_vataga_check_id === checkId) {
                        return this.refreshAfterDecision(checkId);
                    }
                },
            });
        } finally {
            this.state.opening = false;
        }
    }

    async refreshAfterDecision(checkId) {
        if (!this.isCurrentCheck(checkId)) {
            return;
        }
        const record = this.props.record.model.root;
        if (!(await refreshQualityCheckRecord(record, this.ui, true))) {
            // A local edit made while the dialog was open stays intact. The
            // matrix may read fresh server data; reload the form after saving.
            this.pendingCheckId = checkId;
            this.env.bus.trigger(QUALITY_CHECK_MATRIX_REFRESH, { checkId });
        }
    }
}

registry.category("view_widgets").add("quality_unit_admission_buttons", {
    component: QualityUnitAdmissionButtons,
});
