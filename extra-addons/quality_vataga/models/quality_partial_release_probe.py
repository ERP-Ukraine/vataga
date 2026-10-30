"""Rollback-only Enterprise acceptance fixture for one staging Scheduled Action.

Explicit synthetic timer preparation is reported. No existing MO/QCP/user is
modified. The server helper owns exception handling; the safe_eval snippet
contains only a call and one log(). This is NOT run by module installation.
"""
from datetime import timedelta
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class _ProbeRollback(Exception):
    pass


def _assert(condition, message):
    if not condition:
        raise ValidationError(message)


class QualityPartialProbe(models.Model):
    _inherit = 'mrp.production'

    @api.model
    def _quality_partial_probe_timer(self, workorder):
        # Test setup for Enterprise _should_start_timer() == False. A real
        # open interval is still required by the shipped WO completion gate.
        if not workorder.time_ids.filtered(lambda t: t.user_id == self.env.user and not t.date_end):
            self.env['mrp.workcenter.productivity'].create(
                workorder._prepare_timeline_vals(0, fields.Datetime.now()))

    @api.model
    def _quality_partial_probe_closed_time(self, workorder, minutes, anchor):
        capitalized = workorder.production_id._native_partial_history().cost_ids.filtered(
            lambda c: c.workorder_id == workorder).timer_ids
        editable = workorder.time_ids - capitalized
        start = max(capitalized.mapped('date_end')) if capitalized else anchor
        end = start + timedelta(minutes=minutes)
        if editable:
            editable.write({'date_start': start, 'date_end': end})
        else:
            self.env['mrp.workcenter.productivity'].create(workorder._prepare_timeline_vals(0, start, end))

    @api.model
    def _quality_partial_probe_setup(self, tracking='none'):
        if 'workorder_id' not in self.env['quality.check']._fields:
            raise UserError(_('Для тесту потрібна справжня Enterprise-інтеграція QC з Work Orders.'))
        suffix = uuid.uuid4().hex[:12]
        category = self.env['product.category'].create({'name': 'QPART FIFO ' + suffix,
            'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
        product = self.env['product.product'].create({'name': 'QPART finished ' + suffix, 'type': 'product',
            'tracking': tracking, 'categ_id': category.id})
        component = self.env['product.product'].create({'name': 'QPART component ' + suffix, 'type': 'product',
            'categ_id': category.id, 'standard_price': 10})
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1)
        if not warehouse or warehouse.manufacture_steps != 'mrp_one_step':
            raise UserError(_('Потрібен доступний склад з однокроковим виробництвом. Наявні налаштування тест не змінює.'))
        centers = self.env['mrp.workcenter'].create([
            {'name': 'QPART WO%s ' % i + suffix, 'costs_hour': 60} for i in (1, 2, 3)])
        bom = self.env['mrp.bom'].create({'product_tmpl_id': product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': product.uom_id.id,
            'bom_line_ids': [(0, 0, {'product_id': component.id, 'product_qty': 2})],
            'operation_ids': [(0, 0, {'name': 'QPART operation %s' % i, 'sequence': i * 10,
                                     'workcenter_id': center.id, 'time_cycle': 12})
                              for i, center in enumerate(centers, 1)]})
        team = self.env['quality.alert.team'].create({'name': 'QPART ' + suffix})
        test_type = self.env['quality.point.test_type'].search([('technical_name', '=', 'passfail')], limit=1)
        if not test_type:
            raise UserError(_('Не знайдено штатний Enterprise тип контролю passfail.'))
        values = {'title': 'QPART WO2 ' + suffix, 'team_id': team.id, 'test_type_id': test_type.id,
                  'x_control_stage': 'in_process', 'quality_unit_route_required': True,
                  'visual_sample_control_required': True,
                  'picking_type_ids': [(6, 0, warehouse.manu_type_id.ids)]}
        Point = self.env['quality.point']
        operation_fields = [(n, f) for n, f in Point._fields.items()
                            if getattr(f, 'comodel_name', None) == 'mrp.routing.workcenter'
                            and f.type in ('many2one', 'many2many') and not f.compute]
        if not operation_fields:
            raise UserError(_('Не знайдено реальне поле призначення QCP до операції.'))
        operation_name, operation_field = operation_fields[0]
        operation = bom.operation_ids.sorted('sequence')[1]
        values[operation_name] = operation.id if operation_field.type == 'many2one' else [(6, 0, operation.ids)]
        if 'product_ids' in Point._fields:
            values['product_ids'] = [(6, 0, product.ids)]
        if 'company_id' in Point._fields:
            values['company_id'] = self.env.company.id
        point = Point.create(values)
        receipt = self.env['stock.move'].create({'name': 'QPART receipt ' + suffix, 'product_id': component.id,
            'product_uom': component.uom_id.id, 'product_uom_qty': 20, 'price_unit': 10,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': warehouse.lot_stock_id.id, 'company_id': self.env.company.id})
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 20, 'picked': True})
        receipt._action_done()
        production = self.create({'product_id': product.id, 'product_qty': 5,
            'product_uom_id': product.uom_id.id, 'bom_id': bom.id,
            'picking_type_id': warehouse.manu_type_id.id,
            'location_src_id': warehouse.lot_stock_id.id, 'location_dest_id': warehouse.lot_stock_id.id})
        production.action_confirm()
        production.action_assign()
        production.write({'qty_producing': 5, 'allow_workorder_dependencies': True})
        orders = production.workorder_ids.sorted('id')
        _assert(len(orders) == 3, 'Expected three real native workorders')
        orders[1].blocked_by_workorder_ids = orders[0]
        orders[2].blocked_by_workorder_ids = orders[1]
        if tracking == 'lot':
            production.lot_producing_id = self.env['stock.lot'].create({
                'name': 'QPART lot ' + suffix, 'product_id': product.id, 'company_id': self.env.company.id})
        anchor = fields.Datetime.now().replace(microsecond=0) - timedelta(hours=3)
        orders[0].button_start()
        self._quality_partial_probe_timer(orders[0])
        orders[0].button_finish()
        self._quality_partial_probe_closed_time(orders[0], 20, anchor)
        check = self.env['quality.check'].search([('point_id', '=', point.id),
            ('workorder_id', '=', orders[1].id), ('production_id', '=', production.id)], limit=1)
        if not check:
            check = self.env['quality.check'].create({'point_id': point.id, 'team_id': team.id,
                'test_type_id': test_type.id, 'product_id': product.id, 'production_id': production.id,
                'workorder_id': orders[1].id, 'measure_on': 'product'})
        orders[1].button_start(bypass=True)
        self._quality_partial_probe_timer(orders[1])
        check.initialize_qc_unit_identity()
        _assert(len(check.sample_ids) == 5, 'Expected five identified real QC samples')
        for sample in check.sample_ids:
            check.update_measurement_visual_result(sample.id, 'yes')
        check.update_measurement_visual_result(check.sample_ids.sorted('sample_number')[-1].id, 'no')
        check.do_fail()
        orders[1].button_finish()
        self._quality_partial_probe_closed_time(orders[1], 20, anchor + timedelta(minutes=30))
        alert = self.env['quality.alert'].create({'name': 'QPART held fifth ' + suffix,
            'check_id': check.id, 'team_id': team.id})
        orders[2].button_start()
        self._quality_partial_probe_timer(orders[2])
        orders[2].button_finish()
        self._quality_partial_probe_closed_time(orders[2], 20, anchor + timedelta(hours=1))
        good = check.sample_ids.filtered(lambda s: s.sample_result == 'pass').controlled_unit_id
        failed = check.sample_ids.filtered(lambda s: s.sample_result == 'fail').controlled_unit_id
        _assert(len(good) == 4 and len(failed) == 1 and orders[1].qty_produced == 5
                and orders[2].qty_produced == 4, 'Native WO2/WO3 did not confirm the exact 5/4 identities')
        return {'production': production, 'orders': orders, 'check': check, 'alert': alert,
                'good': good, 'failed': failed, 'anchor': anchor, 'component': component}

    @api.model
    def _quality_partial_probe_scenario(self, tracking='none'):
        scene = self._quality_partial_probe_setup(tracking)
        production, orders, check, alert = (scene[n] for n in ('production', 'orders', 'check', 'alert'))
        good, failed = scene['good'], scene['failed']
        all_mos = self.search([]).ids
        original_qc = check.sample_ids.read(['sample_result', 'controlled_unit_id'])
        executions = orders.quality_execution_ids
        old_executions = executions.read(['state', 'controlled_unit_id', 'workorder_id', 'ended_at'])
        first = production._quality_partial_release()
        _assert(len(first.unit_ids) == 4 and set(production.native_stock_fact_ids.unit_id.ids) == set(good.ids),
                'First release did not record exactly four real QUNIT facts')
        _assert(not failed.native_stock_fact_ids and production.state not in ('done', 'cancel'),
                'Held fifth was posted or original MO closed')
        first_facts = production.native_stock_fact_ids
        fact_fields = ['unit_id', 'move_line_id', 'request_key']
        fact_snapshot = first_facts.read(fact_fields)
        old_lines = first_facts.move_line_id
        line_snapshot = old_lines.read(['quantity', 'consume_line_ids', 'lot_id'])
        old_svl = old_lines.move_id.stock_valuation_layer_ids
        svl_snapshot = old_svl.read(['quantity', 'value', 'remaining_qty', 'remaining_value'])
        _assert(sum(old_svl.mapped('value')) == 140, 'First native finished value is not 140')
        # Retry with fifth still held: denial is valid; stock and audit stay fixed.
        try:
            production._quality_partial_release()
        except UserError:
            pass
        _assert(len(production._native_partial_history()) == 1, 'Retry duplicated stock posting')
        alert.write({'decision_type': 'repair', 'approval_user_id': self.env.uid})
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        alert.action_record_repair('QPART: temporary acceptance fixture repair of the held fifth')
        action = alert.action_create_repair_check()
        repeat = self.env['quality.check'].browse(action['res_id'])
        _assert(repeat.sample_ids.controlled_unit_id == failed, 'Repair QC changed physical identity')
        for sample in repeat.sample_ids:
            repeat.update_measurement_visual_result(sample.id, 'yes')
        repeat.do_pass()
        alert.action_close_alert()
        _assert(alert._is_managed_closed(), 'Repair Alert did not close through its normal workflow')
        orders[1].button_finish()
        self._quality_partial_probe_closed_time(orders[1], 10, scene['anchor'])
        orders[2].button_start()
        self._quality_partial_probe_timer(orders[2])
        _assert(orders[2].quality_native_unit_ids == failed, 'WO3 selected released QUNIT again')
        orders[2].button_finish()
        self._quality_partial_probe_closed_time(orders[2], 5, scene['anchor'])
        second = production._quality_partial_release()
        _assert(len(second.unit_ids) == 1 and len(production.native_stock_fact_ids) == 5,
                'Last QUNIT did not receive its one real stock fact')
        _assert(sum(second.unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')) == 35,
                'Second native finished value is not 35')
        _assert(first_facts.read(fact_fields) == fact_snapshot
                and old_lines.read(['quantity', 'consume_line_ids', 'lot_id']) == line_snapshot
                and old_svl.read(['quantity', 'value', 'remaining_qty', 'remaining_value']) == svl_snapshot,
                'First four stock facts, component links or SVL were changed')
        _assert(check.quality_state == 'fail' and check.sample_ids.read(['sample_result', 'controlled_unit_id']) == original_qc,
                'Original mixed QC history changed')
        _assert(executions.read(['state', 'controlled_unit_id', 'workorder_id', 'ended_at']) == old_executions,
                'Old unit executions changed')
        _assert(production.qty_produced == 5 and production.state == 'done' and production.date_finished
                and production.is_locked and not orders.filtered(lambda wo: wo.state != 'done')
                and self.search([]).ids == all_mos,
                'MO did not finish natively without Backorder')
        _assert(len(production._native_partial_history()) == 2, 'Unexpected number of native postings')
        production.button_mark_done()
        _assert(len(production._native_partial_history()) == 2, 'Final native button reposted Stock')
        quants = self.env['stock.quant'].search([('product_id', '=', production.product_id.id),
            ('location_id', '=', production.location_dest_id.id)])
        _assert(sum(quants.mapped('quantity')) == 5, 'Physical destination quantity is not five')
        return {'result': 'PASS', 'first_value': 140, 'second_value': 35, 'total_value': 175,
                'q_units': production.native_stock_fact_ids.unit_id.ids, 'backorders': 0,
                'old_stock_unchanged': True, 'old_qc_unchanged': True, 'old_executions_unchanged': True,
                'mo_state': production.state, 'tracking': tracking,
                'synthetic_timer_preparation': True, 'shop_floor_timer_start_proven': False}

    @api.model
    def _run_quality_partial_release_probe(self, expected_database, tracking='none'):
        """One administrator-triggered, guaranteed-rollback staging probe."""
        if self.env.cr.dbname != expected_database or not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Потрібні системний адміністратор і точна назва тестової staging-бази.'))
        if tracking not in ('none', 'lot'):
            raise ValidationError(_('Тест підтримує лише none або lot.'))
        report = {'result': 'FAIL'}
        before = {model: self.env[model].search_count([]) for model in (
            'mrp.production', 'quality.production.unit', 'quality.check', 'quality.alert',
            'stock.move', 'stock.move.line', 'stock.valuation.layer', 'quality.unit.stock.fact',
            'quality.native.partial.release', 'quality.native.partial.cost')}
        try:
            with self.env.cr.savepoint():
                try:
                    report = self._quality_partial_probe_scenario(tracking)
                except Exception as error:
                    report = {'result': 'FAIL', 'error_type': type(error).__name__, 'error': str(error)}
                raise _ProbeRollback()
        except _ProbeRollback:
            self.env.invalidate_all(flush=False)
        after = {model: self.env[model].search_count([]) for model in before}
        report['rollback'] = 'COMPLETE' if before == after else 'COUNT_MISMATCH'
        if before != after:
            # Also abort the caller's transaction; never commit a failed cleanup.
            raise ValidationError(_('Не підтверджено повний відкат тестових даних.'))
        report['database'] = self.env.cr.dbname
        return report
