from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.stock_minimum_report import MEASURES, PAGE_SIZE


@tagged('post_install', '-at_install')
class TestStockMinimumReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.company.id),
        ]).minimum_stock_control = False
        cls.warehouse = cls.env['stock.warehouse'].create({
            'name': 'Minimum MH', 'code': 'SMH', 'company_id': cls.company.id,
        })
        cls.disabled = cls.env['stock.warehouse'].create({
            'name': 'Minimum disabled', 'code': 'SGL', 'company_id': cls.company.id,
        })
        cls.child = cls.env['stock.location'].create({
            'name': 'Components', 'usage': 'internal',
            'location_id': cls.warehouse.lot_stock_id.id, 'company_id': cls.company.id,
        })
        cls.category = cls.env['product.category'].create({'name': 'Minimum test category'})
        cls.product = cls.env['product.product'].create({
            'name': 'Minimum product A', 'default_code': 'MIN-A',
            'detailed_type': 'product', 'categ_id': cls.category.id,
            'minimum_stock_qty': 100,
        })
        cls.other = cls.env['product.product'].create({
            'name': 'Minimum product B', 'detailed_type': 'product',
            'categ_id': cls.category.id,
        })
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.warehouse.lot_stock_id, 4)
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.child, 9)
        cls.env['stock.quant']._update_available_quantity(cls.other, cls.child, 5)
        cls.env['stock.quant']._update_available_quantity(cls.product, cls.disabled.lot_stock_id, 70)
        cls.stock_user = new_test_user(
            cls.env, login='minimum_stock_user', groups='stock.group_stock_user',
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
        )
        cls.report = cls.env['stock.minimum.control.report'].with_user(cls.stock_user).with_context(
            allowed_company_ids=cls.company.ids,
        )

    def _report(self, **kwargs):
        self.warehouse.minimum_stock_control = True
        return self.report.get_report(expanded_categories=self.category.ids, search='Minimum product', **kwargs)

    def _move(self, source, destination, quantity):
        move = self.env['stock.move'].create({
            'name': 'Minimum test move', 'product_id': self.product.id,
            'product_uom': self.product.uom_id.id, 'product_uom_qty': quantity,
            'location_id': source.id, 'location_dest_id': destination.id,
            'company_id': self.company.id,
        })
        move._action_confirm()
        return move

    def _standard_values(self, **context):
        product = self.product.with_user(self.stock_user).with_context(
            allowed_company_ids=self.company.ids, **context,
        )
        # Force recomputation: Odoo 17 does not include strict in depends_context.
        product.invalidate_recordset(list(MEASURES))
        return [product[field] for field in MEASURES]

    def test_defaults(self):
        self.assertFalse(self.warehouse.minimum_stock_control)
        self.assertEqual(self.other.product_tmpl_id.minimum_stock_qty, 0.0)

    def test_minimum_validation(self):
        for value in (0, 10, 10.5):
            self.other.product_tmpl_id.minimum_stock_qty = value
            self.assertEqual(self.other.product_tmpl_id.minimum_stock_qty, value)
        for value in (-1, -0.5):
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.other.product_tmpl_id.minimum_stock_qty = value
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.env['product.template'].create({'name': 'Invalid minimum', 'minimum_stock_qty': value})

    def test_only_enabled_warehouses(self):
        self.assertFalse(self.report.get_report()['warehouses'])
        result = self._report()
        self.assertEqual([w['id'] for w in result['warehouses']], self.warehouse.ids)

    def test_warehouse_quantities_reservations_and_forecast(self):
        self._move(self.env.ref('stock.stock_location_suppliers'), self.child, 7)
        outgoing = self._move(self.child, self.env.ref('stock.stock_location_customers'), 3)
        outgoing._action_assign()
        result = self._report()
        values = result['products'][self.product.id]['values']['w' + str(self.warehouse.id)]
        self.assertEqual(values, self._standard_values(warehouse=self.warehouse.id))
        self.assertEqual(values, [13, 10, 17])

    def test_exact_location_quantities_no_double_count(self):
        self._move(self.child, self.warehouse.lot_stock_id, 2)._action_assign()
        result = self._report(expanded_warehouses=self.warehouse.ids)
        values = result['products'][self.product.id]['values']
        self.assertNotIn('w' + str(self.warehouse.id), values)
        for location in (self.warehouse.lot_stock_id, self.child):
            self.assertEqual(values['l' + str(location.id)], self._standard_values(
                warehouse=self.warehouse.id, location=location.id, strict=True,
            ))
        parent = values['l' + str(self.warehouse.lot_stock_id.id)]
        child = values['l' + str(self.child.id)]
        self.assertEqual(parent, [4, 4, 6])
        self.assertEqual(child, [9, 7, 7])
        self.assertEqual(parent[0] + child[0], 13)
        self.assertEqual(parent[2] + child[2], 13)

    def test_locations_are_internal_including_archived_stock(self):
        excluded = self.env['stock.location']
        for usage in ('view', 'customer', 'supplier', 'inventory', 'transit'):
            excluded |= self.env['stock.location'].create({
                'name': usage, 'usage': usage, 'location_id': self.warehouse.view_location_id.id,
                'company_id': self.company.id,
            })
        archived = self.env['stock.location'].create({
            'name': 'Archived shelf', 'usage': 'internal', 'active': False,
            'location_id': self.warehouse.view_location_id.id, 'company_id': self.company.id,
        })
        result = self._report(expanded_warehouses=self.warehouse.ids)
        keys = {col['key'] for col in result['warehouses'][0]['columns']}
        self.assertTrue({'l' + str(loc.id) for loc in (self.warehouse.lot_stock_id | self.child | archived)} <= keys)
        self.assertFalse({'l' + str(loc.id) for loc in excluded} & keys)
        shown = self.env['stock.location'].browse([int(key[1:]) for key in keys])
        self.assertTrue(all(loc.usage == 'internal' for loc in shown))

    def test_category_totals_and_zero_minimum(self):
        result = self._report()
        category = next(c for c in result['categories'] if c['id'] == self.category.id)
        self.assertEqual(set(category['product_ids']), {self.product.id, self.other.id})
        self.assertNotIn('minimum', category)
        self.assertEqual(result['products'][self.other.id]['minimum'], 0)
        self.assertIn('[MIN-A]', result['products'][self.product.id]['name'])
        key = 'w' + str(self.warehouse.id)
        self.assertEqual(category['values'][key], [18, 18, 18])
        self.assertEqual(result['totals'][key], category['values'][key])

    def test_variants_share_template_minimum_keep_own_stock(self):
        attribute = self.env['product.attribute'].create({
            'name': 'Minimum size', 'value_ids': [Command.create({'name': 'A'}), Command.create({'name': 'B'})],
        })
        template = self.env['product.template'].create({
            'name': 'Minimum product variants', 'detailed_type': 'product',
            'minimum_stock_qty': 10.5, 'categ_id': self.category.id,
            'attribute_line_ids': [Command.create({
                'attribute_id': attribute.id, 'value_ids': [Command.set(attribute.value_ids.ids)],
            })],
        })
        variants = template.product_variant_ids.sorted('id')
        self.assertEqual(len(variants), 2)
        for variant, quantity in zip(variants, (3, 8)):
            self.env['stock.quant']._update_available_quantity(variant, self.child, quantity)
        result = self._report()
        for variant, quantity in zip(variants, (3, 8)):
            row = result['products'][variant.id]
            self.assertEqual(row['minimum'], 10.5)
            self.assertEqual(row['values']['w' + str(self.warehouse.id)][0], quantity)

    def test_company_isolation_and_forged_context(self):
        company = self.env['res.company'].create({'name': 'Minimum second company'})
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        warehouse.minimum_stock_control = True
        result = self._report(expanded_warehouses=warehouse.ids)
        self.assertEqual([w['id'] for w in result['warehouses']], self.warehouse.ids)
        with self.assertRaises(AccessError):
            self.report.with_context(allowed_company_ids=company.ids).get_report()
        self.stock_user.company_ids += company
        both = self.report.with_context(allowed_company_ids=(self.company | company).ids).get_report()
        self.assertEqual({w['id'] for w in both['warehouses']}, {self.warehouse.id, warehouse.id})
        # Having access to both companies must not force both into the active report.
        one = self.report.get_report()
        self.assertEqual([w['id'] for w in one['warehouses']], self.warehouse.ids)

    def test_report_access_is_read_only(self):
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(self.report.check_access_rights(operation, raise_exception=False))
        outsider = new_test_user(self.env, login='minimum_nonstock_user', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.report.with_user(outsider).get_report()

    def test_pagination_preserves_full_totals(self):
        self.env['product.product'].create([{
            'name': 'Minimum product page %03d' % index, 'detailed_type': 'product',
            'categ_id': self.category.id,
        } for index in range(PAGE_SIZE)])
        first = self._report()
        second = self._report(pages={str(self.category.id): 1})
        self.assertEqual(len(first['products']), PAGE_SIZE)
        self.assertEqual(len(second['products']), 2)
        self.assertFalse(set(first['products']) & set(second['products']))
        self.assertEqual(first['totals'], second['totals'])

    def test_quantities_are_batched_per_scope(self):
        self.env['product.product'].create([{
            'name': 'Minimum product batch %s' % index, 'detailed_type': 'product',
            'categ_id': self.category.id,
        } for index in range(20)])
        batches = []
        original = type(self.report)._quantities

        def capture(report, products, warehouse, location=None):
            batches.append(len(products))
            return original(report, products, warehouse, location)

        with patch.object(type(self.report), '_quantities', capture):
            self._report()
        self.assertEqual(batches, [22])

    def test_stale_quantity_context_is_ignored(self):
        self.warehouse.minimum_stock_control = True
        result = self.report.with_context(
            warehouse=self.disabled.id, location=self.disabled.lot_stock_id.id,
            lot_id=-1, owner_id=-1, package_id=-1, to_date='2000-01-01', strict=True,
        ).get_report(expanded_categories=self.category.ids, search='Minimum product')
        values = result['products'][self.product.id]['values']['w' + str(self.warehouse.id)]
        self.assertEqual(values, [13, 13, 13])

    def test_inherited_views_and_menu(self):
        warehouse_view = self.env['stock.warehouse'].get_view(self.env.ref('stock.view_warehouse').id, 'form')
        self.assertIn('minimum_stock_control', warehouse_view['arch'])
        product_view = self.env['product.template'].get_view(
            self.env.ref('product.product_template_only_form_view').id, 'form',
        )
        self.assertIn('minimum_stock_qty', product_view['arch'])
        menu = self.env.ref('stock_minimum_control_vataga.menu_stock_minimum_control')
        self.assertEqual(menu.parent_id, self.env.ref('stock.menu_warehouse_report'))
