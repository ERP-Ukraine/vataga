/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/views/fields/formatters";
import { stockMinimumClass } from "./stock_minimum_colors";

export class StockMinimumReport extends Component {
    static template = "stock_minimum_control_vataga.Report";

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            data: null, busy: false, error: false, totalExpanded: false,
            warehouses: [], categories: [], pages: {}, search: "", searchInput: "",
        });
        onWillStart(() => this.load());
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
