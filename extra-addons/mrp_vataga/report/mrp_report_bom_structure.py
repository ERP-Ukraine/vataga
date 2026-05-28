from collections import defaultdict, OrderedDict
from datetime import date

from odoo import api, fields, models
from odoo.tools import float_compare


class ReportBomStructure(models.AbstractModel):
    _inherit = "report.mrp.report_bom_structure"

    @api.model
    def _get_selected_warehouse_ids(self):
        warehouse_ids = self.env.context.get("warehouse_ids") or []
        if isinstance(warehouse_ids, (int, str)):
            warehouse_ids = [warehouse_ids]
        if warehouse_ids:
            return [int(warehouse_id) for warehouse_id in warehouse_ids if warehouse_id]
        warehouse_id = self.env.context.get("warehouse")
        return [int(warehouse_id)] if warehouse_id else []

    @api.model
    def _is_multi_warehouse_context(self):
        return len(self._get_selected_warehouse_ids()) > 1

    @api.model
    def _get_multi_warehouse_forecast_quantities(self, product_ids, date_today):
        selected_warehouse_ids = self._get_selected_warehouse_ids()
        forecast_rows = self.env["report.stock.quantity"].search_read(
            [
                ("state", "=", "forecast"),
                ("date", ">=", date_today),
                ("product_id", "in", list(set(product_ids))),
                ("warehouse_id", "in", selected_warehouse_ids),
            ],
            fields=["product_id", "product_qty", "date"],
            order="product_id asc, date asc",
        )
        forecast_by_product = defaultdict(lambda: defaultdict(float))
        for row in forecast_rows:
            product = row.get("product_id")
            if not product:
                continue
            product_id = product[0]
            forecast_date = fields.Date.to_date(row["date"])
            forecast_by_product[product_id][forecast_date] += row["product_qty"]
        return {
            product_id: sorted(product_forecast.items())
            for product_id, product_forecast in forecast_by_product.items()
        }

    @api.model
    def _get_closest_forecasted_date(self, forecast_quantities, required_qty):
        for forecast_date, available_qty in forecast_quantities:
            if available_qty >= required_qty:
                return forecast_date
        return False

    @api.model
    def _get_quantities_info(self, product, bom_uom, product_info, parent_bom=False, parent_product=False):
        if not self._is_multi_warehouse_context():
            return super()._get_quantities_info(
                product,
                bom_uom,
                product_info,
                parent_bom=parent_bom,
                parent_product=parent_product,
            )

        if product.detailed_type != "product":
            return {
                "free_qty": 0,
                "on_hand_qty": 0,
                "stock_loc": "in_stock",
                "free_to_manufacture_qty": 0,
            }

        raw_free_qty_total = 0
        on_hand_qty = 0
        for warehouse_id in self._get_selected_warehouse_ids():
            warehouse_product = product.with_context(warehouse=warehouse_id)
            warehouse_free_qty = warehouse_product.uom_id._compute_quantity(
                warehouse_product.free_qty, bom_uom
            )
            raw_free_qty_total += warehouse_free_qty
            on_hand_qty += warehouse_product.uom_id._compute_quantity(
                warehouse_product.qty_available, bom_uom
            )
        free_qty = max(raw_free_qty_total, 0)

        return {
            "free_qty": free_qty,
            "on_hand_qty": on_hand_qty,
            "stock_loc": "in_stock",
            "free_to_manufacture_qty": free_qty,
        }

    @api.model
    def _get_availabilities(
        self,
        product,
        quantity,
        product_info,
        bom_key,
        quantities_info,
        level,
        ignore_stock=False,
        components=False,
        bom_line=None,
        report_line=False,
    ):
        availabilities = super()._get_availabilities(
            product,
            quantity,
            product_info,
            bom_key,
            quantities_info,
            level,
            ignore_stock=ignore_stock,
            components=components,
            bom_line=bom_line,
            report_line=report_line,
        )
        if (
            self._is_multi_warehouse_context()
            and not ignore_stock
            and level == 0
            and availabilities.get("stock_avail_state") == "available"
        ):
            stock_state = availabilities["stock_avail_state"]
            stock_delay = 0
            availabilities.update(
                {
                    "availability_display": self._format_date_display(stock_state, stock_delay),
                    "availability_state": stock_state,
                    "availability_delay": stock_delay,
                }
            )
        return availabilities

    @api.model
    def _get_components_closest_forecasted(
        self, lines, line_quantities, parent_bom, product_info, parent_product, ignore_stock=False
    ):
        if ignore_stock or not self._is_multi_warehouse_context():
            return super()._get_components_closest_forecasted(
                lines, line_quantities, parent_bom, product_info, parent_product, ignore_stock=ignore_stock
            )

        closest_forecasted = defaultdict(OrderedDict)
        remaining_products = []
        product_quantities_info = defaultdict(OrderedDict)
        for line in lines:
            product = line.product_id
            quantities_info = self._get_quantities_info(
                product, line.product_uom_id, product_info, parent_bom, parent_product
            )
            stock_loc = quantities_info["stock_loc"]
            product_info[product.id]["consumptions"][stock_loc] += line_quantities.get(line.id, 0.0)
            product_quantities_info[product.id][line.id] = product_info[product.id]["consumptions"][stock_loc]
            if (
                product.detailed_type != "product"
                or float_compare(
                    product_info[product.id]["consumptions"][stock_loc],
                    quantities_info["free_qty"],
                    precision_rounding=product.uom_id.rounding,
                )
                <= 0
            ):
                closest_forecasted[product.id][line.id] = date.min
            elif stock_loc != "in_stock":
                closest_forecasted[product.id][line.id] = date.max
            else:
                remaining_products.append(product.id)
                closest_forecasted[product.id][line.id] = None

        date_today = self.env.context.get("from_date", fields.Date.today())
        forecast_quantities = self._get_multi_warehouse_forecast_quantities(
            remaining_products, date_today
        )
        for product_id in remaining_products:
            line_id = next(
                filter(lambda key: not closest_forecasted[product_id][key], closest_forecasted[product_id].keys()),
                None,
            )
            closest_date = self._get_closest_forecasted_date(
                forecast_quantities.get(product_id, []),
                product_quantities_info[product_id][line_id],
            )
            closest_forecasted[product_id][line_id] = closest_date or date.max
        return closest_forecasted

    @api.model
    def _get_stock_availability(self, product, quantity, product_info, quantities_info, bom_line=None):
        if not self._is_multi_warehouse_context():
            return super()._get_stock_availability(
                product, quantity, product_info, quantities_info, bom_line=bom_line
            )

        closest_forecasted = None
        if bom_line:
            closest_forecasted = (
                self.env.context.get("components_closest_forecasted", {})
                .get(product.id, {})
                .get(bom_line.id)
            )
        if closest_forecasted == date.min:
            return ("available", 0)
        if closest_forecasted == date.max:
            return ("unavailable", False)

        date_today = self.env.context.get("from_date", fields.Date.today())
        if product and product.detailed_type != "product":
            return ("available", 0)

        stock_loc = quantities_info["stock_loc"]
        product_info[product.id]["consumptions"][stock_loc] += quantity
        if product and float_compare(
            product_info[product.id]["consumptions"][stock_loc],
            quantities_info["free_qty"],
            precision_rounding=product.uom_id.rounding,
        ) <= 0:
            return ("available", 0)

        if stock_loc == "in_stock":
            if not closest_forecasted:
                closest_forecasted = self._get_closest_forecasted_date(
                    self._get_multi_warehouse_forecast_quantities([product.id], date_today).get(
                        product.id, []
                    ),
                    product_info[product.id]["consumptions"][stock_loc],
            )
            if closest_forecasted:
                return ("expected", (closest_forecasted - date_today).days)
        return ("unavailable", False)
