"""Read-only diagnostic service for the product.analytic analog rollup."""

import json
import os
import uuid
from collections import defaultdict
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO

from odoo import fields


DEFAULT_PRODUCT_CODES = ("RES-BEC-0113", "RES-BEC-0114")
DEFAULT_CONTRACT_REFERENCES = ("SE-10417",)
DEFAULT_DATE_FROM = None
DEFAULT_DATE_TO = None
DEFAULT_ALL_CONTRACTS = False
DEFAULT_WATCH_QUANTITIES = (2440.0, 1760.0, 444.0, 200.0, 244.0)
EPSILON = 1e-7
LOG_CHUNK_SIZE = 18000


def _csv_setting(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return list(default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _boolean_setting(name, default=False):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of 1/0, true/false, yes/no or on/off."
    )


def _date_setting(name, default=None):
    raw_value = os.environ.get(name)
    value = raw_value.strip() if raw_value else default
    if not value:
        return None
    try:
        return fields.Date.to_date(value)
    except Exception as error:
        raise RuntimeError(f"{name} must use YYYY-MM-DD format.") from error


def _float_settings(name, default):
    values = _csv_setting(name, default)
    try:
        return [float(value) for value in values]
    except ValueError as error:
        raise RuntimeError(f"{name} must contain comma-separated numbers.") from error


def configuration_from_environment():
    """Return shell-compatible parameters without duplicating diagnostics."""
    return {
        "product_codes": _csv_setting(
            "ANALOG_DIAG_PRODUCTS", DEFAULT_PRODUCT_CODES
        ),
        "contract_references": _csv_setting(
            "ANALOG_DIAG_CONTRACTS", DEFAULT_CONTRACT_REFERENCES
        ),
        "all_contracts": _boolean_setting(
            "ANALOG_DIAG_ALL_CONTRACTS", DEFAULT_ALL_CONTRACTS
        ),
        "date_from": _date_setting(
            "ANALOG_DIAG_DATE_FROM", DEFAULT_DATE_FROM
        ),
        "date_to": _date_setting("ANALOG_DIAG_DATE_TO", DEFAULT_DATE_TO),
        "watch_quantities": _float_settings(
            "ANALOG_DIAG_WATCH_QUANTITIES", DEFAULT_WATCH_QUANTITIES
        ),
    }


def _normalize_configuration(
    product_codes=None,
    contract_references=None,
    all_contracts=False,
    date_from=None,
    date_to=None,
    watch_quantities=None,
):
    product_codes = (
        DEFAULT_PRODUCT_CODES if product_codes is None else product_codes
    )
    contract_references = (
        DEFAULT_CONTRACT_REFERENCES
        if contract_references is None
        else contract_references
    )
    watch_quantities = (
        DEFAULT_WATCH_QUANTITIES
        if watch_quantities is None
        else watch_quantities
    )
    config = {
        "product_codes": [str(value).strip() for value in product_codes if value],
        "contract_references": [
            str(value).strip() for value in contract_references if value
        ],
        "all_contracts": bool(all_contracts),
        "date_from": fields.Date.to_date(date_from) if date_from else None,
        "date_to": fields.Date.to_date(date_to) if date_to else None,
        "watch_quantities": [float(value) for value in watch_quantities],
    }
    if config["date_from"] and config["date_to"]:
        if config["date_from"] > config["date_to"]:
            raise RuntimeError("date_from must not exceed date_to.")
    if not config["product_codes"]:
        raise RuntimeError("At least one product code is required.")
    if not config["all_contracts"] and not config["contract_references"]:
        raise RuntimeError(
            "At least one contract reference is required unless all_contracts=True."
        )
    return config


def _require_model_api(env):
    requirements = {
        "product.product": {
            "fields": {"default_code", "product_tmpl_id", "uom_id"},
            "methods": {
                "_get_allowed_analog_rollup_target_products",
                "_get_analog_rollup_products",
                "_get_bidirectional_analog_group_products",
                "_get_direct_analog_counterpart_products",
                "_get_selected_analog_rollup_target_product",
            },
        },
        "purchase.order.line": {
            "fields": {
                "product_id",
                "product_qty",
                "product_uom",
                "qty_received",
                "analog_original_product_id",
                "analytic_distribution",
                "seller_contract_id",
                "move_ids",
                "invoice_lines",
            },
            "methods": {
                "_get_analog_original_product_for_rollup",
                "_get_demand_report_seller_contracts",
                "_has_demand_report_seller_contract",
                "_get_po_line_moves",
            },
        },
        "account.move.line": {
            "fields": {
                "product_id",
                "quantity",
                "product_uom_id",
                "analog_original_product_id",
                "analytic_distribution",
                "seller_contract_id",
                "purchase_line_id",
            },
            "methods": {
                "_get_analog_original_product_for_rollup",
                "_get_purchase_lines_for_analog_origin",
                "_get_purchase_line_analog_original_product",
                "_get_seller_contracts_for_analog_target",
            },
        },
        "product.analytic": {
            "fields": {
                "product_id",
                "sale_contract_id",
                "demand",
                "in_invoice",
                "qty_received",
                "closed",
                "account_move_ids",
                "need_to_purchase_ids",
                "kit_bom_ids",
            },
            "methods": {
                "_get_related_invoice_moves",
                "_is_invoice_line_related_to_rollup_product",
                "_is_purchase_line_related_to_rollup_product",
            },
        },
    }
    errors = []
    for model_name, requirement in requirements.items():
        model = env[model_name]
        missing_fields = sorted(requirement["fields"] - set(model._fields))
        missing_methods = sorted(
            method for method in requirement["methods"] if not hasattr(model, method)
        )
        if missing_fields:
            errors.append(f"{model_name}: missing fields {missing_fields}")
        if missing_methods:
            errors.append(f"{model_name}: missing methods {missing_methods}")
    if errors:
        raise RuntimeError("Required module API is unavailable: " + "; ".join(errors))


def _iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat(sep=" ")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _record_ref(record):
    if not record:
        return None
    values = {"id": record.id, "name": record.display_name}
    if "default_code" in record._fields:
        values["default_code"] = record.default_code or None
    if "code" in record._fields:
        values["code"] = record.code or None
    return values


def _number(value):
    return round(float(value or 0.0), 8)


def _close(left, right, epsilon=EPSILON):
    return abs(float(left or 0.0) - float(right or 0.0)) <= epsilon


def _date_in_scope(value, date_from, date_to):
    if not value:
        return not date_from and not date_to
    current_date = value.date() if isinstance(value, datetime) else value
    if date_from and current_date < date_from:
        return False
    if date_to and current_date > date_to:
        return False
    return True


def _distribution_account_ids(record):
    account_ids = set()
    for key in (record.analytic_distribution or {}):
        for candidate in str(key).split(","):
            candidate = candidate.strip()
            if candidate.isdigit():
                account_ids.add(int(candidate))
    return account_ids


def _contract_sources(record, contract, source_kind):
    sources = []
    if record.seller_contract_id == contract:
        sources.append("line.seller_contract_id")
    if contract.id in _distribution_account_ids(record):
        sources.append("analytic_distribution")
    parent = record.order_id if source_kind == "purchase" else record.move_id
    if "seller_contract_id" in parent._fields and parent.seller_contract_id == contract:
        sources.append(
            "order_id.seller_contract_id"
            if source_kind == "purchase"
            else "move_id.seller_contract_id"
        )
    return sources


def _target_classification(product, raw_target):
    allowed = product._get_allowed_analog_rollup_target_products()
    if not raw_target:
        return "blank"
    if raw_target == product and raw_target in allowed:
        return "self"
    if raw_target in allowed:
        return "valid counterpart"
    return "invalid explicit target"


def _purchase_target_info(line):
    # Audited resolver call: the method only reads analog relations and returns
    # a product recordset; it does not alter the line or any stored field.
    raw_target = line.analog_original_product_id
    resolved_target = line._get_analog_original_product_for_rollup()
    allowed = line.product_id._get_allowed_analog_rollup_target_products()
    return {
        "stored_target": _record_ref(raw_target),
        "classification": _target_classification(line.product_id, raw_target),
        "allowed_targets": [_record_ref(product) for product in allowed],
        "resolver_target": _record_ref(resolved_target),
        "resolver_source": "stored target" if raw_target else "blank fallback",
    }


def _invoice_target_info(line):
    raw_target = line.analog_original_product_id
    purchase_lines = line._get_purchase_lines_for_analog_origin()
    purchase_target = line._get_purchase_line_analog_original_product()
    effective_selected = raw_target or purchase_target
    resolved_target = line._get_analog_original_product_for_rollup()
    allowed = line.product_id._get_allowed_analog_rollup_target_products()
    if raw_target:
        resolver_source = "account.move.line.analog_original_product_id"
    elif purchase_target:
        resolver_source = "purchase line target"
    else:
        resolver_source = "blank fallback"
    return {
        "stored_target": _record_ref(raw_target),
        "classification": _target_classification(line.product_id, raw_target),
        "allowed_targets": [_record_ref(product) for product in allowed],
        "purchase_line_ids": purchase_lines.ids,
        "purchase_targets": [
            {
                "purchase_line_id": purchase_line.id,
                "target": _record_ref(purchase_line.analog_original_product_id),
                "resolver_target": _record_ref(
                    purchase_line._get_analog_original_product_for_rollup()
                ),
                "classification": _target_classification(
                    purchase_line.product_id,
                    purchase_line.analog_original_product_id,
                ),
            }
            for purchase_line in purchase_lines
        ],
        "effective_selected_target": _record_ref(effective_selected),
        "resolver_target": _record_ref(resolved_target),
        "resolver_source": resolver_source,
    }


def _convert_quantity(quantity, source_uom, target_uom):
    if not source_uom or not target_uom:
        return {
            "quantity": None,
            "error": "source or target UoM is missing",
        }
    try:
        converted = source_uom._compute_quantity(quantity, target_uom)
        return {
            "quantity": _number(converted),
            "source_uom": source_uom.display_name,
            "target_uom": target_uom.display_name,
            "error": None,
        }
    except Exception as error:
        return {
            "quantity": None,
            "source_uom": source_uom.display_name,
            "target_uom": target_uom.display_name,
            "error": str(error),
        }


def _stock_move_contribution(move, purchase_line):
    conversion = _convert_quantity(
        move.quantity,
        move.product_uom,
        purchase_line.product_uom,
    )
    converted = conversion["quantity"]
    reason = None
    contribution = 0.0
    participates = move.product_id == purchase_line.product_id
    is_return = bool(move._is_purchase_return())
    if not participates:
        reason = "excluded by _get_po_line_moves(): product differs from PO line"
    elif move.state != "done":
        reason = "stock move is not done"
    elif conversion["error"]:
        reason = "UoM conversion failed"
    elif is_return:
        if not move.origin_returned_move_id or move.to_refund:
            contribution = -converted
            reason = "purchase return subtracted by standard qty_received"
        else:
            reason = "purchase return ignored by standard qty_received"
    elif (
        move.origin_returned_move_id
        and move.origin_returned_move_id._is_dropshipped()
        and not move._is_dropshipped_returned()
    ):
        reason = "dropship return to stock ignored by standard qty_received"
    elif (
        move.origin_returned_move_id
        and move.origin_returned_move_id._is_purchase_return()
        and not move.to_refund
    ):
        reason = "return of purchase return ignored by standard qty_received"
    else:
        contribution = converted
        reason = "done stock move added by standard qty_received"
    return {
        "stock_move_id": move.id,
        "picking_id": move.picking_id.id or None,
        "picking": move.picking_id.name or None,
        "state": move.state,
        "date": _iso(move.date),
        "product": _record_ref(move.product_id),
        "ordered_move_quantity": _number(move.product_uom_qty),
        "done_quantity": _number(move.quantity),
        "uom": move.product_uom.display_name,
        "origin_returned_move_id": move.origin_returned_move_id.id or None,
        "is_purchase_return": is_return,
        "to_refund": bool(move.to_refund),
        "participates_in_standard_qty_received": participates,
        "converted_to_po_uom": conversion,
        "contribution_to_po_line_qty_received": _number(contribution),
        "reason": reason,
    }


def _resolve_contracts(env, references):
    AnalyticAccount = env["account.analytic.account"].sudo()
    contracts = AnalyticAccount.browse()
    resolutions = []
    for reference in references:
        normalized = reference.strip()
        explicit_id = None
        if normalized.lower().startswith("id:"):
            candidate = normalized.split(":", 1)[1].strip()
            explicit_id = int(candidate) if candidate.isdigit() else None
        elif normalized.isdigit():
            explicit_id = int(normalized)
        if explicit_id:
            matched = AnalyticAccount.browse(explicit_id).exists()
            strategy = "ID"
        else:
            exact_domain = [
                ("is_plan_seller_contract", "=", True),
                ("name", "=ilike", normalized),
            ]
            if "code" in AnalyticAccount._fields:
                exact_domain = [
                    ("is_plan_seller_contract", "=", True),
                    "|",
                    ("name", "=ilike", normalized),
                    ("code", "=ilike", normalized),
                ]
            matched = AnalyticAccount.search(exact_domain)
            strategy = "exact name/code"
            if not matched:
                fuzzy_domain = [
                    ("is_plan_seller_contract", "=", True),
                    ("name", "ilike", normalized),
                ]
                if "code" in AnalyticAccount._fields:
                    fuzzy_domain = [
                        ("is_plan_seller_contract", "=", True),
                        "|",
                        ("name", "ilike", normalized),
                        ("code", "ilike", normalized),
                    ]
                matched = AnalyticAccount.search(fuzzy_domain)
                strategy = "partial name/code"
        resolutions.append(
            {
                "reference": reference,
                "strategy": strategy,
                "matches": [_record_ref(contract) for contract in matched],
            }
        )
        contracts |= matched
    return contracts, resolutions


def _live_kit_boms(env, product):
    return env["mrp.bom"].sudo().search(
        [
            ("bom_line_ids.product_id", "=", product.id),
            ("type", "=", "phantom"),
        ]
    )


def _source_product_universe(env, products):
    universe = products._get_bidirectional_analog_group_products()
    for target_product in products:
        for bom in _live_kit_boms(env, target_product):
            kit_parents = bom.product_id | bom.product_tmpl_id.product_variant_ids
            for kit_parent in kit_parents:
                universe |= kit_parent._get_analog_rollup_products()
    return universe


def _line_date(line, source_kind):
    if source_kind == "purchase":
        return line.order_id.date_order
    return line.move_id.invoice_date or line.move_id.date


def _line_contracts(line, source_kind):
    if source_kind == "purchase":
        return line._get_demand_report_seller_contracts()
    return line._get_seller_contracts_for_analog_target()


def _purchase_line_detail(line, selected_contracts):
    target = _purchase_target_info(line)
    stock_moves = [
        _stock_move_contribution(move, line)
        for move in line.move_ids.sorted("id")
    ]
    standard_move_total = sum(
        move["contribution_to_po_line_qty_received"] for move in stock_moves
    )
    contracts = line._get_demand_report_seller_contracts()
    matching_contracts = contracts & selected_contracts
    linked_invoice_lines = line.invoice_lines.sorted("id")
    return {
        "purchase_line_id": line.id,
        "purchase_order_id": line.order_id.id,
        "purchase_order": line.order_id.name,
        "purchase_state": line.order_id.state,
        "order_date": _iso(line.order_id.date_order),
        "product": _record_ref(line.product_id),
        "ordered_quantity": _number(line.product_qty),
        "uom": line.product_uom.display_name,
        "stored_qty_received": _number(line.qty_received),
        "qty_received_method": line.qty_received_method,
        "contracts": [_record_ref(contract) for contract in contracts],
        "selected_contract_sources": [
            {
                "contract": _record_ref(contract),
                "sources": _contract_sources(line, contract, "purchase"),
            }
            for contract in matching_contracts
        ],
        "target": target,
        "invoice_lines": [
            {
                "account_move_line_id": invoice_line.id,
                "move_id": invoice_line.move_id.id,
                "move": invoice_line.move_id.name,
                "move_type": invoice_line.move_id.move_type,
                "state": invoice_line.move_id.state,
                "quantity": _number(invoice_line.quantity),
                "target": _record_ref(invoice_line.analog_original_product_id),
            }
            for invoice_line in linked_invoice_lines
        ],
        "pickings": [
            {
                "id": picking.id,
                "name": picking.name,
                "state": picking.state,
                "date_done": _iso(picking.date_done),
            }
            for picking in line.move_ids.mapped("picking_id").sorted("id")
        ],
        "stock_moves": stock_moves,
        "stock_move_contribution_total": _number(standard_move_total),
        "stock_move_vs_stored_difference": _number(
            line.qty_received - standard_move_total
        ),
        "included_by_report_state": line.order_id.state in ("purchase", "done"),
    }


def _invoice_line_exclusion_reason(line, resolved_target):
    if line.move_id.state != "posted":
        return "document is not posted"
    if line.move_id.move_type not in ("in_invoice", "in_refund"):
        return "document is not a vendor bill/refund"
    if not resolved_target:
        return "target is unresolved"
    return None


def _invoice_line_detail(line, selected_contracts):
    target = _invoice_target_info(line)
    resolved_target_id = (
        target["resolver_target"]["id"] if target["resolver_target"] else None
    )
    resolved_target = line.env["product.product"].browse(resolved_target_id)
    signed_quantity = (
        line.quantity if line.move_id.move_type == "in_invoice" else -line.quantity
    )
    conversion = _convert_quantity(
        signed_quantity,
        line.product_uom_id,
        resolved_target.uom_id if resolved_target else line.product_id.uom_id,
    )
    contracts = line._get_seller_contracts_for_analog_target()
    matching_contracts = contracts & selected_contracts
    purchase_lines = line._get_purchase_lines_for_analog_origin()
    resolved_purchase_targets = {
        purchase_line._get_analog_original_product_for_rollup().id
        for purchase_line in purchase_lines
        if purchase_line._get_analog_original_product_for_rollup()
    }
    mismatch = bool(
        purchase_lines
        and (
            len(resolved_purchase_targets) > 1
            or (
                resolved_target
                and resolved_purchase_targets
                and resolved_target.id not in resolved_purchase_targets
            )
        )
    )
    exclusion_reason = _invoice_line_exclusion_reason(line, resolved_target)
    return {
        "account_move_line_id": line.id,
        "account_move_id": line.move_id.id,
        "account_move": line.move_id.name,
        "move_type": line.move_id.move_type,
        "state": line.move_id.state,
        "invoice_date": _iso(line.move_id.invoice_date),
        "accounting_date": _iso(line.move_id.date),
        "product": _record_ref(line.product_id),
        "quantity": _number(line.quantity),
        "signed_quantity": _number(signed_quantity),
        "uom": line.product_uom_id.display_name,
        "contracts": [_record_ref(contract) for contract in contracts],
        "selected_contract_sources": [
            {
                "contract": _record_ref(contract),
                "sources": _contract_sources(line, contract, "invoice"),
            }
            for contract in matching_contracts
        ],
        "purchase_line_ids": purchase_lines.ids,
        "purchase_line_targets": [
            {
                "purchase_line_id": purchase_line.id,
                "purchase_order": purchase_line.order_id.name,
                "target": _record_ref(purchase_line.analog_original_product_id),
            }
            for purchase_line in purchase_lines
        ],
        "invoice_target_differs_from_purchase_target": mismatch,
        "target": target,
        "converted_signed_quantity": conversion,
        "direct_in_invoice_contribution": (
            conversion["quantity"] if not exclusion_reason else 0.0
        ),
        "exclusion_reason": exclusion_reason,
    }


def _demand_sources(env, product, contract, date_from=None, date_to=None):
    lines = env["sale.order.line.purchase"].sudo().search(
        [
            ("product_id", "=", product.id),
            ("sale_contract_id", "=", contract.id),
        ]
    )
    result = []
    for line in lines.sorted("id"):
        order = line.order_line_id.order_id
        in_scope = _date_in_scope(order.date_order, date_from, date_to)
        result.append(
            {
                "sale_order_purchase_line_id": line.id,
                "sale_order_line_id": line.order_line_id.id,
                "sale_order_id": order.id,
                "sale_order": order.name,
                "state": line.state,
                "date": _iso(order.date_order),
                "quantity": _number(line.product_qty),
                "product_analytic_id": line.product_analytic_id.id or None,
                "in_date_scope": in_scope,
            }
        )
    return result


def _invoice_contributions(
    env,
    product,
    contract,
    invoice_lines,
    analytic=None,
    date_from=None,
    date_to=None,
):
    ProductAnalytic = env["product.analytic"]
    contributions = []
    direct_products = product._get_bidirectional_analog_group_products()
    direct_lines = invoice_lines.filtered(
        lambda line: line.product_id in direct_products
        and line.move_id.state == "posted"
        and line.move_id.move_type in ("in_invoice", "in_refund")
        and bool(_contract_sources(line, contract, "invoice"))
        and ProductAnalytic._is_invoice_line_related_to_rollup_product(
            line, product
        )
    )
    for line in direct_lines.sorted("id"):
        signed_quantity = (
            line.quantity
            if line.move_id.move_type == "in_invoice"
            else -line.quantity
        )
        conversion = _convert_quantity(
            signed_quantity,
            line.product_uom_id,
            product.uom_id,
        )
        contributions.append(
            {
                "source_kind": "direct invoice",
                "source_key": f"account.move.line:{line.id}",
                "account_move_line_id": line.id,
                "account_move_id": line.move_id.id,
                "document": line.move_id.name,
                "document_type": line.move_id.move_type,
                "date": _iso(_line_date(line, "invoice")),
                "line_product": _record_ref(line.product_id),
                "raw_target": _record_ref(line.analog_original_product_id),
                "resolved_target": _record_ref(
                    line._get_analog_original_product_for_rollup()
                ),
                "raw_quantity": _number(line.quantity),
                "coefficient": 1.0,
                "contribution": conversion["quantity"],
                "uom_conversion": conversion,
                "in_date_scope": _date_in_scope(
                    _line_date(line, "invoice"), date_from, date_to
                ),
            }
        )

    kit_boms = analytic.kit_bom_ids if analytic else _live_kit_boms(env, product)
    for bom in kit_boms:
        need_bom_lines = bom.bom_line_ids.filtered(
            lambda bom_line: bom_line.product_id == product
        )
        coefficient = sum(
            bom_line.product_uom_id._compute_quantity(
                bom_line.product_qty,
                bom_line.product_id.uom_id,
            )
            for bom_line in need_bom_lines
        )
        kit_parents = bom.product_id | bom.product_tmpl_id.product_variant_ids
        for kit_parent in kit_parents:
            kit_products = kit_parent._get_analog_rollup_products()
            kit_lines = invoice_lines.filtered(
                lambda line: line.product_id in kit_products
                and line.move_id.state == "posted"
                and line.move_id.move_type in ("in_invoice", "in_refund")
                and bool(_contract_sources(line, contract, "invoice"))
                and ProductAnalytic._is_invoice_line_related_to_rollup_product(
                    line, kit_parent
                )
            )
            for line in kit_lines.sorted("id"):
                signed_quantity = (
                    line.quantity
                    if line.move_id.move_type == "in_invoice"
                    else -line.quantity
                )
                conversion = _convert_quantity(
                    signed_quantity,
                    line.product_uom_id,
                    kit_parent.uom_id,
                )
                contribution = (
                    conversion["quantity"] * coefficient
                    if conversion["quantity"] is not None
                    else None
                )
                contributions.append(
                    {
                        "source_kind": "phantom kit invoice",
                        "source_key": (
                            f"account.move.line:{line.id}:bom:{bom.id}:"
                            f"component:{product.id}"
                        ),
                        "account_move_line_id": line.id,
                        "account_move_id": line.move_id.id,
                        "document": line.move_id.name,
                        "document_type": line.move_id.move_type,
                        "date": _iso(_line_date(line, "invoice")),
                        "line_product": _record_ref(line.product_id),
                        "kit_parent": _record_ref(kit_parent),
                        "phantom_bom_id": bom.id,
                        "raw_target": _record_ref(
                            line.analog_original_product_id
                        ),
                        "resolved_target": _record_ref(
                            line._get_analog_original_product_for_rollup()
                        ),
                        "raw_quantity": _number(line.quantity),
                        "coefficient": _number(coefficient),
                        "contribution": (
                            _number(contribution)
                            if contribution is not None
                            else None
                        ),
                        "uom_conversion": conversion,
                        "in_date_scope": _date_in_scope(
                            _line_date(line, "invoice"), date_from, date_to
                        ),
                    }
                )
    return contributions


def _purchase_contributions(
    env,
    product,
    contract,
    purchase_lines,
    analytic=None,
    date_from=None,
    date_to=None,
):
    ProductAnalytic = env["product.analytic"]
    contributions = []
    direct_products = product._get_bidirectional_analog_group_products()
    direct_lines = purchase_lines.filtered(
        lambda line: line.product_id in direct_products
        and line.order_id.state in ("purchase", "done")
        and line._has_demand_report_seller_contract(contract)
        and ProductAnalytic._is_purchase_line_related_to_rollup_product(
            line, product
        )
    )
    for line in direct_lines.sorted("id"):
        conversion = _convert_quantity(
            line.qty_received,
            line.product_uom,
            product.uom_id,
        )
        contributions.append(
            {
                "source_kind": "direct purchase receipt",
                "source_key": f"purchase.order.line:{line.id}",
                "purchase_line_id": line.id,
                "purchase_order_id": line.order_id.id,
                "document": line.order_id.name,
                "date": _iso(line.order_id.date_order),
                "line_product": _record_ref(line.product_id),
                "raw_target": _record_ref(line.analog_original_product_id),
                "target_classification": _target_classification(
                    line.product_id, line.analog_original_product_id
                ),
                "resolved_target": _record_ref(
                    line._get_analog_original_product_for_rollup()
                ),
                "quantity_field": "qty_received",
                "raw_quantity": _number(line.qty_received),
                "ordered_quantity": _number(line.product_qty),
                "coefficient": 1.0,
                "contribution": conversion["quantity"],
                "uom_conversion": conversion,
                "stock_moves": [
                    _stock_move_contribution(move, line)
                    for move in line.move_ids.sorted("id")
                ],
                "in_date_scope": _date_in_scope(
                    line.order_id.date_order, date_from, date_to
                ),
            }
        )

    kit_boms = analytic.kit_bom_ids if analytic else _live_kit_boms(env, product)
    for bom in kit_boms:
        need_bom_lines = bom.bom_line_ids.filtered(
            lambda bom_line: bom_line.product_id == product
        )
        coefficient = sum(
            (
                bom_line.product_uom_id._compute_quantity(
                    bom_line.product_qty,
                    bom_line.product_id.uom_id,
                )
                / bom_line.bom_id.product_qty
            )
            for bom_line in need_bom_lines
        )
        kit_parents = bom.product_id | bom.product_tmpl_id.product_variant_ids
        for kit_parent in kit_parents:
            kit_products = kit_parent._get_analog_rollup_products()
            kit_lines = purchase_lines.filtered(
                lambda line: line.product_id in kit_products
                and line.order_id.state in ("purchase", "done")
                and line._has_demand_report_seller_contract(contract)
                and ProductAnalytic._is_purchase_line_related_to_rollup_product(
                    line, kit_parent
                )
            )
            for line in kit_lines.sorted("id"):
                conversion = _convert_quantity(
                    line.product_qty,
                    line.product_uom,
                    kit_parent.uom_id,
                )
                contribution = (
                    conversion["quantity"] * coefficient
                    if conversion["quantity"] is not None
                    else None
                )
                contributions.append(
                    {
                        "source_kind": "phantom kit purchase",
                        "source_key": (
                            f"purchase.order.line:{line.id}:bom:{bom.id}:"
                            f"component:{product.id}"
                        ),
                        "purchase_line_id": line.id,
                        "purchase_order_id": line.order_id.id,
                        "document": line.order_id.name,
                        "date": _iso(line.order_id.date_order),
                        "line_product": _record_ref(line.product_id),
                        "kit_parent": _record_ref(kit_parent),
                        "phantom_bom_id": bom.id,
                        "raw_target": _record_ref(
                            line.analog_original_product_id
                        ),
                        "target_classification": _target_classification(
                            line.product_id, line.analog_original_product_id
                        ),
                        "resolved_target": _record_ref(
                            line._get_analog_original_product_for_rollup()
                        ),
                        "quantity_field": "product_qty",
                        "warning": (
                            "Current module formula uses ordered product_qty, "
                            "not qty_received, for phantom kits."
                        ),
                        "raw_quantity": _number(line.product_qty),
                        "stored_qty_received": _number(line.qty_received),
                        "coefficient": _number(coefficient),
                        "contribution": (
                            _number(contribution)
                            if contribution is not None
                            else None
                        ),
                        "uom_conversion": conversion,
                        "stock_moves": [
                            _stock_move_contribution(move, line)
                            for move in line.move_ids.sorted("id")
                        ],
                        "in_date_scope": _date_in_scope(
                            line.order_id.date_order, date_from, date_to
                        ),
                    }
                )
    return contributions


def _sum_contributions(contributions, filtered=False):
    selected = (
        [item for item in contributions if item["in_date_scope"]]
        if filtered
        else contributions
    )
    errors = [item for item in selected if item["contribution"] is None]
    total = sum(
        item["contribution"]
        for item in selected
        if item["contribution"] is not None
    )
    return _number(total), errors


def _analytic_row(env, product, contract):
    rows = env["product.analytic"].sudo().search(
        [
            ("product_id", "=", product.id),
            ("sale_contract_id", "=", contract.id),
        ]
    )
    return rows[:1], rows


def _analytic_snapshot(analytic):
    if not analytic:
        return None
    values = {
        "id": analytic.id,
        "product": _record_ref(analytic.product_id),
        "sale_contract": _record_ref(analytic.sale_contract_id),
        "demand": _number(analytic.demand),
        "in_invoice": _number(analytic.in_invoice),
        "qty_received": _number(analytic.qty_received),
        "closed": _number(analytic.closed),
        "account_move_ids": analytic.account_move_ids.ids,
        "need_to_purchase_ids": analytic.need_to_purchase_ids.ids,
        "kit_bom_ids": analytic.kit_bom_ids.ids,
    }
    for field_name in (
        "demand_comment",
        "comment",
        "ua_sale_contract_ids",
        "ua_purchase_contract_ids",
    ):
        if field_name not in analytic._fields:
            continue
        value = analytic[field_name]
        values[field_name] = value.ids if hasattr(value, "ids") else value
    return values


def _comparison_status(stored, expected, field_name):
    stored_value = 0.0 if stored is None else float(stored)
    expected_value = float(expected or 0.0)
    if _close(stored_value, expected_value):
        return "matches"
    if stored is None:
        return "missing product.analytic"
    if not _close(stored_value, 0.0) and _close(expected_value, 0.0):
        return "stale non-zero value"
    if _close(stored_value, 0.0) and not _close(expected_value, 0.0):
        return "missing value"
    if field_name in {"in_invoice", "qty_received"}:
        return "quantity mismatch"
    return "mismatch"


def _build_comparison(
    env,
    products,
    contracts,
    purchase_lines,
    invoice_lines,
    date_from,
    date_to,
):
    rows = []
    filtered_scope_enabled = bool(date_from or date_to)
    for contract in contracts.sorted("id"):
        for product in products.sorted("id"):
            analytic, duplicate_analytics = _analytic_row(env, product, contract)
            snapshot = _analytic_snapshot(analytic)
            demand_sources = _demand_sources(
                env, product, contract, date_from=date_from, date_to=date_to
            )
            for source in demand_sources:
                source["included_by_current_formula"] = bool(
                    (
                        source["product_analytic_id"] == analytic.id
                        if analytic
                        else source["state"] == "sale"
                    )
                )
            linked_demand_sources = [
                source
                for source in demand_sources
                if source["included_by_current_formula"]
            ]
            demand_all = sum(source["quantity"] for source in linked_demand_sources)
            demand_filtered = sum(
                source["quantity"]
                for source in linked_demand_sources
                if source["in_date_scope"]
            )
            invoice_contributions = _invoice_contributions(
                env,
                product,
                contract,
                invoice_lines,
                analytic=analytic,
                date_from=date_from,
                date_to=date_to,
            )
            purchase_contributions = _purchase_contributions(
                env,
                product,
                contract,
                purchase_lines,
                analytic=analytic,
                date_from=date_from,
                date_to=date_to,
            )
            invoice_all, invoice_errors = _sum_contributions(invoice_contributions)
            invoice_filtered, invoice_filtered_errors = _sum_contributions(
                invoice_contributions, filtered=True
            )
            received_all, received_errors = _sum_contributions(
                purchase_contributions
            )
            received_filtered, received_filtered_errors = _sum_contributions(
                purchase_contributions, filtered=True
            )
            expected_all = {
                "demand": _number(demand_all),
                "in_invoice": invoice_all,
                "qty_received": received_all,
                "closed": _number(invoice_all / demand_all) if demand_all else 0.0,
            }
            expected_filtered = {
                "demand": _number(demand_filtered),
                "in_invoice": invoice_filtered,
                "qty_received": received_filtered,
                "closed": (
                    _number(invoice_filtered / demand_filtered)
                    if demand_filtered
                    else 0.0
                ),
            }
            stored_metrics = {
                field_name: snapshot[field_name] if snapshot else None
                for field_name in ("demand", "in_invoice", "qty_received", "closed")
            }
            differences = {
                field_name: (
                    None
                    if stored_metrics[field_name] is None
                    else _number(
                        stored_metrics[field_name] - expected_all[field_name]
                    )
                )
                for field_name in stored_metrics
            }
            statuses = {
                field_name: _comparison_status(
                    stored_metrics[field_name], expected_all[field_name], field_name
                )
                for field_name in stored_metrics
            }
            # Audited helper: it only searches and filters invoice lines/moves.
            # It does not invoke the stored-field compute method.
            expected_account_move_ids = (
                sorted(analytic._get_related_invoice_moves().ids)
                if analytic
                else sorted(
                    {
                        item["account_move_id"]
                        for item in invoice_contributions
                        if item["contribution"] is not None
                    }
                )
            )
            rows.append(
                {
                    "contract": _record_ref(contract),
                    "product": _record_ref(product),
                    "product_analytic": snapshot,
                    "duplicate_product_analytic_ids": duplicate_analytics.ids,
                    "expected_all_time": expected_all,
                    "expected_filtered": (
                        expected_filtered if filtered_scope_enabled else None
                    ),
                    "difference_stored_minus_expected": differences,
                    "status": statuses,
                    "demand_sources": demand_sources,
                    "invoice_contributions": invoice_contributions,
                    "purchase_contributions": purchase_contributions,
                    "expected_account_move_ids": expected_account_move_ids,
                    "account_move_ids_match": (
                        sorted(snapshot["account_move_ids"])
                        == expected_account_move_ids
                        if snapshot
                        else not expected_account_move_ids
                    ),
                    "conversion_errors": {
                        "invoice_all_time": invoice_errors,
                        "invoice_filtered": invoice_filtered_errors,
                        "purchase_all_time": received_errors,
                        "purchase_filtered": received_filtered_errors,
                    },
                    "live_kit_bom_ids": _live_kit_boms(env, product).ids,
                    "stored_kit_bom_ids_match_live": (
                        set(snapshot["kit_bom_ids"])
                        == set(_live_kit_boms(env, product).ids)
                        if snapshot
                        else None
                    ),
                }
            )
    return rows


def _closed_grouping(env, products, contracts, comparison):
    ProductAnalytic = env["product.analytic"].sudo()
    grouping = []
    for contract in contracts.sorted("id"):
        domain = [
            ("sale_contract_id", "=", contract.id),
            ("product_id", "in", products.ids),
        ]
        analytic_rows = ProductAnalytic.search(domain).sorted("id")
        read_group_rows = ProductAnalytic.read_group(
            domain,
            ["demand:sum", "in_invoice:sum", "closed:avg"],
            [],
            lazy=False,
        )
        read_group_result = read_group_rows[0] if read_group_rows else {}
        row_percentages = [
            {
                "product_analytic_id": analytic.id,
                "product": _record_ref(analytic.product_id),
                "demand": _number(analytic.demand),
                "in_invoice": _number(analytic.in_invoice),
                "closed_ratio": _number(analytic.closed),
                "closed_percent": _number(analytic.closed * 100),
            }
            for analytic in analytic_rows
        ]
        average_ratio = (
            sum(row["closed_ratio"] for row in row_percentages)
            / len(row_percentages)
            if row_percentages
            else 0.0
        )
        demand_sum = sum(row["demand"] for row in row_percentages)
        invoice_sum = sum(row["in_invoice"] for row in row_percentages)
        weighted_ratio = invoice_sum / demand_sum if demand_sum else 0.0
        expected_rows = [
            row for row in comparison if row["contract"]["id"] == contract.id
        ]
        expected_demand = sum(
            row["expected_all_time"]["demand"] for row in expected_rows
        )
        expected_invoice = sum(
            row["expected_all_time"]["in_invoice"] for row in expected_rows
        )
        grouping.append(
            {
                "contract": _record_ref(contract),
                "row_percentages": row_percentages,
                "odoo_read_group": {
                    "demand": _number(read_group_result.get("demand", 0.0)),
                    "in_invoice": _number(
                        read_group_result.get("in_invoice", 0.0)
                    ),
                    "closed_ratio": _number(read_group_result.get("closed", 0.0)),
                    "closed_percent": _number(
                        read_group_result.get("closed", 0.0) * 100
                    ),
                },
                "average_of_row_percentages": _number(average_ratio * 100),
                "correct_weighted_stored_percent": _number(weighted_ratio * 100),
                "correct_weighted_source_percent": (
                    _number(expected_invoice / expected_demand * 100)
                    if expected_demand
                    else 0.0
                ),
                "explanation": (
                    "The field uses group_operator='avg', so Odoo averages stored "
                    "row ratios instead of calculating sum(in_invoice)/sum(demand)."
                ),
            }
        )
    return {
        "contracts": grouping,
        "known_examples": [
            {
                "rows": [100.0, 0.0],
                "odoo_average_percent": 50.0,
                "weighted_example": "4200 / 4200",
                "correct_percent": 100.0,
            },
            {
                "rows": [66.66666667, 0.0],
                "odoo_average_percent": 33.33333334,
                "weighted_example": "40 / 60",
                "correct_percent": 66.66666667,
            },
        ],
    }


def _quantity_origins(comparison, watch_quantities):
    origins = []
    for row in comparison:
        contract = row["contract"]
        product = row["product"]
        metric_sources = {
            "demand": row["demand_sources"],
            "in_invoice": row["invoice_contributions"],
            "qty_received": row["purchase_contributions"],
        }
        for metric, sources in metric_sources.items():
            if metric == "demand":
                total = sum(
                    source["quantity"]
                    for source in sources
                    if source["included_by_current_formula"]
                )
                filtered_total = sum(
                    source["quantity"]
                    for source in sources
                    if source["included_by_current_formula"]
                    and source["in_date_scope"]
                )
            else:
                total = sum(
                    source["contribution"]
                    for source in sources
                    if source["contribution"] is not None
                )
                filtered_total = sum(
                    source["contribution"]
                    for source in sources
                    if source["contribution"] is not None
                    and source["in_date_scope"]
                )
            source_values = []
            for source in sources:
                value = (
                    source["quantity"]
                    if metric == "demand"
                    else source["contribution"]
                )
                if value is not None and (
                    metric != "demand" or source["included_by_current_formula"]
                ):
                    source_values.append(value)
            matches = [
                watched
                for watched in watch_quantities
                if _close(total, watched)
                or any(_close(source_value, watched) for source_value in source_values)
            ]
            origins.append(
                {
                    "path": (
                        f"{contract['name']} -> {_product_code_from_ref(product)} "
                        f"-> {metric} {_number(total)}"
                    ),
                    "contract": contract,
                    "product": product,
                    "metric": metric,
                    "total": _number(total),
                    "filtered_total": _number(filtered_total),
                    "matches_watched_quantities": matches,
                    "sources": sources,
                }
            )
    return origins


def _product_code_from_ref(product_ref):
    return product_ref.get("default_code") or product_ref.get("name")


def _duplicate_source_usage(comparison, contract_id, metric):
    usage = defaultdict(list)
    source_field = (
        "invoice_contributions"
        if metric == "in_invoice"
        else "purchase_contributions"
    )
    for row in comparison:
        if row["contract"]["id"] != contract_id:
            continue
        for source in row[source_field]:
            if source["contribution"] is None or _close(source["contribution"], 0):
                continue
            usage[source["source_key"]].append(
                {
                    "product": row["product"],
                    "contribution": source["contribution"],
                }
            )
    return {
        source_key: destinations
        for source_key, destinations in usage.items()
        if len(destinations) > 1
    }


def _diagnoses(
    contracts,
    purchase_details,
    invoice_details,
    comparison,
):
    diagnoses = []
    for contract in contracts.sorted("id"):
        contract_id = contract.id
        contract_purchase_lines = [
            line
            for line in purchase_details
            if any(
                item["contract"]["id"] == contract_id
                for item in line["selected_contract_sources"]
            )
        ]
        contract_invoice_lines = [
            line
            for line in invoice_details
            if any(
                item["contract"]["id"] == contract_id
                for item in line["selected_contract_sources"]
            )
        ]
        contract_rows = [
            row for row in comparison if row["contract"]["id"] == contract_id
        ]
        stale_rows = [
            {
                "product": row["product"],
                "status": row["status"],
                "difference": row["difference_stored_minus_expected"],
            }
            for row in contract_rows
            if any(status != "matches" for status in row["status"].values())
        ]
        blank_purchase_lines = [
            line["purchase_line_id"]
            for line in contract_purchase_lines
            if line["target"]["classification"] == "blank"
            and line["target"]["allowed_targets"]
        ]
        invalid_purchase_lines = [
            line["purchase_line_id"]
            for line in contract_purchase_lines
            if line["target"]["classification"] == "invalid explicit target"
        ]
        blank_invoice_lines = [
            line["account_move_line_id"]
            for line in contract_invoice_lines
            if line["target"]["classification"] == "blank"
            and line["target"]["allowed_targets"]
        ]
        invalid_invoice_lines = [
            line["account_move_line_id"]
            for line in contract_invoice_lines
            if line["target"]["classification"] == "invalid explicit target"
            or not line["target"]["resolver_target"]
        ]
        confirmed_targets = sorted(
            {
                line["target"]["resolver_target"]["default_code"]
                or line["target"]["resolver_target"]["name"]
                for line in contract_purchase_lines
                if line["included_by_report_state"]
                and line["target"]["resolver_target"]
            }
        )
        differing_invoice_targets = [
            line["account_move_line_id"]
            for line in contract_invoice_lines
            if line["invoice_target_differs_from_purchase_target"]
        ]
        duplicate_invoice_sources = _duplicate_source_usage(
            comparison, contract_id, "in_invoice"
        )
        duplicate_purchase_sources = _duplicate_source_usage(
            comparison, contract_id, "qty_received"
        )
        routed_purchase_documents = []
        routed_invoice_documents = []
        for row in contract_rows:
            destination = row["product"]
            for source in row["purchase_contributions"]:
                routed_purchase_documents.append(
                    {
                        "destination_product": destination,
                        "purchase_order": source["document"],
                        "purchase_order_line_id": source["purchase_line_id"],
                        "line_product": source["line_product"],
                        "raw_target": source["raw_target"],
                        "target_classification": source["target_classification"],
                        "resolved_target": source["resolved_target"],
                        "quantity_field": source["quantity_field"],
                        "contribution": source["contribution"],
                        "source_kind": source["source_kind"],
                    }
                )
            for source in row["invoice_contributions"]:
                routed_invoice_documents.append(
                    {
                        "destination_product": destination,
                        "account_move": source["document"],
                        "account_move_line_id": source["account_move_line_id"],
                        "line_product": source["line_product"],
                        "raw_target": source["raw_target"],
                        "resolved_target": source["resolved_target"],
                        "contribution": source["contribution"],
                        "source_kind": source["source_kind"],
                    }
                )
        problem_types = []
        if len(confirmed_targets) > 1:
            problem_types.append("different explicit/resolved targets in PO lines")
        if blank_purchase_lines or blank_invoice_lines:
            problem_types.append("historical blank targets")
        if invalid_purchase_lines or invalid_invoice_lines:
            problem_types.append("invalid or unresolved targets")
        if stale_rows:
            problem_types.append("stale or missing product.analytic values")
        if duplicate_invoice_sources or duplicate_purchase_sources:
            problem_types.append("double counting risk")
        if differing_invoice_targets:
            problem_types.append("invoice targets differ from purchase targets")
        if not problem_types:
            problem_types.append("current analytics match source documents")

        only_stored_differences = bool(stale_rows) and not (
            invalid_purchase_lines
            or invalid_invoice_lines
            or duplicate_invoice_sources
            or duplicate_purchase_sources
        )
        if only_stored_differences:
            backfill_assessment = (
                "A repeated idempotent backfill would likely align stored analytics "
                "with the current source routing. This script does not run it."
            )
        elif stale_rows:
            backfill_assessment = (
                "A backfill may refresh stored fields, but source target problems or "
                "duplicate routing must be resolved separately."
            )
        else:
            backfill_assessment = (
                "No stored/source mismatch was found; a backfill should not change "
                "the distribution."
            )
        diagnoses.append(
            {
                "contract": _record_ref(contract),
                "problem_types": problem_types,
                "confirmed_purchase_targets": confirmed_targets,
                "blank_purchase_line_ids": blank_purchase_lines,
                "blank_invoice_line_ids": blank_invoice_lines,
                "invalid_purchase_line_ids": invalid_purchase_lines,
                "invalid_invoice_line_ids": invalid_invoice_lines,
                "invoice_target_mismatch_line_ids": differing_invoice_targets,
                "stale_or_missing_analytics": stale_rows,
                "duplicate_invoice_sources": duplicate_invoice_sources,
                "duplicate_purchase_sources": duplicate_purchase_sources,
                "routed_purchase_documents": routed_purchase_documents,
                "routed_invoice_documents": routed_invoice_documents,
                "backfill_assessment": backfill_assessment,
                "answers": {
                    "documents_routing_qty_received": routed_purchase_documents,
                    "documents_routing_in_invoice": routed_invoice_documents,
                    "target_classes": {
                        "blank": blank_purchase_lines + blank_invoice_lines,
                        "invalid": invalid_purchase_lines + invalid_invoice_lines,
                        "resolved_purchase_targets": confirmed_targets,
                    },
                    "analytics_match_sources": not stale_rows,
                    "residual_old_values": bool(
                        any(
                            "stale non-zero value" in row["status"].values()
                            for row in contract_rows
                        )
                    ),
                    "backfill": backfill_assessment,
                    "loss_risk": bool(
                        invalid_purchase_lines or invalid_invoice_lines
                    ),
                    "double_count_risk": bool(
                        duplicate_invoice_sources or duplicate_purchase_sources
                    ),
                    "closed_percent": (
                        "Incorrect because Odoo averages stored row percentages; "
                        "the weighted formula is sum(in_invoice)/sum(demand)."
                    ),
                },
            }
        )
    return diagnoses


def _render_table(headers, rows):
    if not rows:
        return "(none)"
    string_rows = [["" if value is None else str(value) for value in row] for row in rows]
    widths = [len(str(header)) for header in headers]
    for row in string_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    template = " | ".join(f"{{:<{width}}}" for width in widths)
    separator = "-+-".join("-" * width for width in widths)
    rendered = [template.format(*headers), separator]
    rendered.extend(template.format(*row) for row in string_rows)
    return "\n".join(rendered)


def _print_human_report(report):
    print("=== HUMAN REPORT BEGIN ===")
    print("=== ANALOG ROLLUP DIAGNOSTICS (READ ONLY) ===")
    general = report["general"]
    print(f"Database: {general['database']}")
    print(f"Run timestamp (UTC): {general['run_timestamp_utc']}")
    print(
        "Module product_alternatives_vataga: "
        f"state={general['module']['state']}, "
        f"installed_version={general['module']['installed_version']}"
    )
    print(general["migration_note"])
    print(f"Products: {general['products']}")
    print(f"Contracts: {general['contracts']}")
    print(f"Date scope: {general['date_scope']}")
    print(
        "Safety: business documents and analytic data were not changed. "
        "A scheduled-action launch still lets Odoo update ir.cron and write "
        "these messages to ir.logging in its separate outer transaction."
    )
    print(
        "Rows: "
        f"purchase={len(report['purchase_lines'])}, "
        f"vendor_bill={len(report['vendor_bill_lines'])}, "
        f"product_analytic={len(report['comparison'])}."
    )
    print("Human sections are capped; the JSON log parts contain every row.")

    print("\n=== PURCHASE ORDER LINES ===")
    print(
        _render_table(
            ["POL", "PO", "state", "product", "ordered", "received", "class", "target"],
            [
                [
                    line["purchase_line_id"],
                    line["purchase_order"],
                    line["purchase_state"],
                    _product_code_from_ref(line["product"]),
                    line["ordered_quantity"],
                    line["stored_qty_received"],
                    line["target"]["classification"],
                    (
                        _product_code_from_ref(line["target"]["resolver_target"])
                        if line["target"]["resolver_target"]
                        else "UNRESOLVED"
                    ),
                ]
                for line in report["purchase_lines"][:25]
            ],
        )
    )

    print("\n=== VENDOR BILL / REFUND LINES ===")
    print(
        _render_table(
            ["AML", "move", "type", "state", "product", "signed qty", "class", "target"],
            [
                [
                    line["account_move_line_id"],
                    line["account_move"],
                    line["move_type"],
                    line["state"],
                    _product_code_from_ref(line["product"]),
                    line["signed_quantity"],
                    line["target"]["classification"],
                    (
                        _product_code_from_ref(line["target"]["resolver_target"])
                        if line["target"]["resolver_target"]
                        else "UNRESOLVED"
                    ),
                ]
                for line in report["vendor_bill_lines"][:25]
            ],
        )
    )

    print("\n=== PRODUCT.ANALYTIC: STORED VS SOURCE ===")
    print(
        _render_table(
            [
                "contract",
                "product",
                "PA",
                "stored demand",
                "expected demand",
                "stored invoice",
                "expected invoice",
                "stored received",
                "expected received",
            ],
            [
                [
                    row["contract"]["name"],
                    _product_code_from_ref(row["product"]),
                    row["product_analytic"]["id"] if row["product_analytic"] else None,
                    row["product_analytic"]["demand"] if row["product_analytic"] else None,
                    row["expected_all_time"]["demand"],
                    row["product_analytic"]["in_invoice"] if row["product_analytic"] else None,
                    row["expected_all_time"]["in_invoice"],
                    row["product_analytic"]["qty_received"] if row["product_analytic"] else None,
                    row["expected_all_time"]["qty_received"],
                ]
                for row in report["comparison"][:50]
            ],
        )
    )

    print("\n=== QUANTITY ORIGINS ===")
    for origin in report["quantity_origins"][:25]:
        watched = (
            f" WATCH={origin['matches_watched_quantities']}"
            if origin["matches_watched_quantities"]
            else ""
        )
        print(f"{origin['path']}{watched}")
        for source in origin["sources"]:
            source_id = (
                source.get("purchase_line_id")
                or source.get("account_move_line_id")
                or source.get("sale_order_purchase_line_id")
            )
            contribution = source.get("contribution", source.get("quantity"))
            print(
                f"  - source={source.get('source_kind', 'demand')} "
                f"id={source_id} document={source.get('document') or source.get('sale_order')} "
                f"contribution={contribution}"
            )

    print("\n=== CLOSED % ===")
    for item in report["closed_grouping"]["contracts"][:50]:
        print(
            f"{item['contract']['name']}: "
            f"Odoo={item['odoo_read_group']['closed_percent']}%, "
            f"avg(rows)={item['average_of_row_percentages']}%, "
            f"weighted(stored)={item['correct_weighted_stored_percent']}%, "
            f"weighted(source)={item['correct_weighted_source_percent']}%"
        )

    print("\n=== DIAGNOSIS ===")
    for diagnosis in report["diagnoses"][:50]:
        print(
            f"{diagnosis['contract']['name']}: "
            + "; ".join(diagnosis["problem_types"])
        )
        print(f"  {diagnosis['backfill_assessment']}")

    print("=== HUMAN REPORT END ===")


def _build_report(env, config):
    _require_model_api(env)

    Product = env["product.product"].sudo().with_context(active_test=False)
    requested_products = Product.search(
        [("default_code", "in", config["product_codes"])]
    )
    found_codes = set(requested_products.mapped("default_code"))
    missing_codes = sorted(set(config["product_codes"]) - found_codes)
    if missing_codes:
        raise RuntimeError(f"Products not found by default_code: {missing_codes}")

    source_products = _source_product_universe(env, requested_products)
    purchase_lines = env["purchase.order.line"].sudo().search(
        [("product_id", "in", source_products.ids)], order="id"
    )
    invoice_lines = env["account.move.line"].sudo().search(
        [
            ("product_id", "in", source_products.ids),
            ("move_id.move_type", "in", ("in_invoice", "in_refund")),
        ],
        order="id",
    )

    contract_resolutions = []
    if config["all_contracts"]:
        contracts = env["account.analytic.account"].sudo().browse()
        for line in purchase_lines:
            contracts |= _line_contracts(line, "purchase")
        for line in invoice_lines:
            contracts |= _line_contracts(line, "invoice")
        contracts |= env["product.analytic"].sudo().search(
            [("product_id", "in", requested_products.ids)]
        ).mapped("sale_contract_id")
        contracts |= env["sale.order.line.purchase"].sudo().search(
            [("product_id", "in", requested_products.ids)]
        ).mapped("sale_contract_id")
        contract_resolutions.append(
            {
                "reference": "ALL_CONTRACTS",
                "strategy": "derived from source documents and product.analytic",
                "matches": [_record_ref(contract) for contract in contracts],
            }
        )
    else:
        contracts, contract_resolutions = _resolve_contracts(
            env, config["contract_references"]
        )
    if not contracts:
        raise RuntimeError(
            "No seller analytic contracts matched the configured references."
        )

    matching_purchase_lines = purchase_lines.filtered(
        lambda line: bool(line._get_demand_report_seller_contracts() & contracts)
    )
    matching_invoice_lines = invoice_lines.filtered(
        lambda line: bool(line._get_seller_contracts_for_analog_target() & contracts)
    )
    display_purchase_lines = matching_purchase_lines.filtered(
        lambda line: _date_in_scope(
            line.order_id.date_order, config["date_from"], config["date_to"]
        )
    )
    display_invoice_lines = matching_invoice_lines.filtered(
        lambda line: _date_in_scope(
            _line_date(line, "invoice"),
            config["date_from"],
            config["date_to"],
        )
    )

    purchase_details = [
        _purchase_line_detail(line, contracts) for line in display_purchase_lines
    ]
    invoice_details = [
        _invoice_line_detail(line, contracts) for line in display_invoice_lines
    ]
    comparison = _build_comparison(
        env,
        requested_products,
        contracts,
        purchase_lines,
        invoice_lines,
        config["date_from"],
        config["date_to"],
    )
    closed_grouping = _closed_grouping(
        env, requested_products, contracts, comparison
    )
    quantity_origins = _quantity_origins(
        comparison, config["watch_quantities"]
    )
    diagnoses = _diagnoses(
        contracts,
        purchase_details,
        invoice_details,
        comparison,
    )

    module = env["ir.module.module"].sudo().search(
        [("name", "=", "product_alternatives_vataga")], limit=1
    )
    module_info = {
        "id": module.id or None,
        "state": module.state or None,
        "installed_version": (
            module.installed_version
            if module and "installed_version" in module._fields
            else None
        ),
        "latest_version": (
            module.latest_version
            if module and "latest_version" in module._fields
            else None
        ),
    }
    direct_links = []
    for product in requested_products.sorted("id"):
        direct_links.append(
            {
                "product": _record_ref(product),
                "direct_counterparts": [
                    _record_ref(counterpart)
                    for counterpart in product._get_direct_analog_counterpart_products()
                ],
                "allowed_rollup_targets": [
                    _record_ref(target)
                    for target in product._get_allowed_analog_rollup_target_products()
                ],
            }
        )

    report = {
        "general": {
            "database": env.cr.dbname,
            "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "transaction_read_only": True,
            "module": module_info,
            "migration_note": (
                "The installed version confirms that the module was upgraded. "
                "It does not prove that a migration produced the expected data; "
                "that is assessed below by comparing stored analytics with sources."
            ),
            "configuration": {
                "product_codes": config["product_codes"],
                "contract_references": config["contract_references"],
                "all_contracts": config["all_contracts"],
                "date_from": _iso(config["date_from"]),
                "date_to": _iso(config["date_to"]),
                "watch_quantities": config["watch_quantities"],
            },
            "products": [_record_ref(product) for product in requested_products],
            "source_product_universe": [
                _record_ref(product) for product in source_products
            ],
            "direct_analog_links": direct_links,
            "contracts": [_record_ref(contract) for contract in contracts],
            "contract_resolution": contract_resolutions,
            "date_scope": {
                "enabled": bool(config["date_from"] or config["date_to"]),
                "note": (
                    "Stored product.analytic remains all-time. The comparison uses "
                    "all-time expected values and additionally reports a filtered "
                    "expected view when dates are configured."
                ),
            },
        },
        "purchase_lines": purchase_details,
        "vendor_bill_lines": invoice_details,
        "stock_moves": [
            {
                "purchase_line_id": line["purchase_line_id"],
                "purchase_order": line["purchase_order"],
                "stored_target": line["target"]["stored_target"],
                "resolver_target": line["target"]["resolver_target"],
                "stored_qty_received": line["stored_qty_received"],
                "moves": line["stock_moves"],
            }
            for line in purchase_details
        ],
        "product_analytics": [
            row["product_analytic"] for row in comparison
        ],
        "comparison": comparison,
        "quantity_origins": quantity_origins,
        "closed_grouping": closed_grouping,
        "diagnoses": diagnoses,
        "safety": {
            "postgresql_transaction_read_only": True,
            "business_data_changed": False,
            "persistent_operations_invoked": False,
            "backfill_or_migration_invoked": False,
            "documents_transitioned": False,
            "final_action": "separate diagnostic transaction rollback",
            "outer_transaction_technical_writes": (
                "When launched by ir.cron, Odoo updates ir.cron execution state "
                "and log() creates ir.logging rows in the outer transaction."
            ),
        },
    }
    return report


def _render_human_report(report):
    output = StringIO()
    with redirect_stdout(output):
        _print_human_report(report)
    return output.getvalue().rstrip()


def _split_log_payload(payload, chunk_size=LOG_CHUNK_SIZE):
    if chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer.")
    return [
        payload[offset:offset + chunk_size]
        for offset in range(0, len(payload), chunk_size)
    ] or [""]


def _build_log_messages(run_id, human_report, json_report, chunk_size=LOG_CHUNK_SIZE):
    messages = [f"ANALOG_DIAG {run_id} HUMAN REPORT\n{human_report}"]
    json_payload = f"=== JSON BEGIN ===\n{json_report}\n=== JSON END ==="
    json_parts = _split_log_payload(json_payload, chunk_size=chunk_size)
    total_parts = len(json_parts)
    messages.extend(
        f"ANALOG_DIAG {run_id} JSON PART {part_number}/{total_parts}\n{part}"
        for part_number, part in enumerate(json_parts, start=1)
    )
    return messages


def run(
    env,
    product_codes=None,
    contract_references=None,
    all_contracts=False,
    date_from=None,
    date_to=None,
    watch_quantities=None,
    log_chunk_size=LOG_CHUNK_SIZE,
):
    """Build diagnostics and return renderings suitable for ``ir.cron`` logs."""
    config = _normalize_configuration(
        product_codes=product_codes,
        contract_references=contract_references,
        all_contracts=all_contracts,
        date_from=date_from,
        date_to=date_to,
        watch_quantities=watch_quantities,
    )
    report = _build_report(env, config)
    run_id = uuid.uuid4().hex
    human_report = _render_human_report(report)
    json_report = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        default=_iso,
    )
    return {
        "run_id": run_id,
        "human_report": human_report,
        "json": json_report,
        "report": report,
        "log_messages": _build_log_messages(
            run_id,
            human_report,
            json_report,
            chunk_size=log_chunk_size,
        ),
    }
