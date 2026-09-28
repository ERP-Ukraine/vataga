/** @odoo-module **/

import { stockMinimumClass } from "@stock_minimum_control_vataga/stock_minimum_colors";
import { StockMinimumReport } from "@stock_minimum_control_vataga/stock_minimum_report";
import { makeTestEnv } from "@web/../tests/helpers/mock_env";
import { registry } from "@web/core/registry";
import { uiService } from "@web/core/ui/ui_service";
import { hotkeyService } from "@web/core/hotkeys/hotkey_service";
import { click, getFixture, mount, nextTick } from "@web/../tests/helpers/utils";

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

async function makeReportEnv(onReport = () => {}) {
    registry.category("services").add("ui", uiService);
    registry.category("services").add("hotkey", hotkeyService);
    return makeTestEnv({
        mockRPC(route, args) {
            if (args.method === "get_report") {
                onReport(args.kwargs);
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
}

QUnit.test("rows expand and locations replace warehouse without duplicate totals", async (assert) => {
    const env = await makeReportEnv();
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

QUnit.test("standard measures dropdown controls columns, colors and saved widths", async (assert) => {
    const env = await makeReportEnv();
    const target = getFixture();
    const report = await mount(StockMinimumReport, target, { env });
    await click(target, ".o_pivot_buttons .dropdown-toggle");
    assert.deepEqual([...target.querySelectorAll('.dropdown-item')].map((el) => el.textContent.trim()),
        ["В наявності", "Доступно", "Прогнозовано"]);
    assert.containsN(target, '.dropdown-item.selected', 3);
    await drag(target, 'w1:free_qty', 50);
    report.onMeasureSelected({ measure: 'free_qty' });
    await nextTick();
    assert.containsNone(target, '[data-column-key="w1:free_qty"]');
    assert.strictEqual(target.querySelector('thead th[colspan]').colSpan, 2);
    assert.containsN(target, 'col', 4);
    report.onMeasureSelected({ measure: 'free_qty' });
    await nextTick();
    assert.strictEqual(report.columnWidth('w1:free_qty'), 170);
    await report.expandAll();
    report.onMeasureSelected({ measure: 'qty_available' });
    await nextTick();
    assert.containsNone(target, 'td.o_stock_minimum_control_red');
    assert.containsN(target, 'tbody tr:last-child .o_pivot_cell_value', 4);
    report.onMeasureSelected({ measure: 'qty_available' });
    await nextTick();
    assert.containsN(target, 'td.o_stock_minimum_control_red', 2);
    for (const measure of Object.keys(report.measures)) report.onMeasureSelected({ measure });
    await nextTick();
    assert.containsN(target, 'col', 2, 'minimum remains with no measures');
    assert.containsNone(target, '.o_pivot_cell_value');
    assert.containsNone(target, 'th[colspan="0"]');
});

QUnit.test("search domain and expand all use one batched reload and preserve layout", async (assert) => {
    const calls = [];
    const env = await makeReportEnv((kwargs) => calls.push(kwargs));
    const target = getFixture();
    const domain = [['categ_id', '=', 5]];
    const report = await mount(StockMinimumReport, target, { env, props: { domain } });
    assert.deepEqual(calls[0].domain, domain);
    await drag(target, 'product', 50);
    calls.length = 0;
    await report.expandAll();
    assert.strictEqual(calls.length, 1);
    assert.deepEqual(calls[0].expanded_categories, [5]);
    assert.deepEqual(calls[0].expanded_warehouses, [1]);
    assert.strictEqual(report.state.totalExpanded, true);
    report.domain = [['default_code', 'ilike', 'A']];
    await report.load();
    await nextTick();
    assert.deepEqual(calls[1].domain, report.domain);
    assert.strictEqual(report.columnWidth('product'), 330);
    assert.strictEqual(getComputedStyle(target.querySelector('.o_smc_minimum')).left, '330px');
});

async function drag(target, key, delta) {
    const handle = target.querySelector(`th[data-column-key="${key}"] .o_resize`);
    handle.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerId: 1, clientX: 100 }));
    window.dispatchEvent(new PointerEvent("pointermove", { pointerId: 1, clientX: 100 + delta }));
    await nextTick();
    window.dispatchEvent(new PointerEvent("pointerup", { pointerId: 1 }));
    await nextTick();
}

QUnit.test("leaf resize, minimum bounds, sticky offsets and persistence across reload/expansion", async (assert) => {
    const env = await makeReportEnv();
    const target = getFixture();
    const report = await mount(StockMinimumReport, target, { env });
    const width = (key) => target.querySelector(`th[data-column-key="${key}"]`).getBoundingClientRect().width;
    assert.containsN(target, "thead .o_resize", 5);
    assert.containsNone(target, "th[colspan] .o_resize", "group headers have no resize handles");
    const original = width("product");
    const neighbor = width("minimum");
    const table = target.querySelector("table");
    const tableWidth = table.getBoundingClientRect().width;
    await drag(target, "product", 80);
    assert.ok(Math.abs(width("product") - original - 80) < 1);
    assert.ok(Math.abs(width("minimum") - neighbor) < 1, "neighbor width is unchanged");
    assert.ok(Math.abs(table.getBoundingClientRect().width - tableWidth - 80) < 1);
    const minimum = target.querySelector("th.o_smc_minimum");
    assert.strictEqual(getComputedStyle(minimum).left, `${report.columnWidth("product")}px`);
    const labelRect = target.querySelector("th.o_smc_label").getBoundingClientRect();
    assert.ok(Math.abs(minimum.getBoundingClientRect().left - labelRect.right) < 1, "sticky columns touch without overlap/gap");
    assert.notOk(table.classList.contains("o_resizing"));
    assert.containsNone(target, ".o_column_resizing");
    await drag(target, "minimum", -1000);
    assert.strictEqual(report.columnWidth("minimum"), report.minimumWidth("minimum"));
    await drag(target, "minimum", 30);
    await drag(target, "w1:qty_available", 45);
    await drag(target, "w1:free_qty", -1000);
    assert.strictEqual(report.columnWidth("w1:free_qty"), report.minimumWidth("w1:free_qty"));
    await click(target, "tbody button");
    await click(target, "tbody tr:nth-child(2) button");
    assert.ok(Math.abs(width("product") - original - 80) < 1);
    assert.strictEqual(width("minimum"), 130);
    assert.strictEqual(width("w1:qty_available"), 165);
    assert.containsOnce(target, "td.o_stock_minimum_control_green");
    await click(target, "thead button");
    assert.containsN(target, "thead .o_resize", 8);
    assert.strictEqual(width("l2:qty_available"), 120, "new locations start with default width");
    assert.ok(Math.abs(width("product") - original - 80) < 1);
    await drag(target, "l2:qty_available", 55);
    assert.strictEqual(width("l2:qty_available"), 175);
    await click(target, "thead button");
    assert.strictEqual(width("w1:qty_available"), 165, "collapsed warehouse restores its width");
    await report.load();
    await nextTick();
    assert.strictEqual(width("minimum"), 130, "refresh preserves widths");
    await click(target, "tbody tr:nth-child(2) button");
    await click(target, "tbody tr:nth-child(2) button");
    assert.strictEqual(width("w1:qty_available"), 165, "category collapse and re-expansion preserve widths");
    await click(target, "thead button");
    assert.strictEqual(width("l2:qty_available"), 175, "location width survives collapse and refresh");
    assert.containsN(target, "td.o_stock_minimum_control_red", 2);
    await drag(target, "product", -1000);
    assert.strictEqual(width("product"), report.minimumWidth("product"));
    assert.strictEqual(getComputedStyle(minimum).left, "140px");
});

QUnit.test("cancelled drag releases listeners and resize handle supports keyboard", async (assert) => {
    const target = getFixture();
    const report = await mount(StockMinimumReport, target, { env: await makeReportEnv() });
    const handle = target.querySelector('th[data-column-key="product"] .o_resize');
    handle.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerId: 8, clientX: 100 }));
    window.dispatchEvent(new PointerEvent("pointermove", { pointerId: 7, clientX: 200 }));
    assert.strictEqual(report.columnWidth("product"), 280, "unrelated pointer ignored");
    window.dispatchEvent(new PointerEvent("pointermove", { pointerId: 8, clientX: 130 }));
    assert.strictEqual(report.columnWidth("product"), 310);
    window.dispatchEvent(new PointerEvent("pointercancel", { pointerId: 8 }));
    window.dispatchEvent(new PointerEvent("pointermove", { pointerId: 8, clientX: 200 }));
    assert.strictEqual(report.columnWidth("product"), 310, "cancel removes move listener");
    assert.strictEqual(report.layout.resizing, null);
    handle.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "ArrowLeft" }));
    await nextTick();
    assert.strictEqual(report.columnWidth("product"), 300);
    assert.containsNone(target, ".o_resizing, .o_column_resizing");
});

