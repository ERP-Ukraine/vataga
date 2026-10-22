from lxml import etree

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase


QUANTITY_FIELDS = ['x_passed_qty', 'x_failed_qty', 'x_pending_qty']
RESULT_FIELDS = QUANTITY_FIELDS + ['in_process_result_quantities_valid']


@tagged('post_install', '-at_install')
class TestQualityCheckResultQuantities(QualityCheckInProcessCase):
    def _filled_check(self, quantity=5, equipment=True):
        check = self._check(self._production(quantity))
        self._fill(check, equipment=equipment)
        return check

    def _assert_quantities(self, check, expected, valid=True):
        self.assertEqual(tuple(check[name] for name in QUANTITY_FIELDS), expected)
        self.assertEqual(check.in_process_result_quantities_valid, valid)
        if valid:
            self.assertEqual(sum(expected), check.in_process_sample_quantity)

    def _numeric(self, check, sample, value):
        cell = sample.measurement_value_ids.filtered(
            lambda item: item.parameter_type == 'numeric',
        )
        return check.update_measurement_value(cell.id, {'numeric_input': value})

    def test_five_pass_samples(self):
        check = self._filled_check()
        self._assert_quantities(check, (5, 0, 0))
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')

    def test_four_pass_and_one_fail_still_block_overall_pass(self):
        check = self._filled_check()
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        self._assert_quantities(check, (4, 1, 0))
        with self.assertRaises(ValidationError):
            check.do_pass()
        self.assertEqual(check.quality_state, 'none')

    def test_three_pass_one_fail_one_pending(self):
        check = self._filled_check()
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        self._numeric(check, check.sample_ids[-2], '')
        self._assert_quantities(check, (3, 1, 1))

    def test_four_pass_one_pending(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids[-1], '')
        self._assert_quantities(check, (4, 0, 1))
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_fail_to_pass_preserves_rework_audit(self):
        check = self._filled_check()
        sample = check.sample_ids[-1]
        self._numeric(check, sample, '11')
        self._assert_quantities(check, (4, 1, 0))
        self._numeric(check, sample, '5')
        self._assert_quantities(check, (5, 0, 0))
        self.assertTrue(sample.x_was_reworked)
        messages = check.message_ids.filtered(
            lambda message: 'змінено з FAIL на PASS після доопрацювання.' in (message.body or ''),
        )
        self.assertEqual(len(messages), 1)
        self._numeric(check, sample, '5')
        self._assert_quantities(check, (5, 0, 0))
        self.assertEqual(check.message_ids.filtered(
            lambda message: 'змінено з FAIL на PASS після доопрацювання.' in (message.body or ''),
        ), messages)

    def test_pass_to_fail(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids[-1], '11')
        self._assert_quantities(check, (4, 1, 0))

    def test_numeric_functional_and_visual_inputs_recompute_counts(self):
        for kind in ('numeric', 'boolean', 'string', 'visual'):
            with self.subTest(kind=kind):
                check = self._filled_check(1)
                sample = check.sample_ids
                if kind == 'visual':
                    check.update_measurement_visual_result(sample.id, 'no')
                    self._assert_quantities(check, (0, 1, 0))
                    check.update_measurement_visual_result(sample.id, 'yes')
                else:
                    cell = sample.measurement_value_ids.filtered(
                        lambda item: item.parameter_type == kind,
                    )
                    bad, good = {
                        'numeric': ({'numeric_input': '11'}, {'numeric_input': '5'}),
                        'boolean': ({'boolean_value': 'no'}, {'boolean_value': 'yes'}),
                        'string': (
                            {'string_value': 'Не працює', 'manual_result': 'fail'},
                            {'string_value': 'Працює', 'manual_result': 'pass'},
                        ),
                    }[kind]
                    check.update_measurement_value(cell.id, bad)
                    self._assert_quantities(check, (0, 1, 0))
                    check.update_measurement_value(cell.id, good)
                self._assert_quantities(check, (1, 0, 0))

    def test_clearing_and_refilling_inputs_updates_pending(self):
        check = self._filled_check(1)
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        self._numeric(check, sample, '')
        self._assert_quantities(check, (0, 0, 1))
        self._numeric(check, sample, '5')
        self._assert_quantities(check, (1, 0, 0))
        check.update_measurement_visual_result(sample.id, False)
        self._assert_quantities(check, (0, 0, 1))
        check.update_measurement_visual_result(sample.id, 'yes')
        self._assert_quantities(check, (1, 0, 0))

    def test_direct_measurement_write_uses_orm_dependencies(self):
        check = self._filled_check(1)
        cell = check.sample_ids.measurement_value_ids.filtered(
            lambda item: item.parameter_type == 'numeric',
        )
        cell.sudo().write({'numeric_value': 11})
        self._assert_quantities(check, (0, 1, 0))
        cell.sudo().write({'numeric_value': 5})
        self._assert_quantities(check, (1, 0, 0))

    def test_unformed_matrix_is_not_a_valid_tally(self):
        point = self.env['quality.point'].create({
            'title': 'Без матриці', 'x_control_stage': 'in_process',
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
        })
        check = self._check(self._production(), point=point)
        self.assertFalse(check.measurement_matrix_required)
        self._assert_quantities(check, (0, 0, 0), valid=False)
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_deferred_matrix_counts_samples_when_quantity_appears(self):
        production = self._production(0)
        check = self._check(production)
        self._assert_quantities(check, (0, 0, 0), valid=False)
        production.qty_producing = 5
        check.initialize_in_process_measurement_matrix()
        self._assert_quantities(check, (0, 0, 5))
        self.assertFalse(check.can_pass_measurement_check)

    def test_missing_sample_invalidates_tally_without_resizing_batch(self):
        check = self._filled_check()
        self.env.flush_all()
        # Simulate damaged historical data in this rolled-back test transaction.
        self.env.cr.execute(
            'DELETE FROM quality_check_sample WHERE id = %s', [check.sample_ids[-1].id],
        )
        check.invalidate_recordset(['sample_ids'])
        check.modified(['sample_ids'])
        self._assert_quantities(check, (4, 0, 0), valid=False)
        self.assertEqual(check.in_process_sample_quantity, 5)
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_all_pass_without_equipment_is_not_overall_pass(self):
        check = self._filled_check(equipment=False)
        self._assert_quantities(check, (5, 0, 0))
        self.assertFalse(check.equipment_selection_complete)
        self.assertFalse(check.can_pass_measurement_check)
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_stored_counts_survive_reload(self):
        check = self._filled_check()
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        self._numeric(check, check.sample_ids[-2], '')
        self._assert_quantities(check, (3, 1, 1))
        check.flush_recordset(RESULT_FIELDS)
        check.invalidate_recordset(RESULT_FIELDS)
        self._assert_quantities(check, (3, 1, 1))

    def test_completed_pass_and_fail_keep_historical_counts(self):
        for result in ('pass', 'fail'):
            with self.subTest(result=result):
                check = self._filled_check()
                expected = (5, 0, 0)
                if result == 'fail':
                    check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
                    expected = (4, 1, 0)
                getattr(check, 'do_' + result)()
                check.production_id.qty_producing = 8
                check._initialize_measurement_snapshot()
                check.flush_recordset(RESULT_FIELDS)
                check.invalidate_recordset(RESULT_FIELDS)
                self._assert_quantities(check, expected)
                self.assertEqual(check.in_process_sample_quantity, 5)
                with self.assertRaises(UserError):
                    self._numeric(check, check.sample_ids[-1], '7')

    def test_changed_current_quantity_does_not_reclassify_frozen_results(self):
        check = self._filled_check()
        check.production_id.qty_producing = 4
        self._assert_quantities(check, (5, 0, 0))
        self.assertTrue(check.get_measurement_matrix_data()['batch_error'])
        self.assertFalse(check.can_pass_measurement_check)
        with self.assertRaises(ValidationError):
            check.do_pass()

    def test_computed_fields_reject_explicit_create_and_write(self):
        check = self._filled_check()
        inspector = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Інспектор кількісних результатів',
            'login': 'quality_result_quantities_inspector',
            'groups_id': [Command.set(self.env.ref('quality.group_quality_user').ids)],
            'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
        })
        for name in RESULT_FIELDS:
            for value in (0, 999):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(UserError):
                        check.write({name: value})
                    with self.assertRaises(UserError):
                        check.with_user(inspector).write({name: value})
                    with self.assertRaises(UserError):
                        check.sudo().with_context(quality_vataga_result_counts=True).write({name: value})
                    with self.assertRaises(UserError):
                        self._check(check.production_id, **{name: value})
        self._assert_quantities(check, (5, 0, 0))

    def test_context_defaults_cannot_forge_stored_counts(self):
        context = {'default_' + name: 999 for name in RESULT_FIELDS}
        check = self._filled_check().with_context(context)
        copied = check.copy({
            'point_id': self.point.id, 'production_id': check.production_id.id,
            'quality_state': 'none',
        })
        self._assert_quantities(copied, (0, 0, 5))
        created = self.env['quality.check'].with_context(context).create({
            'point_id': self.point.id, 'product_id': self.product.id,
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
            'measure_on': 'product', 'production_id': check.production_id.id,
        })
        self._assert_quantities(created, (0, 0, 5))
        created.flush_recordset(RESULT_FIELDS)
        created.invalidate_recordset(RESULT_FIELDS)
        self._assert_quantities(created, (0, 0, 5))

    def test_checks_of_same_production_have_independent_counts(self):
        production = self._production()
        first, second = self._check(production), self._check(production)
        self._fill(first)
        self._assert_quantities(first, (5, 0, 0))
        self._assert_quantities(second, (0, 0, 5))
        self._fill(second)
        second.update_measurement_visual_result(second.sample_ids[-1].id, 'no')
        self._assert_quantities(second, (4, 1, 0))
        self._numeric(first, first.sample_ids[-1], '')
        self._assert_quantities(first, (4, 0, 1))
        self._assert_quantities(second, (4, 1, 0))

    def test_incoming_and_final_keep_manual_samples_and_existing_pass(self):
        for stage in ('incoming', 'final'):
            with self.subTest(stage=stage):
                point = self.point.copy({
                    'x_control_stage': stage, 'visual_sample_control_required': True,
                })
                check = self._check(self._production(), point=point)
                check.add_measurement_samples(3)
                self._fill(check)
                self._assert_quantities(check, (0, 0, 0), valid=False)
                check.do_pass()
                self.assertEqual(check.quality_state, 'pass')

    def test_fields_are_stored_readonly_computed_and_not_copied(self):
        for name in RESULT_FIELDS:
            field = self.env['quality.check']._fields[name]
            self.assertEqual(field.type, 'integer' if name in QUANTITY_FIELDS else 'boolean')
            self.assertEqual(field.compute, '_compute_in_process_result_quantities')
            self.assertTrue(field.store)
            self.assertTrue(field.readonly)
            self.assertFalse(field.copy)
            self.assertFalse(field.compute_sudo)

    def test_results_block_is_readonly_in_process_only_and_before_matrix(self):
        view = self.env['quality.check'].get_view(
            view_id=self.env.ref('quality_control.quality_check_view_form').id,
            view_type='form',
        )
        arch = etree.fromstring(view['arch'])
        blocks = arch.xpath("//group[@name='quality_vataga_control_results']")
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.get('string'), 'Результати контролю')
        self.assertEqual(block.get('invisible'), "x_control_stage != 'in_process'")
        self.assertTrue(block.xpath('following-sibling::notebook'))
        for name in QUANTITY_FIELDS + ['in_process_sample_quantity']:
            fields = arch.xpath("//field[@name='%s']" % name)
            self.assertEqual(len(fields), 1)
            self.assertEqual(fields[0].get('readonly'), '1')
            self.assertTrue(block.xpath(".//field[@name='%s']" % name))
        validity = block.xpath(".//field[@name='in_process_result_quantities_valid']")
        self.assertEqual(len(validity), 1)
        self.assertEqual(validity[0].get('invisible'), '1')
