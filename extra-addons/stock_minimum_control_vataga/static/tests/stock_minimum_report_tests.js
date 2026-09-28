/** @odoo-module **/

import { stockMinimumClass } from "@stock_minimum_control_vataga/stock_minimum_colors";
import { StockMinimumReport } from "@stock_minimum_control_vataga/stock_minimum_report";
import { makeTestEnv } from "@web/../tests/helpers/mock_env";
import { click, getFixture, mount } from "@web/../tests/helpers/utils";

QUnit.module("stock_minimum_control_vataga");

QUnit.test("inclusive thresholds, fractions and zero minimum", (assert) => {
    for (const [quantity, minimum, color] of [
        [99.9, 100, "red"], [100, 100, "yellow"], [105, 100, "yellow"],
        [110, 100, "yellow"], [110.1, 100, "green"], [10.5, 10, "yellow"],
        [-0.5, 0, "red"], [0, 0, "yellow"], [0.5, 0, "green"],
        [-1e-16, 0, "red"], [1e-16, 0, "green"],
        [0.11, 0.1, "yellow"], [0.111, 0.1, "green"],
    ]) {
        assert.strictEqual(stockMinimumClass(quantity, minimum), `o_stock_minimum_control_${color}`,
            `${quantity} / ${minimum}`);
    }
});

QUnit.test("only product on-hand cells are colored", (assert) => {
    const column = { key: "w1" };
    const row = { kind: "product", minimum: 100, values: { w1: [6, 5, 110] } };
    const classify = StockMinimumReport.prototype.cellClass;
    assert.strictEqual(classify(row, column, 0), "o_stock_minimum_control_red");
    assert.strictEqual(classify(row, column, 1), "");
    assert.strictEqual(classify(row, column, 2), "");
    for (const kind of ["category", "total"]) {
        assert.strictEqual(classify({ ...row, kind }, column, 0), "");
    }
});

QUnit.test("rows expand and locations replace warehouse without duplicate totals", async (assert) => {
    const env = await makeTestEnv({
        mockRPC(route, args) {
            if (args.method === "get_report") {
                const expanded = args.kwargs.expanded_warehouses.includes(1);
                const values = expanded ? { l2: [4, 4, 4], l3: [8, 8, 8] } : { w1: [12, 12, 12] };
                return {
                    warehouses: [{ id: 1, name: "MH", can_expand: true, expanded,
                        columns: expanded ? [{ key: "l2", name: "MH/Stock" }, { key: "l3", name: "MH/Stock/Child" }]
                            : [{ key: "w1", name: "MH" }] }],
                    categories: [{ id: 5, name: "Components", count: 1, page: 0, product_ids: [11], values }],
                    products: { 11: { id: 11, name: "[A] Product", minimum: 10, uom: "Units", values } },
                    totals: values, count: 1, page_size: 80, digits: 2,
                };
            }
        },
    });
    const target = getFixture();
    await mount(StockMinimumReport, target, { env });
    assert.containsN(target, "tbody tr", 1);
    await click(target, "tbody button");
    assert.containsN(target, "tbody tr", 2);
    await click(target, "tbody tr:nth-child(2) button");
    assert.containsN(target, "tbody tr", 3);
    assert.containsOnce(target, "td.o_stock_minimum_control_green");
    assert.containsNone(target, ".o_smc_minimum.o_stock_minimum_control_green");
    assert.strictEqual(target.querySelector("tbody tr:first-child .o_smc_minimum").textContent, "");
    await click(target, "thead button");
    assert.containsN(target, "tbody tr:last-child .o_pivot_cell_value", 6);
    assert.containsN(target, "td.o_stock_minimum_control_red", 2);
    assert.strictEqual(target.querySelector("tbody tr:last-child .o_smc_minimum").textContent, "10.00");
    await click(target, "thead button");
    assert.containsN(target, "tbody tr:last-child .o_pivot_cell_value", 3);
    assert.containsOnce(target, "td.o_stock_minimum_control_green");
});
