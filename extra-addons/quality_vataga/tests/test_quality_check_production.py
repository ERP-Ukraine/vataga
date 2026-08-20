from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQualityCheckProduction(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Товар для виробничої перевірки',
        })
        cls.quality_team = cls.env['quality.alert.team'].search([], limit=1)
        if not cls.quality_team:
            cls.quality_team = cls.env['quality.alert.team'].create({
                'name': 'Команда тестування виробництва',
            })
        cls.test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not cls.test_type:
            cls.test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тестовий тип виробництва',
                'technical_name': 'quality_vataga_production_test',
            })
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.env.company.id),
        ], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Склад виробничих перевірок',
                'code': 'QVP',
            })

    def _create_production(self, quantity=1):
        picking_type = self.warehouse.manu_type_id
        return self.env['mrp.production'].create({
            'product_id': self.product.id,
            'product_qty': quantity,
            'product_uom_id': self.product.uom_id.id,
            'picking_type_id': picking_type.id,
            'location_src_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })

    def _create_picking(self):
        picking_type = self.warehouse.in_type_id
        return self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'scheduled_date': fields.Datetime.to_datetime(
                '2026-08-20 10:00:00',
            ),
        })

    def _create_check(self, production=None, picking=None):
        values = {
            'product_id': self.product.id,
            'team_id': self.quality_team.id,
            'test_type_id': self.test_type.id,
            'measure_on': 'product',
        }
        if production:
            values['production_id'] = production.id
        if picking:
            values['picking_id'] = picking.id
        return self.env['quality.check'].create(values)

    def test_uses_standard_mrp_production_field(self):
        production_field = self.env['quality.check']._fields[
            'production_id'
        ]

        self.assertEqual(production_field.type, 'many2one')
        self.assertEqual(production_field.comodel_name, 'mrp.production')

    def test_mrp_check_keeps_production_without_arrival_details(self):
        production = self._create_production()

        check = self._create_check(production=production)

        self.assertEqual(check.production_id, production)
        self.assertFalse(check.picking_id)
        self.assertFalse(check.arrival_warehouse_id)
        self.assertFalse(check.arrival_scheduled_date)

    def test_incoming_and_manual_checks_have_no_production(self):
        picking = self._create_picking()

        incoming_check = self._create_check(picking=picking)
        manual_check = self._create_check()

        self.assertFalse(incoming_check.production_id)
        self.assertEqual(
            incoming_check.arrival_warehouse_id,
            self.warehouse,
        )
        self.assertEqual(
            incoming_check.arrival_scheduled_date,
            picking.scheduled_date,
        )
        self.assertFalse(manual_check.production_id)
        self.assertFalse(manual_check.arrival_warehouse_id)
        self.assertFalse(manual_check.arrival_scheduled_date)

    def test_checks_from_two_productions_keep_their_source(self):
        first_production = self._create_production(quantity=1)
        second_production = self._create_production(quantity=2)

        first_check = self._create_check(production=first_production)
        second_check = self._create_check(production=second_production)

        self.assertEqual(first_check.production_id, first_production)
        self.assertEqual(second_check.production_id, second_production)

    def test_production_supports_search_and_grouping(self):
        first_production = self._create_production(quantity=1)
        second_production = self._create_production(quantity=2)
        first_check = self._create_check(production=first_production)
        second_check = self._create_check(production=second_production)
        domain = [('id', 'in', (first_check | second_check).ids)]

        found = self.env['quality.check'].search(domain + [
            ('production_id', '=', first_production.id),
        ])
        groups = self.env['quality.check'].read_group(
            domain,
            ['production_id'],
            ['production_id'],
            lazy=False,
        )

        self.assertEqual(found, first_check)
        self.assertEqual(
            {
                group['production_id'][0]
                for group in groups
            },
            {first_production.id, second_production.id},
        )

    def test_arrival_fields_keep_their_existing_related_paths(self):
        warehouse_field = self.env['quality.check']._fields[
            'arrival_warehouse_id'
        ]
        scheduled_date_field = self.env['quality.check']._fields[
            'arrival_scheduled_date'
        ]

        self.assertEqual(
            tuple(warehouse_field.related),
            ('picking_id', 'picking_type_id', 'warehouse_id'),
        )
        self.assertEqual(
            tuple(scheduled_date_field.related),
            ('picking_id', 'scheduled_date'),
        )
