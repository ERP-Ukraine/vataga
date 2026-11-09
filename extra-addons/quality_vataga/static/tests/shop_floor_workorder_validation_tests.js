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
        assert.true(card.cssClass.includes("o_fadeout_animation"));
        card.onAnimationEnd({ animationName: "fadeout" });
        const refresh = card.realValidation();
        card.onClickValidateButton();
        await card.onClickCloseProduction();
        assert.deepEqual(calls, [["mrp.workorder", "do_finish", [17]]]);
        assert.false(card.state.validated);
        resolveFinish();
        await refresh;
        assert.true(card.cssClass.includes("d-none"));
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
        assert.false(card.cssClass.includes("o_fadeout_animation"));
        assert.false(card.cssClass.includes("d-none"));
        assert.deepEqual(calls, [["mrp.workorder", "do_finish", [17]], ["remove", 17, false]]);

        fail = false;
        card.state.underValidation = true;
        await card.realValidation();
        assert.true(card.state.validated);
    });
});
