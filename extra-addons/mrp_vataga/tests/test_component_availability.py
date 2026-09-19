from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestComponentAvailability(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.w1, cls.w2, cls.outside = cls.env['stock.warehouse'].create([
            {'name': 'Availability ' + code, 'code': code, 'company_id': cls.env.company.id}
            for code in ('CAV1', 'CAV2', 'CAVX')
        ])
        cls.finished, cls.component, cls.empty = cls.env['product.product'].create([
            {'name': name, 'detailed_type': 'product'}
            for name in ('Availability finished', 'Availability component', 'Availability empty')
        ])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished.product_tmpl_id.id,
            'product_qty': 2,
            'product_uom_id': cls.finished.uom_id.id,
            'bom_line_ids': [fields.Command.create({
                'product_id': cls.component.id, 'product_qty': 4,
                'product_uom_id': cls.component.uom_id.id,
            })],
        })
        cls.child_location = cls.env['stock.location'].create({
            'name': 'Availability shelf', 'usage': 'internal',
            'location_id': cls.w1.lot_stock_id.id,
            'company_id': cls.env.company.id,
        })
        for location, quantity in (
            (cls.child_location, 5), (cls.w2.lot_stock_id, 9),
            (cls.outside.lot_stock_id, 100),
        ):
            cls.env['stock.quant']._update_available_quantity(cls.component, location, quantity)
        for warehouse, location, quantity in (
            (cls.w1, cls.child_location, 2), (cls.w2, cls.w2.lot_stock_id, 4),
        ):
            move = cls.env['stock.move'].create({
                'name': 'Availability reservation', 'product_id': cls.component.id,
                'product_uom': cls.component.uom_id.id, 'product_uom_qty': quantity,
                'location_id': location.id,
                'location_dest_id': cls.env.ref('stock.stock_location_customers').id,
                'picking_type_id': warehouse.out_type_id.id,
                'company_id': cls.env.company.id,
            })
            move._action_confirm()
            move._action_assign()

    def _wizard(self, warehouses=None, **values):
        return self.env['mrp.component.availability'].create({
            'product_id': self.finished.id, 'bom_id': self.bom.id, 'quantity': 10,
            'warehouse_ids': [fields.Command.set((warehouses or (self.w1 | self.w2)).ids)],
            **values,
        })

    def test_warehouse_union_and_reserved_stock(self):
        for warehouses, free, reserved, shortage in (
            (self.w1, 3, 2, 15), (self.w2, 5, 4, 11),
            (self.w1 | self.w2, 8, 6, 6), (self.w2 | self.w1, 8, 6, 6),
        ):
            with self.subTest(warehouses=warehouses.ids):
                wizard = self._wizard(warehouses)
                self.assertFalse(wizard.line_ids)
                action = wizard.action_check()
                self.assertEqual(action['res_id'], wizard.id)
                line = wizard.line_ids
                self.assertEqual(len(line), 1)
                self.assertEqual(line.demand_per_unit, 2)
                self.assertEqual(line.total_demand, 20)
                self.assertEqual(line.free_qty, free)
                self.assertEqual(line.reserved_qty, reserved)
                self.assertEqual(line.shortage_qty, shortage)

    def test_recheck_quantity_warehouses_and_sufficient_stock(self):
        wizard = self._wizard()
        wizard.action_check()
        old_lines = wizard.line_ids
        wizard.action_check()
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertFalse(old_lines.exists())
        wizard.quantity = 7
        wizard.action_check()
        self.assertFalse(wizard.line_ids)
        wizard.warehouse_ids = self.w1
        wizard.action_check()
        self.assertEqual(wizard.line_ids.shortage_qty, 9)

    def test_all_reserved(self):
        self.env['stock.quant']._update_reserved_quantity(self.component, self.child_location, 3)
        wizard = self._wizard(self.w1)
        wizard.action_check()
        self.assertEqual(wizard.line_ids.free_qty, 0)
        self.assertEqual(wizard.line_ids.reserved_qty, 5)
        self.assertEqual(wizard.line_ids.shortage_qty, 15)

    def test_missing_component_and_single_selected_stock_location(self):
        self.bom.bom_line_ids.product_id = self.empty
        wizard = self._wizard()
        wizard.action_check()
        self.assertEqual(wizard.line_ids.shortage_qty, 20)
        self.env['stock.quant']._update_available_quantity(self.empty, self.w2.lot_stock_id, 3)
        wizard.action_check()
        self.assertEqual(wizard.line_ids.free_qty, 3)
        self.assertEqual(wizard.line_ids.shortage_qty, 17)

    def test_duplicate_fractional_lines_and_uom_conversion(self):
        kg = self.env.ref('uom.product_uom_kgm')
        gram = self.env.ref('uom.product_uom_gram')
        dozen = self.env.ref('uom.product_uom_dozen')
        component = self.env['product.product'].create({
            'name': 'Availability mass', 'detailed_type': 'product',
            'uom_id': kg.id, 'uom_po_id': kg.id,
        })
        self.bom.write({
            'product_qty': 2, 'product_uom_id': dozen.id,
            'bom_line_ids': [fields.Command.clear()] + [
                fields.Command.create({
                    'product_id': component.id, 'product_qty': qty, 'product_uom_id': uom.id,
                }) for qty, uom in ((600, gram), (0.6, kg))
            ],
        })
        wizard = self._wizard(quantity=10)
        wizard.action_check()
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertAlmostEqual(wizard.line_ids.demand_per_unit, 0.05)
        self.assertAlmostEqual(wizard.line_ids.shortage_qty, 0.5)

    def test_invalid_parameters_and_product_onchange(self):
        wizard = self._wizard()
        for quantity in (0, -1):
            wizard.quantity = quantity
            with self.assertRaises(ValidationError):
                wizard.action_check()
        wizard.quantity = 1
        wizard.warehouse_ids = False
        with self.assertRaises(ValidationError):
            wizard.action_check()
        wizard.warehouse_ids = self.w1
        wizard.product_id = self.component
        with self.assertRaises(ValidationError):
            wizard.action_check()
        wizard._onchange_product_id()
        self.assertFalse(wizard.bom_id)
        with self.assertRaises(ValidationError):
            wizard.action_check()

    def test_stock_context_does_not_leak(self):
        wizard = self._wizard().with_context(
            warehouse=self.outside.id, location=self.outside.lot_stock_id.id,
            strict=True, to_date='2000-01-01', owner_id=self.env.user.partner_id.id,
        )
        wizard.action_check()
        self.assertEqual(wizard.line_ids.shortage_qty, 6)

    def test_access_and_company_validation(self):
        user = new_test_user(self.env, login='component_report_user', groups='mrp.group_mrp_user')
        wizard = self.env['mrp.component.availability'].with_user(user).create({
            'product_id': self.finished.id, 'bom_id': self.bom.id, 'quantity': 10,
            'warehouse_ids': [fields.Command.set((self.w1 | self.w2).ids)],
        })
        wizard.action_check()
        self.assertEqual(wizard.line_ids.shortage_qty, 6)
        other = self.env['res.company'].create({'name': 'Availability other company'})
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', other.id)], limit=1)
        if not warehouse:
            warehouse = self.env['stock.warehouse'].create({
                'name': 'Availability other warehouse', 'code': 'CAVO', 'company_id': other.id,
            })
        wizard = self._wizard(warehouse).with_context(allowed_company_ids=[self.env.company.id])
        with self.assertRaises((ValidationError, AccessError)):
            wizard.action_check()

    def test_phantom_bom_nets_existing_subassemblies(self):
        self.env['mrp.bom'].create({
            'product_tmpl_id': self.component.product_tmpl_id.id,
            'type': 'phantom',
            'bom_line_ids': [fields.Command.create({
                'product_id': self.empty.id, 'product_qty': 10,
            })],
        })
        wizard = self._wizard()
        wizard.action_check()
        self.assertEqual(wizard.line_ids.product_id, self.empty)
        self.assertEqual(wizard.line_ids.demand_per_unit, 20)
        self.assertEqual(wizard.line_ids.total_demand, 60)

    def test_check_does_not_write_business_records(self):
        wizard = self._wizard()
        quants = self.env['stock.quant'].search([('product_id', '=', self.component.id)])
        before_quants = quants.read(['quantity', 'reserved_quantity', 'write_date'])
        models = ('stock.move', 'mrp.production', 'mrp.bom', 'mrp.bom.line')
        before_records = {name: self.env[name].search([]).read(['write_date']) for name in models}
        wizard.action_check()
        wizard.action_check()
        self.assertEqual(quants.read(['quantity', 'reserved_quantity', 'write_date']), before_quants)
        for name in models:
            self.assertEqual(self.env[name].search([]).read(['write_date']), before_records[name])

    def test_variant_specific_bom_rejected(self):
        attribute = self.env['product.attribute'].create({
            'name': 'Availability size',
            'value_ids': [fields.Command.create({'name': name}) for name in ('A', 'B')],
        })
        template = self.env['product.template'].create({
            'name': 'Availability variants',
            'attribute_line_ids': [fields.Command.create({
                'attribute_id': attribute.id,
                'value_ids': [fields.Command.set(attribute.value_ids.ids)],
            })],
        })
        first, second = template.product_variant_ids
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': template.id, 'product_id': first.id,
            'bom_line_ids': [fields.Command.create({
                'product_id': self.empty.id, 'product_qty': 2,
            })],
        })
        wizard = self._wizard(product_id=second.id, bom_id=bom.id)
        with self.assertRaises(ValidationError):
            wizard.action_check()
        bom.product_id = False
        bom.bom_line_ids.bom_product_template_attribute_value_ids = first.product_template_attribute_value_ids
        wizard.action_check()
        self.assertFalse(wizard.line_ids)
        wizard.product_id = first
        wizard.action_check()
        self.assertEqual(wizard.line_ids.shortage_qty, 20)

    def _child_bom(self, product, components, **values):
        return self.env['mrp.bom'].create({
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_uom_id': product.uom_id.id,
            'bom_line_ids': [fields.Command.create({
                'product_id': component.id, 'product_qty': qty,
                'product_uom_id': component.uom_id.id,
            }) for component, qty in components],
            **values,
        })

    def test_nested_zero_partial_and_full_subassembly_stock(self):
        self._child_bom(self.component, [(self.empty, 3)])
        # Root needs 20 B; fixtures supply 14 physical B (6 reserved).
        wizard = self._wizard()
        wizard.action_check()
        self.assertEqual(wizard.line_ids.demand_per_unit, 6)
        self.assertEqual(wizard.line_ids.total_demand, 18)
        self.assertEqual(wizard.line_ids.product_id, self.empty)
        # Select a warehouse with no B, rather than relying on global stock.
        clean = self.env['stock.warehouse'].create({'name': 'No B', 'code': 'CAVN'})
        wizard.warehouse_ids = clean
        wizard.action_check()
        self.assertEqual(wizard.line_ids.total_demand, 60)
        wizard.warehouse_ids = self.outside
        wizard.action_check()
        self.assertFalse(wizard.line_ids)

    def test_shared_leaf_stock_is_applied_once(self):
        branch = self.env['product.product'].create({'name': 'Second branch', 'detailed_type': 'product'})
        self.bom.write({'bom_line_ids': [fields.Command.create({
            'product_id': branch.id, 'product_qty': 2,
        })]})
        self._child_bom(self.component, [(self.empty, 4)])
        self._child_bom(branch, [(self.empty, 6)])
        self.env['stock.quant']._update_available_quantity(self.empty, self.w1.lot_stock_id, 70)
        wizard = self._wizard()
        wizard.action_check()
        # 6 missing first assemblies * 4 + 10 second assemblies * 6 = 84.
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids.demand_per_unit, 14)
        self.assertEqual(wizard.line_ids.total_demand, 84)
        self.assertEqual(wizard.line_ids.shortage_qty, 14)

    def test_shared_subassembly_at_different_depths(self):
        branch = self.env['product.product'].create({'name': 'Intermediate branch', 'detailed_type': 'product'})
        self.bom.bom_line_ids.write({'product_qty': 10})
        self.bom.write({'bom_line_ids': [fields.Command.create({
            'product_id': branch.id, 'product_qty': 10,
        })]})
        self._child_bom(branch, [(self.component, 1)])
        self._child_bom(self.component, [(self.empty, 3)])
        wizard = self._wizard(quantity=2)
        wizard.action_check()
        # B demand is 10 direct + 10 via the branch. Its 14 units count once.
        self.assertEqual(wizard.line_ids.total_demand, 18)
        self.assertEqual(wizard.line_ids.demand_per_unit, 30)
        # Reverse root line order: allocation must not depend on traversal order.
        self.bom.bom_line_ids[0].sequence = 99
        wizard.action_check()
        self.assertEqual(wizard.line_ids.total_demand, 18)

    def test_multilevel_uom_and_non_unit_child_bom(self):
        middle = self.env['product.product'].create({'name': 'Middle', 'detailed_type': 'product'})
        self._child_bom(self.component, [(middle, 6)], product_qty=2)
        child = self._child_bom(middle, [(self.empty, 24)], product_qty=1,
                               product_uom_id=self.env.ref('uom.product_uom_dozen').id)
        # Same UoM category, different BOM line UoM (1 dozen = 12 units).
        child.bom_line_ids.write({
            'product_qty': 2, 'product_uom_id': self.env.ref('uom.product_uom_dozen').id,
        })
        self.env['stock.quant']._update_available_quantity(middle, self.w2.lot_stock_id, 3)
        wizard = self._wizard()
        wizard.action_check()
        # A -> 2 B -> 6 middle -> 12 leaf. Net: (6 B * 3 - 3 middle) * 2.
        self.assertEqual(wizard.line_ids.demand_per_unit, 12)
        self.assertEqual(wizard.line_ids.total_demand, 30)

    def test_cycle_error_contains_product_path(self):
        # Standard Odoo prevents creating cycles. Bypass only that constraint in
        # this fixture to simulate corrupted/imported legacy data.
        Bom = self.env['mrp.bom']
        constraints = [method for method in Bom._constraint_methods if method.__name__ != '_check_bom_cycle']
        with patch.object(type(Bom), '_constraint_methods', constraints):
            self._child_bom(self.component, [(self.finished, 1)])
        wizard = self._wizard()
        with self.assertRaisesRegex(ValidationError, 'Виявлено цикл специфікацій:.*Availability'):
            wizard.action_check()

    def test_child_bom_selection_variant_template_company_and_type(self):
        attribute = self.env['product.attribute'].create({
            'name': 'Child variation',
            'value_ids': [fields.Command.create({'name': name}) for name in ('A', 'B')],
        })
        template = self.env['product.template'].create({
            'name': 'Child variants', 'detailed_type': 'product',
            'attribute_line_ids': [fields.Command.create({
                'attribute_id': attribute.id, 'value_ids': [fields.Command.set(attribute.value_ids.ids)],
            })],
        })
        first, second = template.product_variant_ids
        self._child_bom(first, [(self.empty, 2)], sequence=10)
        specific = self._child_bom(first, [(self.empty, 3)], product_id=first.id, sequence=1)
        self.bom.bom_line_ids.product_id = first
        wizard = self._wizard()
        wizard.action_check()
        self.assertEqual(wizard.line_ids.total_demand, 60)
        self.bom.bom_line_ids.product_id = second
        wizard.action_check()
        self.assertEqual(wizard.line_ids.total_demand, 40)
        other = self.env['res.company'].create({'name': 'Foreign BOM company'})
        self._child_bom(second, [(self.empty, 99)], company_id=other.id, sequence=0)
        wizard.with_context(allowed_company_ids=[self.env.company.id, other.id]).action_check()
        self.assertEqual(wizard.line_ids.total_demand, 40)
        specific.active = False
        self.bom.bom_line_ids.product_id = first
        wizard.action_check()
        self.assertEqual(wizard.line_ids.total_demand, 40)

    def test_result_action_search_sort_and_group(self):
        wizard = self._wizard()
        wizard.action_check()
        other = self._wizard()
        other.action_check()
        action = wizard.action_open_results()
        Line = self.env['mrp.component.availability.line']
        self.assertEqual(Line.search(action['domain']), wizard.line_ids)
        self.assertEqual(action['target'], 'current')
        for field in ('category_id', 'product_id', 'default_code', 'demand_per_unit',
                      'total_demand', 'free_qty', 'reserved_qty', 'shortage_qty', 'uom_id'):
            self.assertTrue(Line._fields[field].store)
            self.assertEqual(Line.search(action['domain'], order=field), wizard.line_ids)
        groups = Line.read_group(action['domain'], ['total_demand:sum'], ['category_id'])
        self.assertEqual(groups[0]['total_demand'], 20)
        self.assertEqual(Line.search(action['domain'] + [('product_id.name', 'ilike', 'Availability')]), wizard.line_ids)
