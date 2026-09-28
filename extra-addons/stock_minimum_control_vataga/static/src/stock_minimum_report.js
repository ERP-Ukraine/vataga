/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, onWillUpdateProps, useRef, useState, useSubEnv } from "@odoo/owl";
import { WithSearch } from "@web/search/with_search/with_search";
import { Layout } from "@web/search/layout";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { useSearchBarToggler } from "@web/search/search_bar/search_bar_toggler";
import { CogMenu } from "@web/search/cog_menu/cog_menu";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { download } from "@web/core/network/download";
import { getDefaultConfig } from "@web/views/view";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/views/fields/formatters";
import { stockMinimumClass } from "./stock_minimum_colors";

export class StockMinimumReport extends Component {
    static template = "stock_minimum_control_vataga.Report";
    static components = { Dropdown, DropdownItem };

    setup() {
        this.orm = useService("orm");
        this.user = useService("user");
        this.tableRef = useRef("table");
        this.layout = useState({ widths: {}, resizing: null });
        this.measures = {
            qty_available: { name: "qty_available", string: "В наявності" },
            free_qty: { name: "free_qty", string: "Доступно" },
            virtual_available: { name: "virtual_available", string: "Прогнозовано" },
        };
        this.selection = useState({ measures: Object.keys(this.measures) });
        this.domain = this.props.domain || [];
        this.requestId = 0;
        this.state = useState({
            data: null, busy: false, error: false, totalExpanded: false,
            warehouses: [], categories: [], pages: {},
        });
        onWillStart(() => this.load());
        onWillUpdateProps(async (props) => {
            if (JSON.stringify(props.domain || []) !== JSON.stringify(this.domain)) {
                this.domain = props.domain || [];
                this.state.pages = {};
                await this.load();
            }
        });
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
            this.activeIndexes.map((measure) => this.measureKey(column, measure)))];
    }

    get activeIndexes() {
        return Object.keys(this.measures).flatMap((key, index) => this.selection.measures.includes(key) ? [index] : []);
    }

    onMeasureSelected({ measure }) {
        this.toggle(this.selection.measures, measure);
    }

    reportOptions() {
        return { expanded_warehouses: [...this.state.warehouses],
            expanded_categories: [...this.state.categories], pages: { ...this.state.pages }, domain: this.domain };
    }

    async expandAll() {
        this.state.totalExpanded = true;
        this.state.categories = this.state.data.categories.map((category) => category.id);
        this.state.warehouses = this.state.data.warehouses.filter((warehouse) => warehouse.can_expand).map((warehouse) => warehouse.id);
        await this.load();
    }

    async downloadXlsx() {
        await download({ url: "/stock_minimum_control/export_xlsx", data: {
            allowed_company_ids: JSON.stringify(this.user.context.allowed_company_ids),
            options: JSON.stringify({ ...this.reportOptions(), measures: this.selection.measures,
                total_expanded: this.state.totalExpanded }),
        } });
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
        const requestId = ++this.requestId;
        this.state.busy = true;
        this.state.error = false;
        try {
            const data = await this.orm.call("stock.minimum.control.report", "get_report", [], this.reportOptions());
            if (requestId === this.requestId) this.state.data = data;
        } catch {
            if (requestId === this.requestId) this.state.error = true;
        } finally {
            if (requestId === this.requestId) this.state.busy = false;
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

export class StockMinimumPanel extends Component {
    static template = "stock_minimum_control_vataga.Panel";
    static components = { Layout, SearchBar, CogMenu, StockMinimumReport };
    setup() {
        this.searchBarToggler = useSearchBarToggler();
    }
}

export class StockMinimumAction extends Component {
    static template = "stock_minimum_control_vataga.Action";
    static components = { WithSearch, StockMinimumPanel };
    setup() {
        useSubEnv({ config: { ...getDefaultConfig(), ...this.env.config,
            actionId: this.props.action.id, actionType: "ir.actions.client", resModel: "product.product" } });
        this.searchProps = { resModel: "product.product", searchViewId: this.props.action.params.search_view_id,
            context: this.props.action.context, loadIrFilters: true, searchMenuTypes: ["filter", "favorite"],
            hideCustomGroupBy: true, display: { controlPanel: {} } };
    }
}
registry.category("actions").add("stock_minimum_control_vataga.report", StockMinimumAction);
