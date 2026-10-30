"""Enterprise acceptance tests, prepared but not run in Community.

All Quality/WO/Stock models are real. Fixtures belong to TransactionCase and
rollback. The only fault injection raises AFTER the real QUNIT fact insert.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'quality_partial_release')
class TestQualityPartialRelease(TransactionCase):
    def _scene(self):
        return self.env['mrp.production']._quality_partial_probe_setup()

    def _snapshot(self, production):
        self.env.flush_all()
        return {
            'quantity': production.qty_produced,
            'state': production.state,
            'moves': production.move_raw_ids.read(['state', 'quantity', 'picked']),
            'finished': production.move_finished_ids.read(['state', 'quantity', 'picked']),
            'lines': (production.move_raw_ids | production.move_finished_ids).move_line_ids.read(
                ['quantity', 'consume_line_ids', 'lot_id']),
            'svl': (production.move_raw_ids | production.move_finished_ids).stock_valuation_layer_ids.read(
                ['quantity', 'value', 'remaining_qty', 'remaining_value']),
            'quants': self.env['stock.quant'].search([
                ('product_id', 'in', (production.move_raw_ids | production.move_finished_ids).product_id.ids)
            ]).read(['quantity', 'reserved_quantity']),
            'release': production._native_partial_history().ids,
            'cost': production._native_partial_history().cost_ids.ids,
            'facts': production.native_stock_fact_ids.ids,
        }

    def test_four_held_fifth_repair_qc_wo_and_final_native_posting(self):
        result = self.env['mrp.production']._quality_partial_probe_scenario()
        self.assertEqual(result['result'], 'PASS')
        self.assertEqual((result['first_value'], result['second_value'], result['total_value']), (140, 35, 175))

    def test_real_lot_qunit_facts_and_repair(self):
        result = self.env['mrp.production']._quality_partial_probe_scenario('lot')
        self.assertEqual(result['result'], 'PASS')

    def test_fail_identity_and_missing_wo_execution_cannot_be_forced(self):
        scene = self._scene()
        production, failed = scene['production'], scene['failed']
        before = self._snapshot(production)
        with self.assertRaises(UserError):
            production._native_partial_release(['qunit:%s' % failed.id], 'forced-held')
        # WO3 has actually processed four units. Removing one execution must
        # hold that exact identity even though the native quantity stays four.
        execution = scene['orders'][2]._quality_native_history().filtered(
            lambda r: r.controlled_unit_id == scene['good'][:1])
        execution.ensure_one()
        self.env.cr.execute('DELETE FROM quality_unit_execution WHERE id=%s', [execution.id])
        self.env.invalidate_all(flush=False)
        ready, reasons = production._quality_partial_ready_units()
        self.assertEqual(set(ready.ids), set(scene['good'][1:].ids))
        self.assertIn(scene['good'][:1].id, reasons)
        with self.assertRaises(UserError):
            production._native_partial_release(['qunit:%s' % u.id for u in scene['good']], 'forced-four')
        self.assertEqual(self._snapshot(production), before)

    def test_missing_qcp_control_and_missing_admission_hold_exact_units(self):
        scene = self._scene()
        production, good = scene['production'], scene['good']
        admission = self.env['quality.unit.admission'].search([
            ('controlled_unit_id', '=', good[:1].id), ('state', '=', 'confirmed')])
        self.assertTrue(admission)
        # Corrupt disposable evidence to prove quantity alone cannot authorize.
        self.env.cr.execute("UPDATE quality_unit_admission SET state='revoked' WHERE id IN %s", [tuple(admission.ids)])
        self.env.invalidate_all(flush=False)
        ready, reasons = production._quality_partial_ready_units()
        self.assertEqual(set(ready.ids), set(good[1:].ids))
        self.assertIn(good[:1].id, reasons)
        with self.assertRaises(UserError):
            production._native_partial_release(['qunit:%s' % good[:1].id], 'missing-admission')
        check = scene['check']
        self.env.cr.execute('UPDATE quality_check SET operation_run_id=NULL WHERE id=%s', [check.id])
        self.env.invalidate_all(flush=False)
        with self.assertRaises(UserError):
            production._native_partial_release(['qunit:%s' % u.id for u in good], 'missing-qcp-identity')
        self.assertFalse(production.native_stock_fact_ids)
        self.assertEqual(production.qty_produced, 0)

    def test_failure_after_real_stock_fact_insert_rolls_everything_back(self):
        scene = self._scene()
        production = scene['production']
        before = self._snapshot(production)
        model = type(self.env['quality.unit.stock.fact'])
        original = model.create

        def fail_after_insert(records, values):
            result = original(records, values)
            self.assertEqual(len(result), 4)
            self.assertEqual(production.qty_produced, 4)
            raise UserError('Injected failure after real QUNIT facts')

        with patch.object(model, 'create', fail_after_insert):
            with self.assertRaisesRegex(UserError, 'Injected failure'):
                production._quality_partial_release()
        self.assertEqual(self._snapshot(production), before)

    def test_private_probe_requires_exact_database(self):
        with self.assertRaises(AccessError):
            self.env['mrp.production']._run_quality_partial_release_probe('not-this-database')

    def test_scheduled_action_helper_rolls_back_its_whole_fixture(self):
        result = self.env['mrp.production']._run_quality_partial_release_probe(self.env.cr.dbname)
        self.assertEqual(result['rollback'], 'COMPLETE')
        self.assertEqual(result['result'], 'PASS', result)
