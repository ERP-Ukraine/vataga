/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { StatusBarField } from "@web/views/fields/statusbar/statusbar_field";
import { QualityBoardStatusBar } from "@quality_vataga/fields/board_statusbar";

QUnit.module("quality_vataga board statusbar");

QUnit.test("click cannot update or save the client record", async function (assert) {
    let calls = 0;
    await QualityBoardStatusBar.prototype.selectItem.call({
        props: { record: { update() { calls++; }, save() { calls++; } } },
    }, { value: 3 });
    assert.strictEqual(calls, 0);
});

QUnit.test("current legacy stage is not offered outside the board domain", function (assert) {
    // Synthetic IDs test client filtering; production IDs come from XML refs.
    const unpatch = patch(StatusBarField.prototype, {
        getAllItems() {
            return [1, 2, 3, 4].map((value) => ({ value, isSelected: value === 4 }));
        },
    });
    try {
        const items = QualityBoardStatusBar.prototype.getAllItems.call({
            props: { domain: [["id", "in", [1, 2, 3]]] },
        });
        assert.deepEqual(items.map((item) => item.value), [1, 2, 3]);
    } finally {
        unpatch();
    }
});
