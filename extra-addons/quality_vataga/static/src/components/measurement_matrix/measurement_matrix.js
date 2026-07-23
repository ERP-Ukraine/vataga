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
        this.state = useState({
            loading: true,
            saving: false,
            data: {
                columns: [],
                samples: [],
                editable: false,
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
            Object.assign(this.props.record.data, {
                can_pass_measurement_check: this.state.data.can_pass,
                measurement_matrix_complete: this.state.data.is_complete,
                measurement_matrix_has_failure: this.state.data.has_failure,
            });
            if (this.props.record.model?.notify) {
                this.props.record.model.notify();
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
