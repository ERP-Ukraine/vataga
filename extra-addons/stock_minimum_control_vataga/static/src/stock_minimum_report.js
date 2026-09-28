/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/views/fields/formatters";
import { stockMinimumClass } from "./stock_minimum_colors";

export class StockMinimumReport extends Component {
    static template = "stock_minimum_control_vataga.Report";

    setup() {
        this.orm = useService("orm");
        this.tableRef = useRef("table");
        this.layout = useState({ widths: {}, resizing: null });
        this.state = useState({
            data: null, busy: false, error: false, totalExpanded: false,
            warehouses: [], categories: [], pages: {}, search: "", searchInput: "",
        });
        onWillStart(() => this.load());
        onWillUnmount(() => this.stopResize?.());
    }

    columnWidth(key) {
        return this.layout.widths[key] ?? (key === "product" ? 280 : key === "minimum" ? 160 : 120);
    }

    minimumWidth(key) {
        return key === "product" ? 140 : key === "minimum" ? 100 : 72;
    }

    measureKey(column, measure) {
        return `${column.key}:${["qty_available", "free_qty", "virtual_available"][measure]}`;
    }

    get leafKeys() {
        return ["product", "minimum", ...this.columns.flatMap((column) =>
            [0, 1, 2].map((measure) => this.measureKey(column, measure)))];
    }

    get tableStyle() {
        return `--smc-product-width: ${this.columnWidth("product")}px; ` +
            `--smc-minimum-width: ${this.columnWidth("minimum")}px; ` +
            `width: ${this.leafKeys.reduce((sum, key) => sum + this.columnWidth(key), 0)}px;`;
    }

    resizeClass(key) {
        return this.layout.resizing === key ? "o_column_resizing" : "";
    }

    rowHeaderClass(row) {
        const expanded = row.kind === "total" ? this.state.totalExpanded : this.state.categories.includes(row.id);
        return `${this.resizeClass("product")} o_smc_row_${row.kind} ` +
            (row.kind === "product" ? "" : `o_pivot_header_cell_${expanded ? "opened" : "closed"}`);
    }

    onStartResize(ev, key) {
        if (ev.button !== 0) {
            return;
        }
        this.stopResize?.();
        const startX = ev.clientX;
        const width = this.columnWidth(key);
        const pointerId = ev.pointerId;
        this.layout.resizing = key;
        const move = (event) => {
            if (event.pointerId === pointerId) {
                event.preventDefault();
                this.layout.widths[key] = Math.max(this.minimumWidth(key), Math.round(width + event.clientX - startX));
            }
        };
        const stop = (event) => {
            if (event?.pointerId !== undefined && event.pointerId !== pointerId) {
                return;
            }
            window.removeEventListener("pointermove", move);
            for (const type of ["pointerup", "pointercancel", "blur", "keydown"]) {
                window.removeEventListener(type, stop);
            }
            this.layout.resizing = null;
            this.stopResize = null;
        };
        this.stopResize = stop;
        window.addEventListener("pointermove", move);
        for (const type of ["pointerup", "pointercancel", "blur", "keydown"]) {
            window.addEventListener(type, stop);
        }
    }

    onResizeKey(ev, key) {
        if (["ArrowLeft", "ArrowRight"].includes(ev.key)) {
            ev.preventDefault();
            const delta = (ev.key === "ArrowRight" ? 1 : -1) * (ev.shiftKey ? 20 : 10);
            this.layout.widths[key] = Math.max(this.minimumWidth(key), this.columnWidth(key) + delta);
        }
    }

    async load() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.error = false;
        try {
            this.state.data = await this.orm.call("stock.minimum.control.report", "get_report", [], {
                expanded_warehouses: [...this.state.warehouses],
                expanded_categories: [...this.state.categories],
                pages: { ...this.state.pages },
                search: this.state.search,
            });
        } catch {
            this.state.error = true;
        } finally {
            this.state.busy = false;
        }
    }

    async toggleWarehouse(id) {
        this.toggle(this.state.warehouses, id);
        await this.load();
    }

    async toggleCategory(id) {
        this.toggle(this.state.categories, id);
        await this.load();
    }

    toggle(ids, id) {
        const index = ids.indexOf(id);
        if (index < 0) {
            ids.push(id);
        } else {
            ids.splice(index, 1);
        }
    }

    async changePage(category, delta) {
        this.state.pages[category.id] = category.page + delta;
        await this.load();
    }

    async onSearch() {
        this.state.search = this.state.searchInput.trim();
        this.state.pages = {};
        await this.load();
    }

    get columns() {
        return this.state.data.warehouses.flatMap((warehouse) => warehouse.columns);
    }

    get rows() {
        const data = this.state.data;
        const rows = [{ key: "total", kind: "total", values: data.totals }];
        if (!this.state.totalExpanded) {
            return rows;
        }
        for (const category of data.categories) {
            rows.push({ ...category, key: `c${category.id}`, kind: "category" });
            if (this.state.categories.includes(category.id)) {
                for (const id of category.product_ids) {
                    if (data.products[id]) {
                        rows.push({ ...data.products[id], key: `p${id}`, kind: "product" });
                    }
                }
                if (category.count > data.page_size) {
                    rows.push({ ...category, key: `page${category.id}`, kind: "page" });
                }
            }
        }
        return rows;
    }

    format(value) {
        return formatFloat(value, { digits: [16, this.state.data.digits] });
    }

    cellClass(row, column, measure) {
        return row.kind === "product" && measure === 0
            ? stockMinimumClass(row.values[column.key][measure], row.minimum) : "";
    }
}

registry.category("actions").add("stock_minimum_control_vataga.report", StockMinimumReport);
