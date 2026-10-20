from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class QualityCheckInProcessCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({'name': 'Виріб етапу 12'})
        cls.team = cls.env['quality.alert.team'].search([], limit=1)
        if not cls.team:
            cls.team = cls.env['quality.alert.team'].create({'name': 'Контроль партії'})
        cls.test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not cls.test_type:
            cls.test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тип контролю партії',
                'technical_name': 'quality_vataga_stage12_test',
            })
        cls.warehouse = cls.env['stock.warehouse'].search([
            ('company_id', '=', cls.env.company.id),
        ], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Склад етапу 12', 'code': 'Q12',
            })
        cls.category = cls.env['maintenance.equipment.category'].create({
            'name': 'ЗВТ етапу 12', 'requires_equipment_selection': True,
        })
        cls.equipment = cls.env['maintenance.equipment'].create({
            'name': 'ЗВТ партії', 'category_id': cls.category.id,
            'serial_no': 'STAGE12-1',
        })
        cls.point = cls.env['quality.point'].create({
            'title': 'Міжопераційний контроль етапу 12',
            'team_id': cls.team.id, 'test_type_id': cls.test_type.id,
            'picking_type_ids': [Command.set(cls.warehouse.manu_type_id.ids)],
            'x_control_stage': 'in_process',
        })
        for index, parameter_type in enumerate(('numeric', 'boolean', 'string')):
            parameter = cls.env['quality.equipment.parameter'].create({
                'name': 'Параметр партії %s' % parameter_type,
                'parameter_type': parameter_type,
            })
            cls.category.applicable_parameter_ids = [Command.link(parameter.id)]
            values = {
                'quality_point_id': cls.point.id,
                'sequence': (index + 1) * 10,
                'control_type': 'instrumental' if index == 0 else 'functional',
                'equipment_category_ids': [Command.set(cls.category.ids)],
                'parameter_id': parameter.id,
            }
            if parameter_type == 'numeric':
                values.update(min_tolerance_input='0', max_tolerance_input='10')
            else:
                values['text_norm'] = 'Так' if parameter_type == 'boolean' else 'Працює'
            cls.env['quality.control.parameter.line'].create(values)

    def _production(self, quantity=5, uom=None):
        picking_type = self.warehouse.manu_type_id
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 20,
            'product_uom_id': (uom or self.product.uom_id).id,
            'picking_type_id': picking_type.id,
            'location_src_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        production.qty_producing = quantity
        return production

    def _check(self, production=None, point=None, **extra):
        return self.env['quality.check'].create({
            'point_id': (point or self.point).id,
            'product_id': self.product.id,
            'team_id': self.team.id,
            'test_type_id': self.test_type.id,
            'measure_on': 'product',
            'production_id': production.id if production else False,
            **extra,
        })

    def _fill(self, check, equipment=True):
        if equipment:
            check.equipment_selection_ids.write({
                'equipment_ids': [Command.set(self.equipment.ids)],
            })
        for sample in check.sample_ids:
            check.update_measurement_visual_result(sample.id, 'yes')
            for value in sample.measurement_value_ids:
                payload = {
                    'numeric': {'numeric_input': '5'},
                    'boolean': {'boolean_value': 'yes'},
                    'string': {'string_value': 'Працює', 'manual_result': 'pass'},
                }[value.parameter_type]
                check.update_measurement_value(value.id, payload)


