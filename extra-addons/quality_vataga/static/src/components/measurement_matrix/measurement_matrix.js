/** @odoo-module **/

import {
    Component,
    onWillStart,
    onWillUpdateProps,
    useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class QualityMeasurementMatrix extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            saving: false,
            sampleCount: 1,
            addingSamples: false,
            removingSamples: false,
            data: {
                columns: [],
                samples: [],
                editable: false,
                has_failure: false,
                is_complete: false,
                equipment_complete: false,
            },
        });
        onWillStart(() => this.load());
        onWillUpdateProps((nextProps) => {
            if (nextProps.record.resId !== this.props.record.resId) {
                return this.load(nextProps.record.resId);
            }
        });
    }

    get checkId() {
        return Number(this.props.record.resId) || false;
    }

    async load(checkId = this.checkId) {
        if (!checkId) {
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "quality.check",
                "get_measurement_matrix_data",
                [[checkId]]
            );
        } finally {
            this.state.loading = false;
        }
    }

    onSampleCountInput(ev) {
        this.state.sampleCount = ev.target.value;
    }

    async addSamples() {
        if (
            !this.checkId ||
            this.state.addingSamples ||
            this.state.removingSamples ||
            this.state.saving
        ) {
            return;
        }
        const count = Number(this.state.sampleCount);
        if (!Number.isInteger(count) || count < 1) {
            this.notification.add(
                "Кількість зразків повинна бути цілим числом " +
                    "більшим за нуль.",
                { type: "warning" }
            );
            return;
        }

        this.state.addingSamples = true;
        try {
            this.state.data = await this.orm.call(
                "quality.check",
                "add_measurement_samples",
                [[this.checkId], count]
            );
            this.state.sampleCount = 1;
        } finally {
            this.state.addingSamples = false;
        }
    }

    async removeSamples() {
        if (
            !this.checkId ||
            this.state.addingSamples ||
            this.state.removingSamples ||
            this.state.saving
        ) {
            return;
        }
        const count = Number(this.state.sampleCount);
        if (!Number.isInteger(count) || count < 1) {
            this.notification.add(
                "Кількість зразків повинна бути цілим числом " +
                    "більшим за нуль.",
                { type: "warning" }
            );
            return;
        }
        if (count > this.state.data.samples.length) {
            this.notification.add(
                "Неможливо прибрати більше зразків, ніж зараз є у матриці.",
                { type: "warning" }
            );
            return;
        }

        this.state.removingSamples = true;
        try {
            this.state.data = await this.orm.call(
                "quality.check",
                "remove_measurement_samples",
                [[this.checkId], count]
            );
            this.state.sampleCount = 1;
        } finally {
            this.state.removingSamples = false;
        }
    }

    async updateVisualResult(sampleId, ev) {
        await this.applyServerUpdate(
            "update_measurement_visual_result",
            [sampleId, ev.target.value || false]
        );
    }

    async updateNumericValue(cell, ev) {
        await this.applyServerUpdate("update_measurement_value", [
            cell.id,
            { numeric_input: ev.target.value },
        ]);
    }

    async updateBooleanValue(cell, ev) {
        await this.applyServerUpdate("update_measurement_value", [
            cell.id,
            { boolean_value: ev.target.value || false },
        ]);
    }

    async updateStringValue(cell, ev) {
        await this.applyServerUpdate("update_measurement_value", [
            cell.id,
            {
                string_value: ev.target.value,
                manual_result: cell.manual_result || false,
            },
        ]);
    }

    async updateManualResult(cell, ev) {
        await this.applyServerUpdate("update_measurement_value", [
            cell.id,
            {
                string_value: cell.string_value || "",
                manual_result: ev.target.value || false,
            },
        ]);
    }

    async applyServerUpdate(method, args) {
        if (!this.checkId || this.state.saving) {
            return;
        }
        this.state.saving = true;
        try {
            this.state.data = await this.orm.call(
                "quality.check",
                method,
                [[this.checkId], ...args]
            );
            if (!(await this.props.record.isDirty())) {
                await this.props.record.load();
            }
        } finally {
            this.state.saving = false;
        }
    }

    cellClass(cell) {
        return [
            "o_quality_measurement_cell",
            `o_quality_measurement_${cell.result || "pending"}`,
        ].join(" ");
    }

    resultClass(result) {
        return [
            "o_quality_measurement_result",
            `o_quality_measurement_${result || "pending"}`,
        ].join(" ");
    }

    resultLabel(result) {
        if (result === "pass") {
            return "PASS";
        }
        if (result === "fail") {
            return "FAIL";
        }
        return "НЕ ЗАПОВНЕНО";
    }
}

QualityMeasurementMatrix.template =
    "quality_vataga.QualityMeasurementMatrix";
QualityMeasurementMatrix.props = {
    ...standardFieldProps,
};

registry.category("fields").add("quality_measurement_matrix", {
    component: QualityMeasurementMatrix,
    supportedTypes: ["json"],
});
