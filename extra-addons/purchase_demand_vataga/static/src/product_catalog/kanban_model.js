/** @odoo-module */

import { patch } from "@web/core/utils/patch";

import { ProductCatalogKanbanModel } from "@product/product_catalog/kanban_model"

patch(ProductCatalogKanbanModel.prototype, {
    async _loadData(params) {
        const result = await super._loadData(...arguments);
        if (!params.isMonoRecord && !params.groupBy.length) {
            const orderLinesInfo = await this.rpc("/product/catalog/order_lines_info", {
                order_id: params.context.order_id,
                // ERP start
                contract_id: params.context.contract_id,
                // ERP end
                product_ids: result.records.map((rec) => rec.id),
                res_model: params.context.product_catalog_order_model,
            });
            for (const record of result.records) {
                record.productCatalogData = orderLinesInfo[record.id];
            }
        }
        return result;
    }
})
