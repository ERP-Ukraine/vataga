/** @odoo-module **/

import { click, getFixture } from "@web/../tests/helpers/utils";
import { makeView, setupViewRegistries } from "@web/../tests/views/helpers";
import "@quality_vataga/components/measurement_matrix/measurement_matrix";

QUnit.module("quality_vataga measurement matrix completion", (hooks) => {
    hooks.beforeEach(() => setupViewRegistries());

    for (const result of ["pass", "fail"]) {
        QUnit.test(`the same matrix becomes read-only after ${result}`, async (assert) => {
            const target = getFixture();
            let qualityState = "none";
            await makeView({
                type: "form",
                resModel: "quality.check",
                resId: 1,
                serverData: {
                    models: {
                        "quality.check": {
                            fields: {
                                quality_state: {
                                    string: "Status",
                                    type: "selection",
                                    selection: [
                                        ["none", "To Do"], ["pass", "Pass"], ["fail", "Fail"],
                                    ],
                                },
                                measurement_matrix_data: { type: "json", string: "Measurements" },
                            },
                            records: [{ id: 1, quality_state: "none" }],
                        },
                    },
                },
                arch: `<form>
                    <header>
                        <button type="object" name="do_${result}" string="Complete"/>
                        <field name="quality_state" widget="statusbar"/>
                    </header>
                    <field name="measurement_matrix_data" widget="quality_measurement_matrix"/>
                </form>`,
                async mockRPC(route, args, performRPC) {
                    if (args.method === `do_${result}`) {
                        qualityState = result;
                        return false;
                    }
                    if (args.method === "web_read") {
                        const [record] = await performRPC(route, args);
                        return [{ ...record, quality_state: qualityState }];
                    }
                    if (args.method === "get_measurement_matrix_data") {
                        return {
                            editable: true,
                            manual_samples: true,
                            columns: [{ id: 1, parameter_name: "Voltage" }],
                            samples: [{
                                id: 1,
                                visual_result: "yes",
                                display_name: "Sample 1",
                                cells: [{ id: 1, parameter_type: "numeric", numeric_input: "15" }],
                            }],
                        };
                    }
                },
            });
            const input = ".o_quality_measurement_matrix tbody input";
            assert.containsOnce(target, `${input}:enabled`);
            assert.containsOnce(target, ".o_quality_measurement_sample_controls");
            await click(target, `button[name="do_${result}"]`);
            assert.containsOnce(target, `${input}:disabled`);
            assert.containsOnce(target, ".o_quality_measurement_matrix tbody select:disabled");
            assert.containsNone(target, ".o_quality_measurement_sample_controls");
            assert.strictEqual(target.querySelector(input).value, "15");
        });
    }
});
