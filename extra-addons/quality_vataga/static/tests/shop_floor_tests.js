/** @odoo-module **/

import { MrpDisplay } from "@mrp_workorder/mrp_display/mrp_display";
import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import { QualityShopFloorQuantityDialog } from "@quality_vataga/shop_floor/shop_floor";

QUnit.module("quality_vataga Shop Floor", () => {
    function card({ managed = true, last = false, failure = null, reload = async () => {} } = {}) {
        const calls = [];
        const component = Object.create(MrpDisplayRecord.prototype);
        const data = {
            quality_native_managed: managed,
            state: "progress",
            is_last_unfinished_wo: last,
            check_ids: { records: [] },
            employee_ids: { records: [] },
            all_employees_allowed: true,
        };
        const record = { resModel: "mrp.workorder", resId: 17, data };
        const production = { resId: 31, data: { quality_unit_route_required: managed } };
        Object.assign(component, {
            props: { record, production, groups: { workorders: true }, sessionOwner: { id: 42 } },
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
                async reload(value) {
                    calls.push(["reload", value.resId]);
                    await reload(value);
                },
                services: {
                    notification: { add: (...args) => calls.push(["notification", ...args]) },
                },
            },
        });
        return { component, data, calls, production };
    }

    QUnit.test("Mark as Done reloads after one RPC with or without a search model", async (assert) => {
        for (const [last, searchModel] of [[false, {}], [true, undefined]]) {
            const { component, data, calls } = card({ last });
            if (searchModel) {
                component.env.searchModel = searchModel;
            }
            data.check_ids.records.push({ data: { quality_state: "none", quality_shop_floor_deferred: true } });
            assert.strictEqual(component.displayDoneButton, true);
            await Promise.all([component.onClickValidateButton(), component.onClickValidateButton()]);
            assert.deepEqual(calls, [["mrp.workorder", "do_finish", [[17]]], ["reload", 31]]);
            assert.strictEqual(component.state.validated, true);
            data.state = "done";
            assert.strictEqual(component.displayDoneButton, false);
        }
    });

    QUnit.test("Refreshed work center and My WO show the ready successor and hide the finished card", async (assert) => {
        for (const activeWorkcenter of [11, -1]) {
            const next = { resId: 18, data: { state: "waiting", workcenter_id: [11], employee_assigned_ids: { resIds: [42] } } };
            const { component, data, production } = card({
                async reload() {
                    data.state = "done";
                    next.data.state = "ready";
                },
            });
            data.workcenter_id = [11];
            data.employee_assigned_ids = { resIds: [42] };
            const { record } = component.props;
            production.data.workorder_ids = { records: [record, next] };
            component.env.searchModel = {
                state: { workorderFilters: [
                    { name: "progress", isActive: true }, { name: "ready", isActive: true },
                ] },
                recordCache: { ids: [] },
            };
            const display = Object.assign(Object.create(MrpDisplay.prototype), {
                env: component.env,
                model: { root: { records: [production] } },
                state: { activeResModel: "mrp.workorder", activeWorkcenter },
                useEmployee: { employees: { admin: { id: 42 }, connected: [] } },
            });
            assert.deepEqual(display.relevantRecords.map((wo) => wo.resId), [17]);
            await component.onClickValidateButton();
            assert.deepEqual(display.filteredWorkorders.map((wo) => wo.resId), [18]);
            const visible = display.relevantRecords.filter((wo) => wo !== record || !component.cssClass.includes("d-none"));
            assert.deepEqual(visible.map((wo) => wo.resId), [18]);
        }
    });

    QUnit.test("Close Production has no managed RPC and ordinary buttons keep their rules", async (assert) => {
        const managed = card({ last: true });
        managed.component.state.underValidation = true;
        assert.strictEqual(managed.component.displayCloseProductionButton, false);
        await managed.component.onClickCloseProduction();
        managed.component.props.record = { resModel: "mrp.production", data: managed.production.data };
        assert.strictEqual(managed.component.displayDoneButton, false);
        await managed.component.validate();
        assert.deepEqual(managed.calls, []);

        const ordinary = card({ managed: false, last: true });
        ordinary.component.state.underValidation = true;
        assert.strictEqual(ordinary.component.displayCloseProductionButton, true);
        ordinary.data.check_ids.records.push({ data: { quality_state: "none", quality_shop_floor_deferred: true } });
        assert.strictEqual(ordinary.component.displayDoneButton, false);
        assert.strictEqual(ordinary.component.displayCloseProductionButton, false);
    });

    QUnit.test("Failed finish leaves the card retryable and refreshes the server state", async (assert) => {
        const error = new Error("Invalid operation");
        function isOriginalError(reason) {
            return reason === error;
        }
        let startReload, finishReload;
        const reloadStarted = new Promise((resolve) => { startReload = resolve; });
        const reloadPending = new Promise((resolve) => { finishReload = resolve; });
        const { component, calls } = card({
            failure: error,
            async reload() {
                startReload();
                await reloadPending;
                throw new Error("Could not refresh");
            },
        });
        const validation = assert.rejects(component.onClickValidateButton(), isOriginalError);
        await reloadStarted;
        assert.strictEqual(component.qualityFinishing, true);
        await component.onClickValidateButton();
        finishReload();
        await validation;
        assert.deepEqual(calls, [["mrp.workorder", "do_finish", [[17]]], ["reload", 31]]);
        assert.strictEqual(component.state.validated, false);
        assert.strictEqual(component.displayDoneButton, true);
        assert.strictEqual(component.qualityFinishing, false);

        const refreshed = card({ failure: error });
        await assert.rejects(refreshed.component.onClickValidateButton(), isOriginalError);
        assert.deepEqual(refreshed.calls, [["mrp.workorder", "do_finish", [[17]]], ["reload", 31]]);
        assert.strictEqual(refreshed.component.state.validated, false);
    });

    QUnit.test("Failed reload after success warns and permits a read without repeating do_finish", async (assert) => {
        let reloadFails = true;
        const { component, calls, data, production } = card({
            async reload() {
                if (reloadFails) {
                    throw new Error("Could not refresh");
                }
                data.state = "done";
            },
        });
        await component.onClickValidateButton();
        assert.strictEqual(component.state.validated, true);
        assert.strictEqual(component.qualityFinishing, false);
        assert.strictEqual(component.cssClass.includes("d-none"), true);
        await component.onClickValidateButton();
        reloadFails = false;
        await component.env.reload(production);
        assert.strictEqual(component.displayDoneButton, false);
        assert.deepEqual(calls, [
            ["mrp.workorder", "do_finish", [[17]]], ["reload", 31],
            ["notification", "Операцію завершено. Оновіть сторінку, щоб побачити зміни.", { type: "warning" }],
            ["reload", 31],
        ]);
    });

    QUnit.test("An ordinary WO still runs the native precheck and deferred validation", async (assert) => {
        const { component, calls } = card({ managed: false });
        component.props.record.save = async () => calls.push(["save"]);
        let validate;
        component.props.addToValidationStack = (_record, callback) => { validate = callback; };
        component.props.removeFromValidationStack = async () => calls.push(["unstack"]);
        await component.validate();
        assert.strictEqual(component.state.underValidation, true);
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
