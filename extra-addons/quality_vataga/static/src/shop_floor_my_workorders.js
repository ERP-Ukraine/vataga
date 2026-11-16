/** @odoo-module **/

import { useEffect } from "@odoo/owl";
import { MrpDisplay } from "@mrp_workorder/mrp_display/mrp_display";
import { Domain } from "@web/core/domain";
import { patch } from "@web/core/utils/patch";

export async function loadMyWorkorderPage(model, config, scope) {
    const { employeeId, sessionWorkorders, states, workcenters, key } = scope;
    config.myWorkorderPage = { key, ids: [] };
    config.countLimit = Number.MAX_SAFE_INTEGER;
    if (!employeeId) {
        config.offset = 0;
        return { records: [], length: 0 };
    }

    const domain = Domain.and([
        [["production_id", "any", config.domain]],
        [["state", states.length ? "in" : "!=", states.length ? states : "cancel"]],
        Domain.or([
            [["employee_assigned_ids", "in", [employeeId]]],
            [["employee_assigned_ids", "=", false], ["id", "in", sessionWorkorders]],
        ]),
        workcenters ? [["workcenter_id", "in", workcenters]] : [],
    ]).toList();
    const limit = config.limit || model.initialLimit;
    const context = { ...config.context, current_company_id: config.currentCompanyId };
    const searchPage = (offset) => model.orm.webSearchRead("mrp.workorder", domain, {
        specification: { production_id: {} },
        order: "date_start, id",
        offset,
        limit,
        context,
    });
    config.offset ||= 0;
    config.limit = limit;
    let page = await searchPage(config.offset);
    if (config.offset && !page.records.length) {
        config.offset = 0;
        page = await searchPage(0);
    }
    if (!page.records.length) {
        return { records: [], length: 0 };
    }

    // Shop Floor cards need the native MO graph (checks, moves and sibling WOs).
    // Fetch only the distinct parents of the current WO page in one batch.
    const productionIds = [...new Set(page.records.map((row) => row.production_id))];
    const records = await model._loadRecords({ ...config, resIds: productionIds, context });
    config.myWorkorderPage.ids = page.records.map((row) => row.id);
    return { records, length: page.length };
}

patch(MrpDisplay.prototype, {
    setup() {
        super.setup();
        const display = this;
        patch(this.model, {
            async _loadData(config) {
                if (config.isRoot && !config.isMonoRecord) {
                    const scope = display.myWorkorderScope;
                    if (scope) {
                        return loadMyWorkorderPage(this, config, scope);
                    }
                    if (config.myWorkorderPage) {
                        delete config.myWorkorderPage;
                        config.countLimit = this.initialCountLimit;
                    }
                }
                return super._loadData(config);
            },
        });

        let wasMyWorkorders = false;
        let ordinaryOffset = 0;
        let ordinaryLimit = this.state.limit;
        useEffect((scopeKey) => {
            if (!scopeKey && !wasMyWorkorders) {
                return;
            }
            const root = this.model.root;
            if (scopeKey && !wasMyWorkorders) {
                ordinaryOffset = root.offset;
                ordinaryLimit = root.limit;
                this.ordinaryShopFloorCards = {
                    productionCount: this.productions.length,
                    workorders: this.workorders,
                };
            }
            wasMyWorkorders = Boolean(scopeKey);
            this.state.offset = scopeKey ? 0 : ordinaryOffset;
            this.state.limit = ordinaryLimit;
            this.invalidateRecordIdsCache();
            root.load({ offset: this.state.offset, limit: this.state.limit });
        }, () => [this.myWorkorderScope?.key]);

        useEffect((page, offset, needsReload) => {
            if (page?.key !== this.myWorkorderScope?.key || !page) {
                return;
            }
            this.state.offset = offset;
            // A card can finish and reload its parent without reloading the page.
            if (needsReload) {
                this.model.root.load({ offset, limit: this.state.limit });
            }
        }, () => {
            const root = this.model.root;
            const page = root?.config.myWorkorderPage;
            return [page, root?.offset, Boolean(page && this.filteredWorkorders.length < page.ids.length)];
        });
    },

    get myWorkorderScope() {
        if (this.state.activeWorkcenter !== -1) {
            return null;
        }
        const { employees } = this.useEmployee;
        const employeeId = employees.admin.id || false;
        const activeEmployee = employees.connected.find((employee) => employee.id === employeeId);
        const sessionWorkorders = (activeEmployee?.workorder || []).map(({ id }) => id).sort((a, b) => a - b);
        const search = this.env.searchModel;
        const states = search.state.workorderFilters
            .filter((filter) => filter.isActive && filter.name !== "cancel")
            .map((filter) => filter.name);
        const station = Object.values(search.searchItems)
            .find((item) => item.name === "shop_floor_this_station");
        const workcenters = station && search.query.some((item) => item.searchItemId === station.id)
            ? this.state.workcenters.map(({ id }) => id)
            : null;
        return {
            employeeId, sessionWorkorders, states, workcenters,
            key: JSON.stringify([employeeId, sessionWorkorders, states, workcenters]),
        };
    },

    get filteredWorkorders() {
        const workorders = super.filteredWorkorders;
        const scope = this.myWorkorderScope;
        if (!scope) {
            return workorders;
        }
        const page = this.model.root?.config.myWorkorderPage;
        return page?.key === scope.key
            ? workorders.filter(({ resId, data }) => {
                const assigned = data.employee_assigned_ids.resIds;
                return page.ids.includes(resId) && data.state !== "cancel"
                    && (!scope.workcenters || scope.workcenters.includes(data.workcenter_id[0]))
                    && (assigned.includes(scope.employeeId)
                        || (!assigned.length && scope.sessionWorkorders.includes(resId)));
            })
            : [];
    },

    get shopFloorProductionCount() {
        return this.myWorkorderScope && this.ordinaryShopFloorCards
            ? this.ordinaryShopFloorCards.productionCount : this.productions.length;
    },

    get shopFloorWorkcenterCards() {
        if (!this.myWorkorderScope || !this.ordinaryShopFloorCards) {
            return this.filteredWorkorders;
        }
        const { states } = this.myWorkorderScope;
        return this.ordinaryShopFloorCards.workorders.filter(({ data }) =>
            !states.length || states.includes(data.state)
        );
    },

    get shopFloorRelevantCount() {
        const scope = this.myWorkorderScope;
        if (!scope) {
            return this.relevantRecords.length;
        }
        const root = this.model.root;
        return root?.config.myWorkorderPage?.key === scope.key ? root.count : 0;
    },
});
