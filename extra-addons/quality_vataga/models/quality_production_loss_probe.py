"""New fixtures only, guaranteed rollback, real Enterprise employee cost/QC."""
import uuid
from datetime import timedelta

from odoo import _, api, models
from odoo.exceptions import AccessError, ValidationError

from .quality_partial_release_probe import _assert, _ProbeRollback


class QualityProductionLossProbe(models.Model):
    _inherit = 'mrp.production'

    @api.model
    def _quality_loss_probe_setup(self, tracking='none', all_pass=False):
        scene = self._quality_partial_probe_setup(tracking, all_pass=all_pass)
        production, orders = scene['production'], scene['orders']
        Timer = self.env['mrp.workcenter.productivity']
        if not {'employee_id', 'employee_cost', 'total_cost'}.issubset(Timer._fields):
            raise ValidationError(_('Тест потребує реального Enterprise-обліку вартості працівника.'))
        employee = self.env['hr.employee'].create({
            'name': 'QLOSS employee ' + uuid.uuid4().hex[:10],
            'company_id': self.env.company.id, 'hourly_cost': 30})
        # Explicit synthetic work, on NEW fixture intervals only. Real native
        # cost methods must then return equipment 60 + employee 30, never zero.
        orders.time_ids.unlink()
        for index, workorder in enumerate(orders):
            start = scene['anchor'] + timedelta(minutes=20 * index)
            values = workorder._prepare_timeline_vals(0, start, start + timedelta(minutes=20))
            values['employee_id'] = employee.id
            Timer.create(values)
        _assert(sum(t.total_cost for t in orders.time_ids) == 30, 'Native employee cost is not 30')
        _assert(sum(wo._cal_cost() for wo in orders) == 90, 'Native equipment + employee cost is not 90')
        production.qty_producing = 5
        production._set_qty_producing()
        production.move_raw_ids.picked = True
        scene['employee'] = employee
        return scene

    @api.model
    def _quality_loss_probe_scenario(self, tracking='none'):
        scene = self._quality_loss_probe_setup(tracking)
        production, alert, check = (scene[n] for n in ('production', 'alert', 'check'))
        good, failed = scene['good'], scene['failed']
        runs = production._full_release_units().run_ids
        execution = scene['orders'].quality_execution_ids
        before = (check.sample_ids.read(['sample_result', 'controlled_unit_id']),
                  runs.read(['state', 'quantity', 'unit_ids', 'workorder_id']),
                  execution.read(['state', 'controlled_unit_id', 'workorder_id', 'ended_at']))
        mos = self.search([]).ids
        account_moves = self.env['account.move'].search([]).ids
        scraps = self.env['stock.scrap'].search([]).ids
        alert.write({'decision_type': 'scrap', 'approval_user_id': self.env.uid})
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        _assert(not alert.production_loss_fact_ids, 'Board approval fabricated a loss fact')
        alert.action_scrap_defective_units()
        release = production._native_partial_history()
        _assert(len(release) == 1 and release.disposition == 'terminal_loss', 'Wrong terminal settlement')
        _assert((release.allocation['total_value'], release.allocation['good_value'], release.allocation['loss_value'])
                == (190, 152, 38), 'Native actual 190 was not allocated as 152 + 38')
        _assert(set(production.native_stock_fact_ids.unit_id.ids) == set(good.ids), 'Good stock identities differ')
        _assert(alert.production_loss_fact_ids.unit_id == failed and not failed.native_stock_fact_ids,
                'Failed QUNIT has no unique loss or was falsely posted as finished stock')
        _assert((production.product_qty, production.qty_produced, production.state) == (5, 4, 'done'),
                'Plan/actual/native closure differs from 5/4/done')
        _assert(self.search([]).ids == mos and self.env['stock.scrap'].search([]).ids == scraps,
                'Unexpected Backorder, replacement MO or Scrap')
        _assert(self.env['account.move'].search([]).ids == account_moves,
                'Manual periodic valuation unexpectedly created a GL entry')
        _assert(before == (check.sample_ids.read(['sample_result', 'controlled_unit_id']),
                  runs.read(['state', 'quantity', 'unit_ids', 'workorder_id']),
                  execution.read(['state', 'controlled_unit_id', 'workorder_id', 'ended_at'])),
                'Historical QC/QRUN/unit execution changed')
        facts = alert.production_loss_fact_ids.ids
        stock_facts = production.native_stock_fact_ids.ids
        alert.action_scrap_defective_units()
        production.button_mark_done()
        _assert(facts == alert.production_loss_fact_ids.ids and stock_facts == production.native_stock_fact_ids.ids,
                'Retry duplicated losses or stock facts')
        alert.action_close_alert()
        _assert(alert._is_managed_closed(), 'Alert did not close from actual loss evidence')
        return {'result': 'PASS', 'tracking': tracking, 'plan': 5, 'good_quantity': 4, 'loss_quantity': 1,
                'component_cost': 100, 'equipment_cost': 60, 'employee_cost': 30,
                'total_cost': 190, 'good_value': 152, 'loss_value': 38,
                'mo_state': production.state, 'backorders': 0, 'scrap_documents': 0,
                'accounting_status': 'manual_periodic_pending', 'gl_entries_created': 0,
                'synthetic_timer_preparation': True, 'shop_floor_timer_start_proven': False}

    @api.model
    def _run_quality_production_loss_probe(self, expected_database, tracking='none'):
        if self.env.cr.dbname != expected_database or not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Потрібні системний адміністратор і точна назва staging-бази.'))
        if tracking not in ('none', 'lot'):
            raise ValidationError(_('Тест підтримує none або lot.'))
        models_to_check = ('mrp.production', 'hr.employee', 'quality.production.unit', 'quality.production.run',
            'quality.check', 'quality.alert', 'stock.move', 'stock.move.line', 'stock.quant', 'stock.scrap',
            'stock.valuation.layer', 'account.move', 'quality.unit.stock.fact',
            'quality.native.partial.release', 'quality.native.partial.cost',
            'quality.native.production.loss', 'quality.production.loss.fact')
        before = {model: self.env[model].search_count([]) for model in models_to_check}
        report = {'result': 'FAIL'}
        try:
            with self.env.cr.savepoint():
                try:
                    report = self._quality_loss_probe_scenario(tracking)
                except Exception as error:
                    report = {'result': 'FAIL', 'error_type': type(error).__name__, 'error': str(error)}
                raise _ProbeRollback()
        except _ProbeRollback:
            self.env.invalidate_all(flush=False)
        after = {model: self.env[model].search_count([]) for model in models_to_check}
        if before != after:
            raise ValidationError(_('Не підтверджено повний відкат тесту виробничих втрат.'))
        report.update(database=self.env.cr.dbname, rollback='COMPLETE')
        return report
