/** @odoo-module **/

import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { QualityShopFloorQuantityDialog } from "@quality_vataga/shop_floor/shop_floor";

QUnit.module("quality_vataga Shop Floor", () => {
    function card({ managed = true, last = false, failure = null } = {}) {
        const calls = [];
        const component = Object.create(MrpDisplayRecord.prototype);
        const data = {
            quality_native_managed: managed,
            state: "progress",
            is_last_unfinished_wo: last,
            check_ids: { records: [] },
        };
        const record = { resModel: "mrp.workorder", resId: 17, data };
        const production = { resId: 31, data: { quality_unit_route_required: managed } };
        Object.assign(component, {
            props: { record, production },
            resModel: record.resModel,
            record: data,
            state: { underValidation: false, validated: false },
            model: { orm: { async call(...args) {
                calls.push(args);
                if (failure) {
                    throw failure;
                }
            } } },
            env: {
                searchModel: { removeRecordFromCache: (id) => calls.push(["remove", id]) },
                async reload(value) { calls.push(["reload", value.resId]); },
            },
        });
        return { component, data, calls, production };
    }

    QUnit.test("Mark as Done on any managed WO sends one physical RPC and reloads", async (assert) => {
        for (const last of [false, true]) {
            const { component, data, calls } = card({ last });
            data.check_ids.records.push({ data: { quality_state: "none", quality_shop_floor_deferred: true } });
            assert.true(component.displayDoneButton);
            await Promise.all([component.onClickValidateButton(), component.onClickValidateButton()]);
            assert.deepEqual(calls, [["mrp.workorder", "do_finish", [[17]]], ["remove", 17], ["reload", 31]]);
            data.state = "done";
            assert.false(component.displayDoneButton);
        }
    });

    QUnit.test("Close Production has no managed RPC and ordinary buttons keep their rules", async (assert) => {
        const managed = card({ last: true });
        managed.component.state.underValidation = true;
        assert.false(managed.component.displayCloseProductionButton);
        await managed.component.onClickCloseProduction();
        managed.component.props.record = { resModel: "mrp.production", data: managed.production.data };
        assert.false(managed.component.displayDoneButton);
        await managed.component.validate();
        assert.deepEqual(managed.calls, []);

        const ordinary = card({ managed: false, last: true });
        ordinary.component.state.underValidation = true;
        assert.true(ordinary.component.displayCloseProductionButton);
        ordinary.data.check_ids.records.push({ data: { quality_state: "none", quality_shop_floor_deferred: true } });
        assert.false(ordinary.component.displayDoneButton);
        assert.false(ordinary.component.displayCloseProductionButton);
    });

    QUnit.test("Failed finish leaves the card retryable and refreshes the server state", async (assert) => {
        const error = new Error("Invalid operation");
        const { component, calls } = card({ failure: error });
        await assert.rejects(component.onClickValidateButton(), /Invalid operation/);
        assert.deepEqual(calls, [["mrp.workorder", "do_finish", [[17]]], ["reload", 31]]);
        assert.true(component.displayDoneButton);
        assert.false(component.qualityFinishing);
    });

    QUnit.test("An ordinary WO still runs the native precheck and deferred validation", async (assert) => {
        const { component, data, calls } = card({ managed: false });
        data.employee_ids = { records: [] };
        component.props.record.save = async () => calls.push(["save"]);
        component.props.sessionOwner = { id: 42 };
        let validate;
        component.props.addToValidationStack = (_record, callback) => { validate = callback; };
        component.props.removeFromValidationStack = async () => calls.push(["unstack"]);
        await component.validate();
        assert.true(component.state.underValidation);
        await validate();
        assert.deepEqual(calls, [
            ["save"], ["mrp.workorder", "pre_record_production", [17]],
            ["mrp.workorder", "do_finish", [17], { context: { no_start_next: true, mrp_display: true, employee_id: 42 } }],
            ["unstack"], ["reload", 31],
        ]);
    });

    QUnit.test("Register Production sends the entered 2/3 and lot in one RPC", async (assert) => {
        const calls = [];
        const dialog = Object.create(QualityShopFloorQuantityDialog.prototype);
        const production = {
            resId: 31,
            data: { product_qty: 3, product_tracking: "lot" },
            model: { orm: { async call(...args) { calls.push(args); } } },
        };
        dialog.props = {
            production,
            close: () => calls.push(["close"]),
            async reload(value) { calls.push(["reload", value.resId]); },
        };
        dialog.state = { quantity: "", lot: [9, "LOT-9"], saving: false };
        await dialog.register();
        assert.deepEqual(calls, []);
        dialog.state.quantity = "2";
        await Promise.all([dialog.register(), dialog.register()]);
        assert.deepEqual(calls, [
            ["mrp.production", "action_register_quality_shop_floor_quantity", [[31], 2, 9]],
            ["close"], ["reload", 31],
        ]);
    });
});
