/** @odoo-module **/

import { MrpDisplayRecord } from "@mrp_workorder/mrp_display/mrp_display_record";
import "@quality_vataga/quality_quarantine_shop_floor";

QUnit.module("quality_vataga Shop Floor work order validation", () => {
    function makeCard(finish) {
        const card = Object.create(MrpDisplayRecord.prototype);
        const record = {
            resId: 17,
            resModel: "mrp.workorder",
            data: { is_last_unfinished_wo: true },
        };
        const production = { resId: 31, data: { product_qty: 4 } };
        const calls = [];
        Object.defineProperties(card, {
            active: { value: false },
            disabled: { value: false },
            trackingMode: { value: "lot" },
        });
        Object.assign(card, {
            record: record.data,
            resModel: record.resModel,
            state: { underValidation: true, validated: false },
            props: {
                record,
                production,
                async removeFromValidationStack(record, validated = true) {
                    calls.push(["remove", record.resId, validated]);
                },
            },
            env: {
                async reload(record) {
                    calls.push(["reload", record.resId]);
                },
            },
            model: {
                orm: {
                    async call(model, method, args) {
                        calls.push([model, method, args]);
                        await finish();
                    },
                },
            },
        });
        return { card, calls };
    }

    QUnit.test("the last card validates once when the fade and refresh overlap", async (assert) => {
        let resolveFinish;
        const pending = new Promise((resolve) => { resolveFinish = resolve; });
        const { card, calls } = makeCard(() => pending);
        assert.strictEqual(card.cssClass.includes("o_fadeout_animation"), true);
        card.onAnimationEnd({ animationName: "fadeout" });
        const refresh = card.realValidation();
        card.onClickValidateButton();
        await card.onClickCloseProduction();
        assert.deepEqual(calls, [["mrp.workorder", "do_finish", [17]]]);
        assert.strictEqual(card.state.validated, false);
        resolveFinish();
        await refresh;
        assert.strictEqual(card.cssClass.includes("d-none"), true);
        assert.deepEqual(calls, [
            ["mrp.workorder", "do_finish", [17]], ["remove", 17, true], ["reload", 31],
        ]);
    });

    QUnit.test("a failed finish restores the card and permits a retry", async (assert) => {
        let fail = true;
        const { card, calls } = makeCard(async () => {
            if (fail) {
                throw new Error("Finish rejected by server");
            }
        });
        await assert.rejects(card.realValidation(), /Finish rejected by server/);
        assert.strictEqual(card.cssClass.includes("o_fadeout_animation"), false);
        assert.strictEqual(card.cssClass.includes("d-none"), false);
        assert.deepEqual(calls, [["mrp.workorder", "do_finish", [17]], ["remove", 17, false]]);

        fail = false;
        card.state.underValidation = true;
        await card.realValidation();
        assert.strictEqual(card.state.validated, true);
    });

    QUnit.test("a last operation waits for explicit production registration", async (assert) => {
        const { card, calls } = makeCard(async () => {});
        const { production, record } = card.props;
        production.data.product_id = [8, "Product"];
        production.data.quality_shop_floor_registration_required = true;
        record.data.employee_ids = { records: [] };
        record.save = async () => {};
        card.props.sessionOwner = { id: 4 };
        card.props.addToValidationStack = () => calls.push(["queue"]);
        card.state.underValidation = false;
        const dialogs = [];
        card.dialog = { add: (_Dialog, props, options) => dialogs.push({ props, options }) };

        await card.validate();
        await card.validate();
        assert.strictEqual(dialogs.length, 1);
        assert.deepEqual(calls, []);
        dialogs[0].options.onClose();
        assert.deepEqual(calls, [], "cancelling does not queue the operation");

        await card.validate();
        await dialogs[1].props.onRegistered();
        assert.deepEqual(calls, [], "an unconfirmed quantity does not queue the operation");
        production.data.quality_shop_floor_registration_required = false;
        await dialogs[1].props.onRegistered();
        assert.deepEqual(calls, [["mrp.workorder", "pre_record_production", [17]], ["queue"]]);
        assert.strictEqual(card.state.underValidation, true);
    });

    QUnit.test("creating a lot opens the registration dialog for a controlled order", (assert) => {
        const { card, calls } = makeCard(async () => {});
        card.props.production.data.quality_shop_floor_registration_required = true;
        card.registerProduction = () => calls.push(["registration dialog"]);
        card.generateSerialNumber();
        assert.deepEqual(calls, [["registration dialog"]]);
    });
});
