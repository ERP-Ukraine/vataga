/** @odoo-module **/

import { MrpDisplayAction } from "@mrp_workorder/mrp_display/mrp_display_action";
import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { patch } from "@web/core/utils/patch";

patch(MrpDisplayAction.prototype, {
    get fieldsStructure() {
        const fields = super.fieldsStructure;
        return {
            ...fields,
            "quality.check": [...fields["quality.check"], "quality_quarantine_deferred"],
        };
    },
});

patch(MrpDisplayRecord.prototype, {
    _workorderDisplayDoneButton() {
        const checks = this.record.check_ids.records;
        if (!checks.some(({ data }) => data.quality_quarantine_deferred)) {
            return super._workorderDisplayDoneButton();
        }
        return ["pending", "waiting", "ready", "progress"].includes(this.record.state)
            && checks.every(({ data }) =>
                data.quality_quarantine_deferred || ["pass", "fail"].includes(data.quality_state)
            );
    },
});
