"""Prepared real Enterprise acceptance, never a Community QC substitute."""
from unittest.mock import patch
from contextlib import ExitStack

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from ..models.native_enterprise_integration import enterprise_fixture_transaction
from ..models.quality_production_loss_probe import _PROBE


@tagged('post_install', '-at_install', 'quality_production_loss')
class TestQualityProductionLoss(TransactionCase):
    def setUp(self):
        super().setUp()
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.fixture, self.admission = contexts.enter_context(
            enterprise_fixture_transaction(self.env['mrp.production'], {}))
        self.probe_scope = {'cursor': self.fixture.env.cr, 'production_ids': set(),
                            'report': {'checks': []}, 'admission': self.admission}
        token = _PROBE.set(self.probe_scope)
        contexts.callback(_PROBE.reset, token)

    def _scene(self, tracking='none'):
        scene = self.fixture._quality_loss_probe_setup(tracking)
        self.admission.register(scene['production'])
        self.probe_scope['production_ids'].add(scene['production'].id)
        return scene

    def _approve(self, alert):
        alert.write({'decision_type': 'scrap', 'approval_user_id': self.fixture.env.uid})
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()

    def _snapshot(self, scene):
        self.fixture.env.flush_all()
        production = scene['production']
        return {'models': {n: self.fixture.env[n].search([]).ids for n in (
            'stock.move', 'stock.move.line', 'stock.valuation.layer', 'account.move', 'stock.scrap',
            'quality.native.partial.release', 'quality.native.production.loss', 'quality.production.loss.fact')},
            'mo': production.read(['product_qty', 'qty_produced', 'state', 'date_finished', 'is_locked']),
            'facts': production.native_stock_fact_ids.ids,
            'samples': scene['check'].sample_ids.read(['sample_result', 'controlled_unit_id']),
            'quants': self.fixture.env['stock.quant'].search([
                ('product_id', 'in', (production.product_id | scene['component']).ids)
            ]).read(['quantity', 'reserved_quantity'])}

    def test_none_4_pass_1_fail_actual_equipment_and_employee_cost(self):
        result = self.fixture._quality_loss_probe_scenario('none', self.admission)
        self.assertEqual((result['good_value'], result['loss_value']), (152, 38))
        self.assertEqual((result['equipment_cost'], result['employee_cost']), (60, 30))

    def test_lot_4_pass_1_fail_and_unchanged_history(self):
        self.assertEqual(self.fixture._quality_loss_probe_scenario('lot', self.admission)['result'], 'PASS')

    def test_no_board_approval_means_no_loss_or_stock(self):
        scene = self._scene()
        scene['alert'].decision_type = 'scrap'
        before = self._snapshot(scene)
        with self.assertRaises(UserError):
            scene['alert'].action_scrap_defective_units()
        self.assertEqual(self._snapshot(scene), before)

    def test_all_five_pass_have_real_output_190_and_no_losses_none_and_lot(self):
        for tracking in ('none', 'lot'):
            scene = self.fixture._quality_loss_probe_setup(tracking, all_pass=True)
            self.admission.register(scene['production'])
            release = scene['production']._quality_partial_release()
            self.assertFalse(release.loss_ids)
            self.assertEqual(scene['production'].qty_produced, 5)
            self.assertEqual(sum(scene['production'].move_finished_ids.stock_valuation_layer_ids.mapped('value')), 190)

    def test_pass_cannot_replace_the_selected_failed_identity(self):
        scene = self._scene()
        self._approve(scene['alert'])
        before = self._snapshot(scene)
        production = scene['production']
        good = scene['good']
        with self.assertRaises(UserError):
            production._native_loss_close(['qunit:%s' % u.id for u in good[1:] | scene['failed']],
                ['qunit:%s' % good[:1].id], 'substitute-pass')
        self.assertEqual(self._snapshot(scene), before)

    def test_conflicting_open_alert_cannot_be_ignored(self):
        scene = self._scene()
        self._approve(scene['alert'])
        self.fixture.env['quality.alert'].create({
            'name': 'Another unresolved decision for the same failed QUNIT',
            'check_id': scene['check'].id, 'team_id': scene['alert'].team_id.id,
            'decision_type': 'repair'})
        before = self._snapshot(scene)
        with self.assertRaises(UserError):
            scene['alert'].action_scrap_defective_units()
        self.assertEqual(self._snapshot(scene), before)

    def test_exception_after_actual_good_stock_and_loss_fact_rolls_back(self):
        scene = self._scene()
        self._approve(scene['alert'])
        before = self._snapshot(scene)
        model = type(self.fixture.env['quality.production.loss.fact'])
        original = model.create
        reached = []

        def fail_after_real_fact(records, values):
            result = original(records, values)
            reached.append(result.ids)
            self.assertEqual(scene['production'].qty_produced, 4)
            self.assertEqual(result.value, 38)
            raise UserError('Injected after real loss fact')

        with patch.object(model, 'create', fail_after_real_fact):
            with self.assertRaisesRegex(UserError, 'Injected after real loss fact'):
                scene['alert'].action_scrap_defective_units()
        self.assertTrue(reached)
        self.assertEqual(self._snapshot(scene), before)

    def test_lost_unit_cannot_be_repaired_or_capitalized_again(self):
        scene = self._scene()
        self._approve(scene['alert'])
        scene['alert'].action_scrap_defective_units()
        before = self._snapshot(scene)
        with self.assertRaises(UserError):
            scene['failed']._assert_not_stock_posted()
        with self.assertRaises(UserError):
            scene['production']._native_partial_release(['qunit:%s' % scene['failed'].id], 'late-output')
        self.assertEqual(self._snapshot(scene), before)
        with self.assertRaises(UserError):
            scene['alert'].write({'check_id': scene['check'].id})
        with self.assertRaises(UserError):
            scene['check']._guard_unit_admission_evidence_edit(scene['failed'].ids)

    def test_employee_rates_and_capitalized_employee_intervals_cannot_drift(self):
        scene = self._scene()
        self._approve(scene['alert'])
        scene['alert'].action_scrap_defective_units()
        timer = scene['orders'][:1].time_ids
        with self.assertRaises(UserError):
            timer.write({'employee_cost': 31})
        with self.assertRaises(UserError):
            timer.write({'employee_id': False})
        with self.assertRaises(UserError):
            timer.unlink()
        # A stored employee_cost recomputation from an hourly-rate change is
        # also an edit of a capitalized timer and must abort the whole change.
        with self.assertRaises(UserError), self.fixture.env.cr.savepoint():
            scene['employee'].hourly_cost = 40
            self.fixture.env.flush_all()

    def test_rollback_only_staging_probe_none_and_lot(self):
        for tracking in ('none', 'lot'):
            result = self.fixture.env['mrp.production']._run_quality_production_loss_probe(self.fixture.env.cr.dbname, tracking)
            self.assertEqual((result['result'], result['rollback']), ('PASS', 'COMPLETE'), result)
