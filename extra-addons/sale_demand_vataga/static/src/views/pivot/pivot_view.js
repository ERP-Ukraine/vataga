/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { PivotArchParser } from "@web/views/pivot/pivot_arch_parser";
import { PivotController } from "@web/views/pivot/pivot_controller";
import { PivotModel } from "@web/views/pivot/pivot_model";
import { PivotSearchModel } from "@web/views/pivot/pivot_search_model";
import { PivotRendererDemand } from "./pivot_renderer";

const viewRegistry = registry.category("views");

function demandPivotProfileRequested(context = {}) {
    if (context.profile_demand_pivot) {
        return true;
    }
    const url = new URL(window.location.href);
    const hashParams = new URLSearchParams(url.hash.replace(/^#/, ""));
    return (
        url.searchParams.get("profile_demand_pivot") === "1" ||
        hashParams.get("profile_demand_pivot") === "1"
    );
}

function getJsonByteLength(value) {
    return new Blob([JSON.stringify(value)], {
        type: "application/json",
    }).size;
}

export class PivotModelDemand extends PivotModel {
    async load(searchParams) {
        this.demandProfileEnabled = demandPivotProfileRequested(
            searchParams.context
        );
        this.demandProfileRpcCalls = [];
        this.demandProfilePrepareDataMs = 0;

        const profiledSearchParams = this.demandProfileEnabled
            ? {
                  ...searchParams,
                  context: {
                      ...searchParams.context,
                      profile_demand_pivot: true,
                  },
              }
            : searchParams;
        const startedAt = performance.now();
        const result = await super.load(profiledSearchParams);

        if (this.demandProfileEnabled) {
            const finishedAt = performance.now();
            const rpcStarts = this.demandProfileRpcCalls.map(
                (call) => call.startedAt
            );
            const rpcEnds = this.demandProfileRpcCalls.map(
                (call) => call.finishedAt
            );
            const rpcWallMs = rpcStarts.length
                ? Math.max(...rpcEnds) - Math.min(...rpcStarts)
                : 0;
            const combinedCall = this.demandProfileRpcCalls.find(
                (call) =>
                    call.rowGroupBy.includes("product_id") &&
                    call.colGroupBy.includes("sale_contract_id")
            );
            const productCall = this.demandProfileRpcCalls.find(
                (call) =>
                    call.rowGroupBy.includes("product_id") &&
                    !call.colGroupBy.length
            );
            const contractCall = this.demandProfileRpcCalls.find(
                (call) =>
                    !call.rowGroupBy.length &&
                    call.colGroupBy.includes("sale_contract_id")
            );
            const totalCall = this.demandProfileRpcCalls.find(
                (call) => !call.rowGroupBy.length && !call.colGroupBy.length
            );

            console.info("[DemandPivotProfile] client", {
                load_total_ms: Number((finishedAt - startedAt).toFixed(2)),
                rpc_wall_ms: Number(rpcWallMs.toFixed(2)),
                rpc_calls: this.demandProfileRpcCalls.length,
                rpc_calls_total_ms: Number(
                    this.demandProfileRpcCalls
                        .reduce((total, call) => total + call.durationMs, 0)
                        .toFixed(2)
                ),
                pivot_model_non_rpc_estimate_ms: Number(
                    Math.max(0, finishedAt - startedAt - rpcWallMs).toFixed(2)
                ),
                pivot_model_prepare_data_ms: Number(
                    this.demandProfilePrepareDataMs.toFixed(2)
                ),
                product_analytic_records:
                    totalCall?.subGroups[0]?.__count || 0,
                product_groups: productCall?.subGroups.length || 0,
                contract_groups: contractCall?.subGroups.length || 0,
                returned_cells: combinedCall?.subGroups.length || 0,
                response_json_bytes: this.demandProfileRpcCalls.reduce(
                    (total, call) => total + call.responseBytes,
                    0
                ),
                active_measures: [...this.metaData.activeMeasures],
            });
        }
        return result;
    }

    async _getGroupSubdivision(group, rowGroupBy, colGroupBy, config) {
        if (!this.demandProfileEnabled) {
            return super._getGroupSubdivision(
                group,
                rowGroupBy,
                colGroupBy,
                config
            );
        }

        const startedAt = performance.now();
        const result = await super._getGroupSubdivision(
            group,
            rowGroupBy,
            colGroupBy,
            config
        );
        const finishedAt = performance.now();
        const call = {
            rowGroupBy: rowGroupBy.map((groupBy) => this._normalize(groupBy)),
            colGroupBy: colGroupBy.map((groupBy) => this._normalize(groupBy)),
            startedAt,
            finishedAt,
            durationMs: finishedAt - startedAt,
            subGroups: result.subGroups,
            responseBytes: getJsonByteLength(result.subGroups),
        };
        this.demandProfileRpcCalls.push(call);
        console.info("[DemandPivotProfile] read_group", {
            row_group_by: call.rowGroupBy,
            col_group_by: call.colGroupBy,
            rpc_ms: Number(call.durationMs.toFixed(2)),
            groups: call.subGroups.length,
            response_json_bytes: call.responseBytes,
        });
        return result;
    }

    _prepareData(group, groupSubdivisions, config) {
        if (!this.demandProfileEnabled) {
            return super._prepareData(group, groupSubdivisions, config);
        }
        const startedAt = performance.now();
        const result = super._prepareData(group, groupSubdivisions, config);
        this.demandProfilePrepareDataMs += performance.now() - startedAt;
        return result;
    }

    getTable() {
        if (!this.demandProfileEnabled) {
            return super.getTable();
        }
        const startedAt = performance.now();
        const table = super.getTable();
        const finishedAt = performance.now();
        const bodyCells = table.rows.reduce(
            (total, row) => total + 1 + row.subGroupMeasurements.length,
            0
        );
        const headerCells = table.headers.reduce(
            (total, row) => total + row.length,
            0
        );
        console.info("[DemandPivotProfile] table", {
            pivot_table_build_ms: Number((finishedAt - startedAt).toFixed(2)),
            header_rows: table.headers.length,
            body_rows: table.rows.length,
            approximate_cells: headerCells + bodyCells,
        });
        return table;
    }
}

export const pivotView = {
    type: "pivot",
    display_name: _t("Pivot"),
    icon: "oi oi-view-pivot",
    multiRecord: true,
    Controller: PivotController,
    Renderer: PivotRendererDemand,
    Model: PivotModelDemand,
    ArchParser: PivotArchParser,
    SearchModel: PivotSearchModel,
    searchMenuTypes: ["filter", "groupBy", "comparison", "favorite"],

    props: (genericProps, view) => {
        const modelParams = {};
        if (genericProps.state) {
            modelParams.data = genericProps.state.data;
            modelParams.metaData = genericProps.state.metaData;
        } else {
            const { arch, fields, resModel } = genericProps;

            // parse arch
            const archInfo = new view.ArchParser().parse(arch);

            if (!archInfo.activeMeasures.length || archInfo.displayQuantity) {
                archInfo.activeMeasures.unshift("__count");
            }

            modelParams.metaData = {
                activeMeasures: archInfo.activeMeasures,
                colGroupBys: archInfo.colGroupBys,
                defaultOrder: archInfo.defaultOrder,
                disableLinking: Boolean(archInfo.disableLinking),
                fields: fields,
                fieldAttrs: archInfo.fieldAttrs,
                resModel: resModel,
                rowGroupBys: archInfo.rowGroupBys,
                title: archInfo.title || _t("Untitled"),
                widgets: archInfo.widgets,
            };
        }

        return {
            ...genericProps,
            Model: view.Model,
            modelParams,
            Renderer: view.Renderer,
        };
    },
};

viewRegistry.add("pivot_demand", pivotView);
