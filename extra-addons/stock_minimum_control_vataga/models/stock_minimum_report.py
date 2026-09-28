from collections import defaultdict

from odoo import api, models
from odoo.exceptions import AccessError


MEASURES = ('qty_available', 'free_qty', 'virtual_available')
PAGE_SIZE = 80
BATCH_SIZE = 1000


class StockMinimumReport(models.AbstractModel):
    """Read-only RPC service; no persisted product x location fact table."""

    _name = 'stock.minimum.control.report'
    _description = 'Контроль залишків'

    @api.model
    def get_report(self, expanded_warehouses=None, expanded_categories=None,
                   pages=None, search=''):
        self.check_access_rights('read')
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError('Недостатньо прав для перегляду складського звіту.')
        # Resolving env.companies also validates allowed_company_ids supplied by RPC.
        company_ids = self.env.companies.ids
        expanded_warehouses = set(expanded_warehouses or [])
        expanded_categories = set(expanded_categories or [])
        pages = pages or {}
        warehouses = self.env['stock.warehouse'].search([
            ('minimum_stock_control', '=', True),
            ('company_id', 'in', company_ids),
        ], order='name, id')
        locations = self.env['stock.location'].with_context(active_test=False).search([
            ('id', 'child_of', warehouses.view_location_id.ids),
            ('usage', '=', 'internal'),
            ('company_id', 'in', [False] + company_ids),
        ], order='complete_name, id') if warehouses else self.env['stock.location']
        domain = [
            ('detailed_type', '=', 'product'),
            ('company_id', 'in', [False] + company_ids),
        ]
        if search:
            domain += ['|', ('name', 'ilike', str(search)[:200]),
                       ('default_code', 'ilike', str(search)[:200])]
        products = self.env['product.product'].search(domain, order='default_code, name, id')
        by_category = defaultdict(list)
        for product in products:
            by_category[product.categ_id.id].append(product.id)
        categories = products.categ_id.sorted(lambda c: (c.complete_name, c.id))
        category_rows = []
        visible_ids = set()
        for category in categories:
            ids = by_category[category.id]
            page = max(0, min(int(pages.get(str(category.id), 0)), (len(ids) - 1) // PAGE_SIZE))
            selected = ids[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
            if category.id in expanded_categories:
                visible_ids.update(selected)
            category_rows.append({
                'id': category.id, 'name': category.display_name,
                'count': len(ids), 'page': page, 'product_ids': selected,
                'values': {},
            })
        visible_products = {
            product.id: {
                'id': product.id, 'name': product.display_name,
                'minimum': product.product_tmpl_id.minimum_stock_qty,
                'uom': product.uom_id.display_name, 'values': {},
            } for product in products if product.id in visible_ids
        }
        totals = {}
        warehouse_columns = []
        category_values = {row['id']: row['values'] for row in category_rows}
        for warehouse in warehouses:
            warehouse_locations = locations.filtered(
                lambda loc: loc.parent_path.startswith(warehouse.view_location_id.parent_path)
            )
            expanded = warehouse.id in expanded_warehouses and bool(warehouse_locations)
            scopes = warehouse_locations if expanded else warehouse
            columns = [{
                'key': ('l' if expanded else 'w') + str(scope.id),
                'name': scope.display_name,
            } for scope in scopes]
            warehouse_columns.append({
                'id': warehouse.id, 'name': warehouse.display_name,
                'expanded': expanded, 'can_expand': bool(warehouse_locations),
                'columns': columns,
            })
            # Shared products participate in each selected company. Products owned
            # by another company have zero quantities in this warehouse.
            scoped_products = products.filtered(
                lambda p: not p.company_id or p.company_id == warehouse.company_id
            )
            for scope, column in zip(scopes, columns):
                key = column['key']
                totals[key] = [0.0, 0.0, 0.0]
                for values in category_values.values():
                    values[key] = [0.0, 0.0, 0.0]
                for values in visible_products.values():
                    values['values'][key] = [0.0, 0.0, 0.0]
                for start in range(0, len(scoped_products), BATCH_SIZE):
                    batch = scoped_products[start:start + BATCH_SIZE]
                    quantities = self._quantities(batch, warehouse, scope if expanded else None)
                    for product in batch:
                        values = [quantities[product.id][measure] for measure in MEASURES]
                        for index, value in enumerate(values):
                            totals[key][index] += value
                            category_values[product.categ_id.id][key][index] += value
                        if product.id in visible_products:
                            visible_products[product.id]['values'][key] = values
        return {
            'warehouses': warehouse_columns, 'categories': category_rows,
            'products': visible_products, 'totals': totals,
            'count': len(products), 'page_size': PAGE_SIZE,
            'digits': self.env['decimal.precision'].precision_get('Product Unit of Measure'),
        }

    def _quantities(self, products, warehouse, location=None):
        # Clean context: stale date/lot/location filters from another action must
        # not affect this current-stock report. Never widen the active companies.
        context = {
            'lang': self.env.context.get('lang'),
            'tz': self.env.context.get('tz'),
            'allowed_company_ids': [warehouse.company_id.id],
            'warehouse': warehouse.id,
            'location': location.id if location else False,
            'strict': bool(location),
        }
        scoped = products.with_context(context).with_company(warehouse.company_id)
        # Standard Odoo computation includes reservations and pending moves.
        # Call the batch implementation directly: `strict` is not part of the
        # computed fields' depends_context in Odoo 17, so cached field reads can
        # otherwise reuse a non-strict location value in this same transaction.
        return scoped._compute_quantities_dict(None, None, None)
