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
    get cssClass() {
        const cssClass = super.cssClass;
        if (this.resModel === "mrp.workorder" && this.record.is_last_unfinished_wo
            && this.state.underValidation && !this.state.validated) {
            // The native animation-end handler also validates the last work order.
            return `${cssClass} o_fadeout_animation`;
        }
        return cssClass;
    },

    onClickValidateButton() {
        if (!this.qualityWorkorderValidation) {
            return super.onClickValidateButton();
        }
    },

    onClickCloseProduction() {
        if (!this.qualityWorkorderValidation) {
            return super.onClickCloseProduction();
        }
    },

    async workorderValidation(skipRemoveFromStack = false) {
        if (this.qualityWorkorderValidation) {
            return this.qualityWorkorderValidation;
        }
        const validation = super.workorderValidation(skipRemoveFromStack);
        this.qualityWorkorderValidation = validation;
        try {
            return await validation;
        } catch (error) {
            if (this.state.underValidation) {
                this.state.underValidation = false;
                if (!skipRemoveFromStack) {
                    await this.props.removeFromValidationStack(this.props.record, false);
                }
            }
            throw error;
        } finally {
            this.qualityWorkorderValidation = null;
        }
    },

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
