import base64

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase


@tagged('post_install', '-at_install')
class TestQualityStage14(QualityCheckInProcessCase):
    def _filled_check(self, quantity=1):
        check = self._check(self._production(quantity))
        self._fill(check)
        return check

    def _cell(self, sample, parameter_type):
        return sample.measurement_value_ids.filtered(
            lambda value: value.parameter_type == parameter_type,
        )

    def _numeric(self, check, sample, value):
        return check.update_measurement_value(
            self._cell(sample, 'numeric').id, {'numeric_input': value},
        )

    def _messages(self, check):
        return check.message_ids.filtered(
            lambda message: 'змінено з FAIL на PASS після доопрацювання.' in (message.body or ''),
        )

    def _alert(self, check, **values):
        return self.env['quality.alert'].create({
            'check_id': check.id, 'team_id': self.team.id,
            'description': '<p>Нотатка інспектора</p>', **values,
        })

    def _inspector(self, suffix, approval=False):
        group = self.env.ref('quality_vataga.group_quality_board_approval' if approval else 'quality.group_quality_user')
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Stage 14 ' + suffix, 'login': 'quality_stage14_' + suffix,
            'groups_id': [Command.set(group.ids)],
            'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
        })

    def test_all_input_types_record_direct_fail_to_pass_with_exact_chatter(self):
        for kind in ('visual', 'numeric', 'boolean', 'string'):
            with self.subTest(kind=kind):
                check = self._filled_check()
                sample = check.sample_ids
                if kind == 'visual':
                    fail = lambda: check.update_measurement_visual_result(sample.id, 'no')
                    fix = lambda: check.update_measurement_visual_result(sample.id, 'yes')
                elif kind == 'numeric':
                    fail = lambda: self._numeric(check, sample, '11')
                    fix = lambda: self._numeric(check, sample, '5')
                else:
                    value = self._cell(sample, kind)
                    bad = {'boolean_value': 'no'} if kind == 'boolean' else {'string_value': 'Не працює', 'manual_result': 'fail'}
                    good = {'boolean_value': 'yes'} if kind == 'boolean' else {'string_value': 'Працює', 'manual_result': 'pass'}
                    fail = lambda: check.update_measurement_value(value.id, bad)
                    fix = lambda: check.update_measurement_value(value.id, good)
                fail()
                self.assertEqual(sample.sample_result, 'fail')
                self.assertFalse(sample.x_was_reworked)
                data = fix()
                self.assertEqual(sample.sample_result, 'pass')
                self.assertTrue(sample.x_was_reworked)
                self.assertTrue(data['samples'][0]['x_was_reworked'])
                messages = self._messages(check)
                self.assertEqual(len(messages), 1)
                self.assertIn('Статус зразка №1 змінено з FAIL на PASS після доопрацювання.', messages.body)
                fix()
                self.assertEqual(self._messages(check), messages)

    def test_later_fail_preserves_flag_and_new_real_rework_gets_own_message(self):
        check = self._filled_check()
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        self._numeric(check, sample, '5')
        self._numeric(check, sample, '12')
        self.assertEqual(sample.sample_result, 'fail')
        self.assertTrue(sample.x_was_reworked)
        self.assertEqual(len(self._messages(check)), 1)
        self._numeric(check, sample, '5')
        self.assertTrue(sample.x_was_reworked)
        self.assertEqual(len(self._messages(check)), 2)

    def test_fail_to_pending_to_pass_is_not_a_direct_rework_transition(self):
        check = self._filled_check()
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        self._numeric(check, sample, '')
        self.assertEqual(sample.sample_result, 'pending')
        self._numeric(check, sample, '5')
        self.assertEqual(sample.sample_result, 'pass')
        self.assertFalse(sample.x_was_reworked)
        self.assertFalse(self._messages(check))

    def test_fixing_one_of_two_failures_waits_for_entire_sample_to_pass(self):
        check = self._filled_check()
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        check.update_measurement_visual_result(sample.id, 'no')
        self._numeric(check, sample, '5')
        self.assertEqual(sample.sample_result, 'fail')
        self.assertFalse(sample.x_was_reworked)
        check.update_measurement_visual_result(sample.id, 'yes')
        self.assertTrue(sample.x_was_reworked)
        self.assertEqual(len(self._messages(check)), 1)

    def test_model_level_measurement_write_uses_same_computed_transition(self):
        check = self._filled_check()
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        self._cell(sample, 'numeric').sudo().write({'numeric_value': 5})
        self.assertTrue(sample.x_was_reworked)
        self.assertEqual(len(self._messages(check)), 1)

    def test_numeric_payload_cannot_force_pass_outside_tolerances(self):
        check = self._filled_check()
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        check.update_measurement_value(self._cell(sample, 'numeric').id, {
            'numeric_input': '12', 'manual_result': 'pass', 'x_was_reworked': True,
        })
        self.assertEqual(sample.sample_result, 'fail')
        self.assertFalse(sample.x_was_reworked)
        self.assertFalse(self._messages(check))

    def test_completed_check_rejects_rpc_and_model_writes(self):
        check = self._filled_check()
        sample = check.sample_ids
        self._numeric(check, sample, '11')
        check.do_fail()
        value = self._cell(sample, 'numeric')
        for operation in (
            lambda: self._numeric(check, sample, '5'),
            lambda: check.update_measurement_visual_result(sample.id, 'yes'),
            lambda: value.sudo().write({'numeric_value': 5}),
            lambda: sample.sudo().write({'visual_result': 'yes'}),
        ):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(value.numeric_value, 11)
        self.assertFalse(sample.x_was_reworked)
        self.assertFalse(self._messages(check))

    def test_client_cannot_forge_or_reset_rework_flag(self):
        check = self._filled_check()
        sample = check.sample_ids
        for values in ({'x_was_reworked': True}, {'sample_result': 'pass'}):
            with self.assertRaises(UserError):
                sample.sudo().with_context(quality_vataga_reworked=True).write(values)
        with self.assertRaises(UserError):
            self.env['quality.check.sample'].sudo().with_context(
                quality_vataga_sample_initialization=True,
            ).create({'quality_check_id': check.id, 'sample_number': 2, 'x_was_reworked': True})
        self._numeric(check, sample, '11')
        self._numeric(check, sample, '5')
        with self.assertRaises(UserError):
            sample.sudo().write({'x_was_reworked': False})
        self.assertTrue(sample.x_was_reworked)

    def test_context_default_cannot_mark_new_samples_as_reworked(self):
        check = self.env['quality.check'].with_context(default_x_was_reworked=True).create({
            'point_id': self.point.id, 'product_id': self.product.id,
            'production_id': self._production(1).id,
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
            'measure_on': 'product',
        })
        self.assertFalse(check.sample_ids.x_was_reworked)

    def test_inspector_is_chatter_author_and_parent_rules_cannot_be_bypassed(self):
        check = self._filled_check()
        sample = check.sample_ids
        user = self._inspector('writer')
        self._numeric(check, sample, '11')
        self._numeric(check.with_user(user), sample, '5')
        self.assertEqual(self._messages(check).author_id, user.partner_id)
        self._numeric(check, sample, '11')
        self.env['ir.rule'].create({
            'name': 'Stage14 deny parent writes',
            'model_id': self.env['ir.model']._get_id('quality.check'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': False,
            'perm_create': False, 'perm_write': True, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError):
            self._numeric(check.with_user(user), sample, '5')
        with self.assertRaises(AccessError):
            self._cell(sample, 'numeric').with_user(user).sudo().write({'numeric_value': 5})
        self.assertEqual(sample.sample_result, 'fail')
        self.assertEqual(len(self._messages(check)), 1)

    def test_open_check_alert_contains_only_current_failed_parameters_and_visual(self):
        check = self._filled_check(3)
        first, repaired, visual = check.sample_ids
        self._numeric(check, first, '11')
        check.update_measurement_value(self._cell(first, 'boolean').id, {'boolean_value': 'no'})
        self._numeric(check, repaired, '12')
        self._numeric(check, repaired, '5')
        check.update_measurement_visual_result(visual.id, 'no')
        self.assertEqual(check.quality_state, 'none')
        details = check._get_current_matrix_failure_data()
        self.assertEqual(len(details), 3)
        self.assertEqual({row['sample_number'] for row in details}, {1, 3})
        numeric = next(row for row in details if row['actual_value'] == '11')
        self.assertEqual(numeric['expected_value'], '0 – 10')
        self.assertTrue(numeric['failure_reason'])
        alert = self._alert(check)
        snapshot = alert.vataga_matrix_failure_snapshot
        self.assertIn('Зразок №1', snapshot)
        self.assertIn('Зразок №3', snapshot)
        self.assertNotIn('Зразок №2', snapshot)
        self.assertNotIn(self._cell(first, 'string').column_id.parameter_name, snapshot)
        self.assertIn('норма: 0 – 10', snapshot)
        self.assertIn('візуальний контроль', snapshot)
        self.assertIn('Нотатка інспектора', alert.description)
        self.assertEqual(alert.description.count('Виявлені невідповідності'), 1)
        self.assertEqual(alert.stage_id, self.env.ref('quality_vataga.quality_alert_stage_new'))
        self.assertFalse(alert.is_approved)
        original = alert.description
        for _ in range(2):
            alert.read(['description', 'vataga_matrix_failure_snapshot'])
            check.get_measurement_matrix_data()
        self.assertEqual(alert.description, original)
        self.assertEqual(alert.vataga_matrix_failure_snapshot, snapshot)

    def test_alert_uses_snapshot_norm_after_qcp_changed(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        self.point.control_parameter_line_ids.filtered(
            lambda line: line.parameter_type == 'numeric',
        ).write({'max_tolerance_input': '100'})
        alert = self._alert(check)
        self.assertIn('0 – 10', alert.vataga_matrix_failure_snapshot)
        self.assertNotIn('100', alert.vataga_matrix_failure_snapshot)

    def test_repaired_samples_are_absent_and_existing_alert_stays_historical(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        old_alert = self._alert(check)
        old_snapshot = old_alert.vataga_matrix_failure_snapshot
        self._numeric(check, check.sample_ids, '5')
        new_alert = self._alert(check)
        self.assertFalse(new_alert.vataga_matrix_failure_snapshot)
        self.assertNotIn('Виявлені невідповідності', new_alert.description)
        self.assertIn('Нотатка інспектора', new_alert.description)
        self.assertEqual(old_alert.vataga_matrix_failure_snapshot, old_snapshot)

    def test_alert_defaults_and_create_only_acl_preserve_inspector_description(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        user = self._inspector('create_only')
        self.env['ir.rule'].create({
            'name': 'Stage14 deny alert writes',
            'model_id': self.env['ir.model']._get_id('quality.alert'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': False,
            'perm_create': False, 'perm_write': True, 'perm_unlink': False,
        })
        alert = self.env['quality.alert'].with_user(user).with_context(
            default_check_id=check.id, default_description='<p>Контекст інспектора</p>',
            default_vataga_matrix_failure_snapshot='FORGED',
        ).create({'team_id': self.team.id})
        self.assertIn('Контекст інспектора', alert.description)
        self.assertIn('11', alert.vataga_matrix_failure_snapshot)
        self.assertNotIn('FORGED', alert.vataga_matrix_failure_snapshot)

    def test_snapshot_is_server_owned_and_description_is_editable(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        with self.assertRaises(UserError):
            self._alert(check, vataga_matrix_failure_snapshot='FORGED')
        alert = self._alert(check)
        snapshot = alert.vataga_matrix_failure_snapshot
        with self.assertRaises(UserError):
            alert.write({'vataga_matrix_failure_snapshot': False})
        alert.write({'description': '<p>Уточнення інспектора</p>'})
        self.assertIn('Уточнення інспектора', alert.description)
        self.assertEqual(alert.vataga_matrix_failure_snapshot, snapshot)

    def test_inspector_can_attach_photo_without_changing_failure_snapshot(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        user = self._inspector('photo')
        alert = self._alert(check).with_user(user)
        snapshot = alert.vataga_matrix_failure_snapshot
        alert.write({'description': '<p>Additional defect description</p>'})
        # Minimal real PNG attachment, stored only in the disposable ORM test.
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==')
        message = alert.message_post(body='Defect photo', attachments=[('defect.png', png)])
        self.assertEqual(len(message.attachment_ids), 1)
        self.assertEqual(message.attachment_ids.res_model, 'quality.alert')
        self.assertEqual(message.attachment_ids.res_id, alert.id)
        self.assertEqual(alert.vataga_matrix_failure_snapshot, snapshot)
        self.assertIn('Additional defect description', alert.description)

    def test_failure_html_is_escaped_and_plain_snapshot_retains_original_text(self):
        check = self._filled_check()
        value = self._cell(check.sample_ids, 'string')
        check.update_measurement_value(value.id, {
            'string_value': '<script>alert(1)</script>', 'manual_result': 'fail',
        })
        alert = self._alert(check)
        self.assertIn('<script>alert(1)</script>', alert.vataga_matrix_failure_snapshot)
        self.assertNotIn('<script>', alert.description)
        self.assertIn('&lt;script&gt;', alert.description)

    def test_incoming_keeps_manual_matrix_and_existing_alert_condition(self):
        point = self.point.copy({'x_control_stage': 'incoming', 'visual_sample_control_required': True})
        check = self._check(point=point)
        check.add_measurement_samples(1)
        sample = check.sample_ids
        check.update_measurement_visual_result(sample.id, 'no')
        check.update_measurement_visual_result(sample.id, 'yes')
        self.assertFalse(sample.x_was_reworked)
        self.assertFalse(self._messages(check))
        check.update_measurement_visual_result(sample.id, 'no')
        open_alert = self._alert(check)
        self.assertFalse(open_alert.vataga_matrix_failure_snapshot)
        self.assertNotIn('Виявлені невідповідності', open_alert.description)
        check.do_fail()
        failed_alert = self._alert(check)
        self.assertIn('візуальний контроль', failed_alert.description)
        self.assertFalse(failed_alert.vataga_matrix_failure_snapshot)

    def test_new_alert_snapshot_does_not_change_board_approval_guards(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        approver = self._inspector('approver', approval=True)
        alert = self._alert(check, decision_type='full_control', approval_user_id=approver.id)
        snapshot = alert.vataga_matrix_failure_snapshot
        with self.assertRaises(UserError):
            alert.write({'stage_id': self.env.ref('quality_vataga.quality_alert_stage_board_execution').id})
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.with_user(approver).action_approve_technical_board()
        self.assertTrue(alert.is_approved)
        self.assertEqual(alert.approved_by_id, approver)
        self.assertEqual(alert.vataga_matrix_failure_snapshot, snapshot)
        with self.assertRaises(UserError):
            alert.write({'decision_type': 'accept_as_is'})
