from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from lxml import etree
from psycopg2 import IntegrityError, errors

from odoo import api, Command, fields, sql_db
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_unit_admission as admission_tests
from ..models.quality_transfer_mrp import MrpProductionTransfer


@tagged('post_install', '-at_install')
class TestQualityUnitTransfer(QualityCheckInProcessCase):
    _workorder = admission_tests.TestQualityUnitAdmission._workorder
    _identify = admission_tests.TestQualityUnitAdmission._identify
    _ready = admission_tests.TestQualityUnitAdmission._ready
    _decision = admission_tests.TestQualityUnitAdmission._decision
    _admissions = admission_tests.TestQualityUnitAdmission._admissions
    _user = admission_tests.TestQualityUnitAdmission._user

    def _route(self, admitted=True):
        check = self._ready()
        source = check.operation_run_id.workorder_id
        destination = self._workorder(check.production_id, 'Складання')
        check.production_id.write({'state': 'progress', 'allow_workorder_dependencies': True})
        destination.write({'blocked_by_workorder_ids': [Command.set(source.ids)]})
        source.write({'state': 'progress'})
        if admitted:
            self._decision(check).action_apply()
        return check, source, destination

    def _send(self, check, destination, samples=None):
        samples = samples if samples is not None else check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')
        return self.env['quality.unit.transfer.wizard'].create({
            'workorder_id': check.operation_run_id.workorder_id.id, 'check_id': check.id,
            'mode': 'send', 'destination_workorder_id': destination.id,
            'line_ids': [Command.create({'sample_id': sample.id, 'selected': True}) for sample in samples],
        })

    def _receive(self, workorder, records, mode='receive', reason=''):
        return self.env['quality.unit.transfer.wizard'].create({
            'workorder_id': workorder.id, 'mode': mode, 'reason': reason,
            'line_ids': [Command.create({'transfer_id': record.id, 'selected': True}) for record in records],
        })

    def _transfers(self, check, active=True):
        domain = [('source_run_id', '=', check.operation_run_id.id)]
        if active:
            domain.append(('state', 'in', ('sent', 'received')))
        return self.env['quality.unit.transfer'].search(domain)

    def test_send_four_of_five_without_manufacturing_or_stock_changes(self):
        check, source, destination = self._route()
        production = check.production_id
        before = (
            production.qty_producing, production.qty_produced, production.product_qty,
            source.qty_produced, destination.qty_produced, source.state, destination.state,
            production.move_raw_ids.read(), production.move_finished_ids.read(),
            check.in_process_sample_quantity, check.sample_ids.ids, check.operation_run_id.unit_ids.ids,
        )
        result = self._send(check, destination).action_apply()
        records = self._transfers(check)
        self.assertEqual(len(records), 4)
        self.assertEqual(records.controlled_unit_id, check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass').controlled_unit_id)
        self.assertEqual(set(records.mapped('state')), {'sent'})
        self.assertEqual((check.x_admitted_qty, check.x_transferred_qty, check.x_received_qty, check.x_awaiting_transfer_qty, check.x_held_qty), (4, 4, 0, 0, 1))
        self.assertTrue(production.quality_transfer_controlled)
        self.assertEqual(result['infos']['quality_vataga_check_id'], check.id)
        self.assertEqual(before, (
            production.qty_producing, production.qty_produced, production.product_qty,
            source.qty_produced, destination.qty_produced, source.state, destination.state,
            production.move_raw_ids.read(), production.move_finished_ids.read(),
            check.in_process_sample_quantity, check.sample_ids.ids, check.operation_run_id.unit_ids.ids,
        ))

    def test_fail_and_pending_cannot_be_sent_even_with_forged_selection(self):
        check, source, destination = self._route(admitted=False)
        sample = check.sample_ids[-1]
        for result in ('fail', 'pending'):
            with self.subTest(result=result):
                check.update_measurement_visual_result(sample.id, 'no' if result == 'fail' else False)
                self.assertEqual(sample.sample_result, result)
                with self.assertRaises(ValidationError):
                    self._send(check, destination, sample).action_apply()
        self.assertFalse(self._transfers(check))
        self.assertFalse(check.production_id.quality_transfer_controlled)

    def test_pass_without_explicit_admission_is_not_sent(self):
        check, source, destination = self._route(admitted=False)
        with self.assertRaises(ValidationError):
            self._send(check, destination, check.sample_ids[:1]).action_apply()

    def test_mixed_valid_and_invalid_selection_is_atomic(self):
        check, source, destination = self._route()
        with self.assertRaises(ValidationError):
            self._send(check, destination, check.sample_ids).action_apply()
        self.assertFalse(self._transfers(check))
        self.assertFalse(check.production_id.quality_transfer_controlled)

    def test_revoked_or_suspended_admission_is_not_sent(self):
        check, source, destination = self._route()
        sample = check.sample_ids[0]
        self._decision(check, sample, mode='revoke', reason='Повторний контроль').action_apply()
        with self.assertRaises(ValidationError):
            self._send(check, destination, sample).action_apply()
        check.production_id.qty_producing = 6
        with self.assertRaises(ValidationError):
            self._send(check, destination, check.sample_ids[1:2]).action_apply()

    def test_send_retry_is_idempotent_but_new_request_cannot_double_send(self):
        check, source, destination = self._route()
        wizard = self._send(check, destination)
        wizard.action_apply()
        messages = check.message_ids.ids
        wizard.action_apply()
        self.assertEqual(messages, check.message_ids.ids)
        self.assertEqual(len(self._transfers(check)), 4)
        with self.assertRaises(ValidationError):
            self._send(check, destination).action_apply()

    def test_receipt_retry_and_exact_same_qunit_identifiers(self):
        check, source, destination = self._route()
        self._send(check, destination).action_apply()
        records = self._transfers(check)
        unit_ids = check.operation_run_id.unit_ids.ids
        wizard = self._receive(destination, records)
        result = wizard.action_apply()
        messages = check.message_ids.ids
        wizard.action_apply()
        self.assertEqual(check.message_ids.ids, messages)
        self.assertEqual(set(records.mapped('state')), {'received'})
        self.assertEqual(destination.quality_incoming_received_qty, 4)
        self.assertEqual(check.x_received_qty, 4)
        self.assertEqual(unit_ids, check.operation_run_id.unit_ids.ids)
        self.assertEqual(self.env['quality.production.unit'].search_count([('production_id', '=', check.production_id.id)]), 5)
        self.assertEqual(result['infos']['quality_vataga_workorder_id'], destination.id)
        self.assertFalse(result['infos']['quality_vataga_check_id'])
        self.assertEqual(records.controlled_unit_id, destination.quality_transfer_in_ids.controlled_unit_id)

    def test_wrong_destination_and_missing_direct_dependency(self):
        check, source, destination = self._route()
        destination.blocked_by_workorder_ids = [Command.clear()]
        for wrong in (source, destination, self._workorder(self._production(), 'Чуже MO')):
            with self.assertRaises(ValidationError):
                self._send(check, wrong).action_apply()
        self.assertFalse(check.production_id.quality_transfer_controlled)

    def test_join_route_is_ambiguous_and_rejected(self):
        check, source, destination = self._route()
        other = self._workorder(check.production_id, 'Інша передумова')
        destination.blocked_by_workorder_ids = [Command.set((source | other).ids)]
        with self.assertRaises(ValidationError):
            self._send(check, destination).action_apply()

    def test_fork_route_is_not_silently_chosen(self):
        check, source, destination = self._route()
        other = self._workorder(check.production_id, 'Паралельна наступна операція')
        other.blocked_by_workorder_ids = [Command.set(source.ids)]
        with self.assertRaises(ValidationError):
            self._send(check, destination).action_apply()

    def test_foreign_mo_sample_and_foreign_receipt_are_rejected(self):
        check, source, destination = self._route()
        foreign, unused, other_destination = self._route()
        with self.assertRaises(ValidationError):
            self._send(check, destination, foreign.sample_ids[:1])
        self._send(check, destination).action_apply()
        with self.assertRaises(ValidationError):
            self._receive(other_destination, self._transfers(check))

    def test_company_and_parent_record_rules_are_respected(self):
        check, source, destination = self._route()
        self._send(check, destination).action_apply()
        inspector = self._user()
        record = self._transfers(check)[0]
        other = self.env['res.company'].create({'name': 'Інша компанія передач'})
        inspector.company_ids = [Command.link(other.id)]
        with self.assertRaises(AccessError):
            record.with_user(inspector).with_context(allowed_company_ids=other.ids).read(['name'])
        self.env['ir.rule'].create({
            'name': 'Hide transfer destination', 'model_id': self.env['ir.model']._get_id('mrp.workorder'),
            'domain_force': "[('id', '!=', %d)]" % destination.id,
        })
        with self.assertRaises(AccessError):
            record.with_user(inspector).read(['name'])
        self.assertFalse(self.env['quality.unit.transfer'].with_user(inspector).search([('id', '=', record.id)]))

    def test_transfer_to_another_company_is_rejected(self):
        check, source, destination = self._route()
        other = self.env['res.company'].create({'name': 'Компанія іншої операції'})
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', other.id)], limit=1)
        if not warehouse:
            warehouse = self.env['stock.warehouse'].with_company(other).create({'name': 'Інша компанія', 'code': 'QTBC', 'company_id': other.id})
        production = self.env['mrp.production'].with_company(other).create({
            'product_id': self.product.id, 'product_qty': 5, 'product_uom_id': self.product.uom_id.id,
            'company_id': other.id, 'picking_type_id': warehouse.manu_type_id.id,
            'location_src_id': warehouse.lot_stock_id.id, 'location_dest_id': warehouse.lot_stock_id.id,
        })
        center = self.env['mrp.workcenter'].with_company(other).create({'name': 'Чужа компанія', 'company_id': other.id})
        workorder = self.env['mrp.workorder'].with_company(other).create({
            'name': 'Чужа компанія', 'production_id': production.id,
            'workcenter_id': center.id, 'product_uom_id': self.product.uom_id.id,
        })
        with self.assertRaises((AccessError, ValidationError)):
            self._send(check, workorder).action_apply()
        self.assertFalse(self._transfers(check))

    def test_direct_rpc_mutations_and_forged_context_are_rejected(self):
        check, source, destination = self._route()
        model = self.env['quality.unit.transfer']
        for values in ({}, {'admission_id': self._admissions(check)[0].id, 'destination_workorder_id': destination.id, 'request_key': 'forged'}):
            with self.assertRaises(UserError):
                model.with_context(quality_vataga_unit_transfer=True).create(values)
        self._send(check, destination).action_apply()
        record = self._transfers(check)[0]
        for operation in (
            lambda: record.write({'state': 'received'}), lambda: record.sudo().write({'destination_workorder_id': source.id}),
            record.unlink, lambda: record.event_ids.write({'reason': 'Змінено'}), record.event_ids.unlink,
            lambda: check.production_id.write({'quality_transfer_controlled': False}),
            lambda: self.env['quality.unit.transfer.event'].create({'transfer_id': record.id, 'action': 'received'}),
        ):
            with self.assertRaises((UserError, AccessError)):
                operation()

    def test_user_without_inspector_permission_cannot_transfer_or_receive(self):
        check, source, destination = self._route()
        user = self._user(quality=False)
        with self.assertRaises(AccessError):
            check.with_user(user).action_open_unit_transfer_wizard()
        self._send(check, destination).action_apply()
        with self.assertRaises(AccessError):
            destination.with_user(user).action_open_quality_unit_receipt_wizard()

    def test_sent_or_received_unit_blocks_admission_revocation(self):
        check, source, destination = self._route()
        sample = check.sample_ids[0]
        self._send(check, destination, sample).action_apply()
        for received in (False, True):
            if received:
                self._receive(destination, self._transfers(check)).action_apply()
            with self.assertRaises(UserError):
                self._decision(check, sample, mode='revoke', reason='Скасувати').action_apply()
        self.assertEqual(check.x_admitted_qty, 4)

    def test_return_keeps_audit_and_allows_explicit_revocation_or_resend(self):
        check, source, destination = self._route()
        sample = check.sample_ids[0]
        original = self._send(check, destination, sample)
        original.action_apply()
        record = self._transfers(check)
        self._receive(destination, record).action_apply()
        self.assertEqual(record.event_ids.mapped('action'), ['sent', 'received'])
        with self.assertRaises(ValidationError):
            self._receive(destination, record, mode='return').action_apply()
        returned = self._receive(destination, record, mode='return', reason='Повторне вимірювання')
        returned.action_apply()
        returned.action_apply()
        self.assertEqual(record.event_ids.mapped('action'), ['sent', 'received', 'returned'])
        self.assertTrue(record.sent_at and record.received_at and record.returned_at)
        self.assertEqual(record.return_reason, 'Повторне вимірювання')
        self.assertEqual((check.x_transferred_qty, check.x_received_qty, check.x_awaiting_transfer_qty), (0, 0, 4))
        with self.assertRaises(UserError):
            original.action_apply()
        self._decision(check, sample, mode='revoke', reason='Після повернення').action_apply()
        self._decision(check, sample).action_apply()
        self._send(check, destination, sample).action_apply()
        self.assertEqual(len(self._transfers(check, active=False)), 2)
        self.assertTrue(check.production_id.quality_transfer_controlled)
        self.assertTrue(check.message_ids.filtered(lambda item: 'Повторне вимірювання' in (item.body or '')))
        self.assertTrue(check.production_id.message_ids.filtered(lambda item: sample.controlled_unit_id.name in (item.body or '')))

    def test_return_refuses_a_later_registered_run(self):
        check, source, destination = self._route()
        later_check = self._check(check.production_id, **({'workorder_id': destination.id} if 'workorder_id' in check._fields else {}))
        self._send(check, destination).action_apply()
        records = self._transfers(check)
        self._receive(destination, records).action_apply()
        # Emulate later historical registration imported by trusted server
        # code. The normal identification path is already blocked here.
        self.cr.execute("""
            INSERT INTO quality_production_run
                (name, production_id, workorder_id, product_id, company_id, quantity,
                 origin_check_id, registration_mode, responsible_id, state)
            SELECT name || '/later', production_id, %s, product_id, company_id, quantity,
                   %s, 'reuse', responsible_id, 'registered'
            FROM quality_production_run WHERE id = %s RETURNING id
        """, [destination.id, later_check.id, check.operation_run_id.id])
        later_id = self.cr.fetchone()[0]
        self.cr.execute('INSERT INTO quality_production_run_unit_rel (run_id, unit_id) SELECT %s, unit_id FROM quality_production_run_unit_rel WHERE run_id = %s', [later_id, check.operation_run_id.id])
        with self.assertRaises(UserError):
            self._receive(destination, records, mode='return', reason='Зупинити передачу').action_apply()
        self.assertEqual(set(records.mapped('state')), {'received'})

    def test_native_completion_and_low_level_writes_are_blocked(self):
        check, source, destination = self._route()
        self._send(check, destination).action_apply()
        production = check.production_id
        operations = [
            source.button_finish, source.button_done, source.action_mark_as_done,
            destination.button_start, source._update_finished_move,
            production.pre_button_mark_done, production.button_mark_done, production._post_inventory, production._split_productions,
            lambda: source.write({'state': 'done'}), lambda: destination.write({'state': 'progress'}),
            lambda: source.write({'qty_produced': 5}), lambda: destination.write({'qty_producing': 4}),
            lambda: production.write({'state': 'done'}), lambda: production.write({'qty_producing': 4}),
            lambda: destination.write({'blocked_by_workorder_ids': [Command.clear()]}),
            lambda: self.env['mrp.workcenter.productivity'].create({'workorder_id': destination.id}),
        ]
        for operation in operations:
            with self.assertRaises(UserError):
                operation()
        self.assertNotEqual(source.state, 'done')
        self.assertNotEqual(destination.state, 'progress')
        self.assertEqual(production.qty_producing, 5)
        self.assertEqual(check.in_process_sample_quantity, 5)

    def test_dependency_cannot_be_changed_through_an_uncontrolled_neighbour(self):
        check, source, destination = self._route()
        foreign = self._workorder(self._production(), 'Звичайна незалежна операція')
        self._send(check, destination).action_apply()
        for vals in (
            {'blocked_by_workorder_ids': [Command.link(source.id)]},
            {'needed_by_workorder_ids': [Command.link(destination.id)]},
        ):
            with self.assertRaises(UserError):
                foreign.write(vals)
        with self.assertRaises(UserError):
            self.env['mrp.workorder'].create({
                'name': 'Обхід через нову операцію', 'production_id': foreign.production_id.id,
                'blocked_by_workorder_ids': [Command.link(source.id)],
            })

    def test_stock_boundary_blocks_create_direct_done_and_line_quantity(self):
        check, source, destination = self._route()
        production = check.production_id
        move = self.env['stock.move'].create({
            'name': 'Перевірка межі випуску', 'product_id': self.product.id,
            'product_uom': self.product.uom_id.id, 'product_uom_qty': 5,
            'location_id': production.location_src_id.id, 'location_dest_id': production.location_dest_id.id,
            'production_id': production.id,
        })
        self._send(check, destination).action_apply()
        for operation in (
            move._action_done, lambda: move.write({'state': 'done'}),
            lambda: move.write({'production_id': False}), lambda: move.write({'quantity': 5}),
            lambda: move.copy(),
            lambda: self.env['stock.move.line'].create({'move_id': move.id, 'quantity': 5}),
        ):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(production.qty_produced, 0)

    def test_ordinary_production_has_no_transfer_restrictions(self):
        production = self._production(5)
        source = self._workorder(production, 'Звичайна операція')
        source.button_start()
        source.button_finish()
        self.assertEqual(source.state, 'done')
        self.assertEqual(source.qty_produced, 5)
        production.qty_producing = 4
        self.assertEqual(production.qty_producing, 4)
        self.assertFalse(production.quality_transfer_controlled)

    def test_successful_qc_starts_workorder_with_enterprise_bypass(self):
        check = self._ready(fail_last=False)
        source = check.operation_run_id.workorder_id
        original = type(source).button_start
        with patch.object(type(source), 'button_start', autospec=True, side_effect=original) as start:
            check.do_pass()
        self.assertEqual(check.quality_state, 'pass')
        self.assertTrue(any(call.kwargs.get('bypass') is True for call in start.call_args_list))
        self.assertEqual(source.state, 'progress')
        self.assertFalse(check.production_id.quality_transfer_controlled)

    def test_enterprise_bypass_cannot_start_protected_route(self):
        check, source, destination = self._route()
        self._send(check, destination).action_apply()
        before = (source.state, destination.state, source.time_ids.ids, destination.time_ids.ids,
                  check.production_id.qty_producing, check.production_id.qty_produced)
        for workorder in (source, destination):
            for kwargs in ({}, {'bypass': False}, {'bypass': True}):
                with self.subTest(workorder=workorder.id, kwargs=kwargs), self.assertRaises(UserError):
                    workorder.button_start(**kwargs)
        self.assertEqual(before, (source.state, destination.state, source.time_ids.ids, destination.time_ids.ids,
                                 check.production_id.qty_producing, check.production_id.qty_produced))

    def test_running_source_timer_can_be_stopped_after_transfer(self):
        check, source, destination = self._route()
        source.duration_expected = 1
        source.button_start()
        timer = source.time_ids.filtered(lambda item: not item.date_end)
        timer.date_start = fields.Datetime.now() - timedelta(minutes=2)
        self._send(check, destination).action_apply()
        source.button_pending()
        self.assertTrue(all(source.time_ids.mapped('date_end')))
        self.assertEqual(source.qty_produced, 0)
        self.assertEqual(check.production_id.qty_produced, 0)
        with self.assertRaises(UserError):
            source.time_ids[:1].write({'date_end': False})

    def test_boundary_prevents_downstream_registration_and_duplicate_qunit(self):
        check, source, destination = self._route()
        next_check = self._check(check.production_id)
        self._send(check, destination).action_apply()
        with self.assertRaises(UserError):
            self._identify(next_check, workorder=destination, mode='reuse', units=check.operation_run_id.unit_ids)
        self.assertFalse(next_check.operation_run_id)
        self.assertEqual(self.env['quality.production.unit'].search_count([('production_id', '=', check.production_id.id)]), 5)

    def test_sql_unique_safety_net_and_serialization_failures_are_not_swallowed(self):
        check, source, destination = self._route()
        before = check.production_id.quality_transfer_revision
        self._send(check, destination).action_apply()
        self.assertGreater(check.production_id.quality_transfer_revision, before)
        record = self._transfers(check)[0]
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.cr.execute("""
                INSERT INTO quality_unit_transfer
                    (name, check_id, admission_id, controlled_unit_id, production_id, source_run_id,
                     source_workorder_id, destination_workorder_id, company_id, state, request_key, sent_by_id, sent_at)
                SELECT name, check_id, admission_id, controlled_unit_id, production_id, source_run_id,
                       source_workorder_id, destination_workorder_id, company_id, state, 'concurrent-request', sent_by_id, sent_at
                FROM quality_unit_transfer WHERE id = %s
            """, [record.id])
        # Let the normal Odoo service retry the entire transaction after a
        # concurrent MO/evidence revision; never turn it into a second receipt.
        wizard = self._receive(destination, record)
        with patch.object(type(check.production_id), '_lock_quality_transfer_scope', side_effect=errors.SerializationFailure('concurrent update')):
            with self.assertRaises(errors.SerializationFailure):
                wizard.action_apply()
        self.assertEqual(record.state, 'sent')

    def test_parallel_activation_blocks_native_finish_from_an_old_snapshot(self):
        database = sql_db.db_connect(self.env.cr.dbname)
        uid = self.env.uid
        # Separate committed fixtures are needed for genuine PostgreSQL
        # transactions; never commit TransactionCase's own fixture cursor.
        with database.cursor() as cr:
            cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name='mrp_production' AND column_name='quality_transfer_controlled'")
            if not cr.fetchone():
                self.skipTest('Transfer schema installation has not been committed yet.')
            env = api.Environment(cr, uid, {})
            warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
            if not warehouse.manu_type_id:
                self.skipTest('Committed MRP installation data is required for concurrent transactions.')
            product = env['product.product'].create({'name': 'Concurrent transfer boundary fixture'})
            center = env['mrp.workcenter'].create({'name': 'Concurrent transfer boundary fixture'})
            production = env['mrp.production'].create({
                'product_id': product.id, 'product_qty': 5, 'product_uom_id': product.uom_id.id,
                'picking_type_id': warehouse.manu_type_id.id,
                'location_src_id': warehouse.lot_stock_id.id, 'location_dest_id': warehouse.lot_stock_id.id,
            })
            production.qty_producing = 5
            workorder = env['mrp.workorder'].create({
                'name': 'Concurrent boundary', 'production_id': production.id,
                'workcenter_id': center.id, 'product_uom_id': product.uom_id.id,
            })
            workorder.write({'state': 'progress'})
            ids = production.id, workorder.id, product.id, center.id
            cr.commit()
        snapshot_ready, activated = Event(), Event()

        def finish_from_old_snapshot():
            with database.cursor() as cr:
                env = api.Environment(cr, uid, {})
                workorder = env['mrp.workorder'].browse(ids[1])
                self.assertEqual(workorder.state, 'progress')
                snapshot_ready.set()
                if not activated.wait(15):
                    raise AssertionError('Activation did not finish')
                try:
                    workorder.button_finish()
                except errors.SerializationFailure:
                    cr.rollback()
                else:
                    raise AssertionError('A stale transaction must retry after concurrent activation')
                fresh = api.Environment(cr, uid, {})['mrp.workorder'].browse(ids[1])
                with self.assertRaises(UserError):
                    fresh.button_finish()
                return 'blocked'

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(finish_from_old_snapshot)
                self.assertTrue(snapshot_ready.wait(15))
                with database.cursor() as cr:
                    production = api.Environment(cr, uid, {})['mrp.production'].browse(ids[0])
                    production._lock_quality_transfer_scope()
                    production._enable_quality_transfer_control()
                    cr.commit()
                activated.set()
                self.assertEqual(future.result(timeout=20), 'blocked')
            with database.cursor() as cr:
                self.assertEqual(api.Environment(cr, uid, {})['mrp.workorder'].browse(ids[1]).state, 'progress')
        finally:
            activated.set()
            with database.cursor() as cr:
                env = api.Environment(cr, uid, {})
                production = env['mrp.production'].browse(ids[0])
                # Test-only cleanup of the new committed fixture; no public
                # API exposes this reset on a real controlled production.
                super(MrpProductionTransfer, production).write({'quality_transfer_controlled': False})
                moves = production.move_raw_ids | production.move_finished_ids
                production.action_cancel()
                moves.unlink()
                production.unlink()
                env['product.product'].browse(ids[2]).unlink()
                env['mrp.workcenter'].browse(ids[3]).unlink()
                cr.commit()

    def test_views_explicit_actions_and_cancel_do_not_activate_control(self):
        check, source, destination = self._route()
        for action in (check.action_open_unit_transfer_wizard(), source.action_open_quality_unit_transfer_wizard(), destination.action_open_quality_unit_receipt_wizard(), destination.action_open_quality_unit_return_wizard()):
            self.assertEqual(action['views'], [(self.env.ref('quality_vataga.quality_unit_transfer_wizard_form').id, 'form')])
        self.assertFalse(check.production_id.quality_transfer_controlled)
        self.assertFalse(self._transfers(check))
        view = self.env['mrp.workorder'].get_view(view_id=self.env.ref('mrp.mrp_production_workorder_form_view_inherit').id, view_type='form')
        arch = etree.fromstring(view['arch'])
        self.assertEqual(len(arch.xpath("//page[@name='quality_unit_transfers']")), 1)
        self.assertEqual(len(arch.xpath("//widget[@name='quality_unit_transfer_workorder_buttons']")), 1)

    def test_alert_technical_board_and_identity_are_unchanged(self):
        check, source, destination = self._route()
        alert = self.env['quality.alert'].create({'name': 'Невідповідність під час передачі', 'check_id': check.id, 'team_id': self.team.id})
        before = (alert.read(), check.sample_ids.measurement_value_ids.read(), check.sample_ids.mapped('x_was_reworked'))
        self._send(check, destination).action_apply()
        self._receive(destination, self._transfers(check)).action_apply()
        self.assertEqual(before, (alert.read(), check.sample_ids.measurement_value_ids.read(), check.sample_ids.mapped('x_was_reworked')))
