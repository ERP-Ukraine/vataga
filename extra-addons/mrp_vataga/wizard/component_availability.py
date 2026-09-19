from collections import defaultdict
from graphlib import CycleError, TopologicalSorter

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

    def _get_stock_quantities(self, products):
        """One stock scope for both assemblies and leaves, with no double counting."""
        products = products.with_context(
            warehouse=self.warehouse_ids.ids, location=False, strict=False,
            lot_id=None, owner_id=None, package_id=None, from_date=False, to_date=False,
        )
        products.invalidate_recordset(['qty_available', 'free_qty'])
        result = {}
        for product in products:
            free = product.free_qty
            reserved = max(product.qty_available - free, 0.0)
            result[product.id] = (free, reserved, free + reserved)
        return result

    def _get_bom_graph(self):
        """Build each product's normalized edges once, finding BOMs in batches.

        Both normal and phantom BOMs are eligible. Use the selected root BOM's
        company (falling back to the product/current company) and operation type.
        The full structure is needed even below stocked assemblies to compute
        the theoretical norm independently of stock.
        """
        Product = self.env['product.product']
        company = self.bom_id.company_id or self.product_id.company_id or self.env.company
        boms = {self.product_id.id: self.bom_id}
        graph = {}
        pending = self.product_id
        while pending:
            next_ids = set()
            for product in pending:
                bom = boms[product.id]
                graph[product.id] = None  # None is a leaf; {} is an empty BOM.
                if not bom:
                    continue
                output = bom.product_uom_id._compute_quantity(
                    bom.product_qty, product.uom_id, round=False,
                )
                if output <= 0:
                    raise ValidationError(_('Кількість у специфікації %s має бути більше нуля.') % bom.display_name)
                edges = defaultdict(float)
                for line in bom.bom_line_ids:
                    if line._skip_bom_line(product) or not line.product_qty:
                        continue
                    edges[line.product_id.id] += line.product_uom_id._compute_quantity(
                        line.product_qty, line.product_id.uom_id, round=False,
                    ) / output
                graph[product.id] = dict(edges)
                next_ids.update(edges)
            pending = Product.browse(sorted(next_ids - boms.keys()))
            found = self.env['mrp.bom']._bom_find(
                pending, company_id=company.id,
                picking_type=self.bom_id.picking_type_id, bom_type=False,
            )
            boms.update({product.id: found[product] for product in pending})

        parents = {product_id: set() for product_id in graph}
        for parent, children in graph.items():
            for child in children or {}:
                parents[child].add(parent)
        try:
            order = list(TopologicalSorter(parents).static_order())
        except CycleError as error:
            # graphlib uses an explicit DFS path and reports the actual cycle.
            path = ' → '.join(
                '%s [%s]' % (Product.browse(pid).display_name, boms[pid].display_name)
                for pid in error.args[1]
            )
            raise ValidationError(_('Виявлено цикл специфікацій: %s') % path) from error
        return graph, order

    def _get_leaf_demands(self, graph, order, stock):
        """Aggregate every incoming branch BEFORE netting an assembly's stock.

        A topological pass handles arbitrary nesting without Python recursion.
        The root represents production to perform, so its own stock is ignored.
        Leaves are netted only by action_check after their demand is aggregated.
        """
        theoretical = defaultdict(float, {self.product_id.id: 1.0})
        actual = defaultdict(float, {self.product_id.id: float(self.quantity)})
        for product_id in order:
            children = graph[product_id]
            if children is None:
                continue
            to_produce = actual[product_id]
            if product_id != self.product_id.id:
                to_produce = max(to_produce - max(stock[product_id][2], 0.0), 0.0)
            for child, factor in children.items():
                theoretical[child] += theoretical[product_id] * factor
                actual[child] += to_produce * factor
        return {
            pid: (theoretical[pid], actual[pid])
            for pid in order if graph[pid] is None and actual[pid] > 0
        }

    def action_open_results(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        action = self.env['ir.actions.actions']._for_xml_id(
            'mrp_vataga.action_mrp_component_availability_lines',
        )
        action['domain'] = [('wizard_id', '=', self.id)]
        return action

    def action_check(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self._validate_parameters()

        graph, order = self._get_bom_graph()
        stock = self._get_stock_quantities(self.env['product.product'].browse(order))
        demand = self._get_leaf_demands(graph, order, stock)
        products = self.env['product.product'].browse(list(demand))
        values = []
        for product in products:
            per_unit, total_demand = demand[product.id]
            free, reserved, physical = stock[product.id]
            shortage = max(total_demand - physical, 0.0)
            if float_is_zero(shortage, precision_rounding=product.uom_id.rounding):
                continue
            values.append(fields.Command.create({
                'product_id': product.id,
                'demand_per_unit': per_unit,
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
    _order = 'category_id, product_id, id'

    wizard_id = fields.Many2one(
        'mrp.component.availability', required=True, ondelete='cascade', index=True,
    )
    product_id = fields.Many2one('product.product', string='Назва товару', required=True)
    category_id = fields.Many2one(related='product_id.categ_id', string='Категорія товару', store=True)
    default_code = fields.Char(related='product_id.default_code', string='Код товару', store=True)
    uom_id = fields.Many2one(related='product_id.uom_id', string='Од. виміру', store=True)
    demand_per_unit = fields.Float(
        string='Попит на 1', digits='Product Unit of Measure',
        help='Теоретична норма на один готовий виріб за всіма рівнями BOM, без урахування залишків.',
    )
    total_demand = fields.Float(
        string='Попит загальний', digits='Product Unit of Measure',
        help='Потреба для плану після покриття готовими підзбірками зі складу. '
             'Може бути меншою за «Попит на 1 × Кількість».',
    )
    free_qty = fields.Float(string='Наявна кількість', digits='Product Unit of Measure')
    reserved_qty = fields.Float(string='Зарезервована кількість', digits='Product Unit of Measure')
    shortage_qty = fields.Float(string='Нестача', digits='Product Unit of Measure')
