from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from ..models.quality_unit_admission import ADMISSION_CONTEXT
from .test_quality_check_in_process import QualityCheckInProcessCase


@tagged('post_install', '-at_install')
class TestQualityUnitAdmission(QualityCheckInProcessCase):
    def _workorder(self, production, name='Пайка'):
        center = self.env['mrp.workcenter'].create({'name': 'Допуск — ' + name})
        return self.env['mrp.workorder'].create({
            'name': name, 'workcenter_id': center.id, 'production_id': production.id,
            'product_uom_id': self.product.uom_id.id,
        })

    def _identify(self, check, workorder=None, mode='new', run=None, units=None):
        wizard = self.env['quality.check.identification.wizard'].create({
            'check_id': check.id, 'mode': mode, 'confirm_mapping': True,
            'workorder_id': workorder.id if workorder else False,
            'run_id': run.id if run else False,
            'line_ids': [Command.create({
                'sample_id': sample.id, 'controlled_unit_id': units[index].id if units else False,
            }) for index, sample in enumerate(check.sample_ids.sorted('sample_number'))],
        })
        wizard.action_apply()
        return check.operation_run_id

    def _ready(self, quantity=5, fail_last=True):
        production = self._production(quantity)
        workorder = self._workorder(production)
        extra = {'workorder_id': workorder.id} if 'workorder_id' in self.env['quality.check']._fields else {}
        check = self._check(production, **extra)
        self._identify(check, workorder)
        self._fill(check)
        if fail_last:
            check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        return check

    def _decision(self, check, samples=None, mode='confirm', reason=''):
        selected = set((samples if samples is not None else check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')).ids)
        return self.env['quality.unit.admission.wizard'].create({
            'check_id': check.id, 'mode': mode, 'reason': reason,
            'line_ids': [Command.create({'sample_id': sample.id, 'selected': sample.id in selected}) for sample in check.sample_ids],
        })

    def _admissions(self, check, state='confirmed'):
        domain = [('operation_run_id', '=', check.operation_run_id.id)]
        if state:
            domain.append(('state', '=', state))
        return self.env['quality.unit.admission'].search(domain)

    def _user(self, manager=False, quality=True):
        groups = [self.env.ref('base.group_user').id, self.env.ref('mrp.group_mrp_user').id]
        if quality:
            groups.append(self.env.ref('quality.group_quality_manager' if manager else 'quality.group_quality_user').id)
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Інспектор допуску', 'login': 'unit_admission_test_user',
            'groups_id': [Command.set(groups)], 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
        })

    def test_four_pass_units_of_five_can_be_admitted_while_qc_is_open(self):
        check = self._ready()
        self.assertEqual(check.quality_state, 'none')
        self.assertEqual((check.x_passed_qty, check.x_failed_qty, check.x_pending_qty), (4, 1, 0))
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (0, 5))
        self._decision(check).action_apply()
        records = self._admissions(check)
        self.assertEqual(len(records), 4)
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (4, 1))
        self.assertEqual(check.quality_state, 'none')
        self.assertEqual(records.controlled_unit_id, check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass').controlled_unit_id)
        self.assertEqual(records.inspector_id, self.env.user)
        self.assertTrue(all(records.mapped('confirmed_at')))
        self.assertTrue(all('PASS' in text for text in records.mapped('evidence_snapshot')))
        self.assertEqual(records.workorder_id, check.operation_run_id.workorder_id)

    def test_fail_cannot_be_admitted_even_if_checkbox_is_forged(self):
        check = self._ready()
        wizard = self._decision(check, check.sample_ids[-1:])
        self.assertFalse(wizard.line_ids.filtered(lambda line: line.selected).eligible)
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        self.assertFalse(self._admissions(check))

    def test_pending_cannot_be_admitted(self):
        check = self._ready(1, fail_last=False)
        check.update_measurement_visual_result(check.sample_ids.id, False)
        self.assertEqual(check.sample_ids.sample_result, 'pending')
        with self.assertRaises(ValidationError):
            self._decision(check, check.sample_ids).action_apply()

    def test_invalid_batch_or_missing_cells_blocks_admission(self):
        check = self._ready()
        check.production_id.qty_producing = 6
        with self.assertRaises(ValidationError):
            self._decision(check).action_apply()
        check.production_id.qty_producing = 5
        cell = check.sample_ids[0].measurement_value_ids[0]
        self.env.cr.execute('DELETE FROM quality_check_measurement_value WHERE id = %s', [cell.id])
        check.sample_ids[0].invalidate_recordset(['measurement_value_ids'])
        with self.assertRaises(ValidationError):
            self._decision(check).action_apply()
        self.assertFalse(self._admissions(check))

    def test_required_equipment_must_be_selected(self):
        check = self._ready()
        check.equipment_selection_ids.write({'equipment_ids': [Command.clear()]})
        with self.assertRaises(ValidationError):
            self._decision(check).action_apply()

    def test_two_wizards_and_repeated_requests_do_not_duplicate_admissions(self):
        check = self._ready()
        first, second = self._decision(check), self._decision(check)
        first.action_apply()
        inspector, dates = self._admissions(check).inspector_id, self._admissions(check).mapped('confirmed_at')
        messages = check.message_ids.ids
        first.action_apply()
        second.action_apply()
        self.assertEqual(len(self._admissions(check)), 4)
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (4, 1))
        self.assertEqual(check.message_ids.ids, messages)
        self.assertEqual(self._admissions(check).inspector_id, inspector)
        self.assertEqual(self._admissions(check).mapped('confirmed_at'), dates)

    def test_direct_orm_and_forged_context_cannot_create_edit_or_delete(self):
        check = self._ready()
        self._decision(check).action_apply()
        record = self._admissions(check)[0]
        for action in (
            lambda: self.env['quality.unit.admission'].create({'check_id': check.id}),
            lambda: self.env['quality.unit.admission'].with_context(**{ADMISSION_CONTEXT: True}).create({}),
            lambda: record.with_context(**{ADMISSION_CONTEXT: True}).write({'state': 'revoked'}),
            lambda: record.sudo().write({'inspector_id': self.env.uid}),
            lambda: record.sudo().unlink(),
            lambda: check.write({'x_admitted_qty': 99}),
            lambda: check.write({'x_held_qty': 0}),
            lambda: check.write({'unit_admission_ids': [Command.clear()]}),
        ):
            with self.assertRaises(UserError):
                action()

    def test_inspector_requires_quality_role_and_parent_write_access(self):
        check = self._ready()
        user = self._user(quality=False)
        with self.assertRaises(AccessError):
            check.with_user(user).action_open_unit_admission_wizard()
        user.groups_id = [Command.link(self.env.ref('quality.group_quality_user').id)]
        self.env['ir.rule'].create({
            'name': 'Deny admission parent QC', 'model_id': self.env['ir.model']._get_id('quality.check'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': True, 'perm_write': True,
            'perm_create': False, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError):
            check.with_user(user).action_open_unit_admission_wizard()

    def test_authorized_inspector_is_recorded(self):
        check = self._ready()
        inspector = self._user()
        wizard = self._decision(check)
        values = {'check_id': check.id, 'mode': 'confirm', 'line_ids': [Command.create({
            'sample_id': line.sample_id.id, 'selected': line.selected,
        }) for line in wizard.line_ids]}
        self.env['quality.unit.admission.wizard'].with_user(inspector).create(values).action_apply()
        self.assertEqual(self._admissions(check).inspector_id, inspector)

    def test_inspector_cannot_exempt_required_checks_or_forge_defaults(self):
        check = self._ready()
        inspector = self._user()
        with self.assertRaises(AccessError):
            check.with_user(inspector).write({'unit_admission_role': 'optional'})
        created = self.env['quality.check'].with_context(
            default_unit_admission_role='optional', default_x_admitted_qty=99, default_x_held_qty=0,
        ).create({
            'point_id': self.point.id, 'product_id': self.product.id,
            'production_id': check.production_id.id, 'team_id': self.team.id,
            'test_type_id': self.test_type.id, 'measure_on': 'product',
        })
        self.assertEqual(created.unit_admission_role, 'required')
        self.assertEqual((created.x_admitted_qty, created.x_held_qty), (0, 5))

    def test_wizard_source_and_computed_eligibility_cannot_be_forged(self):
        check = self._ready()
        wizard = self._decision(check)
        line = wizard.line_ids[0]
        for record, values in (
            (wizard, {'check_id': False}), (wizard, {'request_key': 'forged'}),
            (wizard, {'mode': 'revoke'}), (line, {'sample_id': check.sample_ids[-1].id}),
            (line, {'eligible': True}), (line, {'expected_admission_id': False}),
        ):
            with self.assertRaises(UserError):
                record.write(values)

    def test_foreign_mo_sample_cannot_be_injected(self):
        check, foreign = self._ready(), self._ready()
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['quality.unit.admission.wizard'].create({
                'check_id': check.id, 'mode': 'confirm',
                'line_ids': [Command.create({'sample_id': foreign.sample_ids[0].id, 'selected': True})],
            })

    def test_wrong_workorder_or_absent_workorder_is_rejected(self):
        check = self._check(self._production(1))
        self._identify(check)
        self._fill(check)
        with self.assertRaises(ValidationError):
            self._decision(check).action_apply()
        check = self._ready(1, fail_last=False)
        foreign = self._production(1)
        # Emulate an external corrupt WO relation; the admission service must
        # reject it even though the readonly UI cannot make this change.
        self.env.cr.execute('UPDATE mrp_workorder SET production_id = %s WHERE id = %s', [foreign.id, check.operation_run_id.workorder_id.id])
        check.operation_run_id.workorder_id.invalidate_recordset(['production_id'])
        with self.assertRaises(ValidationError):
            self._decision(check).action_apply()

    def test_admitted_visual_and_numeric_results_cannot_be_changed(self):
        check = self._ready()
        self._decision(check).action_apply()
        sample = check.sample_ids[0]
        numeric = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'numeric')
        for action in (
            lambda: check.update_measurement_visual_result(sample.id, 'no'),
            lambda: check.update_measurement_value(numeric.id, {'numeric_input': '11'}),
            lambda: sample.sudo().write({'visual_result': 'no'}),
            lambda: numeric.sudo().write({'numeric_value': 11, 'has_numeric_value': True}),
            lambda: check.equipment_selection_ids.write({'equipment_ids': [Command.clear()]}),
        ):
            with self.assertRaises(UserError):
                action()
        self.assertEqual(sample.sample_result, 'pass')
        self.assertEqual(len(self._admissions(check)), 4)

    def test_unadmitted_fail_can_be_reworked_without_automatic_admission(self):
        check = self._ready()
        self._decision(check).action_apply()
        sample = check.sample_ids[-1]
        check.update_measurement_visual_result(sample.id, 'yes')
        self.assertEqual(sample.sample_result, 'pass')
        self.assertTrue(sample.x_was_reworked)
        self.assertEqual((check.x_passed_qty, check.x_admitted_qty, check.x_held_qty), (5, 4, 1))
        self.assertFalse(self._admissions(check).filtered(lambda record: record.controlled_unit_id == sample.controlled_unit_id))
        self._decision(check, sample).action_apply()
        self.assertEqual(check.x_admitted_qty, 5)

    def test_required_additional_check_blocks_only_its_failing_unit(self):
        check = self._ready(fail_last=False)
        additional = self._check(check.production_id)
        self._identify(additional, mode='existing', run=check.operation_run_id, units=check.sample_ids.controlled_unit_id)
        self._fill(additional)
        additional.update_measurement_visual_result(additional.sample_ids[2].id, 'no')
        with self.assertRaises(ValidationError):
            self._decision(check, check.sample_ids[2]).action_apply()
        self._decision(check, check.sample_ids[:2] | check.sample_ids[3:]).action_apply()
        self.assertEqual(len(self._admissions(check)), 4)
        with self.assertRaises(UserError):
            additional.update_measurement_visual_result(additional.sample_ids[0].id, 'no')

    def test_optional_check_and_retest_in_another_run_are_independent(self):
        check = self._ready(fail_last=False)
        optional = self._check(check.production_id)
        manager = self._user(manager=True)
        optional.with_user(manager).write({'unit_admission_role': 'optional'})
        self._identify(optional, mode='existing', run=check.operation_run_id, units=check.sample_ids.controlled_unit_id)
        self._fill(optional)
        optional.update_measurement_visual_result(optional.sample_ids[0].id, 'no')
        retest = self._check(check.production_id)
        self._identify(retest, workorder=check.operation_run_id.workorder_id, mode='reuse', units=check.sample_ids.controlled_unit_id)
        self.assertEqual(retest.sample_ids[0].sample_result, 'pending')
        self._decision(check).action_apply()
        self.assertEqual(check.x_admitted_qty, 5)
        self.assertEqual(optional.x_admitted_qty, 5)
        self.assertEqual(retest.x_admitted_qty, 0)
        optional.update_measurement_visual_result(optional.sample_ids[1].id, 'no')
        self.assertEqual(check.x_admitted_qty, 5)

    def test_another_stage_does_not_block_admission(self):
        check = self._ready(fail_last=False)
        incoming = self.point.copy({'x_control_stage': 'incoming', 'visual_sample_control_required': True})
        other = self._check(check.production_id, point=incoming)
        other.add_measurement_samples(1)
        other.update_measurement_visual_result(other.sample_ids.id, 'no')
        self._decision(check).action_apply()
        self.assertEqual(check.x_admitted_qty, 5)

    def test_global_pass_does_not_create_unit_permissions(self):
        check = self._ready(fail_last=False)
        check.do_pass()
        self.assertEqual(check.x_passed_qty, 5)
        self.assertFalse(self._admissions(check))
        self.assertEqual(check.x_admitted_qty, 0)

    def test_revocation_has_reason_audit_and_preserves_confirmation(self):
        check = self._ready()
        self._decision(check).action_apply()
        sample = check.sample_ids[0]
        record = self._admissions(check).filtered(lambda item: item.sample_id == sample)
        original = (record.inspector_id.id, record.confirmed_at, record.evidence_snapshot)
        with self.assertRaises(ValidationError):
            self._decision(check, sample, mode='revoke').action_apply()
        wizard = self._decision(check, sample, mode='revoke', reason='Повторне вимірювання')
        wizard.action_apply()
        messages = check.message_ids.ids
        wizard.action_apply()
        self.assertEqual(messages, check.message_ids.ids)
        self.assertEqual(record.state, 'revoked')
        self.assertTrue(record.revoked_by_id)
        self.assertTrue(record.revoked_at)
        self.assertEqual(original, (record.inspector_id.id, record.confirmed_at, record.evidence_snapshot))
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (3, 2))
        check.update_measurement_visual_result(sample.id, 'no')
        check.update_measurement_visual_result(sample.id, 'yes')
        self.assertEqual(check.x_admitted_qty, 3)
        self._decision(check, sample).action_apply()
        self.assertEqual(check.x_admitted_qty, 4)
        self.assertEqual(len(self._admissions(check, state=False)), 5)
        self.assertTrue(check.message_ids.filtered(lambda message: 'Повторне вимірювання' in (message.body or '')))

    def test_old_confirmation_or_revocation_request_cannot_act_on_new_decision(self):
        check = self._ready(1, fail_last=False)
        confirmation = self._decision(check)
        confirmation.action_apply()
        revocation = self._decision(check, mode='revoke', reason='Повторний контроль')
        revocation.action_apply()
        with self.assertRaises(UserError):
            confirmation.action_apply()
        self._decision(check).action_apply()
        with self.assertRaises(UserError):
            revocation.action_apply()
        self.assertEqual(len(self._admissions(check)), 1)

    def test_later_explicit_run_prevents_unsafe_revocation(self):
        check = self._ready()
        self._decision(check).action_apply()
        later = self._check(check.production_id)
        self._identify(later, workorder=self._workorder(check.production_id, 'Складання'), mode='reuse', units=check.sample_ids.controlled_unit_id)
        with self.assertRaises(UserError):
            self._decision(check, check.sample_ids[0], mode='revoke', reason='Скасувати').action_apply()
        self.assertEqual(len(self._admissions(check)), 4)

    def test_scope_cannot_change_while_any_unit_is_admitted(self):
        check = self._ready()
        self._decision(check).action_apply()
        manager = self._user(manager=True)
        with self.assertRaises(UserError):
            check.with_user(manager).write({'unit_admission_role': 'optional'})
        additional = self._check(check.production_id)
        with self.assertRaises(UserError):
            self._identify(additional, mode='existing', run=check.operation_run_id, units=check.sample_ids.controlled_unit_id)
        self.assertFalse(additional.operation_run_id)
        self.assertEqual(len(self._admissions(check)), 4)

    def test_hidden_mandatory_check_blocks_instead_of_being_ignored(self):
        check = self._ready()
        additional = self._check(check.production_id)
        self._identify(additional, mode='existing', run=check.operation_run_id, units=check.sample_ids.controlled_unit_id)
        inspector = self._user()
        self.env['ir.rule'].create({
            'name': 'Hide mandatory admission QC', 'model_id': self.env['ir.model']._get_id('quality.check'),
            'domain_force': "[('id', '!=', %d)]" % additional.id,
            'perm_read': True, 'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })
        error = check.with_user(inspector)._get_unit_admission_error(check.sample_ids[0].with_user(inspector))
        self.assertIn('Немає доступу', error)

    def test_admission_reads_respect_company_and_parent_mo_rules(self):
        check = self._ready()
        self._decision(check).action_apply()
        inspector = self._user()
        record = self._admissions(check)[0]
        other = self.env['res.company'].create({'name': 'Інша компанія допуску'})
        inspector.company_ids = [Command.link(other.id)]
        with self.assertRaises(AccessError):
            record.with_user(inspector).with_context(allowed_company_ids=other.ids).read(['name'])
        self.env['ir.rule'].create({
            'name': 'Hide admission MO', 'model_id': self.env['ir.model']._get_id('mrp.production'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': True, 'perm_write': True,
            'perm_create': False, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError):
            record.with_user(inspector).read(['name'])
        self.assertFalse(self.env['quality.unit.admission'].with_user(inspector).search([('id', '=', record.id)]))

    def test_alert_board_matrix_and_production_are_unchanged(self):
        check = self._ready()
        alert = self.env['quality.alert'].create({'name': 'Допуск та утримання', 'check_id': check.id, 'team_id': self.team.id})
        production, workorder = check.production_id, check.operation_run_id.workorder_id
        before = (
            check.sample_ids.measurement_value_ids.read(), check.sample_ids.mapped('x_was_reworked'),
            alert.read(), production.state, production.qty_producing, production.qty_produced,
            production.move_raw_ids.read(), production.move_finished_ids.read(), workorder.state,
        )
        self._decision(check).action_apply()
        self.assertEqual(before, (
            check.sample_ids.measurement_value_ids.read(), check.sample_ids.mapped('x_was_reworked'),
            alert.read(), production.state, production.qty_producing, production.qty_produced,
            production.move_raw_ids.read(), production.move_finished_ids.read(), workorder.state,
        ))

    def test_shared_revision_is_updated_to_serialize_evidence_and_decisions(self):
        check = self._ready()
        before = check.operation_run_id.unit_admission_revision
        self._decision(check).action_apply()
        self.assertGreater(check.operation_run_id.unit_admission_revision, before)
        before = check.operation_run_id.unit_admission_revision
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'yes')
        self.assertGreater(check.operation_run_id.unit_admission_revision, before)
        self.assertEqual(len(self._admissions(check)), 4)

    def test_changed_batch_suspends_effectiveness_without_rewriting_audit(self):
        check = self._ready()
        self._decision(check).action_apply()
        records = self._admissions(check)
        before = records.mapped('confirmed_at')
        check.production_id.qty_producing = 6
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (0, 5))
        self.assertTrue(all(not record.is_effective for record in records))
        self.assertTrue(all(records.mapped('ineffective_reason')))
        self.assertEqual(records.mapped('confirmed_at'), before)
        self.assertTrue(all(record.state == 'confirmed' for record in records))

    def test_database_active_unique_index_is_present(self):
        self.env.cr.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'quality_unit_admission_active_unique'")
        definition = self.env.cr.fetchone()
        self.assertTrue(definition)
        self.assertIn('UNIQUE', definition[0])
        self.assertIn('confirmed', definition[0])
        check = self._ready(1, fail_last=False)
        self._decision(check).action_apply()
        record = self._admissions(check)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env.cr.execute("""
                INSERT INTO quality_unit_admission
                    (name, check_id, sample_id, controlled_unit_id, operation_run_id, workorder_id,
                     production_id, product_id, company_id, inspector_id, confirmed_at, state,
                     request_key, evidence_snapshot)
                SELECT name, check_id, sample_id, controlled_unit_id, operation_run_id, workorder_id,
                       production_id, product_id, company_id, inspector_id, confirmed_at, state,
                       'concurrent-forged-request', evidence_snapshot
                FROM quality_unit_admission WHERE id = %s
            """, [record.id])

    def test_wizard_and_matrix_expose_explicit_choice_and_separate_counters(self):
        check = self._ready()
        defaults = self.env['quality.unit.admission.wizard'].with_context(default_check_id=check.id).default_get(['check_id', 'line_ids'])
        self.assertTrue(all(not command[2].get('selected') for command in defaults['line_ids']))
        self._decision(check).action_apply()
        data = check.get_measurement_matrix_data()
        self.assertEqual((data['admitted_quantity'], data['held_quantity']), (4, 1))
        self.assertEqual(sum(row['unit_admission_label'] == 'Допущено' for row in data['samples']), 4)
        view = self.env['quality.unit.admission.wizard'].get_view(
            view_id=self.env.ref('quality_vataga.quality_unit_admission_wizard_form').id, view_type='form',
        )
        arch = etree.fromstring(view['arch'])
        self.assertEqual(arch.xpath("//field[@name='selected']")[0].get('readonly'), 'not eligible')
        self.assertEqual(arch.xpath("//field[@name='reason']")[0].get('required'), "mode == 'revoke'")
