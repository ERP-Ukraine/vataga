"""Native MRP lifecycle bridge. No QUNIT models, valuation or stock algorithm.

The capability is Python-local and scoped to one validated server transaction;
neither RPC context nor a persisted unlock flag can create it. Community tests
exercise this exact bridge with real MRP/Stock, independently of Enterprise QC.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round


_OPERATION = ContextVar('quality_native_backorder_operation', default=None)


@dataclass
class NativeBackorderOperation:
    production: object
    quantity: int
    unit_ids: tuple = ()
    remaining_ids: tuple = ()
    children: set = field(default_factory=set)

    @contextmanager
    def activate(self):
        token = _OPERATION.set(self)
        try:
            yield self
        finally:
            _OPERATION.reset(token)


def native_operation():
    return _OPERATION.get()


def native_operation_allows(productions):
    operation = native_operation()
    return bool(operation and productions and all(
        production.env.cr is operation.production.env.cr
        and (production.id == operation.production.id or production.id in operation.children)
        for production in productions))


def assert_native_inventory_allowed(productions):
    operation = native_operation()
    if operation and operation.quantity <= 0 and native_operation_allows(productions):
        raise UserError(_('Робочий інтервал ремонту не дозволяє складське проведення або Backorder.'))


class NativeBackorderProduction(models.Model):
    _inherit = 'mrp.production'

    quality_native_parent_id = fields.Many2one(
        'mrp.production', string='Поштучний маршрут: вихідний MO',
        readonly=True, copy=False, ondelete='restrict', index=True)

    def pre_button_mark_done(self):
        assert_native_inventory_allowed(self)
        return super().pre_button_mark_done()

    def _post_inventory(self, cancel_backorder=False):
        assert_native_inventory_allowed(self)
        return super()._post_inventory(cancel_backorder=cancel_backorder)

    def _split_productions(self, amounts=False, cancel_remaining_qty=False, set_consumed_qty=False):
        assert_native_inventory_allowed(self)
        return super()._split_productions(amounts=amounts, cancel_remaining_qty=cancel_remaining_qty, set_consumed_qty=set_consumed_qty)

    def _get_backorder_mo_vals(self):
        values = super()._get_backorder_mo_vals()
        operation = native_operation()
        if operation and self == operation.production:
            values['quality_native_parent_id'] = self.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        operation = native_operation()
        for values in vals_list:
            if 'quality_native_parent_id' in values and (
                    not operation or operation.quantity <= 0 or values['quality_native_parent_id'] != operation.production.id):
                raise UserError(_('Преемність MO встановлює лише штатний захищений Backorder.'))
        context = dict(self.env.context)
        context.pop('default_quality_native_parent_id', None)
        records = super(NativeBackorderProduction, self.with_context(context)).create(vals_list)
        children = records.filtered('quality_native_parent_id')
        if children:
            operation.children.update(children.ids)
            for child in children:
                if (child.company_id != operation.production.company_id
                        or child.product_id != operation.production.product_id
                        or child.bom_id != operation.production.bom_id
                        or child.product_uom_id != operation.production.product_uom_id):
                    raise ValidationError(_('Backorder не відповідає вихідному MO.'))
            if 'quality_transfer_controlled' in children._fields:
                for child in children:
                    child._enable_quality_transfer_control()
                    if ('quality_unit_route_required' in operation.production._fields
                            and operation.production.quality_unit_route_required):
                        child._enable_required_unit_route()
        return records

    def write(self, vals):
        if 'quality_native_parent_id' in vals:
            raise UserError(_('Історія преемності MO незмінна.'))
        return super().write(vals)

    def _native_quality_complete(self):
        """Only a validated caller may enter this Python-local capability."""
        self.ensure_one()
        operation = native_operation()
        if not operation or operation.production != self:
            raise UserError(_('Немає захищеної операції поштучного випуску.'))
        original_quantity = self.product_qty
        if (self.qty_produced or self.state not in ('confirmed', 'progress', 'to_close')
                or not 0 < operation.quantity <= original_quantity
                or (operation.quantity < original_quantity and self.picking_type_id.create_backorder != 'ask')
                or self.product_id.type != 'product'
                or self.product_id.tracking != 'none'
                or self.move_byproduct_ids
                or self.move_raw_ids.filtered(lambda move: move.manual_consumption)):
            raise ValidationError(_(
                'Потрібен відкритий MO без попереднього випуску, без tracking/побічної продукції '
                'та ручного споживання. Для неповної партії потрібен Backorder «Запитувати».'))
        for move in self.move_raw_ids.filtered('picked'):
            expected = float_round(operation.quantity * move.unit_factor, precision_rounding=move.product_uom.rounding)
            if float_compare(move.quantity, expected, precision_rounding=move.product_uom.rounding):
                raise ValidationError(_('Вже зафіксоване споживання не відповідає вибраним QUNIT. Автоматичне переписування фактичного споживання заборонене.'))
        # Native UI quantity preparation; no fabricated moves or valuation.
        self.qty_producing = operation.quantity
        self._set_qty_producing()
        action = self.button_mark_done()
        if operation.quantity < original_quantity:
            if not isinstance(action, dict) or action.get('res_model') != 'mrp.production.backorder':
                raise ValidationError(_('Odoo не повернув очікуваний штатний майстер Backorder. Операцію скасовано.'))
            wizard = self.env['mrp.production.backorder'].with_context(action['context']).create({})
            wizard.action_backorder()
        if self.state != 'done' or self.qty_produced != operation.quantity:
            raise ValidationError(_('Штатний випуск не підтвердив вибране поштучне кількісне значення.'))
        children = self.browse(sorted(operation.children))
        expected = original_quantity - operation.quantity
        if ((expected and (len(children) != 1 or children.product_qty != expected))
                or (not expected and children)):
            raise ValidationError(_('Штатний Backorder не відповідає точному залишку.'))
        for child in children:
            if (set(child.workorder_ids.quality_native_parent_workorder_id.ids) != set(self.workorder_ids.ids)
                    or len(child.workorder_ids) != len(self.workorder_ids)):
                raise ValidationError(_('Не підтверджено однозначне копіювання всіх Work Orders.'))
            mapping = {row.quality_native_parent_workorder_id.id: row for row in child.workorder_ids}
            for original in self.workorder_ids:
                # Native copy_data (unlike copy) does not restore ad-hoc WO
                # dependencies. Copy that actual graph onto new WOs only.
                mapping[original.id].blocked_by_workorder_ids = [Command.set([
                    mapping[predecessor.id].id for predecessor in original.blocked_by_workorder_ids])]
        return children


class NativeBackorderWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    quality_native_parent_workorder_id = fields.Many2one(
        'mrp.workorder', string='Поштучний маршрут: вихідна операція',
        readonly=True, copy=False, ondelete='restrict', index=True)

    def copy_data(self, default=None):
        values = super().copy_data(default)
        operation = native_operation()
        if operation and self.production_id == operation.production:
            for original, copied in zip(self, values):
                copied['quality_native_parent_workorder_id'] = original.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        operation = native_operation()
        for values in vals_list:
            if 'quality_native_parent_workorder_id' in values:
                original = self.browse(values['quality_native_parent_workorder_id'])
                if not operation or operation.quantity <= 0 or original.production_id != operation.production:
                    raise UserError(_('Преемність операцій встановлює лише штатне копіювання Backorder.'))
        context = dict(self.env.context)
        context.pop('default_quality_native_parent_workorder_id', None)
        return super(NativeBackorderWorkorder, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'quality_native_parent_workorder_id' in vals:
            raise UserError(_('Історія преемності операцій незмінна.'))
        return super().write(vals)


class NativeRepairInventoryBoundary(models.Model):
    _inherit = 'stock.move'

    def _native_repair_inventory_check(self):
        assert_native_inventory_allowed(self.production_id | self.raw_material_production_id | self.workorder_id.production_id)

    def _action_done(self, cancel_backorder=False):
        self._native_repair_inventory_check()
        return super()._action_done(cancel_backorder=cancel_backorder)

    def write(self, vals):
        if vals.get('state') == 'done':
            self._native_repair_inventory_check()
        return super().write(vals)
