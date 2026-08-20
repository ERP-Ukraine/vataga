from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQualityCheckArrivalDetails(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Товар для перевірки надходження',
        })
        cls.quality_team = cls.env['quality.alert.team'].search([], limit=1)
        if not cls.quality_team:
            cls.quality_team = cls.env['quality.alert.team'].create({
                'name': 'Команда тестування надходження',
            })
        cls.test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not cls.test_type:
            cls.test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тестовий тип надходження',
                'technical_name': 'quality_vataga_arrival_details_test',
            })
        cls.first_warehouse = cls.env['stock.warehouse'].create({
            'name': 'Склад надходження A',
            'code': 'QVA',
        })
        cls.second_warehouse = cls.env['stock.warehouse'].create({
            'name': 'Склад надходження B',
            'code': 'QVB',
        })

    def _create_picking(
        self,
        warehouse=None,
        scheduled_date='2026-08-20 10:00:00',
        backorder=None,
    ):
        warehouse = warehouse or self.first_warehouse
        values = {
            'picking_type_id': warehouse.in_type_id.id,
            'location_id': warehouse.in_type_id.default_location_src_id.id,
            'location_dest_id': (
                warehouse.in_type_id.default_location_dest_id.id
            ),
            'scheduled_date': fields.Datetime.to_datetime(scheduled_date),
        }
        if backorder:
            values['backorder_id'] = backorder.id
        return self.env['stock.picking'].create(values)

    def _create_check(self, picking=None):
        values = {
            'product_id': self.product.id,
            'team_id': self.quality_team.id,
            'test_type_id': self.test_type.id,
            'measure_on': 'product',
        }
        if picking:
            values['picking_id'] = picking.id
        return self.env['quality.check'].create(values)

    def test_incoming_check_uses_current_picking_warehouse_and_date(self):
        scheduled_date = fields.Datetime.to_datetime(
            '2026-08-20 10:00:00',
        )
        picking = self._create_picking(scheduled_date=scheduled_date)

        check = self._create_check(picking)

        self.assertEqual(check.arrival_warehouse_id, self.first_warehouse)
        self.assertEqual(check.arrival_scheduled_date, scheduled_date)

    def test_scheduled_date_change_updates_existing_check(self):
        picking = self._create_picking(
            scheduled_date='2026-08-20 10:00:00',
        )
        check = self._create_check(picking)
        updated_date = fields.Datetime.to_datetime(
            '2026-08-23 14:30:00',
        )

        picking.scheduled_date = updated_date

        self.assertEqual(check.arrival_scheduled_date, updated_date)

    def test_checks_from_different_warehouses_keep_their_warehouse(self):
        first_check = self._create_check(self._create_picking())
        second_check = self._create_check(self._create_picking(
            warehouse=self.second_warehouse,
        ))

        self.assertEqual(
            first_check.arrival_warehouse_id,
            self.first_warehouse,
        )
        self.assertEqual(
            second_check.arrival_warehouse_id,
            self.second_warehouse,
        )

    def test_picking_type_change_updates_existing_check_warehouse(self):
        picking = self._create_picking()
        check = self._create_check(picking)

        picking.picking_type_id = self.second_warehouse.in_type_id

        self.assertEqual(
            check.arrival_warehouse_id,
            self.second_warehouse,
        )

    def test_backorder_uses_its_own_picking_date_and_warehouse(self):
        parent_date = fields.Datetime.to_datetime(
            '2026-08-20 10:00:00',
        )
        backorder_date = fields.Datetime.to_datetime(
            '2026-08-25 10:00:00',
        )
        parent = self._create_picking(scheduled_date=parent_date)
        backorder = self._create_picking(
            warehouse=self.second_warehouse,
            scheduled_date=backorder_date,
            backorder=parent,
        )

        parent_check = self._create_check(parent)
        backorder_check = self._create_check(backorder)

        self.assertEqual(parent_check.arrival_scheduled_date, parent_date)
        self.assertEqual(
            parent_check.arrival_warehouse_id,
            self.first_warehouse,
        )
        self.assertEqual(
            backorder_check.arrival_scheduled_date,
            backorder_date,
        )
        self.assertEqual(
            backorder_check.arrival_warehouse_id,
            self.second_warehouse,
        )

    def test_manual_check_has_no_arrival_details(self):
        check = self._create_check()

        self.assertFalse(check.arrival_warehouse_id)
        self.assertFalse(check.arrival_scheduled_date)

    def test_mrp_or_workorder_check_without_picking(self):
        check = self._create_check()

        self.assertFalse(check.picking_id)
        self.assertFalse(check.arrival_warehouse_id)
        self.assertFalse(check.arrival_scheduled_date)

    def test_stored_fields_support_search_ordering(self):
        first_check = self._create_check(self._create_picking(
            warehouse=self.first_warehouse,
            scheduled_date='2026-08-25 10:00:00',
        ))
        second_check = self._create_check(self._create_picking(
            warehouse=self.second_warehouse,
            scheduled_date='2026-08-20 10:00:00',
        ))
        domain = [('id', 'in', (first_check | second_check).ids)]

        ascending_dates = self.env['quality.check'].search(
            domain,
            order='arrival_scheduled_date asc',
        )
        descending_dates = self.env['quality.check'].search(
            domain,
            order='arrival_scheduled_date desc',
        )
        ordered_warehouses = self.env['quality.check'].search(
            domain,
            order='arrival_warehouse_id asc',
        )

        self.assertEqual(ascending_dates, second_check | first_check)
        self.assertEqual(descending_dates, first_check | second_check)
        expected_warehouse_order = (first_check | second_check).sorted(
            key=lambda check: check.arrival_warehouse_id.id,
        )
        self.assertEqual(ordered_warehouses, expected_warehouse_order)

    def test_stored_fields_support_grouping(self):
        first_check = self._create_check(self._create_picking(
            warehouse=self.first_warehouse,
            scheduled_date='2026-08-20 10:00:00',
        ))
        second_check = self._create_check(self._create_picking(
            warehouse=self.second_warehouse,
            scheduled_date='2026-08-25 10:00:00',
        ))
        domain = [('id', 'in', (first_check | second_check).ids)]

        warehouse_groups = self.env['quality.check'].read_group(
            domain,
            ['arrival_warehouse_id'],
            ['arrival_warehouse_id'],
            lazy=False,
        )
        date_groups = self.env['quality.check'].read_group(
            domain,
            ['arrival_scheduled_date'],
            ['arrival_scheduled_date:day'],
            lazy=False,
        )

        grouped_warehouse_ids = {
            group['arrival_warehouse_id'][0]
            for group in warehouse_groups
        }
        self.assertEqual(
            grouped_warehouse_ids,
            {self.first_warehouse.id, self.second_warehouse.id},
        )
        self.assertEqual(len(date_groups), 2)
