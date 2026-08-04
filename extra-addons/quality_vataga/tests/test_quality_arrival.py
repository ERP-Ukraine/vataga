from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQualityArrival(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.incoming_type = cls.env['stock.picking.type'].search([
            ('code', '=', 'incoming'),
            ('default_location_src_id', '!=', False),
            ('default_location_dest_id', '!=', False),
        ], limit=1)
        cls.outgoing_type = cls.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('default_location_src_id', '!=', False),
            ('default_location_dest_id', '!=', False),
        ], limit=1)
        cls.internal_type = cls.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('default_location_src_id', '!=', False),
            ('default_location_dest_id', '!=', False),
        ], limit=1)
        cls.quality_team = cls.env['quality.alert.team'].create({
            'name': 'Команда перевірки фактичного надходження',
        })
        cls.test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not cls.test_type:
            cls.test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тест фактичного надходження',
                'technical_name': 'quality_vataga_arrival_test',
            })
        cls.product = cls.env['product.product'].create({
            'name': 'Товар для перевірки фактичного надходження',
        })
        cls.point = cls._create_point(
            'Контроль після фактичного надходження',
            user=cls.env.user,
        )
        cls.point_without_user = cls._create_point(
            'Контроль без відповідального інспектора',
        )
        cls.visual_point = cls._create_point(
            'Візуальний контроль після надходження',
            user=cls.env.user,
            visual=True,
        )
        cls.activity_type = cls.env.ref(
            'quality_vataga.mail_activity_type_quality_arrival_inspection',
        )
        cls.quality_check_model = cls.env['ir.model']._get('quality.check')

    @classmethod
    def _create_point(cls, title, user=None, visual=False):
        point = cls.env['quality.point'].create({
            'title': title,
            'team_id': cls.quality_team.id,
            'test_type_id': cls.test_type.id,
            'user_id': user.id if user else False,
            'picking_type_ids': [Command.set(cls.incoming_type.ids)],
        })
        point.visual_sample_control_required = visual
        return point

    def _create_picking(self, picking_type=None, backorder=None):
        picking_type = picking_type or self.incoming_type
        values = {
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        }
        if backorder:
            values['backorder_id'] = backorder.id
        return self.env['stock.picking'].create(values)

    def _create_check(self, picking=None, point=None, user=None):
        point = point or self.point
        values = {
            'point_id': point.id,
            'team_id': point.team_id.id,
            'test_type_id': point.test_type_id.id,
            'product_id': self.product.id,
            'measure_on': 'product',
        }
        if picking:
            values['picking_id'] = picking.id
        if user:
            values['user_id'] = user.id
        return self.env['quality.check'].create(values)

    def _arrival_activities(self, check):
        return self.env['mail.activity'].search([
            ('res_model_id', '=', self.quality_check_model.id),
            ('res_id', '=', check.id),
            ('activity_type_id', '=', self.activity_type.id),
        ])

    def test_incoming_check_waits_and_blocks_pass_and_fail(self):
        picking = self._create_picking()
        check = self._create_check(picking)

        self.assertEqual(
            check.inspection_readiness_state,
            'waiting_arrival',
        )
        with self.assertRaisesRegex(UserError, 'Товар ще не позначено'):
            check.do_pass()
        with self.assertRaisesRegex(UserError, 'Товар ще не позначено'):
            check.do_fail()

    def test_arrival_sets_audit_fields_and_does_not_touch_stock(self):
        picking = self._create_picking()
        move = self.env['stock.move'].create({
            'name': self.product.display_name,
            'picking_id': picking.id,
            'product_id': self.product.id,
            'product_uom_qty': 7,
            'product_uom': self.product.uom_id.id,
            'location_id': picking.location_id.id,
            'location_dest_id': picking.location_dest_id.id,
        })
        check = self._create_check(picking)
        initial_state = picking.state
        initial_planned_quantity = move.product_uom_qty
        initial_done_quantity = move.quantity
        initial_check_count = len(picking.check_ids)

        picking.action_confirm_quality_arrival()

        self.assertTrue(picking.quality_arrival_confirmed)
        self.assertTrue(picking.quality_arrival_confirmed_at)
        self.assertEqual(picking.quality_arrival_confirmed_by_id, self.env.user)
        self.assertEqual(check.inspection_readiness_state, 'ready')
        self.assertEqual(picking.state, initial_state)
        self.assertEqual(move.product_uom_qty, initial_planned_quantity)
        self.assertEqual(move.quantity, initial_done_quantity)
        self.assertEqual(len(picking.check_ids), initial_check_count)

    def test_arrival_creates_one_activity_and_is_idempotent(self):
        picking = self._create_picking()
        check = self._create_check(picking)
        picking_messages_before = len(picking.message_ids)
        check_messages_before = len(check.message_ids)

        picking.action_confirm_quality_arrival()
        confirmed_at = picking.quality_arrival_confirmed_at
        confirmed_by = picking.quality_arrival_confirmed_by_id
        picking_messages_after = len(picking.message_ids)
        check_messages_after = len(check.message_ids)

        self.assertEqual(len(self._arrival_activities(check)), 1)
        self.assertEqual(self._arrival_activities(check).user_id, self.env.user)
        self.assertGreater(picking_messages_after, picking_messages_before)
        self.assertGreater(check_messages_after, check_messages_before)

        result = picking.action_confirm_quality_arrival()

        self.assertEqual(result['params']['type'], 'info')
        self.assertEqual(len(self._arrival_activities(check)), 1)
        self.assertEqual(picking.quality_arrival_confirmed_at, confirmed_at)
        self.assertEqual(picking.quality_arrival_confirmed_by_id, confirmed_by)
        self.assertEqual(len(picking.message_ids), picking_messages_after)
        self.assertEqual(len(check.message_ids), check_messages_after)

    def test_no_recipient_warns_without_rolling_back_arrival(self):
        picking = self._create_picking()
        check = self._create_check(
            picking,
            point=self.point_without_user,
        )

        result = picking.action_confirm_quality_arrival()

        self.assertTrue(picking.quality_arrival_confirmed)
        self.assertEqual(check.inspection_readiness_state, 'ready')
        self.assertFalse(self._arrival_activities(check))
        self.assertEqual(result['params']['type'], 'warning')
        self.assertTrue(any(
            'відповідального інспектора не визначено' in (body or '')
            for body in check.message_ids.mapped('body')
        ))

    def test_check_responsible_has_priority_over_qcp_responsible(self):
        inspector = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Окремий відповідальний інспектор',
            'login': 'quality_arrival_check_inspector',
            'email': 'quality-arrival-inspector@example.com',
            'groups_id': [Command.set([
                self.env.ref('base.group_user').id,
                self.env.ref('quality.group_quality_user').id,
            ])],
        })
        picking = self._create_picking()
        check = self._create_check(picking, user=inspector)

        picking.action_confirm_quality_arrival()

        self.assertEqual(
            self._arrival_activities(check).user_id,
            inspector,
        )

    def test_pass_and_fail_complete_only_arrival_activity(self):
        todo_type = self.env.ref('mail.mail_activity_data_todo')
        for method_name in ('do_pass', 'do_fail'):
            picking = self._create_picking()
            check = self._create_check(picking)
            picking.action_confirm_quality_arrival()
            foreign_activity = check.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=self.env.user.id,
                summary='Не пов’язана із надходженням activity',
            )
            self.assertEqual(len(self._arrival_activities(check)), 1)

            getattr(check, method_name)()

            self.assertFalse(self._arrival_activities(check))
            self.assertTrue(foreign_activity.exists())
            self.assertEqual(foreign_activity.activity_type_id, todo_type)

    def test_outgoing_and_internal_are_not_supported(self):
        for picking_type in (self.outgoing_type, self.internal_type):
            picking = self._create_picking(picking_type=picking_type)
            check = self._create_check(picking)
            self.assertEqual(
                check.inspection_readiness_state,
                'not_applicable',
            )
            with self.assertRaisesRegex(UserError, 'лише для вхідного'):
                picking.action_confirm_quality_arrival()

    def test_manual_check_is_not_blocked(self):
        check = self._create_check()

        self.assertEqual(
            check.inspection_readiness_state,
            'not_applicable',
        )
        check.do_fail()
        self.assertEqual(check.quality_state, 'fail')

    def test_mrp_style_check_without_incoming_picking_is_not_blocked(self):
        check = self._create_check()
        check.measure_on = 'operation'

        check._ensure_ready_for_inspection()
        self.assertEqual(
            check.inspection_readiness_state,
            'not_applicable',
        )

    def test_backorder_has_independent_arrival_state(self):
        picking = self._create_picking()
        backorder = self._create_picking(backorder=picking)

        picking.action_confirm_quality_arrival()

        self.assertTrue(picking.quality_arrival_confirmed)
        self.assertFalse(backorder.quality_arrival_confirmed)
        self.assertFalse(backorder.quality_arrival_confirmed_at)
        self.assertFalse(backorder.quality_arrival_confirmed_by_id)

    def test_cancelled_and_completed_checks_get_no_new_activity(self):
        cancelled_picking = self._create_picking()
        cancelled_check = self._create_check(cancelled_picking)
        cancelled_picking.write({'state': 'cancel'})
        with self.assertRaisesRegex(UserError, 'скасованого'):
            cancelled_picking.action_confirm_quality_arrival()
        self.assertFalse(self._arrival_activities(cancelled_check))

        picking = self._create_picking()
        completed_check = self._create_check(picking)
        completed_check.write({'quality_state': 'pass'})
        picking.action_confirm_quality_arrival()
        self.assertEqual(completed_check.quality_state, 'pass')
        self.assertFalse(self._arrival_activities(completed_check))

    def test_done_incoming_is_logically_ready_without_notification(self):
        picking = self._create_picking()
        check = self._create_check(picking)
        picking.write({'state': 'done'})

        self.assertEqual(check.inspection_readiness_state, 'ready')
        with self.assertRaisesRegex(UserError, 'завершеного'):
            picking.action_confirm_quality_arrival()
        self.assertFalse(self._arrival_activities(check))

    def test_visual_matrix_is_locked_until_arrival_then_works(self):
        picking = self._create_picking()
        check = self._create_check(picking, point=self.visual_point)

        with self.assertRaisesRegex(UserError, 'Товар ще не позначено'):
            check.add_measurement_samples(1)
        with self.assertRaisesRegex(UserError, 'Товар ще не позначено'):
            check.remove_measurement_samples(1)

        picking.action_confirm_quality_arrival()
        check.add_measurement_samples(1)
        sample = check.sample_ids
        check.update_measurement_visual_result(sample.id, 'yes')
        self.assertEqual(sample.visual_result, 'yes')

        check.add_measurement_samples(1)
        check.remove_measurement_samples(1)
        self.assertEqual(len(check.sample_ids), 1)

    def test_direct_arrival_field_write_is_rejected(self):
        picking = self._create_picking()
        with self.assertRaisesRegex(UserError, 'лише дією'):
            picking.write({'quality_arrival_confirmed': True})
        with self.assertRaisesRegex(UserError, 'лише дією'):
            self.env['stock.picking'].create({
                'picking_type_id': self.incoming_type.id,
                'location_id': self.incoming_type.default_location_src_id.id,
                'location_dest_id': (
                    self.incoming_type.default_location_dest_id.id
                ),
                'quality_arrival_confirmed': True,
            })

    def test_user_without_stock_access_cannot_confirm_arrival(self):
        restricted_user = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Користувач без складських прав',
            'login': 'quality_arrival_restricted_user',
            'email': 'quality-arrival-restricted@example.com',
            'groups_id': [Command.set([
                self.env.ref('base.group_user').id,
            ])],
        })
        picking = self._create_picking()

        with self.assertRaises(AccessError):
            picking.with_user(restricted_user).action_confirm_quality_arrival()
        self.assertFalse(picking.quality_arrival_confirmed)
