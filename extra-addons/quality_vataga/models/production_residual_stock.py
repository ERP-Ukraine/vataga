"""Private Stock executor. Rights remain those of the assigned board executor."""
from math import isclose, isfinite

from odoo import _
from odoo.exceptions import UserError

from .stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


CASE_CONTEXT = 'quality_vataga_production_case'
CASE_OPERATION = object()


def case_context(env):
    return {'allowed_company_ids': env.context.get('allowed_company_ids', env.companies.ids),
            CASE_CONTEXT: CASE_OPERATION, QUARANTINE_CONTEXT: QUARANTINE_OPERATION}


def case_internal(env):
    return env.context.get(CASE_CONTEXT) is CASE_OPERATION


def check_residual_stock(env, scopes, *, lock=False):
    unit = env.ref('uom.product_uom_unit')
    for scope in scopes:
        source = env['stock.move.line'].sudo().browse(scope['id']).exists()
        if (len(source) != 1 or source.state != 'done' or source.move_id.id != scope['move_id']
                or source.company_id.id != scope['company_id'] or source.product_id.id != scope['product_id']
                or source.lot_id.id != scope['lot_id'] or source.owner_id or source.result_package_id
                or not source.location_dest_id.quality_quarantine
                or source.location_dest_id.company_id != source.company_id
                or source.company_id not in env.companies):
            raise UserError(_('Джерело залишку браку не відповідає підтвердженому карантину.'))
        pieces = scope['quantity']
        quantity = unit._compute_quantity(pieces, source.product_id.uom_id, round=False)
        rounding = source.product_id.uom_id.rounding
        if (not isfinite(pieces) or pieces <= 0 or pieces != int(pieces)
                or not isclose(quantity / rounding, round(quantity / rounding), rel_tol=0, abs_tol=1e-9)):
            raise UserError(_('Кількість залишку має точно відповідати цілим виробам.'))
        quants = env['stock.quant'].sudo().search([
            ('company_id', '=', source.company_id.id), ('location_id', '=', source.location_dest_id.id),
            ('product_id', '=', source.product_id.id), ('lot_id', '=', source.lot_id.id),
        ])
        if lock and quants:
            quants.flush_recordset()
            env.cr.execute('SELECT id FROM stock_quant WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(quants.ids)])
            quants.invalidate_recordset()
        if (any(quant.reserved_quantity or (quant.quantity and (quant.owner_id or quant.package_id))
                or quant.quantity < 0 for quant in quants)
                or not isclose(sum(quants.mapped('quantity')), quantity, rel_tol=0, abs_tol=1e-9)):
            raise UserError(_('Фактичний карантин має точно відповідати залишку без резерву, власника та пакування.'))


def complete_residual_scrap(env, scopes, origin, *, alert_id=False):
    check_residual_stock(env, scopes, lock=True)
    unit = env.ref('uom.product_uom_unit')
    scraps = env['stock.scrap']
    for scope in scopes:
        source = env['stock.move.line'].browse(scope['id'])
        values = {
            'company_id': source.company_id.id, 'product_id': source.product_id.id,
            'product_uom_id': source.product_id.uom_id.id,
            'scrap_qty': unit._compute_quantity(scope['quantity'], source.product_id.uom_id, round=False),
            'location_id': source.location_dest_id.id, 'lot_id': source.lot_id.id,
            'origin': origin, 'should_replenish': False,
        }
        if alert_id:
            values['quality_production_case_id'] = alert_id
        scrap = env['stock.scrap'].with_context(case_context(env)).create(values)
        if (not scrap.scrap_location_id.active or not scrap.scrap_location_id.scrap_location
                or scrap.scrap_location_id.usage != 'inventory'
                or scrap.scrap_location_id.company_id not in (env['res.company'], source.company_id)):
            raise UserError(_('Не налаштовано коректну локацію списання цієї компанії.'))
        scrap.action_validate()
        lines = scrap.move_ids.move_line_ids.filtered(lambda line: line.quantity > 0)
        if (scrap.state != 'done' or len(lines) != 1
                or lines.location_id != source.location_dest_id or lines.lot_id != source.lot_id
                or lines.location_dest_id != scrap.scrap_location_id
                or not isclose(lines.quantity_product_uom, values['scrap_qty'], rel_tol=0, abs_tol=1e-9)):
            raise UserError(_('Не вдалося провести точне списання залишку з карантину.'))
        scraps |= scrap
    return scraps
