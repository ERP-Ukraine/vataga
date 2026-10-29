from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from lxml import etree
from psycopg2 import IntegrityError

from odoo import api, Command, sql_db
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from ..models.quality_production_identity import IDENTIFICATION_CONTEXT, IDENTIFICATION_TOKEN
from .test_quality_check_in_process import QualityCheckInProcessCase


@tagged('post_install', '-at_install')
class TestQualityProductionIdentity(QualityCheckInProcessCase):
    def _wizard(self, check, mode='new', run=None, units=None, workorder=None, confirmed=True):
        samples = check.sample_ids.sorted('sample_number')
        values = {
            'check_id': check.id, 'mode': mode, 'confirm_mapping': confirmed,
            'run_id': run.id if run else False,
            'workorder_id': workorder.id if workorder else False,
            'line_ids': [Command.create({
                'sample_id': sample.id,
                'controlled_unit_id': units[index].id if units else False,
            }) for index, sample in enumerate(samples)],
        }
        return self.env['quality.check.identification.wizard'].create(values)

    def _register(self, check, **kwargs):
        wizard = self._wizard(check, **kwargs)
        wizard.action_apply()
        return check.operation_run_id

    def _workorder(self, production, suffix):
        center = self.env['mrp.workcenter'].create({'name': 'Ідентифікація ' + suffix})
        return self.env['mrp.workorder'].create({
            'name': 'Операція ' + suffix, 'workcenter_id': center.id,
            'production_id': production.id, 'product_uom_id': self.product.uom_id.id,
        })

    def _inspector(self):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Інспектор ідентифікації', 'login': 'quality_identity_inspector',
            'groups_id': [Command.set([
                self.env.ref('quality.group_quality_user').id,
                self.env.ref('mrp.group_mrp_user').id,
            ])],
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
        })

    def test_register_five_units_and_matrix_correspondence(self):
        check = self._check(self._production())
        tracking = self.product.tracking
        run = self._register(check)
        self.assertEqual(self.product.tracking, tracking)
        self.assertEqual(run.quantity, 5)
        self.assertEqual(run.state, 'registered')
        self.assertEqual(run.production_id, check.production_id)
        self.assertEqual(run.product_id, self.product)
        self.assertEqual(run.company_id, self.env.company)
        self.assertEqual(run.origin_check_id, check)
        self.assertEqual(run.responsible_id, self.env.user)
        self.assertTrue(run.create_date)
        self.assertEqual(run.unit_ids, check.sample_ids.controlled_unit_id)
        self.assertEqual(run.unit_ids.mapped('unit_number'), [1, 2, 3, 4, 5])
        self.assertEqual(len(set(run.unit_ids.mapped('name'))), 5)
        data = check.get_measurement_matrix_data()
        self.assertEqual([row['controlled_unit_id'] for row in data['samples']], run.unit_ids.ids)
        self.assertEqual([row['controlled_unit_name'] for row in data['samples']], run.unit_ids.mapped('name'))
        self.assertEqual((check.x_passed_qty, check.x_failed_qty, check.x_pending_qty), (0, 0, 5))
        self.assertEqual(check.quality_state, 'none')
        self.assertFalse(check.can_pass_measurement_check)

    def test_equal_quantities_on_same_mo_require_reuse_of_existing_units(self):
        production = self._production()
        first, second = self._check(production), self._check(production)
        first_run = self._register(first)
        with self.assertRaises(ValidationError):
            self._register(second)
        self.assertFalse(second.operation_run_id)
        self.assertFalse(second.sample_ids.controlled_unit_id)
        second_run = self._register(second, mode='reuse', units=first_run.unit_ids)
        self.assertNotEqual(first_run, second_run)
        self.assertEqual(set(first_run.unit_ids.ids), set(second_run.unit_ids.ids))
        self.assertEqual(len(first_run.unit_ids | second_run.unit_ids), 5)

    def test_additional_check_reuses_explicit_permuted_mapping(self):
        production = self._production()
        first = self._check(production)
        run = self._register(first)
        second = self._check(production)
        units = run.unit_ids.sorted('unit_number', reverse=True)
        self._register(second, mode='existing', run=run, units=units)
        self.assertEqual(second.operation_run_id, run)
        self.assertEqual([sample.controlled_unit_id.id for sample in second.sample_ids.sorted('sample_number')], units.ids)
        self.assertEqual(set(run.check_ids.ids), {first.id, second.id})
        self.assertEqual(len(self.env['quality.production.unit'].search([('production_id', '=', production.id)])), 5)

    def test_existing_run_requires_explicit_units_even_with_equal_numbers(self):
        production = self._production()
        run = self._register(self._check(production))
        second = self._check(production)
        wizard = self._wizard(second, mode='existing', run=run)
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        self.assertFalse(second.operation_run_id)
        self.assertFalse(second.sample_ids.controlled_unit_id)

    def test_next_workorder_reuses_units_without_production_changes(self):
        production = self._production()
        first_wo, next_wo = self._workorder(production, '1'), self._workorder(production, '2')
        first_values = {'workorder_id': first_wo.id} if 'workorder_id' in self.env['quality.check']._fields else {}
        next_values = {'workorder_id': next_wo.id} if first_values else {}
        first = self._check(production, **first_values)
        original = self._register(first, workorder=first_wo)
        second = self._check(production, **next_values)
        before = (production.state, production.qty_producing, first_wo.state, next_wo.state,
                  production.move_raw_ids.ids, production.move_finished_ids.ids)
        next_run = self._register(second, mode='reuse', units=original.unit_ids, workorder=next_wo)
        self.assertNotEqual(next_run, original)
        self.assertEqual(next_run.unit_ids, original.unit_ids)
        self.assertEqual(next_run.workorder_id, next_wo)
        self.assertEqual(next_run.unit_ids.registration_run_id, original)
        self.assertEqual(before, (production.state, production.qty_producing, first_wo.state, next_wo.state,
                                  production.move_raw_ids.ids, production.move_finished_ids.ids))

    def test_rework_retains_identifier_and_audit(self):
        check = self._check(self._production(1))
        unit = self._register(check).unit_ids
        self._fill(check)
        cell = check.sample_ids.measurement_value_ids.filtered(lambda value: value.parameter_type == 'numeric')
        check.update_measurement_value(cell.id, {'numeric_input': '11'})
        check.update_measurement_value(cell.id, {'numeric_input': '5'})
        self.assertEqual(check.sample_ids.controlled_unit_id, unit)
        self.assertTrue(check.sample_ids.x_was_reworked)
        self.assertEqual((check.x_passed_qty, check.x_failed_qty), (1, 0))

    def test_retest_one_existing_unit_uses_new_check_and_run(self):
        production = self._production(1)
        old = self._check(production)
        unit = self._register(old).unit_ids
        self._fill(old)
        old.update_measurement_visual_result(old.sample_ids.id, 'no')
        old.do_fail()
        retest = self._check(production)
        run = self._register(retest, mode='reuse', units=unit)
        self._fill(retest)
        self.assertEqual(retest.sample_ids.controlled_unit_id, old.sample_ids.controlled_unit_id)
        self.assertNotEqual(run, old.operation_run_id)
        self.assertEqual(old.sample_ids.sample_result, 'fail')
        self.assertEqual(retest.sample_ids.sample_result, 'pass')
        self.assertEqual(old.quality_state, 'fail')

    def test_repeated_and_overlapping_wizards_register_once_and_lock(self):
        check = self._check(self._production())
        first, second = self._wizard(check), self._wizard(check)
        cursor_type = type(self.env.cr)
        execute = cursor_type.execute
        statements = []

        def recording(cursor, query, *args, **kwargs):
            if cursor is self.env.cr:
                statements.append(str(query))
            return execute(cursor, query, *args, **kwargs)

        with patch.object(cursor_type, 'execute', recording):
            first.action_apply()
            run = check.operation_run_id
            first.action_apply()
            second.action_apply()
        self.assertEqual(check.operation_run_id, run)
        self.assertEqual(len(run.unit_ids), 5)
        self.assertEqual(self.env['quality.production.run'].search_count([('origin_check_id', '=', check.id)]), 1)
        self.assertEqual(self.env['quality.production.unit'].search_count([('production_id', '=', check.production_id.id)]), 5)
        self.assertTrue(any('mrp_production' in query and 'FOR UPDATE' in query for query in statements))
        self.assertTrue(any('quality_check' in query and 'FOR UPDATE' in query for query in statements))

    def test_database_constraint_prevents_second_origin_run(self):
        check = self._check(self._production())
        self._register(check)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env['quality.production.run'].with_context(**{IDENTIFICATION_CONTEXT: IDENTIFICATION_TOKEN}).create({
                'production_id': check.production_id.id, 'origin_check_id': check.id,
                'quantity': 5, 'registration_mode': 'new',
            })

    def test_identifiers_are_unique_across_parallel_sequence_transactions(self):
        sequence = self.env.ref('quality_vataga.sequence_quality_production_unit')
        database = sql_db.db_connect(self.env.cr.dbname)
        with database.cursor() as probe:
            probe.execute('SELECT id FROM ir_sequence WHERE id = %s', [sequence.id])
            if not probe.fetchone():
                self.skipTest('Sequence installation data is not committed yet.')
        uid, context = self.env.uid, dict(self.env.context)

        def allocate():
            with database.cursor() as cursor:
                env = api.Environment(cursor, uid, context)
                return [env['ir.sequence'].browse(sequence.id).next_by_id() for _index in range(5)]

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(allocate) for _index in range(2)]
            names = [name for future in futures for name in future.result(timeout=30)]
        self.assertEqual(len(names), len(set(names)))

    def test_duplicate_unit_in_wizard_and_sample_sql_constraint(self):
        check = self._check(self._production())
        run = self._register(check)
        second = self._check(check.production_id)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self._wizard(second, mode='existing', run=run, units=[run.unit_ids[0]] * 5)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            # Exercise the SQL safety net against an erroneous trusted write.
            self.env.cr.execute(
                'UPDATE quality_check_sample SET controlled_unit_id = %s WHERE id = %s',
                [check.sample_ids[0].controlled_unit_id.id, check.sample_ids[1].id],
            )

    def test_wrong_mo_units_or_run_are_rejected(self):
        foreign = self._register(self._check(self._production()))
        target = self._check(self._production())
        for mode in ('existing', 'reuse'):
            wizard = self._wizard(target, mode=mode, run=foreign if mode == 'existing' else None, units=foreign.unit_ids)
            with self.assertRaises(ValidationError):
                wizard.action_apply()
        self.assertFalse(target.operation_run_id)

    def test_wrong_sample_rows_are_rejected(self):
        production = self._production()
        target, foreign = self._check(production), self._check(production)
        wizard = self._wizard(foreign)
        with self.assertRaises(UserError):
            wizard.check_id = target
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.env['quality.check.identification.line'].create({
                'wizard_id': self._wizard(target).id, 'sample_id': foreign.sample_ids[0].id,
            })
        self.assertFalse(target.operation_run_id)

    def test_product_and_company_metadata_cannot_change_after_registration(self):
        check = self._check(self._production())
        run = self._register(check)
        other_product = self.env['product.product'].create({'name': 'Інший ідентифікований виріб'})
        other_company = self.env['res.company'].create({'name': 'Інша компанія ідентифікації'})
        for record, vals in (
            (run, {'product_id': other_product.id}),
            (run.unit_ids, {'company_id': other_company.id}),
            (check.production_id, {'product_id': other_product.id}),
            (check.production_id, {'company_id': other_company.id}),
        ):
            with self.assertRaises(UserError):
                record.write(vals)
        if 'company_id' in check._fields:
            with self.assertRaises(UserError):
                check.write({'company_id': other_company.id})

    def test_lot_must_match_product_and_company_and_does_not_enable_tracking(self):
        self.product.tracking = 'lot'
        check = self._check(self._production(1))
        other = self.env['product.product'].create({'name': 'Виріб чужої партії', 'tracking': 'lot'})
        lot = self.env['stock.lot'].create({'name': 'FOREIGN-ID-LOT', 'product_id': other.id, 'company_id': self.env.company.id})
        wizard = self._wizard(check)
        wizard.line_ids.lot_id = lot
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        self.assertFalse(check.operation_run_id)
        self.assertEqual(self.env['quality.production.unit'].search_count([('production_id', '=', check.production_id.id)]), 0)
        valid = self.env['stock.lot'].create({'name': 'REAL-ID-LOT', 'product_id': self.product.id, 'company_id': self.env.company.id})
        wizard.line_ids.lot_id = valid
        wizard.action_apply()
        self.assertEqual(check.sample_ids.controlled_unit_id.lot_id, valid)
        self.assertEqual(self.product.tracking, 'lot')

    def test_serial_has_only_one_internal_identity(self):
        self.product.tracking = 'serial'
        production = self._production(1)
        lot = self.env['stock.lot'].create({'name': 'REAL-ID-SERIAL', 'product_id': self.product.id, 'company_id': self.env.company.id})
        first = self._check(production)
        wizard = self._wizard(first)
        wizard.line_ids.lot_id = lot
        wizard.action_apply()
        second = self._check(production)
        duplicate = self._wizard(second)
        duplicate.line_ids.lot_id = lot
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            duplicate.action_apply()
        self.assertFalse(second.operation_run_id)

    def test_foreign_company_lot_is_rejected(self):
        self.product.tracking = 'lot'
        other = self.env['res.company'].create({'name': 'Компанія чужої партії'})
        lot = self.env['stock.lot'].with_company(other).create({
            'name': 'FOREIGN-COMPANY-ID-LOT', 'product_id': self.product.id, 'company_id': other.id,
        })
        check = self._check(self._production(1))
        wizard = self._wizard(check)
        wizard.line_ids.lot_id = lot
        with self.assertRaises((AccessError, ValidationError)):
            wizard.action_apply()
        self.assertFalse(check.operation_run_id)

    def test_no_automatic_binding_of_open_or_historical_checks(self):
        check = self._check(self._production())
        self._fill(check)
        check.do_pass()
        old = (check.sample_ids.ids, check.sample_ids.measurement_value_ids.read(), check.message_ids.ids)
        for _iteration in range(2):
            check._initialize_measurement_snapshot()
            data = check.get_measurement_matrix_data()
            self.assertTrue(all(not row['controlled_unit_id'] for row in data['samples']))
        self.assertFalse(check.operation_run_id)
        self.assertFalse(check.sample_ids.controlled_unit_id)
        self.assertEqual(old, (check.sample_ids.ids, check.sample_ids.measurement_value_ids.read(), check.message_ids.ids))
        self.assertEqual(self.env['quality.production.run'].search_count([('origin_check_id', '=', check.id)]), 0)

    def test_copy_does_not_reuse_identity_and_completed_run_stays_frozen(self):
        check = self._check(self._production())
        run = self._register(check)
        self._fill(check)
        check.do_pass()
        check.production_id.qty_producing = 8
        self.assertEqual(len(check.get_measurement_matrix_data()['samples']), 5)
        self.assertEqual(run.quantity, 5)
        copy = check.copy({'production_id': check.production_id.id, 'point_id': self.point.id, 'quality_state': 'none'})
        self.assertFalse(copy.operation_run_id)
        self.assertFalse(copy.sample_ids.controlled_unit_id)
        self.assertEqual(len(run.unit_ids), 5)

    def test_binding_preserves_results_counts_alert_snapshots_and_chatter(self):
        check = self._check(self._production())
        self._fill(check)
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        alert = self.env['quality.alert'].create({'name': 'Ідентифікація без втрати історії', 'check_id': check.id, 'team_id': self.team.id})
        values = check.sample_ids.measurement_value_ids.read()
        before = (check.x_passed_qty, check.x_failed_qty, check.x_pending_qty, check.message_ids.ids,
                  alert.description, alert.x_sorting_parameter, alert.vataga_matrix_failure_snapshot)
        self._register(check)
        self.assertEqual(values, check.sample_ids.measurement_value_ids.read())
        self.assertEqual(before, (check.x_passed_qty, check.x_failed_qty, check.x_pending_qty, check.message_ids.ids,
                                  alert.description, alert.x_sorting_parameter, alert.vataga_matrix_failure_snapshot))
        self.assertEqual(check.quality_state, 'none')

    def test_cannot_replace_units_even_before_or_after_entering_results(self):
        production = self._production()
        check = self._check(production)
        run = self._register(check)
        other = self._register(self._check(production))
        for entered in (False, True):
            if entered:
                self._fill(check)
            wizard = self._wizard(check, mode='existing', run=other, units=other.unit_ids)
            with self.assertRaises(UserError):
                wizard.action_apply()
            with self.assertRaises(UserError):
                check.sample_ids[0].sudo().write({'controlled_unit_id': other.unit_ids[0].id})
        self.assertEqual(check.operation_run_id, run)

    def test_direct_orm_and_forged_context_cannot_bind_or_register(self):
        check = self._check(self._production())
        run = self._register(check)
        for action in (
            lambda: check.write({'operation_run_id': False}),
            lambda: check.sample_ids.sudo().write({'controlled_unit_id': False}),
            lambda: run.with_context(**{IDENTIFICATION_CONTEXT: True}).write({'unit_ids': [Command.clear()]}),
            lambda: run.unit_ids.sudo().write({'name': 'FORGED'}),
            lambda: self.env['quality.production.run'].with_context(**{IDENTIFICATION_CONTEXT: True}).create({}),
            lambda: self.env['quality.production.unit'].with_context(**{IDENTIFICATION_CONTEXT: True}).create({}),
            lambda: check.unlink(), lambda: run.unlink(), lambda: run.unit_ids.unlink(),
        ):
            with self.assertRaises(UserError):
                action()

    def test_forged_defaults_do_not_identify_new_checks_or_samples(self):
        check = self._check(self._production())
        unit = self._register(check).unit_ids[0]
        created = self.env['quality.check'].with_context(
            default_operation_run_id=check.operation_run_id.id,
            default_controlled_unit_id=unit.id,
        ).create({
            'point_id': self.point.id, 'product_id': self.product.id,
            'team_id': self.team.id, 'test_type_id': self.test_type.id,
            'production_id': check.production_id.id, 'measure_on': 'product',
        })
        self.assertFalse(created.operation_run_id)
        self.assertFalse(created.sample_ids.controlled_unit_id)

    def test_no_confirmation_and_no_implicit_wizard_mode(self):
        check = self._check(self._production())
        wizard = self._wizard(check, confirmed=False)
        with self.assertRaises(UserError):
            wizard.action_apply()
        defaults = self.env['quality.check.identification.wizard'].with_context(
            default_check_id=check.id, default_mode='new', default_confirm_mapping=True,
        ).default_get(['check_id', 'mode', 'confirm_mapping', 'line_ids'])
        self.assertFalse(defaults['mode'])
        self.assertFalse(defaults['confirm_mapping'])
        self.assertTrue(all('controlled_unit_id' not in command[2] for command in defaults['line_ids']))

    def test_parent_check_rules_protect_registration_searches_and_reads(self):
        check = self._check(self._production())
        run = self._register(check)
        inspector = self._inspector()
        self.env['ir.rule'].create({
            'name': 'Deny identity parent QC', 'model_id': self.env['ir.model']._get_id('quality.check'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': True,
            'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError):
            check.with_user(inspector).action_open_identification_wizard()
        with self.assertRaises(AccessError):
            run.with_user(inspector).read(['name'])
        self.assertFalse(self.env['quality.production.unit'].with_user(inspector).search([('id', 'in', run.unit_ids.ids)]))

    def test_company_rule_blocks_other_active_company(self):
        check = self._check(self._production())
        run = self._register(check)
        inspector = self._inspector()
        other = self.env['res.company'].create({'name': 'Неактивна компанія ідентифікації'})
        inspector.company_ids = [Command.link(other.id)]
        restricted = run.with_user(inspector).with_context(allowed_company_ids=other.ids)
        with self.assertRaises(AccessError):
            restricted.read(['name'])
        self.assertFalse(self.env['quality.production.unit'].with_user(inspector).with_context(
            allowed_company_ids=other.ids,
        ).search([('id', 'in', run.unit_ids.ids)]))

    def test_parent_production_rules_protect_identity_reads(self):
        check = self._check(self._production())
        run = self._register(check)
        inspector = self._inspector()
        self.env['ir.rule'].create({
            'name': 'Deny identity parent MO', 'model_id': self.env['ir.model']._get_id('mrp.production'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': True,
            'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError):
            run.unit_ids.with_user(inspector).read(['name'])
        self.assertFalse(self.env['quality.production.run'].with_user(inspector).search([('id', '=', run.id)]))

    def test_unit_history_respects_rules_on_subsequent_checks(self):
        production = self._production(1)
        original = self._check(production)
        run = self._register(original)
        additional = self._check(production)
        self._register(additional, mode='existing', run=run, units=run.unit_ids)
        inspector = self._inspector()
        self.env['ir.rule'].create({
            'name': 'Hide additional identity check', 'model_id': self.env['ir.model']._get_id('quality.check'),
            'domain_force': "[('id', '!=', %d)]" % additional.id,
            'perm_read': True, 'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })
        unit = run.unit_ids.with_user(inspector)
        self.assertEqual(unit.sample_ids.ids, original.sample_ids.ids)
        self.assertEqual(unit.check_ids.ids, original.ids)

    def test_user_without_quality_role_cannot_open_registration(self):
        check = self._check(self._production())
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Перегляд без реєстрації', 'login': 'quality_identity_reader',
            'groups_id': [Command.set(self.env.ref('base.group_user').ids)],
        })
        with self.assertRaises(AccessError):
            self.env['quality.check.identification.wizard'].with_user(user).create({
                'check_id': check.id, 'mode': 'new',
            })

    def test_identification_views_use_readonly_links_and_explicit_mapping(self):
        inspector = self._inspector()
        view = self.env['quality.check'].with_user(inspector).get_view(
            view_id=self.env.ref('quality_control.quality_check_view_form').id,
            view_type='form',
        )
        arch = etree.fromstring(view['arch'])
        self.assertEqual(len(arch.xpath("//button[@name='action_open_identification_wizard']")), 1)
        links = arch.xpath("//field[@name='operation_run_id']")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].get('readonly'), '1')
        wizard = self.env['quality.check.identification.wizard'].with_user(inspector).get_view(
            view_id=self.env.ref('quality_vataga.quality_check_identification_wizard_form').id,
            view_type='form',
        )
        form = etree.fromstring(wizard['arch'])
        self.assertTrue(form.xpath("//field[@name='confirm_mapping']"))
        lines = form.xpath("//field[@name='line_ids']/tree")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].get('create'), '0')
        self.assertEqual(lines[0].get('delete'), '0')
        self.assertTrue(lines[0].xpath("field[@name='sample_id'][@force_save='1']"))
        self.assertTrue(lines[0].xpath("field[@name='controlled_unit_id']"))

    def test_explicit_workorder_does_not_require_optional_qc_workorder_link(self):
        production = self._production()
        check = self._check(production)
        workorder = self._workorder(production, 'без зв’язку QC')
        if 'workorder_id' in check._fields:
            self.assertFalse(check.workorder_id)
        run = self._register(check, workorder=workorder)
        self.assertEqual(run.workorder_id, workorder)

    def test_closed_and_unformed_checks_cannot_register(self):
        check = self._check(self._production())
        self._fill(check)
        check.do_pass()
        with self.assertRaises(UserError):
            self._wizard(check).action_apply()
        deferred = self._check(self._production(0))
        with self.assertRaises(ValidationError):
            self._wizard(deferred).action_apply()
