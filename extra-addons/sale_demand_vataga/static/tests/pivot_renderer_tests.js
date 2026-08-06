/** @odoo-module **/

import {
    getClosedCellClass,
    getClosedCellColor,
    getPivotCellClasses,
} from "@sale_demand_vataga/views/pivot/pivot_renderer";

QUnit.module("sale_demand_vataga", () => {
    QUnit.test("closed color follows the displayed percentage", (assert) => {
        assert.strictEqual(
            getClosedCellColor(0),
            null,
            "0% keeps the standard pivot background"
        );
        assert.strictEqual(getClosedCellColor(0.7), "#d9bfc7", "70% is pink");
        assert.strictEqual(getClosedCellColor(0.85), "#e4daa8", "85% is yellow");
        assert.strictEqual(
            getClosedCellColor(0.999966),
            "#71a064",
            "a value displayed as 100% uses the original green"
        );
        assert.strictEqual(
            getClosedCellColor(1),
            "#71a064",
            "100% uses the original green"
        );
        assert.strictEqual(
            getClosedCellColor(1.01),
            "#779bb5",
            "values above 100% remain blue"
        );
    });

    QUnit.test("closed color classes match the thresholds", (assert) => {
        assert.strictEqual(getClosedCellClass(0), "");
        assert.strictEqual(getClosedCellClass(0.01), "closed-low");
        assert.strictEqual(getClosedCellClass(0.7), "closed-low");
        assert.strictEqual(getClosedCellClass(0.85), "closed-medium");
        assert.strictEqual(getClosedCellClass(0.999966), "closed-complete");
        assert.strictEqual(getClosedCellClass(1), "closed-complete");
        assert.strictEqual(getClosedCellClass(1.01), "closed-over");
    });

    QUnit.test("custom classes are added only to closed cells", (assert) => {
        const commonCell = {
            measure: "demand",
            value: 1,
            isBold: false,
        };
        const closedCell = {
            ...commonCell,
            measure: "closed",
        };
        const zeroClosedCell = {
            ...closedCell,
            value: 0,
        };

        assert.notOk(
            Object.keys(getPivotCellClasses(commonCell)).some((className) =>
                className.startsWith("closed-")
            )
        );
        assert.ok(getPivotCellClasses(closedCell)["closed-complete"]);
        assert.notOk(
            Object.keys(getPivotCellClasses(zeroClosedCell)).some((className) =>
                className.startsWith("closed-")
            ),
            "a 0% closed cell has no custom background class"
        );
    });
});
