import json
import logging
from collections import defaultdict, OrderedDict
from datetime import date, datetime

from odoo import api, fields, models
from odoo.tools import float_compare


_logger = logging.getLogger(__name__)


class ReportBomStructure(models.AbstractModel):
    _inherit = "report.mrp.report_bom_structure"

    @api.model
    def _is_multi_warehouse_debug_enabled(self):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "mrp_vataga.bom_multiwarehouse_debug_enabled"
        )
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    @api.model
    def _json_debug_value(self, value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if hasattr(value, "ids"):
            return value.ids
        return value

    @api.model
    def _append_multi_warehouse_debug(self, event, payload):
        if not self._is_multi_warehouse_debug_enabled():
            return

        config = self.env["ir.config_parameter"].sudo()
        output_param = "mrp_vataga.bom_multiwarehouse_debug_output"
        try:
            limit = int(
                config.get_param("mrp_vataga.bom_multiwarehouse_debug_limit", default="300")
                or 300
            )
        except ValueError:
            limit = 300
        try:
            current_output = json.loads(config.get_param(output_param, default="[]") or "[]")
        except ValueError:
            current_output = []

        entry = {
            "event": event,
            "payload": payload,
            "context": {
                "warehouse": self.env.context.get("warehouse"),
                "warehouse_ids": self.env.context.get("warehouse_ids"),
                "from_date": self._json_debug_value(self.env.context.get("from_date")),
            },
        }
        current_output.append(entry)
        if limit > 0:
            current_output = current_output[-limit:]

        config.set_param(
            output_param,
            json.dumps(
                current_output,
                ensure_ascii=False,
                default=self._json_debug_value,
                indent=2,
            ),
        )
        _logger.info("BOM multi-warehouse debug %s: %s", event, payload)

    @api.model
    def reset_multi_warehouse_debug_output(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "mrp_vataga.bom_multiwarehouse_debug_output", "[]"
        )

    @api.model
    def _get_debug_line_summary(self, line, level=0):
        if isinstance(line, list):
            return [
                self._get_debug_line_summary(item, level=level)
                for item in line
            ]
        if not isinstance(line, dict):
            return line

        interesting_keys = (
            "name",
            "product_id",
            "product",
            "product_qty",
            "quantity",
            "free_qty",
            "on_hand_qty",
            "available_qty",
            "availability",
            "availability_state",
            "components_available",
            "stock",
        )
        summary = {
            key: value
            for key, value in line.items()
            if key in interesting_keys
            or "qty" in key
            or "avail" in key
            or "stock" in key
        }
        components = line.get("components") or []
        if components and level < 3:
            summary["components"] = [
                self._get_debug_line_summary(component, level=level + 1)
                for component in components
            ]
        elif components:
            summary["components_count"] = len(components)
        return summary

    @api.model
    def debug_multi_warehouse_bom(self, bom_id, warehouse_ids, quantity=1.0, product_variant_id=False):
        config = self.env["ir.config_parameter"].sudo()
        config.set_param("mrp_vataga.bom_multiwarehouse_debug_enabled", "1")
        self.reset_multi_warehouse_debug_output()

        selected_warehouse_ids = [
            int(warehouse_id) for warehouse_id in warehouse_ids if warehouse_id
        ]
        debug_report = self.with_context(
            warehouse_ids=selected_warehouse_ids,
            warehouse=selected_warehouse_ids[0] if selected_warehouse_ids else False,
        )
        try:
            bom_data = debug_report.get_html(bom_id, quantity, product_variant_id)
            debug_report._append_multi_warehouse_debug(
                "bom_html_result",
                {
                    "bom_id": bom_id,
                    "quantity": quantity,
                    "product_variant_id": product_variant_id,
                    "selected_warehouse_ids": selected_warehouse_ids,
                    "lines": debug_report._get_debug_line_summary(bom_data.get("lines")),
                },
            )
        except Exception as error:
            debug_report._append_multi_warehouse_debug(
                "bom_html_error",
                {
                    "bom_id": bom_id,
                    "quantity": quantity,
                    "product_variant_id": product_variant_id,
                    "selected_warehouse_ids": selected_warehouse_ids,
                    "error": repr(error),
                },
            )
            raise
        return config.get_param("mrp_vataga.bom_multiwarehouse_debug_output")

    @api.model
    def _get_selected_warehouse_ids(self):
        warehouse_ids = self.env.context.get("warehouse_ids") or []
        if isinstance(warehouse_ids, (int, str)):
            warehouse_ids = [warehouse_ids]
        if warehouse_ids:
            selected_warehouse_ids = [
                int(warehouse_id) for warehouse_id in warehouse_ids if warehouse_id
            ]
            self._append_multi_warehouse_debug(
                "selected_warehouses",
                {
                    "source": "warehouse_ids",
                    "selected_warehouse_ids": selected_warehouse_ids,
                },
            )
            return selected_warehouse_ids
        warehouse_id = self.env.context.get("warehouse")
        selected_warehouse_ids = [int(warehouse_id)] if warehouse_id else []
        self._append_multi_warehouse_debug(
            "selected_warehouses",
            {
                "source": "warehouse",
                "selected_warehouse_ids": selected_warehouse_ids,
            },
        )
        return selected_warehouse_ids

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
            fields=["product_id", "product_qty", "date", "warehouse_id"],
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
        result = {
            product_id: sorted(product_forecast.items())
            for product_id, product_forecast in forecast_by_product.items()
        }
        self._append_multi_warehouse_debug(
            "forecast_quantities",
            {
                "product_ids": product_ids,
                "selected_warehouse_ids": selected_warehouse_ids,
                "date_today": date_today,
                "forecast_rows": forecast_rows,
                "forecast_by_product": {
                    product_id: [
                        {"date": forecast_date, "available_qty": available_qty}
                        for forecast_date, available_qty in product_forecast.items()
                    ]
                    for product_id, product_forecast in forecast_by_product.items()
                },
            },
        )
        return result

    @api.model
    def _get_closest_forecasted_date(self, forecast_quantities, required_qty):
        for forecast_date, available_qty in forecast_quantities:
            if available_qty >= required_qty:
                self._append_multi_warehouse_debug(
                    "closest_forecasted_date",
                    {
                        "required_qty": required_qty,
                        "matched_date": forecast_date,
                        "matched_available_qty": available_qty,
                        "forecast_quantities": forecast_quantities,
                    },
                )
                return forecast_date
        self._append_multi_warehouse_debug(
            "closest_forecasted_date",
            {
                "required_qty": required_qty,
                "matched_date": False,
                "forecast_quantities": forecast_quantities,
            },
        )
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
        warehouse_quantities = []
        for warehouse_id in self._get_selected_warehouse_ids():
            warehouse = self.env["stock.warehouse"].browse(warehouse_id)
            warehouse_product = product.with_context(warehouse=warehouse_id)
            warehouse_free_qty = warehouse_product.uom_id._compute_quantity(
                warehouse_product.free_qty, bom_uom
            )
            warehouse_free_qty_used = max(warehouse_free_qty, 0)
            warehouse_on_hand_qty = warehouse_product.uom_id._compute_quantity(
                warehouse_product.qty_available, bom_uom
            )
            raw_free_qty_total += warehouse_free_qty
            on_hand_qty += warehouse_on_hand_qty
            warehouse_quantities.append(
                {
                    "warehouse_id": warehouse_id,
                    "warehouse_name": warehouse.display_name,
                    "raw_free_qty": warehouse_product.free_qty,
                    "raw_qty_available": warehouse_product.qty_available,
                    "free_qty_in_bom_uom": warehouse_free_qty,
                    "free_qty_used_in_total": warehouse_free_qty_used,
                    "on_hand_qty_in_bom_uom": warehouse_on_hand_qty,
                    "product_uom": warehouse_product.uom_id.display_name,
                    "bom_uom": bom_uom.display_name,
                }
            )
        free_qty = max(raw_free_qty_total, 0)

        self._append_multi_warehouse_debug(
            "quantities_info",
            {
                "product_id": product.id,
                "product_display_name": product.display_name,
                "product_default_code": product.default_code,
                "bom_uom": bom_uom.display_name,
                "raw_free_qty_total": raw_free_qty_total,
                "free_qty_total": free_qty,
                "on_hand_qty_total": on_hand_qty,
                "warehouse_quantities": warehouse_quantities,
            },
        )

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
            self._append_multi_warehouse_debug(
                "top_level_stock_availability_override",
                {
                    "product_id": product.id,
                    "product_display_name": product.display_name,
                    "product_default_code": product.default_code,
                    "stock_state": stock_state,
                    "stock_delay": stock_delay,
                    "quantity": quantity,
                    "free_qty": quantities_info.get("free_qty"),
                    "on_hand_qty": quantities_info.get("on_hand_qty"),
                },
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
            self._append_multi_warehouse_debug(
                "stock_availability",
                {
                    "decision": "available",
                    "reason": "closest_forecasted_date_min",
                    "product_id": product.id,
                    "bom_line_id": bom_line.id if bom_line else False,
                    "quantity": quantity,
                },
            )
            return ("available", 0)
        if closest_forecasted == date.max:
            self._append_multi_warehouse_debug(
                "stock_availability",
                {
                    "decision": "unavailable",
                    "reason": "closest_forecasted_date_max",
                    "product_id": product.id,
                    "bom_line_id": bom_line.id if bom_line else False,
                    "quantity": quantity,
                },
            )
            return ("unavailable", False)

        date_today = self.env.context.get("from_date", fields.Date.today())
        if product and product.detailed_type != "product":
            self._append_multi_warehouse_debug(
                "stock_availability",
                {
                    "decision": "available",
                    "reason": "not_stockable_product",
                    "product_id": product.id,
                    "bom_line_id": bom_line.id if bom_line else False,
                    "quantity": quantity,
                },
            )
            return ("available", 0)

        stock_loc = quantities_info["stock_loc"]
        product_info[product.id]["consumptions"][stock_loc] += quantity
        if product and float_compare(
            product_info[product.id]["consumptions"][stock_loc],
            quantities_info["free_qty"],
            precision_rounding=product.uom_id.rounding,
        ) <= 0:
            self._append_multi_warehouse_debug(
                "stock_availability",
                {
                    "decision": "available",
                    "reason": "consumption_lte_free_qty",
                    "product_id": product.id,
                    "product_display_name": product.display_name,
                    "product_default_code": product.default_code,
                    "bom_line_id": bom_line.id if bom_line else False,
                    "quantity": quantity,
                    "stock_loc": stock_loc,
                    "consumption": product_info[product.id]["consumptions"][stock_loc],
                    "free_qty": quantities_info["free_qty"],
                    "on_hand_qty": quantities_info["on_hand_qty"],
                    "precision_rounding": product.uom_id.rounding,
                },
            )
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
                self._append_multi_warehouse_debug(
                    "stock_availability",
                    {
                        "decision": "expected",
                        "reason": "forecast_found",
                        "product_id": product.id,
                        "product_display_name": product.display_name,
                        "product_default_code": product.default_code,
                        "bom_line_id": bom_line.id if bom_line else False,
                        "quantity": quantity,
                        "stock_loc": stock_loc,
                        "consumption": product_info[product.id]["consumptions"][stock_loc],
                        "free_qty": quantities_info["free_qty"],
                        "on_hand_qty": quantities_info["on_hand_qty"],
                        "closest_forecasted": closest_forecasted,
                        "days": (closest_forecasted - date_today).days,
                    },
                )
                return ("expected", (closest_forecasted - date_today).days)
        self._append_multi_warehouse_debug(
            "stock_availability",
            {
                "decision": "unavailable",
                "reason": "no_free_qty_or_forecast",
                "product_id": product.id,
                "product_display_name": product.display_name,
                "product_default_code": product.default_code,
                "bom_line_id": bom_line.id if bom_line else False,
                "quantity": quantity,
                "stock_loc": stock_loc,
                "consumption": product_info[product.id]["consumptions"][stock_loc],
                "free_qty": quantities_info["free_qty"],
                "on_hand_qty": quantities_info["on_hand_qty"],
            },
        )
        return ("unavailable", False)
