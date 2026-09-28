from unittest.mock import patch
from io import BytesIO
from zipfile import ZipFile

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, new_test_user, tagged

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

    def test_search_domain_and_filtered_totals(self):
        self.warehouse.minimum_stock_control = True
        category_b = self.env['product.category'].create({'name': 'Minimum category B'})
        self.other.write({'minimum_stock_qty': 200, 'categ_id': category_b.id})
        both_ids = (self.product | self.other).ids
        all_data = self.report.get_report(domain=[('id', 'in', both_ids)])
        key = 'w%s' % self.warehouse.id
        self.assertEqual(all_data['totals'][key][0], 18)
        for domain in (
            [('categ_id', '=', self.category.id)],
            ['|', ('name', 'ilike', 'MIN-A'), ('default_code', 'ilike', 'MIN-A')],
            ['|', ('name', 'ilike', self.product.name), ('default_code', 'ilike', self.product.name)],
        ):
            data = self.report.get_report(domain=domain, expanded_categories=self.category.ids)
            self.assertEqual(list(data['products']), self.product.ids)
            self.assertEqual([c['id'] for c in data['categories']], self.category.ids)
            self.assertEqual(data['totals'][key][0], 13)
            self.assertEqual(data['categories'][0]['values'][key][0], 13)
        data = self.report.get_report(domain=[('categ_id', '=', category_b.id)])
        self.assertEqual(data['totals'][key][0], 5)
        self.other.minimum_stock_qty = 0
        for domain in ([('id', '=', self.other.id)], ['|', ('minimum_stock_qty', '=', 0), ('id', 'in', both_ids)]):
            data = self.report.get_report(domain=domain, expanded_categories=category_b.ids)
            self.assertNotIn(self.other.id, data['products'])
            self.assertNotIn(category_b.id, [c['id'] for c in data['categories']])

    def test_export_xlsx_filters_measures_and_access(self):
        self.warehouse.minimum_stock_control = True
        self.product.name = '=FORMULA()'
        content = self.report._export_xlsx({
            'domain': [('id', '=', self.product.id)], 'measures': ['qty_available'],
            'total_expanded': True, 'expanded_categories': self.category.ids,
            'expanded_warehouses': self.warehouse.ids,
        })
        with ZipFile(BytesIO(content)) as archive:
            strings = archive.read('xl/sharedStrings.xml').decode()
            sheet = etree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            self.assertIn('=FORMULA()', strings)
            self.assertIn('В наявності', strings)
            self.assertNotIn('Прогнозовано', strings)
            self.assertNotIn('Доступно', strings)
            self.assertFalse(sheet.xpath('//*[local-name()="f"]'))
            self.assertEqual(len(sheet.xpath('//*[local-name()="row"]')), 4)
        outsider = new_test_user(self.env, login='minimum_export_no_stock', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.report.with_user(outsider)._export_xlsx({})

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

    def test_category_totals_exclude_zero_minimum(self):
        result = self._report()
        category = next(c for c in result['categories'] if c['id'] == self.category.id)
        self.assertEqual(category['product_ids'], self.product.ids)
        self.assertNotIn('minimum', category)
        self.assertNotIn(self.other.id, result['products'])
        self.assertIn('[MIN-A]', result['products'][self.product.id]['name'])
        key = 'w' + str(self.warehouse.id)
        self.assertEqual(category['values'][key], [13, 13, 13])
        self.assertEqual(result['totals'][key], category['values'][key])

    def test_only_positive_minimum_products_categories_and_quantities(self):
        configured = self.env['product.product'].create({
            'name': 'Minimum product C', 'detailed_type': 'product',
            'minimum_stock_qty': 10.5, 'categ_id': self.category.id,
        })
        self.env['stock.quant']._update_available_quantity(configured, self.child, 8)
        empty_category = self.env['product.category'].create({'name': 'Unconfigured category'})
        excluded = self.env['product.product'].create({
            'name': 'Minimum product unconfigured', 'detailed_type': 'product',
            'categ_id': empty_category.id,
        })
        self.env['stock.quant']._update_available_quantity(excluded, self.child, 200)
        moves = self.env['stock.move'].create([{
            'name': 'Excluded pending move', 'product_id': self.other.id,
            'product_uom': self.other.uom_id.id, 'product_uom_qty': quantity,
            'location_id': source.id, 'location_dest_id': destination.id,
            'company_id': self.company.id,
        } for source, destination, quantity in (
            (self.child, self.env.ref('stock.stock_location_customers'), 2),
            (self.env.ref('stock.stock_location_suppliers'), self.child, 20),
        )])
        moves._action_confirm()._action_assign()
        computed_ids = set()
        original = type(self.report)._quantities

        def capture(report, products, warehouse, location=None):
            computed_ids.update(products.ids)
            return original(report, products, warehouse, location)

        with patch.object(type(self.report), '_quantities', capture):
            result = self._report()
        self.assertEqual(set(result['products']), {self.product.id, configured.id})
        self.assertEqual(computed_ids, {self.product.id, configured.id})
        self.assertEqual(result['count'], 2)
        self.assertEqual([c['id'] for c in result['categories']], self.category.ids)
        category = result['categories'][0]
        self.assertEqual(category['count'], 2)
        self.assertEqual(set(category['product_ids']), {self.product.id, configured.id})
        key = 'w' + str(self.warehouse.id)
        self.assertEqual(category['values'][key], [21, 21, 21])
        self.assertEqual(result['totals'][key], [21, 21, 21])

    def test_search_cannot_include_zero_minimum(self):
        self.warehouse.minimum_stock_control = True
        self.other.default_code = 'MIN-ZERO-SEARCH'
        for search in (self.other.name, self.other.default_code):
            result = self.report.get_report(search=search, expanded_categories=self.category.ids)
            self.assertEqual(result['count'], 0)
            self.assertFalse(result['products'])
            self.assertFalse(result['categories'])
            self.assertEqual(result['totals']['w' + str(self.warehouse.id)], [0, 0, 0])
        for search in (self.product.name, self.product.default_code):
            result = self.report.get_report(search=search, expanded_categories=self.category.ids)
            self.assertEqual(set(result['products']), {self.product.id})
            self.assertEqual(result['count'], 1)

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
        with Form(variants[0], view='product.product_normal_form_view') as form:
            form.minimum_stock_qty = 12.5
        self.assertEqual(template.minimum_stock_qty, 12.5)
        self.assertEqual(variants[1].minimum_stock_qty, 12.5)
        result = self._report()
        for variant in variants:
            self.assertEqual(result['products'][variant.id]['minimum'], 12.5)
        template.minimum_stock_qty = 0
        result = self._report()
        self.assertFalse(set(variants.ids) & set(result['products']))
        self.assertEqual(result['categories'][0]['product_ids'], self.product.ids)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['totals']['w' + str(self.warehouse.id)], [13, 13, 13])

    def test_minimum_editable_in_variant_and_template_forms(self):
        self.assertEqual(self.other.minimum_stock_qty, 0)
        for record, view in (
            (self.other, 'product.product_normal_form_view'),
            (self.other.product_tmpl_id, 'product.product_template_only_form_view'),
        ):
            for value in (100, 10.5):
                with Form(record, view=view) as form:
                    form.minimum_stock_qty = value
                self.env.flush_all()
                self.env.invalidate_all()
                self.assertEqual(Form(record, view=view).minimum_stock_qty, value)
                self.assertEqual(self.other.product_tmpl_id.minimum_stock_qty, value)
                result = self._report()
                self.assertEqual(result['products'][self.other.id]['minimum'], value)
            with self.assertRaises(ValidationError), self.cr.savepoint():
                with Form(record, view=view) as form:
                    form.minimum_stock_qty = -0.5

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
        foreign_product = self.env['product.product'].create({
            'name': 'Foreign minimum', 'detailed_type': 'product',
            'minimum_stock_qty': 10, 'company_id': company.id,
        })
        filtered = self.report.get_report(domain=[('id', '=', foreign_product.id)])
        self.assertEqual(filtered['count'], 0)
        self.assertEqual([w['id'] for w in filtered['warehouses']], self.warehouse.ids)

    def test_report_access_is_read_only(self):
        for operation in ('write', 'create', 'unlink'):
            self.assertFalse(self.report.check_access_rights(operation, raise_exception=False))
        outsider = new_test_user(self.env, login='minimum_nonstock_user', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.report.with_user(outsider).get_report()

    def test_pagination_preserves_full_totals(self):
        configured = self.env['product.product'].create([{
            'name': 'Minimum product page %03d' % index, 'detailed_type': 'product',
            'categ_id': self.category.id, 'minimum_stock_qty': 10.5,
        } for index in range(PAGE_SIZE)])
        self.env['product.product'].create([{
            'name': 'Minimum product zero page %03d' % index, 'detailed_type': 'product',
            'categ_id': self.category.id,
        } for index in range(PAGE_SIZE)])
        first = self._report()
        second = self._report(pages={str(self.category.id): 1})
        self.assertEqual(len(first['products']), PAGE_SIZE)
        self.assertEqual(len(second['products']), 1)
        self.assertFalse(set(first['products']) & set(second['products']))
        self.assertEqual(set(first['products']) | set(second['products']), set((configured | self.product).ids))
        for result in (first, second):
            self.assertEqual(result['count'], PAGE_SIZE + 1)
            self.assertEqual(result['categories'][0]['count'], PAGE_SIZE + 1)
            self.assertEqual(result['totals']['w' + str(self.warehouse.id)], [13, 13, 13])
        self.assertEqual(first['totals'], second['totals'])

    def test_quantities_are_batched_per_scope(self):
        self.env['product.product'].create([{
            'name': 'Minimum product batch %s' % index, 'detailed_type': 'product',
            'categ_id': self.category.id, 'minimum_stock_qty': 1,
        } for index in range(20)])
        batches = []
        original = type(self.report)._quantities

        def capture(report, products, warehouse, location=None):
            batches.append(len(products))
            return original(report, products, warehouse, location)

        with patch.object(type(self.report), '_quantities', capture):
            self._report()
        self.assertEqual(batches, [21])

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
        self.stock_user.write({'groups_id': [
            Command.link(self.env.ref('stock.group_production_lot').id),
            Command.link(self.env.ref('product.group_stock_packaging').id),
        ]})
        for model, view in (
            ('product.template', 'product.product_template_only_form_view'),
            ('product.product', 'product.product_normal_form_view'),
        ):
            product_view = self.env[model].with_user(self.stock_user).get_view(self.env.ref(view).id, 'form')
            arch = etree.fromstring(product_view['arch'])
            fields = arch.xpath("//page[@name='inventory']/group[@name='minimum_stock_control']/field[@name='minimum_stock_qty']")
            self.assertEqual(len(fields), 1)
            control = fields[0].getparent()
            self.assertEqual(control.get('colspan'), '4')
            self.assertEqual(control.getparent().tag, 'page')
            self.assertEqual(control.getnext().get('name'), 'packaging')
            preceding = control.getprevious()
            self.assertEqual(preceding.get('name'), 'inventory')
            self.assertEqual(len(preceding.xpath(".//group[@name='traceability']")), 1)
        menu = self.env.ref('stock_minimum_control_vataga.menu_stock_minimum_control')
        self.assertEqual(menu.parent_id, self.env.ref('stock.menu_warehouse_report'))
