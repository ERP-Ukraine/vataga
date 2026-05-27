/** @odoo-module **/

import { useService } from "@web/core/utils/hooks";
import { onMounted, onPatched } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { PivotRendererDemand } from "@sale_demand_vataga/views/pivot/pivot_renderer";

const COMMENT_HEADER_LABEL = "Примітки";
const COMMENT_HEADER_TITLES = new Set([
    COMMENT_HEADER_LABEL,
    "Примітка",
    "Comment",
    "Seller Analytic Comment",
]);

patch(PivotRendererDemand.prototype, {
    setup() {
        super.setup(...arguments);
        this.orm = useService("orm");
        this.analogProductNames = new Set();
        onMounted(() => {
            this.loadAnalogProductNames();
        });
        onPatched(() => {
            this.renderAnalogMarkers();
        });
    },

    async loadAnalogProductNames() {
        const analogs = await this.orm.searchRead("product.analog", [], ["product_id"]);
        this.analogProductNames = new Set(
            analogs
                .map((analog) => analog.product_id && analog.product_id[1])
                .filter(Boolean)
        );
        this.renderAnalogMarkers();
    },

    getCommentColumnIndexes() {
        const table = this.tableRef?.el;
        const headerRow = table?.querySelector("thead tr:last-child");
        if (!headerRow) {
            return [];
        }
        return [...headerRow.children]
            .map((cell, index) => ({ cell, index }))
            .filter(({ cell }) => {
                const title = cell.textContent.trim();
                return COMMENT_HEADER_TITLES.has(title);
            })
            .map(({ index }) => index);
    },

    renameCommentHeaders() {
        const table = this.tableRef?.el;
        const headerRow = table?.querySelector("thead tr:last-child");
        if (!headerRow) {
            return;
        }
        for (const cell of headerRow.children) {
            const title = cell.textContent.trim();
            if (COMMENT_HEADER_TITLES.has(title) && title !== COMMENT_HEADER_LABEL) {
                cell.textContent = COMMENT_HEADER_LABEL;
            }
        }
    },

    getRowProductName(row) {
        const copyIcon = row.querySelector("th i.fa-copy[data-tooltip]");
        return copyIcon?.dataset.tooltip || "";
    },

    isAnalogProductRow(row) {
        const productName = this.getRowProductName(row);
        return productName && this.analogProductNames.has(productName);
    },

    centerAnalogMarkerCell(cell) {
        const valueElement = cell.querySelector(".o_value") || cell;
        cell.classList.remove("text-start", "text-end");
        cell.classList.add("text-center");
        cell.style.setProperty("text-align", "center", "important");
        cell.style.setProperty("vertical-align", "middle", "important");
        valueElement.classList.remove("text-start", "text-end");
        valueElement.classList.add("text-center");
        valueElement.style.setProperty("display", "block", "important");
        valueElement.style.setProperty("width", "100%", "important");
        valueElement.style.setProperty("text-align", "center", "important");
    },

    renderAnalogMarkers() {
        const table = this.tableRef?.el;
        if (!table) {
            return;
        }
        this.renameCommentHeaders();
        if (!this.analogProductNames.size) {
            return;
        }
        const commentColumnIndexes = this.getCommentColumnIndexes();
        if (!commentColumnIndexes.length) {
            return;
        }
        for (const row of table.querySelectorAll("tbody tr")) {
            if (!this.isAnalogProductRow(row)) {
                continue;
            }
            const cells = [...row.children];
            for (const columnIndex of commentColumnIndexes) {
                const cell = cells[columnIndex];
                if (!cell) {
                    continue;
                }
                const valueElement = cell.querySelector(".o_value") || cell;
                const value = valueElement.textContent.trim();
                if (!value) {
                    valueElement.textContent = "(A)";
                } else if (!value.includes("(A)")) {
                    valueElement.textContent = `${value} (A)`;
                }
                this.centerAnalogMarkerCell(cell);
            }
        }
    },
});
