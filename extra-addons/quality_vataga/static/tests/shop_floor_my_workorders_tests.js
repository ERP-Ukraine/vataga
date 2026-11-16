/** @odoo-module **/

import { MrpDisplay } from "@mrp_workorder/mrp_display/mrp_display";
import { loadMyWorkorderPage } from "@quality_vataga/shop_floor_my_workorders";

QUnit.module("quality_vataga Shop Floor My WO", () => {
    QUnit.test("filters real WOs before paging and batches only the parents on that page", async (assert) => {
        const context = { allowed_company_ids: [1] };
        const moDomain = [["name", "ilike", "CAB/MO/"]];
        const config = { offset: 40, limit: 40, domain: moDomain, context, currentCompanyId: 1 };
        const model = {
            orm: {
                async webSearchRead(modelName, domain, kwargs) {
                    assert.strictEqual(modelName, "mrp.workorder");
                    assert.deepEqual(kwargs.context, { ...context, current_company_id: 1 });
                    assert.strictEqual(kwargs.offset, 40);
                    assert.strictEqual(kwargs.limit, 40);
                    assert.ok(domain.some((leaf) => Array.isArray(leaf)
                        && leaf[0] === "production_id" && leaf[1] === "any"
                        && leaf[2][0][0] === "name"));
                    assert.ok(domain.some((leaf) => Array.isArray(leaf)
                        && leaf[0] === "employee_assigned_ids" && leaf[1] === "in" && leaf[2][0] === 220));
                    assert.ok(domain.some((leaf) => Array.isArray(leaf)
                        && leaf[0] === "state" && leaf[2][0] === "ready"));
                    assert.ok(domain.some((leaf) => Array.isArray(leaf)
                        && leaf[0] === "workcenter_id" && leaf[2][0] === 3));
                    return {
                        records: [{ id: 667, production_id: 16463 }, { id: 668, production_id: 16463 }],
                        length: 42,
                    };
                },
            },
            async _loadRecords(parentConfig) {
                assert.deepEqual(parentConfig.resIds, [16463]);
                return [{ id: 16463 }];
            },
        };
        const result = await loadMyWorkorderPage(model, config, {
            employeeId: 220, sessionWorkorders: [201], states: ["ready"], workcenters: [3], key: "scope",
        });
        assert.deepEqual(result, { records: [{ id: 16463 }], length: 42 });
        assert.deepEqual(config.myWorkorderPage, { key: "scope", ids: [667, 668] });
        assert.deepEqual(config.domain, moDomain);
    });

    QUnit.test("an empty later page returns to the first page", async (assert) => {
        const config = { domain: [], offset: 40, limit: 40 };
        const offsets = [];
        const model = {
            orm: {
                async webSearchRead(_name, _domain, { offset }) {
                    offsets.push(offset);
                    return { records: [], length: 0 };
                },
            },
        };
        assert.deepEqual(await loadMyWorkorderPage(model, config, {
            employeeId: 220, sessionWorkorders: [], states: [], workcenters: null, key: "scope",
        }), { records: [], length: 0 });
        assert.deepEqual(offsets, [40, 0]);
        assert.strictEqual(config.offset, 0);
    });

    QUnit.test("the active employee and the selected WO page control which cards render", (assert) => {
        const wo = (resId, state, assigned) => ({
            resId,
            data: { state, employee_assigned_ids: { resIds: assigned } },
        });
        const display = Object.create(MrpDisplay.prototype);
        Object.assign(display, {
            state: { activeWorkcenter: -1, workcenters: [] },
            useEmployee: { employees: {
                admin: { id: 220 },
                connected: [
                    { id: 220, workorder: [{ id: 4 }, { id: 5 }] },
                    { id: 162, workorder: [] },
                ],
            } },
            env: { searchModel: {
                state: { workorderFilters: [{ name: "ready", isActive: true }] },
                searchItems: {},
                query: [],
            } },
            model: { root: {
                config: {},
                records: [{ data: { workorder_ids: { records: [
                    wo(1, "ready", [220]), wo(2, "pending", [220]), wo(3, "ready", [220]),
                    wo(4, "ready", []), wo(5, "ready", [162]),
                ] } } }],
            } },
        });
        display.model.root.config.myWorkorderPage = { key: display.myWorkorderScope.key, ids: [1, 2, 4, 5] };
        assert.deepEqual(display.filteredWorkorders.map(({ resId }) => resId), [1, 4]);
        display.useEmployee.employees.admin.id = 162;
        assert.deepEqual(display.filteredWorkorders, []);
        display.model.root.config.myWorkorderPage = { key: display.myWorkorderScope.key, ids: [5] };
        assert.deepEqual(display.filteredWorkorders.map(({ resId }) => resId), [5]);
        display.state.activeWorkcenter = 3;
        assert.deepEqual(display.filteredWorkorders.map(({ resId }) => resId), [1, 3, 4, 5]);
    });
});
