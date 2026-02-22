/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { formatFloat } from "@web/views/fields/formatters";

import { ProductCatalogPurchaseOrderLine } from "@purchase/product_catalog/purchase_order_line/purchase_order_line"

patch(ProductCatalogPurchaseOrderLine.prototype, {
    get demand(){
        const digits = [false, this.env.precision];
        const options = { digits, decimalPoint: ".", thousandsSep: "" };
        return parseFloat(formatFloat(this.props.demand + this.props.start_quantity - this.props.quantity, options));
    },
})

patch(ProductCatalogPurchaseOrderLine, {
    props : {
        ...ProductCatalogPurchaseOrderLine.props,
        demand: Number,
        in_contract: Boolean,
        start_quantity: Number,
    }
})
