from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQualityCheckProductQuantity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.dozen_uom = cls.env.ref('uom.product_uom_dozen')
        cls.product = cls.env['product.product'].create({
            'name': 'Товар для перевірки кількості',
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        cls.other_product = cls.env['product.product'].create({
            'name': 'Інший товар у переміщенні',
            'uom_id': cls.unit_uom.id,
            'uom_po_id': cls.unit_uom.id,
        })
        cls.quality_team = cls.env['quality.alert.team'].search([], limit=1)
        if not cls.quality_team:
            cls.quality_team = cls.env['quality.alert.team'].create({
                'name': 'Команда тестування кількості товару',
            })
        cls.test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not cls.test_type:
            cls.test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тестовий тип кількості товару',
                'technical_name': 'quality_vataga_product_quantity_test',
            })
        cls.picking_type = cls.env['stock.picking.type'].search([
            ('default_location_src_id', '!=', False),
            ('default_location_dest_id', '!=', False),
        ], limit=1)
        cls.source_location = cls.picking_type.default_location_src_id
        cls.destination_location = (
            cls.picking_type.default_location_dest_id
        )

    def _create_picking(self, backorder=None):
        values = {
            'picking_type_id': self.picking_type.id,
            'location_id': self.source_location.id,
            'location_dest_id': self.destination_location.id,
        }
        if backorder:
            values['backorder_id'] = backorder.id
        return self.env['stock.picking'].create(values)

    def _create_move(
        self,
        picking,
        product=None,
        quantity=1,
        uom=None,
    ):
        product = product or self.product
        uom = uom or product.uom_id
        return self.env['stock.move'].create({
            'name': product.display_name,
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': quantity,
            'product_uom': uom.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
        })

    def _create_move_line(self, move, quantity, uom=None):
        uom = uom or move.product_uom
        return self.env['stock.move.line'].create({
            'move_id': move.id,
            'picking_id': move.picking_id.id,
            'product_id': move.product_id.id,
            'product_uom_id': uom.id,
            'quantity': quantity,
            'location_id': move.location_id.id,
            'location_dest_id': move.location_dest_id.id,
        })

    def _create_check(self, **extra_values):
        values = {
            'product_id': self.product.id,
            'team_id': self.quality_team.id,
            'test_type_id': self.test_type.id,
            'measure_on': 'product',
        }
        values.update(extra_values)
        return self.env['quality.check'].create(values)

    def test_direct_move_line_uses_only_its_quantity_and_converts_uom(self):
        picking = self._create_picking()
        move = self._create_move(
            picking,
            quantity=3,
            uom=self.dozen_uom,
        )
        selected_line = self._create_move_line(
            move,
            quantity=2,
            uom=self.dozen_uom,
        )
        self._create_move_line(move, quantity=1, uom=self.dozen_uom)

        check = self._create_check(
            picking_id=picking.id,
            move_line_id=selected_line.id,
            measure_on='move_line',
        )

        self.assertTrue(check.has_operation_product_quantity)
        self.assertEqual(check.operation_product_uom_id, self.unit_uom)
        self.assertEqual(check.operation_product_quantity, 24)
        self.assertIn('24', check.operation_product_quantity_label)

    def test_product_check_uses_unique_move_actual_quantity(self):
        picking = self._create_picking()
        move = self._create_move(picking, quantity=20)
        self._create_move_line(move, quantity=8)
        self._create_move_line(move, quantity=6)
        other_move = self._create_move(
            picking,
            product=self.other_product,
            quantity=100,
        )
        self._create_move_line(other_move, quantity=100)

        check = self._create_check(picking_id=picking.id)

        self.assertTrue(check.has_operation_product_quantity)
        self.assertEqual(check.operation_product_quantity, 14)

    def test_incoming_check_exposes_picking_and_quantity_label(self):
        picking = self._create_picking()
        move = self._create_move(picking, quantity=555)
        self._create_move_line(move, quantity=555)

        check = self._create_check(picking_id=picking.id)

        self.assertEqual(check.picking_id, picking)
        self.assertEqual(check.operation_product_quantity, 555)
        self.assertIn('555', check.operation_product_quantity_label)

    def test_product_check_falls_back_to_planned_quantity(self):
        picking = self._create_picking()
        self._create_move(picking, quantity=20)

        check = self._create_check(picking_id=picking.id)

        self.assertTrue(check.has_operation_product_quantity)
        self.assertEqual(check.operation_product_quantity, 20)

    def test_current_backorder_does_not_include_other_picking(self):
        current_picking = self._create_picking()
        current_move = self._create_move(current_picking, quantity=5)
        self._create_move_line(current_move, quantity=5)
        backorder_picking = self._create_picking(backorder=current_picking)
        backorder_move = self._create_move(backorder_picking, quantity=15)
        self._create_move_line(backorder_move, quantity=15)

        current_check = self._create_check(picking_id=current_picking.id)
        backorder_check = self._create_check(picking_id=backorder_picking.id)

        self.assertEqual(current_check.picking_id, current_picking)
        self.assertEqual(backorder_check.picking_id, backorder_picking)
        self.assertEqual(current_check.operation_product_quantity, 5)
        self.assertEqual(backorder_check.operation_product_quantity, 15)
        self.assertIn('15', backorder_check.operation_product_quantity_label)

    def test_ambiguous_moves_and_manual_check_hide_quantity(self):
        picking = self._create_picking()
        self._create_move(picking, quantity=4)
        self._create_move(picking, quantity=6)

        ambiguous_check = self._create_check(picking_id=picking.id)
        manual_check = self._create_check()

        self.assertFalse(ambiguous_check.has_operation_product_quantity)
        self.assertFalse(ambiguous_check.operation_product_quantity_label)
        self.assertFalse(manual_check.picking_id)
        self.assertFalse(manual_check.has_operation_product_quantity)
        self.assertFalse(manual_check.operation_product_quantity_label)

    def test_existing_check_recomputes_after_move_line_change(self):
        picking = self._create_picking()
        move = self._create_move(picking, quantity=12)
        move_line = self._create_move_line(move, quantity=7)
        check = self._create_check(picking_id=picking.id)

        self.assertEqual(check.operation_product_quantity, 7)
        move_line.quantity = 9
        self.assertEqual(check.operation_product_quantity, 9)
