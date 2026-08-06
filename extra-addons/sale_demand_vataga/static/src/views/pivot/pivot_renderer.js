/** @odoo-module */


import { download } from "@web/core/network/download";
import { PivotRenderer } from "@web/views/pivot/pivot_renderer"
import {
    onMounted,
    onPatched,
    onWillPatch,
    useRef,
} from "@odoo/owl";

const CLOSED_CELL_COLORS = {
    "closed-low": "#d9bfc7",
    "closed-medium": "#e4daa8",
    "closed-complete": "#71a064",
    "closed-over": "#779bb5",
};

export function getClosedCellClass(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
        return "";
    }
    if (value === 0) {
        return "";
    }
    if (value > 1) {
        return "closed-over";
    }
    if (Math.round(value * 100) === 100) {
        return "closed-complete";
    }
    if (value <= 0.7) {
        return "closed-low";
    }
    return "closed-medium";
}

export function getClosedCellColor(value) {
    return CLOSED_CELL_COLORS[getClosedCellClass(value)] || null;
}

export function getPivotCellClasses(cell) {
    const classes = {
        o_empty: cell.value === undefined,
        "cursor-pointer": cell.value !== undefined,
        "fw-bold": cell.isBold,
    };
    if (cell.measure === "closed") {
        const closedClass = getClosedCellClass(cell.value);
        if (closedClass) {
            classes[closedClass] = true;
        }
    }
    return classes;
}

export class PivotRendererDemand extends PivotRenderer {
    setup() {
        this.rootRef = useRef("root");
        super.setup();
        if (this.model.demandProfileEnabled) {
            this.demandRenderStartedAt = performance.now();
            onMounted(() => this.logDemandRenderProfile("mounted"));
            onPatched(() => this.logDemandRenderProfile("patched"));
            onWillPatch(() => {
                this.demandRenderStartedAt = performance.now();
            });
        }
    }
    logDemandRenderProfile(stage) {
        const table = this.tableRef.el;
        const rows = table ? [...table.rows] : [];
        const valueCells = table
            ? table.querySelectorAll("td.o_pivot_cell_value")
            : [];
        console.info("[DemandPivotProfile] renderer", {
            stage,
            renderer_ms: Number(
                (performance.now() - this.demandRenderStartedAt).toFixed(2)
            ),
            dom_cells: table ? table.querySelectorAll("th, td").length : 0,
            dom_rows: rows.length,
            max_dom_columns: rows.length
                ? Math.max(...rows.map((row) => row.cells.length))
                : 0,
            value_cells: valueCells.length,
            inline_style_value_cells: table
                ? table.querySelectorAll("td.o_pivot_cell_value[style]").length
                : 0,
            inline_style_elements: table
                ? table.querySelectorAll("[style]").length
                : 0,
        });
    }
    onStartResize(ev) {
        this.resizing = true;
        const table = this.tableRef.el;
        const th = ev.target.closest("th");
        const handler = th.querySelector(".o_resize");
        table.style.width = `${Math.floor(table.getBoundingClientRect().width)}px`;
        const thPosition = [...th.parentNode.children].indexOf(th);
        const resizingColumnElements = [...table.getElementsByTagName("tr")]
            .filter((tr) => tr.children.length === th.parentNode.children.length)
            .map((tr) => tr.children[thPosition]);
        const initialX = ev.clientX;
        const initialWidth = th.getBoundingClientRect().width;
        const initialTableWidth = table.getBoundingClientRect().width;
        const resizeStoppingEvents = ["keydown", "pointerdown", "pointerup"];

        // fix the width so that if the resize overflows, it doesn't affect the layout of the parent
        if (!this.rootRef.el.style.width) {
            this.rootWidthFixed = true;
            this.rootRef.el.style.width = `${Math.floor(
                this.rootRef.el.getBoundingClientRect().width
            )}px`;
        }

        // Apply classes to table and selected column
        table.classList.add("o_resizing");
        for (const el of resizingColumnElements) {
            el.classList.add("o_column_resizing");
            handler.classList.add("bg-primary", "opacity-100");
            handler.classList.remove("bg-black-25", "opacity-50-hover");
        }
        // Mousemove event : resize header
        const resizeHeader = (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            let delta = ev.clientX - initialX;
            delta = this.isRTL ? -delta : delta;
            const newWidth = Math.max(10, initialWidth + delta);
            const tableDelta = newWidth - initialWidth;
            th.style.width = `${Math.floor(newWidth)}px`;
            th.style.maxWidth = `${Math.floor(newWidth)}px`;
            table.style.width = `${Math.floor(initialTableWidth + tableDelta)}px`;
        };
        window.addEventListener("pointermove", resizeHeader);

        // Mouse or keyboard events : stop resize
        const stopResize = (ev) => {
            this.resizing = false;
            // freeze column size after resizing
            this.keepColumnWidths = true;
            // Ignores the 'left mouse button down' event as it used to start resizing
            if (ev.type === "pointerdown" && ev.button === 0) {
                return;
            }
            ev.preventDefault();
            ev.stopPropagation();

            table.classList.remove("o_resizing");
            for (const el of resizingColumnElements) {
                el.classList.remove("o_column_resizing");
                handler.classList.remove("bg-primary", "opacity-100");
                handler.classList.add("bg-black-25", "opacity-50-hover");
            }

            window.removeEventListener("pointermove", resizeHeader);
            for (const eventType of resizeStoppingEvents) {
                window.removeEventListener(eventType, stopResize);
            }

            // we remove the focus to make sure that the there is no focus inside
            // the tr.  If that is the case, there is some css to darken the whole
            // thead, and it looks quite weird with the small css hover effect.
            document.activeElement.blur();
        };
        // We have to listen to several events to properly stop the resizing function. Those are:
        // - pointerdown (e.g. pressing right click)
        // - pointerup : logical flow of the resizing feature (drag & drop)
        // - keydown : (e.g. pressing 'Alt' + 'Tab' or 'Windows' key)
        for (const eventType of resizeStoppingEvents) {
            window.addEventListener(eventType, stopResize);
        }
    }
    getPivotCellClasses(cell) {
        return getPivotCellClasses(cell);
    }
    onDownloadButtonClicked() {
        if (this.model.getTableWidth() > 16384) {
            throw new Error(
                _t(
                    "For Excel compatibility, data cannot be exported if there are more than 16384 columns.\n\nTip: try to flip axis, filter further or reduce the number of measures."
                )
            );
        }
        const table = this.model.exportData();
        download({
            url: "/web/pivot/demand/export_xlsx",
            data: { data: new Blob([JSON.stringify(table)], { type: "application/json" }) },
        });
    }
    async copyText(ev) {
        navigator.clipboard.writeText(ev.target.dataset['tooltip']);
        this.notification.add("Text copied to clipboard", {
            type: "success",
        });
    }
}
PivotRendererDemand.template = "sale_demand_vataga.PivotRendererDemand";
