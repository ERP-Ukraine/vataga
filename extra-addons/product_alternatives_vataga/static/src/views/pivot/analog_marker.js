/** @odoo-module **/

import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";
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
        onMounted(() => {
            this.startAnalogMarkerObserver();
            this.scheduleRenderAnalogMarkers();
        });
        onPatched(() => {
            this.scheduleRenderAnalogMarkers();
        });
        onWillUnmount(() => {
            this.analogMarkerObserver?.disconnect();
        });
    },

    scheduleRenderAnalogMarkers() {
        window.requestAnimationFrame(() => this.renderAnalogMarkers());
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

    startAnalogMarkerObserver() {
        const table = this.tableRef?.el;
        if (!table || this.analogMarkerObserver) {
            return;
        }
        this.analogMarkerObserver = new MutationObserver(() => {
            this.scheduleRenderAnalogMarkers();
        });
        this.analogMarkerObserver.observe(table, {
            childList: true,
            characterData: true,
            subtree: true,
        });
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
        const commentColumnIndexes = this.getCommentColumnIndexes();
        if (!commentColumnIndexes.length) {
            return;
        }
        for (const row of table.querySelectorAll("tbody tr")) {
            const cells = [...row.children];
            for (const columnIndex of commentColumnIndexes) {
                const cell = cells[columnIndex];
                if (!cell) {
                    continue;
                }
                const valueElement = cell.querySelector(".o_value") || cell;
                const value = valueElement.textContent.trim();
                if (value.includes("(A)")) {
                    this.centerAnalogMarkerCell(cell);
                }
            }
        }
    },
});
