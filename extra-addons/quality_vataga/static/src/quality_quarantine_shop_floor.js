/** @odoo-module **/

import { MrpDisplayAction } from "@mrp_workorder/mrp_display/mrp_display_action";
import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { MrpRegisterProductionDialog } from "@mrp_workorder/mrp_display/dialog/mrp_register_production_dialog";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

class QualityOutputRegistrationDialog extends MrpRegisterProductionDialog {
    static props = { ...MrpRegisterProductionDialog.props, onRegistered: Function };

    async doActionAndClose(action, saveModel = true, reloadChecks = false) {
        const { onRegistered } = this.props;
        await super.doActionAndClose(action, saveModel, reloadChecks);
        if (saveModel) {
            await onRegistered();
        }
    }
}

patch(MrpDisplayAction.prototype, {
    get fieldsStructure() {
        const fields = super.fieldsStructure;
        return {
            ...fields,
            "mrp.production": [...fields["mrp.production"], "quality_shop_floor_registration_required"],
            "quality.check": [...fields["quality.check"], "quality_quarantine_deferred"],
        };
    },
});

patch(MrpDisplayRecord.prototype, {
    generateSerialNumber() {
        if (this.props.production.data.quality_shop_floor_registration_required) {
            return this.registerProduction();
        }
        return super.generateSerialNumber();
    },

    async validate() {
        const production = this.props.production;
        if (this.resModel === "mrp.workorder" && this.record.is_last_unfinished_wo
            && production.data.quality_shop_floor_registration_required) {
            if (!this.qualityRegistrationDialogOpen) {
                this.qualityRegistrationDialogOpen = true;
                this.dialog.add(QualityOutputRegistrationDialog, {
                    body: "",
                    record: production,
                    reload: this.env.reload.bind(this),
                    title: _t("Register Production: %s", production.data.product_id[1]),
                    qtyToProduce: this.record.qty_remaining,
                    onRegistered: async () => {
                        // Reload can replace the records backing the rendered card.
                        await new Promise((resolve) => browser.requestAnimationFrame(resolve));
                        const currentProduction = this.env.model.root.records.find(
                            (record) => record.resId === production.resId
                        );
                        if (currentProduction && !currentProduction.data.quality_shop_floor_registration_required) {
                            const currentWorkorder = currentProduction.data.workorder_ids.records.find(
                                (record) => record.resId === this.props.record.resId
                            );
                            await currentWorkorder?.component?.validate();
                        }
                    },
                }, { onClose: () => { this.qualityRegistrationDialogOpen = false; } });
            }
            return;
        }
        return super.validate();
    },

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
