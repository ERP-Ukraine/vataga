/** @odoo-module **/

import { getClosedCellColor } from "@sale_demand_vataga/views/pivot/pivot_renderer";

QUnit.module("sale_demand_vataga", () => {
    QUnit.test("closed color follows the displayed percentage", (assert) => {
        assert.strictEqual(getClosedCellColor(0.7), "#d9bfc7", "70% is pink");
        assert.strictEqual(getClosedCellColor(0.85), "#e4daa8", "85% is yellow");
        assert.strictEqual(
            getClosedCellColor(0.999966),
            "#d9ead3",
            "a value displayed as 100% is light green"
        );
        assert.strictEqual(getClosedCellColor(1), "#d9ead3", "100% is light green");
        assert.strictEqual(
            getClosedCellColor(1.01),
            "#779bb5",
            "values above 100% remain blue"
        );
    });
});