@tagged('post_install', '-at_install')
class TestQualityCheckInProcess(QualityCheckInProcessCase):
    def test_default_incoming_keeps_manual_samples_and_final_has_no_auto_logic(self):
        incoming = self.env['quality.point'].create({
            'title': 'Вхідний за замовчуванням',
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
            'visual_sample_control_required': True,
        })
        self.assertEqual(incoming.x_control_stage, 'incoming')
        final = self.point.copy({
            'x_control_stage': 'final', 'visual_sample_control_required': True,
        })
        for point in (incoming, final):
            check = self._check(self._production(), point=point)
            self.assertFalse(check.sample_ids)
            self.assertFalse(check.in_process_sample_quantity)
            check.add_measurement_samples(3)
            check.remove_measurement_samples(1)
            self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2])
            self.assertEqual(check.get_measurement_matrix_data()['control_stage'], point.x_control_stage)

    def test_five_samples_have_complete_snapshot_cells(self):
        check = self._check(self._production())
        self.assertEqual(check.in_process_sample_quantity, 5)
        self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2, 3, 4, 5])
        self.assertEqual(len(check.measurement_column_ids), 3)
        for sample in check.sample_ids:
            self.assertEqual(len(sample.measurement_value_ids), 3)
            self.assertEqual(sample.measurement_value_ids.column_id, check.measurement_column_ids)
            self.assertEqual(sample.measurement_value_ids.quality_check_id, check)
        snapshot_names = check.measurement_column_ids.mapped('parameter_name')
        self.point.control_parameter_line_ids.parameter_id.write({'name': 'Оновлена назва'})
        check._initialize_measurement_snapshot()
        self.assertEqual(check.measurement_column_ids.mapped('parameter_name'), snapshot_names)

    def test_repeated_open_and_initialization_preserve_rows_and_values(self):
        check = self._check(self._production())
        self._fill(check)
        sample_ids = check.sample_ids.ids
        cell_ids = check.sample_ids.measurement_value_ids.ids
        for _iteration in range(2):
            check._initialize_measurement_snapshot()
            check.initialize_in_process_measurement_matrix()
            check.action_initialize_measurement_matrix()
            self.assertEqual(len(check.get_measurement_matrix_data()['samples']), 5)
        self.assertEqual(check.sample_ids.ids, sample_ids)
        self.assertEqual(check.sample_ids.measurement_value_ids.ids, cell_ids)
        self.assertTrue(check.can_pass_measurement_check)

    def test_manual_rpc_and_sample_model_mutations_are_blocked(self):
        check = self._check(self._production())
        for operation in (
            lambda: check.add_measurement_samples(1),
            lambda: check.remove_measurement_samples(1),
            check.action_add_measurement_samples,
            lambda: check.sample_ids[-1:].sudo().unlink(),
            lambda: self.env['quality.check.sample'].sudo().with_context(
                quality_vataga_sample_initialization=True,
                quality_vataga_in_process_initialization=True,
            ).create({'quality_check_id': check.id, 'sample_number': 6}),
        ):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(len(check.sample_ids), 5)

    def test_zero_quantity_defers_and_can_generate_after_quantity_appears(self):
        production = self._production(0)
        check = self._check(production)
        self.assertTrue(check.measurement_matrix_required)
        self.assertFalse(check.sample_ids)
        self.assertFalse(check.in_process_sample_quantity)
        self.assertTrue(check.initialize_in_process_measurement_matrix()['batch_error'])
        with self.assertRaises(ValidationError):
            check.do_pass()
        production.qty_producing = 5
        data = check.initialize_in_process_measurement_matrix()
        self.assertFalse(data['batch_error'])
        self.assertEqual(len(check.sample_ids), 5)

    def test_missing_source_can_be_bound_later(self):
        check = self._check()
        self.assertFalse(check.sample_ids)
        with self.assertRaises(ValidationError):
            check.do_pass()
        check.production_id = self._production()
        check._initialize_measurement_snapshot()
        self.assertEqual(len(check.sample_ids), 5)

    def test_fractional_and_negative_quantity_never_round_or_generate(self):
        for quantity in (2.5, -1):
            check = self._check(self._production(quantity))
            self.assertFalse(check.sample_ids)
            self.assertFalse(check.can_pass_measurement_check)
            self.assertTrue(check.initialize_in_process_measurement_matrix()['batch_error'])
            with self.assertRaises(ValidationError):
                check.do_pass()

    def test_uom_conversion_counts_individual_units(self):
        check = self._check(self._production(0.5, self.env.ref('uom.product_uom_dozen')))
        self.assertEqual(check.in_process_sample_quantity, 6)
        self.assertEqual(len(check.sample_ids), 6)

    def test_non_unit_product_is_not_interpreted_as_pieces(self):
        kilogram = self.env.ref('uom.product_uom_kgm')
        self.product.write({'uom_id': kilogram.id, 'uom_po_id': kilogram.id})
        check = self._check(self._production(5))
        self.assertFalse(check.sample_ids)
        self.assertTrue(check.get_measurement_matrix_data()['batch_error'])

    def test_wrong_product_is_not_a_reliable_batch(self):
        other_product = self.env['product.product'].create({'name': 'Інший виріб'})
        check = self._check(self._production(), product_id=other_product.id)
        self.assertFalse(check.sample_ids)
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_workorder_uses_its_production_and_rejects_conflicting_sources(self):
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('quality_mrp_workorder is not installed')
        production = self._production()
        workcenter = self.env['mrp.workcenter'].create({'name': 'Етап 12'})
        workorder = self.env['mrp.workorder'].create({
            'name': 'Операція етапу 12', 'workcenter_id': workcenter.id,
            'production_id': production.id, 'product_uom_id': self.product.uom_id.id,
        })
        check = self._check(production, workorder_id=workorder.id)
        self.assertEqual(len(check.sample_ids), 5)
        conflict = self._check(self._production(), workorder_id=workorder.id)
        self.assertFalse(conflict.sample_ids)
        with self.assertRaises(ValidationError):
            conflict.do_pass()

    def test_pass_blocks_pending_cells_and_direct_state_write(self):
        check = self._check(self._production())
        self.assertFalse(check.can_pass_measurement_check)
        with self.assertRaises(ValidationError):
            check.do_pass()
        with self.assertRaises(ValidationError):
            check.write({'quality_state': 'pass'})
        check.equipment_selection_ids.write({'equipment_ids': [Command.set(self.equipment.ids)]})
        check.sample_ids.sudo().write({'visual_result': 'yes'})
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_pass_requires_equipment_after_all_samples_pass(self):
        check = self._check(self._production())
        self._fill(check, equipment=False)
        self.assertEqual(set(check.sample_ids.mapped('sample_result')), {'pass'})
        self.assertFalse(check.can_pass_measurement_check)
        with self.assertRaises(ValidationError):
            check.do_pass()
        check.equipment_selection_ids.write({'equipment_ids': [Command.set(self.equipment.ids)]})
        self.assertTrue(check.can_pass_measurement_check)
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')
        self.assertIn('STAGE12-1', check.equipment_selection_ids.equipment_display_list_snapshot)

    def test_one_failed_sample_blocks_pass(self):
        check = self._check(self._production())
        self._fill(check)
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        with self.assertRaises(ValidationError):
            check.do_pass()
        self.assertFalse(check.can_pass_measurement_check)

    def test_changed_batch_preserves_results_and_blocks_pass_until_restored(self):
        production = self._production()
        check = self._check(production)
        self._fill(check)
        ids_before = check.sample_ids.measurement_value_ids.ids
        for quantity in (6, 4, 0, 5.5):
            production.qty_producing = quantity
            data = check.initialize_in_process_measurement_matrix()
            self.assertTrue(data['batch_error'])
            self.assertEqual(check.sample_ids.measurement_value_ids.ids, ids_before)
            self.assertEqual(check.in_process_sample_quantity, 5)
            self.assertFalse(check.can_pass_measurement_check)
            with self.assertRaises(ValidationError):
                check.do_pass()
        production.qty_producing = 5
        self.assertTrue(check.can_pass_measurement_check)
        check.do_pass()

    def test_generated_batch_source_and_snapshot_quantity_are_immutable(self):
        check = self._check(self._production())
        for values in (
            {'production_id': self._production().id},
            {'product_id': False}, {'in_process_sample_quantity': 6},
            {'x_control_stage': 'incoming'},
        ):
            with self.assertRaises(UserError):
                check.write(values)

    def test_unconfigured_in_process_point_cannot_pass_without_matrix(self):
        point = self.env['quality.point'].create({
            'title': 'Без параметрів', 'x_control_stage': 'in_process',
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
        })
        check = self._check(self._production(), point=point)
        self.assertFalse(check.measurement_matrix_required)
        self.assertFalse(check.can_pass_measurement_check)
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_visual_only_batch_generates_without_dynamic_cells(self):
        point = self.env['quality.point'].create({
            'title': 'Візуальна партія', 'x_control_stage': 'in_process',
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
            'visual_sample_control_required': True,
        })
        check = self._check(self._production(), point=point)
        self.assertEqual(len(check.sample_ids), 5)
        self.assertFalse(check.sample_ids.measurement_value_ids)
        check.sample_ids.sudo().write({'visual_result': 'yes'})
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')

    def test_completed_check_is_never_regenerated_and_stage_cannot_reclassify_history(self):
        production = self._production()
        check = self._check(production)
        self._fill(check)
        check.do_pass()
        sample_ids = check.sample_ids.ids
        column_ids = check.measurement_column_ids.ids
        production.qty_producing = 10
        check._initialize_measurement_snapshot()
        data = check.get_measurement_matrix_data()
        self.assertEqual(check.sample_ids.ids, sample_ids)
        self.assertEqual(check.measurement_column_ids.ids, column_ids)
        self.assertEqual(check.quality_state, 'pass')
        self.assertTrue(check.measurement_matrix_complete)
        self.assertFalse(data['batch_error'])
        with self.assertRaises(UserError):
            self.point.x_control_stage = 'incoming'

    def test_sample_count_corruption_blocks_pass_without_deleting_results(self):
        check = self._check(self._production())
        self._fill(check)
        self.env.flush_all()
        self.env.cr.execute('DELETE FROM quality_check_sample WHERE id = %s', [check.sample_ids[-1].id])
        self.env.invalidate_all()
        self.assertEqual(len(check.sample_ids), 4)
        with self.assertRaises(ValidationError):
            check.do_pass()
        check.initialize_in_process_measurement_matrix()
        self.assertEqual(len(check.sample_ids), 4)

    def test_missing_cell_keeps_existing_structure_validation(self):
        check = self._check(self._production())
        self._fill(check)
        self.env.flush_all()
        value = check.sample_ids[-1].measurement_value_ids[-1]
        self.env.cr.execute(
            'DELETE FROM quality_check_measurement_value WHERE id = %s',
            [value.id],
        )
        self.env.invalidate_all()
        self.assertEqual(len(check.sample_ids), 5)
        with self.assertRaisesRegex(ValidationError, 'неповний набір'):
            check.do_pass()
