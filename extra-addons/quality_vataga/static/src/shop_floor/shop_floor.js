/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { MrpDisplayAction } from "@mrp_workorder/mrp_display/mrp_display_action";
import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

export class QualityShopFloorQuantityDialog extends Component {
    static template = "quality_vataga.ShopFloorQuantityDialog";
    static components = { Dialog, Many2XAutocomplete };
    static props = { close: Function, production: Object, reload: Function };

    setup() {
        const { qty_producing, lot_producing_id } = this.props.production.data;
        this.title = _t("Реєстрація виробництва");
        this.state = useState({ quantity: qty_producing || "", lot: lot_producing_id, saving: false });
    }

    get lotField() {
        const { product_id, company_id } = this.props.production.data;
        return {
            id: "quality_shop_floor_lot",
            fieldString: _t("Партія/серійний номер"),
            resModel: "stock.lot",
            value: this.state.lot?.[1] || "",
            activeActions: { create: true, createEdit: true },
            getDomain: () => [["product_id", "=", product_id[0]], ["company_id", "in", [false, company_id[0]]]],
            context: { default_product_id: product_id[0], default_company_id: company_id[0] },
            update: this.updateLot.bind(this),
        };
    }

    async updateLot(records) {
        let lot = records?.[0];
        if (lot && !lot.display_name) {
            [lot] = await this.props.production.model.orm.read("stock.lot", [lot.id], ["display_name"]);
        }
        this.state.lot = lot ? [lot.id, lot.display_name] : false;
    }

    get canRegister() {
        const quantity = Number(this.state.quantity);
        return Number.isInteger(quantity) && quantity > 0 && quantity <= this.props.production.data.product_qty
            && (this.props.production.data.product_tracking === "none" || this.state.lot);
    }

    async register() {
        if (!this.canRegister || this.state.saving) {
            return;
        }
        this.state.saving = true;
        const { production, reload, close } = this.props;
        try {
            await production.model.orm.call("mrp.production", "action_register_quality_shop_floor_quantity", [
                [production.resId], Number(this.state.quantity), this.state.lot?.[0] || false,
            ]);
            close();
            await reload(production);
        } finally {
            this.state.saving = false;
        }
    }
}

patch(MrpDisplayAction.prototype, {
    get fieldsStructure() {
        const fields = super.fieldsStructure;
        return {
            ...fields,
            "mrp.production": [...fields["mrp.production"], "quality_unit_route_required"],
            "mrp.workorder": [...fields["mrp.workorder"], "quality_native_managed"],
            "quality.check": [...fields["quality.check"], "quality_shop_floor_deferred"],
        };
    },
});

patch(MrpDisplayRecord.prototype, {
    get displayDoneButton() {
        if (this.props.record.resModel === "mrp.production" && this.props.production.data.quality_unit_route_required) {
            return false;
        }
        return super.displayDoneButton;
    },

    get displayCloseProductionButton() {
        return !this.props.production.data.quality_unit_route_required && super.displayCloseProductionButton;
    },

    _workorderDisplayDoneButton() {
        if (!this.props.record.data.quality_native_managed) {
            return super._workorderDisplayDoneButton();
        }
        return this.record.state === "progress" && this.record.check_ids.records.every(({ data }) =>
            data.quality_shop_floor_deferred || ["pass", "fail"].includes(data.quality_state)
        );
    },

    displayInstruction(record) {
        if (this.props.record.data.quality_native_managed && record?.data.quality_shop_floor_deferred) {
            const checks = this.checks;
            record = checks.slice(checks.indexOf(record) + 1).find(({ data }) =>
                !data.quality_shop_floor_deferred && data.quality_state === "none"
            );
            if (!record) {
                return;
            }
        }
        return super.displayInstruction(record);
    },

    registerProduction() {
        const { production } = this.props;
        if (!production.data.quality_unit_route_required) {
            return super.registerProduction();
        }
        this.dialog.add(QualityShopFloorQuantityDialog, { production, reload: this.env.reload });
    },

    quickRegisterProduction() {
        if (this.props.production.data.quality_unit_route_required) {
            return this.registerProduction();
        }
        return super.quickRegisterProduction();
    },

    generateSerialNumber() {
        if (this.props.production.data.quality_unit_route_required) {
            return this.registerProduction();
        }
        return super.generateSerialNumber();
    },

    onClickValidateButton() {
        if (this.props.record.data.quality_native_managed) {
            return this.validate();
        }
        return super.onClickValidateButton();
    },

    async validate() {
        const { record, production } = this.props;
        if (!production.data.quality_unit_route_required) {
            return super.validate();
        }
        if (!record.data.quality_native_managed || this.qualityFinishing) {
            return;
        }
        this.qualityFinishing = true;
        try {
            try {
                await this.model.orm.call("mrp.workorder", "do_finish", [[record.resId]]);
            } catch (error) {
                await this.env.reload(production).catch(() => {});
                throw error;
            }
            this.env.searchModel.removeRecordFromCache(record.resId);
            await this.env.reload(production);
        } finally {
            this.qualityFinishing = false;
        }
    },

    onClickCloseProduction() {
        if (!this.props.production.data.quality_unit_route_required) {
            return super.onClickCloseProduction();
        }
    },
});
