"""Private Stock executor for an already validated, whole-piece MRP allocation."""
from collections import defaultdict
from math import isclose, isfinite

from odoo import _, Command
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from .stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


def complete_partial_production_release(releases, allocations):
    unit = releases.env.ref('uom.product_uom_unit')
    sources = releases.quality_held_move_id.move_line_ids.filtered(lambda line: line.quantity > 0)
    if not allocations or set(allocations) - set(sources.ids):
        raise UserError(_('Розподіл не відповідає фактичним рядкам випуску.'))
    if any(move.state != 'draft' for move in releases):
        raise UserError(_('Складське рішення для цього випуску вже виконано.'))
    expected = defaultdict(float)
    for line in sources:
        expected[line.company_id.id, line.location_dest_id.id, line.product_id.id,
                 line.lot_id.id, line.result_package_id.id, line.owner_id.id] += line.quantity_product_uom
    for (company, location, product, lot, package, owner), quantity in expected.items():
        quants = releases.env['stock.quant'].sudo().search([
            ('company_id', '=', company), ('location_id', '=', location), ('product_id', '=', product),
            ('lot_id', '=', lot), ('package_id', '=', package), ('owner_id', '=', owner),
        ])
        rounding = releases.env['product.product'].browse(product).uom_id.rounding
        if any(quants.mapped('reserved_quantity')) or float_compare(
                sum(quants.mapped('quantity')), quantity, precision_rounding=rounding) < 0:
            raise UserError(_('Фактичний залишок карантину не відповідає утриманому випуску.'))
    pickings = releases.env['stock.picking']
    for move in releases:
        lines = move.quality_held_move_id.move_line_ids.filtered(lambda line: line.id in allocations)
        if not lines:
            continue
        if (not move.location_dest_id.active or move.location_dest_id.usage != 'internal'
                or move.location_dest_id.quality_quarantine
                or move.location_dest_id.company_id != move.company_id
                or not move.location_id.quality_quarantine or move.picking_id):
            raise UserError(_('Неприпустимий стан або призначення руху часткового випуску.'))
        context = {'allowed_company_ids': move.company_id.ids, QUARANTINE_CONTEXT: QUARANTINE_OPERATION}
        move = move.with_context(context)
        line_values = []
        total_pieces = 0
        for line in lines:
            count = allocations[line.id]
            available = line.product_uom_id._compute_quantity(line.quantity, unit, round=False)
            quantity = unit._compute_quantity(count, line.product_uom_id, round=False)
            if (not isfinite(count) or count <= 0 or count != int(count) or count > available
                    or not isclose(quantity / line.product_uom_id.rounding,
                                   round(quantity / line.product_uom_id.rounding), abs_tol=1e-9)):
                raise UserError(_('Кількість часткового випуску не відповідає фактичному джерелу.'))
            if move.product_id.tracking == 'serial' and (not line.lot_id or count != 1 or available != 1):
                raise UserError(_('Серійний виріб має відповідати одній підтвердженій одиниці.'))
            total_pieces += count
            line_values.append({
                'product_id': line.product_id.id, 'product_uom_id': line.product_uom_id.id,
                'location_id': move.location_id.id, 'location_dest_id': move.location_dest_id.id,
                'lot_id': line.lot_id.id, 'owner_id': line.owner_id.id,
                'package_id': line.result_package_id.id, 'result_package_id': line.result_package_id.id,
                'quantity': quantity, 'picked': True,
            })
        quantity = unit._compute_quantity(total_pieces, move.product_uom, round=False)
        if not isclose(quantity / move.product_uom.rounding, round(quantity / move.product_uom.rounding), abs_tol=1e-9):
            raise UserError(_('Кількість не представима в одиниці складського руху.'))
        # A separate picking prevents grouping with unrelated draft transfers.
        picking = move.env['stock.picking'].create({
            'picking_type_id': move.picking_type_id.id, 'company_id': move.company_id.id,
            'location_id': move.location_id.id, 'location_dest_id': move.location_dest_id.id,
            'origin': move.origin,
        })
        move.write({'product_uom_qty': quantity, 'picking_id': picking.id})
        move._action_confirm(merge=False)
        # Only the unposted release lines may be replaced; never the MO receipt.
        move.move_line_ids.unlink()
        move.write({'move_line_ids': [Command.create(values) for values in line_values], 'picked': True})
        picking.with_context(cancel_backorder=True)._action_done()
        pickings |= picking
    return pickings