QUnit.test("multiline rows keep numbers top/right aligned after resizing", async (assert) => {
    const target = getFixture();
    const report = await mount(StockMinimumReport, target, { env: await makeReportEnv() });
    await click(target, "tbody button");
    await click(target, "tbody tr:nth-child(2) button");
    report.state.data.products[11].name = "[LONG] Комплектуючий виріб із дуже довгою назвою для перевірки перенесення тексту на три або більше рядків";
    // Explicit cell alignment must win over inherited table/theme alignment.
    target.querySelector("table").style.verticalAlign = "baseline";
    await nextTick();
    const checkAlignment = () => {
        for (const row of target.querySelectorAll("tbody tr")) {
            const cells = [...row.querySelectorAll(".o_smc_minimum, .o_pivot_cell_value")];
            assert.ok(cells.every((cell) => getComputedStyle(cell).verticalAlign === "top"));
            assert.ok(cells.every((cell) => getComputedStyle(cell).textAlign === "right"));
            assert.strictEqual(getComputedStyle(row.querySelector(".o_smc_label")).verticalAlign, "top");
        }
        const product = target.querySelector("tbody tr:last-child");
        const label = product.querySelector(".o_smc_label span");
        assert.ok(label.getBoundingClientRect().height >= parseFloat(getComputedStyle(label).lineHeight) * 3,
            "product label wraps onto at least three lines");
        const tops = [...product.querySelectorAll("td")].map((cell) => {
            const range = document.createRange();
            range.selectNodeContents(cell.querySelector(".o_value") || cell);
            return range.getBoundingClientRect().top;
        });
        assert.ok(Math.max(...tops) - Math.min(...tops) < 1, "all four numeric text tops coincide");
        assert.containsOnce(target, "td.o_stock_minimum_control_green");
    };
    checkAlignment();
    await drag(target, "product", -80);
    await drag(target, "minimum", 30);
    await drag(target, "w1:qty_available", 40);
    await drag(target, "w1:free_qty", 20);
    await drag(target, "w1:virtual_available", -20);
    checkAlignment();
    assert.strictEqual(report.columnWidth("product"), 200);
    assert.strictEqual(report.columnWidth("minimum"), 190);
    assert.strictEqual(report.columnWidth("w1:qty_available"), 160);
    assert.containsNone(target, "thead .align-top", "header alignment is unchanged");
});
