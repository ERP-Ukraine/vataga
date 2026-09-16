from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero


class MrpComponentAvailability(models.TransientModel):
    _name = 'mrp.component.availability'
    _description = 'Наявність комплектуючих'

    product_id = fields.Many2one('product.product', string='Товар', required=True)
    product_tmpl_id = fields.Many2one(related='product_id.product_tmpl_id')
    product_uom_id = fields.Many2one(related='product_id.uom_id', string='Од. виміру')
    bom_id = fields.Many2one('mrp.bom', string='Специфікація', required=True)
    quantity = fields.Integer(string='Кількість', default=1, required=True)
    warehouse_ids = fields.Many2many('stock.warehouse', string='Склади', required=True)
    line_ids = fields.One2many(
        'mrp.component.availability.line', 'wizard_id', string='Нестача комплектуючих',
        readonly=True,
    )
    checked = fields.Boolean(readonly=True)

    def _bom_matches_product(self):
        self.ensure_one()
        return bool(
            self.product_id and self.bom_id
            and self.bom_id.product_tmpl_id == self.product_id.product_tmpl_id
            and (not self.bom_id.product_id or self.bom_id.product_id == self.product_id)
        )

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.bom_id and not self._bom_matches_product():
            self.bom_id = False

    @api.onchange('product_id', 'bom_id', 'quantity', 'warehouse_ids')
    def _onchange_parameters(self):
        self.line_ids = [fields.Command.clear()]
        self.checked = False

    def _validate_parameters(self):
        self.ensure_one()
        if not self.product_id:
            raise ValidationError(_('Виберіть товар.'))
        if not self.bom_id:
            raise ValidationError(_('Виберіть специфікацію.'))
        if self.quantity <= 0:
            raise ValidationError(_('Кількість має бути цілим числом більше нуля.'))
        if not self.warehouse_ids:
            raise ValidationError(_('Виберіть хоча б один склад.'))
        if not self._bom_matches_product():
            raise ValidationError(_('Специфікація не відповідає вибраному товару.'))
        if not self.bom_id.active or self.bom_id.product_qty <= 0:
            raise ValidationError(_('Потрібна активна специфікація з кількістю більше нуля.'))
        # Validate the selected records as well as the UI domains; never elevate access.
        for records in (self.product_id, self.bom_id, self.warehouse_ids):
            records.check_access_rights('read')
            records.check_access_rule('read')
            if records.mapped('company_id') - self.env.companies:
                raise ValidationError(_('Вибрані записи мають належати дозволеним компаніям.'))

    def action_check(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self._validate_parameters()

        # The entered quantity is in the finished product's default UoM.
        bom_output = self.bom_id.product_uom_id._compute_quantity(
            self.bom_id.product_qty, self.product_id.uom_id, round=False,
        )
        demand = defaultdict(float)
        for line in self.bom_id.bom_line_ids:
            if line._skip_bom_line(self.product_id):
                continue
            demand[line.product_id.id] += line.product_uom_id._compute_quantity(
                line.product_qty, line.product_id.uom_id, round=False,
            ) / bom_output

        # Odoo accepts a list of warehouses and builds ONE union of their view
        # location hierarchies. Each quant is counted once, even for overlapping
        # roots. Clear unrelated stock filters inherited from a calling action.
        products = self.env['product.product'].browse(list(demand)).with_context(
            warehouse=self.warehouse_ids.ids, location=False, strict=False,
            lot_id=None, owner_id=None, package_id=None, from_date=False, to_date=False,
        )
        # Refresh computed stock fields on repeated checks in the same environment.
        products.invalidate_recordset(['qty_available', 'free_qty'])
        values = []
        for product in products:
            total_demand = demand[product.id] * self.quantity
            free = product.free_qty
            reserved = max(product.qty_available - free, 0.0)
            shortage = max(total_demand - (free + reserved), 0.0)
            if float_is_zero(shortage, precision_rounding=product.uom_id.rounding):
                continue
            values.append(fields.Command.create({
                'product_id': product.id,
                'demand_per_unit': demand[product.id],
                'total_demand': total_demand,
                'free_qty': free,
                'reserved_qty': reserved,
                'shortage_qty': shortage,
            }))
        self.write({'line_ids': [fields.Command.clear()] + values, 'checked': True})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Наявність комплектуючих'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }


class MrpComponentAvailabilityLine(models.TransientModel):
    _name = 'mrp.component.availability.line'
    _description = 'Нестача комплектуючого'
    _order = 'id'

    wizard_id = fields.Many2one(
        'mrp.component.availability', required=True, ondelete='cascade', index=True,
    )
    product_id = fields.Many2one('product.product', string='Назва товару', required=True)
    category_id = fields.Many2one(related='product_id.categ_id', string='Категорія товару')
    default_code = fields.Char(related='product_id.default_code', string='Код товару')
    uom_id = fields.Many2one(related='product_id.uom_id', string='Од. виміру')
    demand_per_unit = fields.Float(string='Попит на 1', digits='Product Unit of Measure')
    total_demand = fields.Float(string='Попит загальний', digits='Product Unit of Measure')
    free_qty = fields.Float(string='Наявна кількість', digits='Product Unit of Measure')
    reserved_qty = fields.Float(string='Зарезервована кількість', digits='Product Unit of Measure')
    shortage_qty = fields.Float(string='Нестача', digits='Product Unit of Measure')
