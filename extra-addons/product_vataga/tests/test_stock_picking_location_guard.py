from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestStockPickingLocationGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].create(
            {
                'name': 'Vataga Location Guard Test Warehouse',
                'code': 'VLG',
                'company_id': cls.env.company.id,
            }
        )
        cls.location_a = cls._create_location('Location A')
        cls.location_b = cls._create_location('Location B')
        cls.location_c = cls._create_location('Location C')
        cls.scrap_location = cls.env['stock.location'].create(
            {
                'name': 'Guard Scrap Location',
                'location_id': cls.warehouse.view_location_id.id,
                'usage': 'inventory',
                'scrap_location': True,
                'company_id': cls.env.company.id,
            }
        )
        cls.product = cls.env['product.product'].create(
            {
                'name': 'Stock Picking Location Guard Product',
                'detailed_type': 'product',
            }
        )

    @classmethod
    def _create_location(cls, name):
        return cls.env['stock.location'].create(
            {
                'name': name,
                'location_id': cls.warehouse.view_location_id.id,
                'usage': 'internal',
                'company_id': cls.env.company.id,
            }
        )

    def _create_picking_with_move(
        self,
        move_source,
        move_destination,
        picking_type=None,
    ):
        picking = self.env['stock.picking'].create(
            {
                'picking_type_id': (
                    picking_type or self.warehouse.int_type_id
                ).id,
                'location_id': self.location_a.id,
                'location_dest_id': self.location_b.id,
            }
        )
        move = self.env['stock.move'].create(
            {
                'name': self.product.display_name,
                'picking_id': picking.id,
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'location_id': move_source.id,
                'location_dest_id': move_destination.id,
            }
        )
        return picking, move

    def test_internal_destination_mismatch_is_blocked(self):
        picking, _move = self._create_picking_with_move(
            self.location_a,
            self.location_c,
        )

        with self.assertRaises(UserError):
            picking._check_internal_move_locations()

    def test_internal_source_mismatch_is_blocked(self):
        picking, _move = self._create_picking_with_move(
            self.location_c,
            self.location_b,
        )

        with self.assertRaises(UserError):
            picking._check_internal_move_locations()

    def test_matching_internal_move_is_allowed(self):
        picking, _move = self._create_picking_with_move(
            self.location_a,
            self.location_b,
        )

        picking._check_internal_move_locations()

    def test_cancelled_mismatching_move_is_ignored(self):
        picking, move = self._create_picking_with_move(
            self.location_a,
            self.location_c,
        )
        move._action_cancel()

        self.assertEqual(move.state, 'cancel')
        picking._check_internal_move_locations()

    def test_scrapped_mismatching_move_is_ignored(self):
        picking, move = self._create_picking_with_move(
            self.location_a,
            self.scrap_location,
        )

        self.assertTrue(move.scrapped)
        picking._check_internal_move_locations()

    def test_non_internal_mismatch_is_ignored(self):
        picking, _move = self._create_picking_with_move(
            self.location_c,
            self.location_b,
            picking_type=self.warehouse.in_type_id,
        )

        self.assertNotEqual(picking.picking_type_code, 'internal')
        picking._check_internal_move_locations()

    def test_button_validate_runs_guard_before_standard_validation(self):
        picking, move = self._create_picking_with_move(
            self.location_a,
            self.location_c,
        )

        with self.assertRaisesRegex(
            UserError,
            'Неможливо провести внутрішнє переміщення',
        ):
            picking.button_validate()

        self.assertEqual(picking.state, 'draft')
        self.assertEqual(move.state, 'draft')
