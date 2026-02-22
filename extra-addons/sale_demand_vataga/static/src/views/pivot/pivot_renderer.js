/** @odoo-module */


import { download } from "@web/core/network/download";
import { PivotRenderer } from "@web/views/pivot/pivot_renderer"
import {
    useRef,
} from "@odoo/owl";

export class PivotRendererDemand extends PivotRenderer {
    setup() {
        this.rootRef = useRef("root");
        return super.setup();
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
    get_color(cell) {
        function nearlyEqual(a, b, epsilon = 1e-8) {
            return Math.abs(a - b) < epsilon;
        }
        if (cell.measure === 'closed' && cell.value !== undefined) {
            if (cell.value <= 0.7){
                return '#d9bfc7'
            }
            if (nearlyEqual(cell.value, 1)){
                return '#71a064'
            }
            if (cell.value < 1){
                return '#e4daa8'
            }
            if (cell.value > 1){
                return '#779bb5'
            }
        }
        return 'white'
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