/** @odoo-module **/

import "@quality_vataga/components/measurement_matrix/measurement_matrix";
import { click, editSelect, getFixture } from "@web/../tests/helpers/utils";
import { makeView, setupViewRegistries } from "@web/../tests/views/helpers";

QUnit.module("quality_vataga measurement matrix", (hooks) => {
    let target;

    hooks.beforeEach(() => {
        target = getFixture();
        setupViewRegistries();
    });

    function matrix(stage, samples = []) {
        return {
            check_id: 1,
            control_stage: stage,
            editable: true,
            columns: [],
            samples,
            batch_quantity: samples.length,
            batch_error: false,
            can_pass: false,
            has_failure: false,
            is_complete: false,
            equipment_complete: true,
        };
    }

    function sample(id) {
        return {
            id,
            sample_number: id,
            display_name: `Зразок №${id}`,
            visual_result: "",
            sample_result: "pending",
            cells: [],
        };
    }

    async function openMatrix(data, onRPC) {
        await makeView({
            type: "form",
            resModel: "quality.check",
            resId: 1,
            arch: '<form><field name="measurement_matrix_data" widget="quality_measurement_matrix" readonly="1"/></form>',
            serverData: {
                models: {
                    "quality.check": {
                        fields: { measurement_matrix_data: { type: "json", string: "Матриця" } },
                        records: [{ id: 1, measurement_matrix_data: { quality_check_id: 1 } }],
                    },
                },
            },
            mockRPC(route, args) {
                if (args.model === "quality.check" && [
                    "get_measurement_matrix_data",
                    "initialize_in_process_measurement_matrix",
                    "update_measurement_visual_result",
                ].includes(args.method)) {
                    onRPC?.(args);
                    return structuredClone(data);
                }
            },
        });
    }

    QUnit.test("incoming retains manual sample controls", async (assert) => {
        const calls = [];
        await openMatrix(matrix("incoming"), (args) => calls.push(args.method));
        assert.containsOnce(target, ".o_quality_measurement_sample_controls");
        assert.containsOnce(target, "#quality_vataga_sample_count");
        assert.strictEqual(calls.filter((name) => name === "initialize_in_process_measurement_matrix").length, 0);
    });

    QUnit.test("shows escaped unit identities while historical samples remain unbound", async (assert) => {
        const identified = sample(1);
        identified.controlled_unit_name = 'QUNIT/2026/<b>000001</b>';
        await openMatrix(matrix("in_process", [identified, sample(2)]));
        assert.containsOnce(target, ".o_quality_controlled_unit");
        assert.strictEqual(target.querySelector(".o_quality_controlled_unit").textContent.trim(), identified.controlled_unit_name);
        assert.containsNone(target, ".o_quality_controlled_unit b");
        assert.containsN(target, "tbody tr", 2);
    });

    QUnit.test("shows inspector admission separately from the sample result", async (assert) => {
        const approved = sample(1);
        approved.controlled_unit_name = "QUNIT/2026/000006";
        approved.sample_result = "pass";
        approved.unit_admission_label = "Допущено";
        const held = sample(2);
        held.sample_result = "pass";
        held.unit_admission_label = "Утримано";
        await openMatrix(matrix("in_process", [approved, held]));
        assert.containsN(target, ".o_quality_unit_admission", 2);
        assert.strictEqual(target.querySelector(".o_quality_unit_admission").textContent.trim(), "Допущено");
        assert.strictEqual(target.querySelectorAll(".o_quality_unit_admission")[1].textContent.trim(), "Утримано");
    });

    QUnit.test("formed in_process batch is read without reinitialization and edits results", async (assert) => {
        const data = matrix("in_process", [sample(1), sample(2)]);
        const calls = [];
        await openMatrix(data, (args) => {
            calls.push(args.method);
            if (args.method === "update_measurement_visual_result") {
                const [, id, result] = args.args;
                data.samples.find((row) => row.id === id).visual_result = result;
            }
        });
        assert.containsNone(target, ".o_quality_measurement_sample_controls");
        assert.containsNone(target, "#quality_vataga_sample_count");
        assert.containsN(target, "tbody tr", 2);
        assert.strictEqual(calls.filter((name) => name === "initialize_in_process_measurement_matrix").length, 0);
        await editSelect(target, "tbody tr:first-child select", "yes");
        assert.ok(calls.includes("update_measurement_visual_result"));
        assert.strictEqual(target.querySelector("tbody tr:first-child select").value, "yes");
    });

    QUnit.test("in_process can retry after the production quantity appears", async (assert) => {
        const data = matrix("in_process");
        data.batch_error = "Фактичну кількість ще не встановлено";
        await openMatrix(data);
        assert.containsNone(target, "tbody tr");
        assert.ok(target.textContent.includes(data.batch_error));
        data.samples = [sample(1), sample(2)];
        data.batch_quantity = 2;
        data.batch_error = false;
        await click(target, ".o_quality_measurement_matrix .btn-secondary");
        assert.containsN(target, "tbody tr", 2);
        assert.containsNone(target, ".o_quality_measurement_sample_controls");
    });

    QUnit.test("completed in_process checks do not generate on open", async (assert) => {
        const data = matrix("in_process", [sample(1)]);
        data.editable = false;
        const calls = [];
        await openMatrix(data, (args) => calls.push(args.method));
        assert.deepEqual(calls, ["get_measurement_matrix_data"]);
        assert.containsNone(target, "button");
        assert.ok(target.querySelector("tbody select").disabled);
    });

    QUnit.test("reworked sample shows a readonly badge", async (assert) => {
        const row = sample(1);
        row.x_was_reworked = true;
        await openMatrix(matrix("in_process", [row]));
        assert.containsOnce(target, "tbody small");
        assert.strictEqual(target.querySelector("tbody small").textContent.trim(), "Після доопрацювання");
        assert.containsNone(target, "input[type=checkbox]");
    });
});
