/** @odoo-module **/

import {
    Component,
    onWillDestroy,
    toRaw,
    useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { QUALITY_CHECK_MATRIX_REFRESH, refreshQualityCheckRecord } from "./admission_buttons";

export class QualityMeasurementMatrix extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.ui = useService("ui");
        this.loadVersion = 0;
        this.initializationAttempted = new Set();
        this.identityInitializationAttempted = new Set();
        this.state = useState({
            loading: true,
            saving: false,
            unitChoices: {},
            sampleCount: 1,
            addingSamples: false,
            removingSamples: false,
            data: {
                control_stage: "incoming",
                columns: [],
                samples: [],
                editable: false,
                has_failure: false,
                is_complete: false,
                equipment_complete: false,
            },
        });
        useRecordObserver((record, props) => {
            // The JSON marker is replaced by a server read even if its check
            // ID/content stays equal. Local edits retain that marker object.
            const marker = toRaw(record.data[props.name]);
            const dirty = record.dirty;
            if (record === this.observedRecord && marker === this.observedMarker) {
                if (!dirty && (this.parentRefreshPending || this.initializationDeferred)) {
                    return this.load(record);
                }
                return;
            }
            this.observedRecord = record;
            this.observedMarker = marker;
            return this.load(record);
        });
        useBus(this.env.bus, QUALITY_CHECK_MATRIX_REFRESH, ({ detail }) => {
            if (detail.checkId === this.checkId) {
                this.load(this.props.record, { initialize: false, syncForm: false });
            }
        });
        onWillDestroy(() => {
            this.loadVersion++;
        });
    }

    get checkId() {
        return Number(this.props.record.resId) || false;
    }

    async load(record = this.props.record, { initialize = true, syncForm = true } = {}) {
        const checkId = Number(record.resId) || false;
        const version = ++this.loadVersion;
        if (!checkId) {
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        try {
            let data = await this.orm.call(
                "quality.check",
                "get_measurement_matrix_data",
                [[checkId]]
            );
            if (version !== this.loadVersion) {
                return;
            }
            let generated = false;
            const missingBatch = data.control_stage === "in_process" &&
                data.editable && !data.batch_quantity && !data.samples.length;
            if (initialize) {
                this.initializationDeferred = false;
            }
            if (
                initialize && missingBatch &&
                !this.initializationAttempted.has(checkId)
            ) {
                const dirty = await record.isDirty();
                if (version !== this.loadVersion) {
                    return;
                }
                if (dirty) {
                    this.initializationDeferred = true;
                } else {
                    this.initializationAttempted.add(checkId);
                    data = await this.orm.call(
                        "quality.check",
                        "initialize_in_process_measurement_matrix",
                        [[checkId]]
                    );
                    generated = data.batch_quantity > 0 && data.samples.length > 0;
                }
            }
            if (version !== this.loadVersion) {
                return;
            }
            if (initialize && data.can_initialize_identity && data.batch_quantity > 0 &&
                !this.identityInitializationAttempted.has(checkId)) {
                const dirty = await record.isDirty();
                if (version !== this.loadVersion) {
                    return;
                }
                if (dirty) {
                    this.initializationDeferred = true;
                } else {
                    this.identityInitializationAttempted.add(checkId);
                    data = await this.orm.call('quality.check', 'initialize_qc_unit_identity', [[checkId]]);
                }
            }
            if (version !== this.loadVersion) {
                return;
            }
            this.state.data = data;
            if (syncForm && data.control_stage === "in_process") {
                const quantityStale = "in_process_sample_quantity" in record.data &&
                    record.data.in_process_sample_quantity !== data.batch_quantity;
                const identityStale = "operation_run_id" in record.data &&
                    "operation_run_id" in data &&
                    (record.data.operation_run_id?.[0] || false) !== data.operation_run_id;
                this.parentRefreshPending = generated || quantityStale || identityStale;
                if (this.parentRefreshPending) {
                    this.parentRefreshPending = !(await refreshQualityCheckRecord(record, this.ui));
                }
            }
        } finally {
            if (version === this.loadVersion) {
                this.state.loading = false;
            }
        }
    }

    onSampleCountInput(ev) {
        this.state.sampleCount = ev.target.value;
    }

    async bindUnits() {
        if (this.state.saving || !this.checkId) {
            return;
        }
        const record = this.props.record;
        const checkId = this.checkId;
        this.state.saving = true;
        try {
            if ((await record.isDirty()) && (await record.save()) === false) {
                return;
            }
            if (this.checkId !== checkId) {
                return;
            }
            const mapping = this.state.data.samples.map((sample) => ({
                sample_id: sample.id,
                unit_id: Number(this.state.unitChoices[sample.id]) || 0,
            }));
            const data = await this.orm.call('quality.check', 'bind_measurement_units', [[checkId], mapping]);
            if (this.checkId === checkId) {
                this.state.data = data;
                await refreshQualityCheckRecord(record, this.ui);
            }
        } finally {
            this.state.saving = false;
        }
    }

    async refreshInProcessMatrix() {
        const record = this.props.record;
        this.initializationAttempted.delete(this.checkId);
        this.identityInitializationAttempted.delete(this.checkId);
        await this.load(record, { syncForm: false });
        await refreshQualityCheckRecord(record, this.ui);
    }

    async addSamples() {
        if (
            !this.checkId ||
            this.state.data.control_stage === "in_process" ||
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
            this.state.data.control_stage === "in_process" ||
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
