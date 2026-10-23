/** @odoo-module **/

import { Component, EventBus, markRaw, reactive, useState, xml } from "@odoo/owl";
import { QualityMeasurementMatrix } from "@quality_vataga/components/measurement_matrix/measurement_matrix";
import { QualityUnitAdmissionButtons, QualityUnitTransferWorkorderButtons, QUALITY_CHECK_MATRIX_REFRESH } from "@quality_vataga/components/measurement_matrix/admission_buttons";
import { click, findChildren, getFixture, makeDeferred, mount, nextTick } from "@web/../tests/helpers/utils";

// Real OWL components and useRecordObserver, with deterministic server replies.
// This deliberately reloads the SAME record/JSON content: comparing resId alone
// cannot pass these tests. Dialog callbacks follow action_service's infos API.
QUnit.module("quality_vataga matrix refresh", () => {
    class CheckForm extends Component {
        static components = { QualityMeasurementMatrix, QualityUnitAdmissionButtons };
        static props = ["record"];
        static template = xml`
            <div>
                <QualityUnitAdmissionButtons record="state.record"/>
                <span class="batch" t-esc="state.record.data.in_process_sample_quantity"/>
                <span class="passed" t-esc="state.record.data.x_passed_qty"/>
                <span class="failed" t-esc="state.record.data.x_failed_qty"/>
                <span class="pending" t-esc="state.record.data.x_pending_qty"/>
                <span class="admitted" t-esc="state.record.data.x_admitted_qty"/>
                <span class="held" t-esc="state.record.data.x_held_qty"/>
                <span class="transferred" t-esc="state.record.data.transferred"/>
                <span class="received" t-esc="state.record.data.received"/>
                <span class="awaiting" t-esc="state.record.data.awaiting"/>
                <span class="note" t-esc="state.record.data.note"/>
                <span class="history" t-esc="state.record.data.history.length"/>
                <span class="chatter" t-esc="state.record.data.messages.length"/>
                <QualityMeasurementMatrix record="state.record" name="'measurement_matrix_data'" readonly="true"/>
            </div>`;
        setup() {
            this.state = useState({ record: this.props.record });
        }
    }

    class WorkorderForm extends CheckForm {
        static components = { QualityUnitTransferWorkorderButtons };
        static template = xml`
            <div>
                <QualityUnitTransferWorkorderButtons record="state.record"/>
                <span class="transferred" t-esc="state.record.data.transferred"/>
                <span class="received" t-esc="state.record.data.received"/>
                <span class="awaiting" t-esc="state.record.data.awaiting"/>
                <span class="note" t-esc="state.record.data.note"/>
                <span t-foreach="state.record.data.receipts" t-as="unit" t-key="unit" class="received-unit" t-esc="unit"/>
            </div>`;
    }

    async function settle() {
        await nextTick();
        await nextTick();
    }

    async function fixture({ approved = [], sent = [], received = [], model = "quality.check", unformed = false, staleQuantity = false, dirty = false, stage = "in_process" } = {}) {
        const target = getFixture();
        const calls = [];
        const servers = new Map();
        const bus = new EventBus();
        let closeCallback;
        let openedAction;
        let dialogOpen = false;
        let rootLoads = 0;
        let lateRead;

        function server(id) {
            if (!servers.has(id)) {
                servers.set(id, {
                    formed: !unformed, approved: new Set(approved), history: [], messages: [], note: "saved",
                    sent: new Set(sent), received: new Set(received), returned: new Set(),
                });
            }
            return servers.get(id);
        }
        function matrix(id) {
            const source = server(id);
            return {
                check_id: id, control_stage: stage, editable: true, columns: [],
                batch_quantity: source.formed ? 5 : 0, batch_error: false,
                equipment_complete: true, is_complete: false, has_failure: true,
                samples: source.formed ? Array.from({ length: 5 }, (_, i) => ({
                    id: id * 10 + i, sample_number: i + 1, display_name: `Зразок №${i + 1}`,
                    controlled_unit_name: `QUNIT/${id}/${i + 1}`, visual_result: i === 4 ? "no" : "yes",
                    sample_result: i === 4 ? "fail" : "pass", cells: [],
                    unit_admission_label: source.approved.has(i) ? "Допущено" : "Утримано",
                    unit_admission_reason: source.approved.has(i) ? "" : "Потрібне рішення інспектора",
                    unit_transfer_label: source.received.has(i) ? "Прийнято наступною операцією" : source.sent.has(i) ? "Передано" : source.returned.has(i) ? "Повернено" : "",
                })) : [],
            };
        }
        function formData(id) {
            const source = server(id);
            return {
                measurement_matrix_data: { quality_check_id: id },
                in_process_sample_quantity: source.formed ? 5 : 0,
                x_passed_qty: source.formed ? 4 : 0, x_failed_qty: source.formed ? 1 : 0, x_pending_qty: 0,
                x_admitted_qty: source.approved.size, x_held_qty: source.formed ? 5 - source.approved.size : 0,
                history: [...source.history], messages: [...source.messages], note: source.note,
                transferred: source.sent.size, received: source.received.size,
                awaiting: source.approved.size - source.sent.size, quality_transfer_has_run: true,
                receipts: [...source.received].map((i) => `QUNIT/${id}/${i + 1}`),
            };
        }
        const orm = {
            async call(rpcModel, method, args) {
                calls.push({ model: rpcModel, method, args });
                const id = args[0][0];
                if (method === "get_measurement_matrix_data") {
                    if (lateRead?.id === id) {
                        const read = lateRead;
                        lateRead = null;
                        return read.deferred;
                    }
                    return matrix(id);
                }
                if (method === "initialize_in_process_measurement_matrix") {
                    server(id).formed = true;
                    return matrix(id);
                }
                if (method === "web_read") {
                    return formData(id);
                }
                if (method === "write") {
                    server(id).note = args[1].note;
                    return true;
                }
                if (method.startsWith("action_open_unit_admission")) {
                    return {
                        type: "ir.actions.act_window", res_model: "quality.unit.admission.wizard",
                        target: "new", view_mode: "form", views: [[123, "form"]],
                        context: {
                            default_check_id: id,
                            default_mode: method === "action_open_unit_admission_revocation_wizard" ? "revoke" : "confirm",
                        },
                    };
                }
                if (method.includes("unit_transfer_wizard") || method.includes("unit_receipt_wizard") || method.includes("unit_return_wizard")) {
                    return {
                        type: "ir.actions.act_window", res_model: "quality.unit.transfer.wizard",
                        target: "new", views: [[456, "form"]], view_mode: "form",
                    };
                }
                if (method === "action_apply") {
                    const [, mode, units, fail] = args;
                    if (fail) {
                        throw new Error("Admission refused");
                    }
                    for (const unit of units) {
                        const source = server(id);
                        if (mode === "send") {
                            source.sent.add(unit);
                        } else if (mode === "receive") {
                            source.received.add(unit);
                        } else if (mode === "return") {
                            source.sent.delete(unit);
                            source.received.delete(unit);
                            source.returned.add(unit);
                        } else {
                            mode === "revoke" ? source.approved.delete(unit) : source.approved.add(unit);
                        }
                    }
                    server(id).history.push(mode);
                    server(id).messages.push(mode);
                    return {
                        type: "ir.actions.act_window_close",
                        infos: model === "mrp.workorder" ? { quality_vataga_workorder_id: id } : { quality_vataga_check_id: id },
                    };
                }
                throw new Error(`Unexpected RPC: ${rpcModel}.${method}`);
            },
        };
        function record(id) {
            const rec = reactive({
                id: `record_${id}`, resId: id, resModel: model, context: {}, dirty: false,
                data: formData(id),
                async isDirty() { return this.dirty; },
                async load() { this.data = await orm.call(this.resModel, "web_read", [[id]]); },
                async save() {
                    await orm.call(this.resModel, "write", [[id], { note: this.data.note }]);
                    this.dirty = false;
                    await this.load();
                    return true;
                },
            });
            rec.model = markRaw({
                root: rec,
                async load() {
                    rootLoads++;
                    await rec.load();
                    bus.trigger("MAIL:RELOAD-THREAD", { model: rec.resModel, id });
                },
            });
            return rec;
        }
        const first = record(1);
        if (staleQuantity) {
            first.data.in_process_sample_quantity = 0;
        }
        if (dirty) {
            first.data.note = "unsaved";
            first.dirty = true;
        }
        const env = {
            bus,
            services: {
                orm, notification: { add() {} }, ui: { block() {}, unblock() {} },
                action: {
                    async doAction(action, options) {
                        // Odoo 17 preprocesses direct actions via views.map().
                        openedAction = { ...action, views: action.views.map((view) => [...view]) };
                        dialogOpen = true;
                        closeCallback = options.onClose;
                    },
                },
            },
        };
        const form = await mount(model === "mrp.workorder" ? WorkorderForm : CheckForm, target, { env, props: { record: first } });
        await settle();
        return {
            target, form, first, bus, calls, server, matrix, record,
            get rootLoads() { return rootLoads; },
            get dialogOpen() { return dialogOpen; },
            get openedAction() { return openedAction; },
            async open(mode = "confirm") {
                const selector = {
                    confirm: ".o_quality_confirm_admission", revoke: ".o_quality_revoke_admission",
                    transfer: ".o_quality_send_units", send: ".o_quality_send_units",
                    receive: ".o_quality_receive_units", return: ".o_quality_return_units",
                }[mode];
                await click(target, selector);
            },
            async apply(mode = "confirm", units = [0, 1, 2, 3], fail = false) {
                const result = await orm.call("quality.unit.admission.wizard", "action_apply", [[1], mode, units, fail]);
                dialogOpen = false;
                await closeCallback(result.infos);
                await settle();
            },
            async cancel(infos) { dialogOpen = false; await closeCallback(infos); await settle(); },
            deferRead(id) { const deferred = makeDeferred(); lateRead = { id, deferred }; return deferred; },
            matrixComponent() { return findChildren(form, (node) => node.component instanceof QualityMeasurementMatrix).component; },
        };
    }

    function text(ctx, selector) { return ctx.target.querySelector(selector).textContent.trim(); }
    function labels(ctx) { return [...ctx.target.querySelectorAll(".o_quality_unit_admission")].map((node) => node.textContent.trim()); }
    function rpcCount(ctx, method) { return ctx.calls.filter((call) => call.method === method).length; }

    QUnit.test("confirm 4 PASS units updates same-record matrix, counters, history and chatter", async (assert) => {
        const ctx = await fixture();
        let mailReloads = 0;
        ctx.bus.addEventListener("MAIL:RELOAD-THREAD", () => mailReloads++);
        await ctx.open();
        await ctx.apply();
        assert.notOk(ctx.dialogOpen);
        assert.strictEqual(ctx.first.resId, 1);
        assert.strictEqual(text(ctx, ".admitted"), "4");
        assert.strictEqual(text(ctx, ".held"), "1");
        assert.deepEqual(labels(ctx), ["Допущено", "Допущено", "Допущено", "Допущено", "Утримано"]);
        assert.strictEqual(text(ctx, ".passed"), "4");
        assert.strictEqual(text(ctx, ".failed"), "1");
        assert.strictEqual(text(ctx, ".pending"), "0");
        assert.strictEqual(text(ctx, ".history"), "1");
        assert.strictEqual(text(ctx, ".chatter"), "1");
        assert.strictEqual(mailReloads, 1);
    });

    QUnit.test("revocation changes 4/1 to 3/2 and only the selected QUNIT", async (assert) => {
        const ctx = await fixture({ approved: [0, 1, 2, 3] });
        await ctx.open("revoke");
        await ctx.apply("revoke", [0]);
        assert.strictEqual(text(ctx, ".admitted"), "3");
        assert.strictEqual(text(ctx, ".held"), "2");
        assert.deepEqual(labels(ctx), ["Утримано", "Допущено", "Допущено", "Допущено", "Утримано"]);
        assert.strictEqual(ctx.target.querySelector(".o_quality_unit_admission").title, "Потрібне рішення інспектора");
    });

    QUnit.test("explicit reconfirmation changes 3/2 back to 4/1", async (assert) => {
        const ctx = await fixture({ approved: [1, 2, 3] });
        await ctx.open();
        await ctx.apply("confirm", [0]);
        assert.strictEqual(text(ctx, ".admitted"), "4");
        assert.strictEqual(text(ctx, ".held"), "1");
        assert.strictEqual(labels(ctx)[0], "Допущено");
    });

    for (const mode of ["confirm", "revoke"]) {
        QUnit.test(`${mode} opens an explicit modal form; cancel and X do not refresh`, async (assert) => {
            const ctx = await fixture();
            const reads = rpcCount(ctx, "get_measurement_matrix_data");
            for (const infos of [undefined, { special: true }]) {
                await ctx.open(mode);
                assert.ok(ctx.dialogOpen);
                assert.strictEqual(ctx.openedAction.res_model, "quality.unit.admission.wizard");
                assert.strictEqual(ctx.openedAction.target, "new");
                assert.strictEqual(ctx.openedAction.view_mode, "form");
                assert.deepEqual(ctx.openedAction.views, [[123, "form"]]);
                assert.deepEqual(ctx.openedAction.context, { default_check_id: 1, default_mode: mode });
                await ctx.cancel(infos);
            }
            assert.strictEqual(ctx.rootLoads, 0);
            assert.strictEqual(rpcCount(ctx, "web_read"), 0);
            assert.strictEqual(rpcCount(ctx, "get_measurement_matrix_data"), reads);
        });
    }

    for (const mode of ["confirm", "revoke"]) {
        QUnit.test(`${mode} error leaves dialog and unconfirmed UI unchanged`, async (assert) => {
            const ctx = await fixture({ approved: mode === "revoke" ? [0, 1, 2, 3] : [] });
            const before = labels(ctx);
            await ctx.open(mode);
            try {
                await ctx.apply(mode, [0], true);
                assert.ok(false, "RPC must reject");
            } catch (error) {
                assert.strictEqual(error.message, "Admission refused");
            }
            assert.ok(ctx.dialogOpen);
            assert.deepEqual(labels(ctx), before);
            assert.strictEqual(ctx.rootLoads, 0);
        });
    }

    QUnit.test("initial generation synchronizes frozen quantity immediately without a loop", async (assert) => {
        const ctx = await fixture({ unformed: true });
        assert.strictEqual(text(ctx, ".batch"), "5");
        assert.strictEqual(ctx.target.querySelectorAll("tbody tr").length, 5);
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 1);
        assert.strictEqual(rpcCount(ctx, "web_read"), 1);
        await settle();
        assert.strictEqual(rpcCount(ctx, "web_read"), 1);
    });

    QUnit.test("already formed rows with stale form quantity synchronize without initialization", async (assert) => {
        const ctx = await fixture({ staleQuantity: true });
        assert.strictEqual(text(ctx, ".batch"), "5");
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 0);
        assert.strictEqual(rpcCount(ctx, "web_read"), 1);
    });

    QUnit.test("reloading the same check never reinitializes a formed batch", async (assert) => {
        const ctx = await fixture();
        await ctx.first.model.load();
        await settle();
        await ctx.first.model.load();
        await settle();
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 0);
        assert.strictEqual(ctx.target.querySelectorAll("tbody tr").length, 5);
        assert.strictEqual(rpcCount(ctx, "get_measurement_matrix_data"), 3);
    });

    QUnit.test("dirty initial form defers generation and retains edits until saved", async (assert) => {
        const ctx = await fixture({ unformed: true, dirty: true });
        assert.strictEqual(text(ctx, ".note"), "unsaved");
        assert.strictEqual(rpcCount(ctx, "web_read"), 0);
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 0);
        await ctx.first.save();
        await settle();
        assert.strictEqual(text(ctx, ".note"), "unsaved");
        assert.strictEqual(text(ctx, ".batch"), "5");
        assert.strictEqual(ctx.server(1).note, "unsaved");
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 1);
    });

    QUnit.test("edits made while wizard is open survive; form sync follows save", async (assert) => {
        const ctx = await fixture();
        await ctx.open();
        ctx.first.data.note = "local note";
        ctx.first.dirty = true;
        await ctx.apply();
        assert.strictEqual(text(ctx, ".note"), "local note");
        assert.strictEqual(ctx.rootLoads, 0);
        assert.strictEqual(labels(ctx)[0], "Допущено");
        await ctx.first.save();
        await settle();
        assert.strictEqual(text(ctx, ".note"), "local note");
        assert.strictEqual(text(ctx, ".admitted"), "4");
        assert.strictEqual(text(ctx, ".held"), "1");
        assert.strictEqual(ctx.rootLoads, 1);
    });

    QUnit.test("closing old wizard or another check's refresh never changes current check", async (assert) => {
        const ctx = await fixture();
        await ctx.open();
        const other = ctx.record(2);
        ctx.form.state.record = other;
        await settle();
        const reads = rpcCount(ctx, "get_measurement_matrix_data");
        ctx.bus.trigger(QUALITY_CHECK_MATRIX_REFRESH, { checkId: 1 });
        await ctx.apply();
        assert.strictEqual(ctx.rootLoads, 0);
        assert.strictEqual(rpcCount(ctx, "get_measurement_matrix_data"), reads);
        assert.strictEqual(text(ctx, ".admitted"), "0");
        assert.ok(ctx.target.querySelector(".o_quality_controlled_unit").textContent.includes("QUNIT/2/"));
    });

    QUnit.test("late matrix RPC for previous check cannot overwrite current check", async (assert) => {
        const ctx = await fixture();
        const oldData = ctx.matrix(1);
        const deferred = ctx.deferRead(1);
        const pending = ctx.matrixComponent().load(ctx.first);
        ctx.form.state.record = ctx.record(2);
        await settle();
        deferred.resolve(oldData);
        await pending;
        await settle();
        assert.ok(ctx.target.querySelector(".o_quality_controlled_unit").textContent.includes("QUNIT/2/"));
        assert.strictEqual(text(ctx, ".admitted"), "0");
    });

    QUnit.test("incoming control keeps manual controls and does not initialize a batch", async (assert) => {
        const ctx = await fixture({ stage: "incoming" });
        assert.strictEqual(ctx.target.querySelectorAll(".o_quality_measurement_sample_controls").length, 1);
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 0);
        assert.strictEqual(rpcCount(ctx, "web_read"), 0);
    });

    QUnit.test("existing explicit refresh still synchronizes the form without reinitializing", async (assert) => {
        const ctx = await fixture();
        ctx.server(1).approved.add(0);
        await ctx.matrixComponent().refreshInProcessMatrix();
        await settle();
        assert.strictEqual(text(ctx, ".admitted"), "1");
        assert.strictEqual(text(ctx, ".held"), "4");
        assert.strictEqual(labels(ctx)[0], "Допущено");
        assert.strictEqual(rpcCount(ctx, "initialize_in_process_measurement_matrix"), 0);
    });

    QUnit.test("sending four QUNIT refreshes QC counters, matrix statuses and audit without F5", async (assert) => {
        const ctx = await fixture({ approved: [0, 1, 2, 3] });
        await ctx.open("transfer");
        assert.strictEqual(ctx.openedAction.res_model, "quality.unit.transfer.wizard");
        await ctx.apply("send");
        assert.strictEqual(text(ctx, ".transferred"), "4");
        assert.strictEqual(text(ctx, ".received"), "0");
        assert.strictEqual(text(ctx, ".awaiting"), "0");
        assert.strictEqual(text(ctx, ".held"), "1");
        assert.strictEqual(ctx.target.querySelectorAll(".o_quality_unit_transfer").length, 4);
        assert.ok([...ctx.target.querySelectorAll(".o_quality_unit_transfer")].every((node) => node.textContent.trim() === "Передано"));
        assert.strictEqual(text(ctx, ".history"), "1");
        assert.strictEqual(text(ctx, ".chatter"), "1");
    });

    QUnit.test("workorder receipt refreshes exact QUNIT and counters without affecting QC bus", async (assert) => {
        const ctx = await fixture({ model: "mrp.workorder", approved: [0, 1, 2, 3], sent: [0, 1, 2, 3] });
        let qcEvents = 0;
        ctx.bus.addEventListener(QUALITY_CHECK_MATRIX_REFRESH, () => qcEvents++);
        await ctx.open("receive");
        await ctx.apply("receive");
        assert.strictEqual(text(ctx, ".received"), "4");
        assert.deepEqual([...ctx.target.querySelectorAll(".received-unit")].map((node) => node.textContent), ["QUNIT/1/1", "QUNIT/1/2", "QUNIT/1/3", "QUNIT/1/4"]);
        assert.strictEqual(ctx.rootLoads, 1);
        assert.strictEqual(qcEvents, 0);
        assert.ok(ctx.calls.some((call) => call.model === "mrp.workorder" && call.method === "action_open_quality_unit_receipt_wizard"));
    });

    QUnit.test("return refreshes workorder counts; dirty form is preserved until save", async (assert) => {
        const ctx = await fixture({ model: "mrp.workorder", approved: [0, 1, 2, 3], sent: [0, 1, 2, 3], received: [0, 1, 2, 3] });
        await ctx.open("return");
        ctx.first.data.note = "unsaved transfer note";
        ctx.first.dirty = true;
        await ctx.apply("return", [0]);
        assert.strictEqual(ctx.rootLoads, 0);
        assert.strictEqual(text(ctx, ".note"), "unsaved transfer note");
        await ctx.first.save();
        await settle();
        assert.strictEqual(text(ctx, ".transferred"), "3");
        assert.strictEqual(text(ctx, ".received"), "3");
        assert.strictEqual(text(ctx, ".awaiting"), "1");
        assert.strictEqual(text(ctx, ".note"), "unsaved transfer note");
    });

    QUnit.test("transfer wizard cancel and failed receipt never reload or change confirmed data", async (assert) => {
        const ctx = await fixture({ model: "mrp.workorder", approved: [0], sent: [0] });
        for (const mode of ["send", "receive", "return"]) {
            await ctx.open(mode);
            await ctx.cancel({ special: true });
        }
        await ctx.open("receive");
        try {
            await ctx.apply("receive", [0], true);
            assert.ok(false, "RPC must reject");
        } catch (error) {
            assert.strictEqual(error.message, "Admission refused");
        }
        assert.strictEqual(text(ctx, ".received"), "0");
        assert.strictEqual(ctx.rootLoads, 0);
        assert.ok(ctx.dialogOpen);
    });

    QUnit.test("receipt from a previous workorder cannot refresh another workorder", async (assert) => {
        const ctx = await fixture({ model: "mrp.workorder", sent: [0] });
        await ctx.open("receive");
        ctx.form.state.record = ctx.record(2);
        await settle();
        await ctx.apply("receive", [0]);
        assert.strictEqual(ctx.rootLoads, 0);
        assert.strictEqual(text(ctx, ".received"), "0");
    });
});
