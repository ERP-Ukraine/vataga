"""Rollback-only native employee/analytic audit. NEVER authorizes cost providers.

No PartialPosting capability, price override, Stock posting or fake checkpoint
is used. The comparison reference is in memory, not a booked cost document.
Real immutable-checkpoint and 4+1 acceptance remain gated until Enterprise QA.
"""
import json
import math
import uuid
from datetime import timedelta
from types import SimpleNamespace

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .native_partial_release import _CORE_COST, _read


class _AuditRollback(Exception):
    def __init__(self, value=None):
        self.value = value


def _assert(condition, message):
    if not condition:
        raise ValidationError(message)


class NativeEmployeeCostAudit(models.Model):
    _inherit = 'mrp.production'

    @api.model
    def _native_employee_audit_setup(self):
        """Only fresh fixture rows. Reuse an existing analytic plan read-only.

        Creating a new plan can change the registry/schema; a savepoint does
        not restore Python registry fields. This probe must not do that.
        """
        if not {'employee_cost', 'employee_id', 'total_cost'}.issubset(self.env['mrp.workcenter.productivity']._fields):
            raise ValidationError(_('Потрібні справжні Enterprise-поля вартості працівників.'))
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM account_analytic_line')
        previous_analytic_id = self.env.cr.fetchone()[0]
        suffix = 'QCOST-' + uuid.uuid4().hex[:12]
        plan = _read(self.env.ref('analytic.analytic_plan_projects'))
        accounts = self.env['account.analytic.account'].create([
            {'name': suffix + ' ' + label, 'plan_id': plan.id, 'company_id': self.env.company.id}
            for label in ('MO', 'WC')])
        center = self.env['mrp.workcenter'].create({
            'name': suffix, 'costs_hour': 60, 'employee_costs_hour': 50,
            'analytic_distribution': {str(accounts[1].id): 100}})
        employees = self.env['hr.employee'].create([
            {'name': suffix + ' A', 'company_id': self.env.company.id, 'hourly_cost': 20},
            {'name': suffix + ' B', 'company_id': self.env.company.id, 'hourly_cost': 40}])
        category = self.env['product.category'].create({
            'name': suffix, 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
        products = self.env['product.product'].create([
            {'name': suffix + ' finished', 'type': 'product', 'categ_id': category.id},
            {'name': suffix + ' component', 'type': 'product', 'categ_id': category.id, 'standard_price': 10}])
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': products[0].product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': products[0].uom_id.id,
            'bom_line_ids': [(0, 0, {'product_id': products[1].id, 'product_qty': 2})],
            'operation_ids': [(0, 0, {'name': suffix, 'workcenter_id': center.id, 'time_cycle': 12})]})
        warehouse = _read(self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1))
        if not warehouse:
            raise ValidationError(_('Немає доступного складу цієї компанії.'))
        production = self.create({
            'name': suffix, 'product_id': products[0].id, 'product_qty': 5,
            'product_uom_id': products[0].uom_id.id, 'bom_id': bom.id,
            'picking_type_id': warehouse.manu_type_id.id,
            'location_src_id': warehouse.lot_stock_id.id, 'location_dest_id': warehouse.lot_stock_id.id,
            'analytic_distribution': {str(accounts[0].id): 100}})
        production.action_confirm()
        workorder = production.workorder_ids
        workorder.ensure_one()
        loss_type = self.env['mrp.workcenter.productivity.loss.type'].create({'loss_type': 'productive'})
        loss = self.env['mrp.workcenter.productivity.loss'].create({
            'name': suffix, 'loss_id': loss_type.id, 'manual': False})
        scene = {'production': production, 'workorder': workorder, 'center': center,
                 'employees': employees, 'accounts': accounts, 'products': products, 'loss': loss,
                 'previous_analytic_id': previous_analytic_id,
                 'anchor': fields.Datetime.now().replace(microsecond=0) - timedelta(hours=6)}
        scene['timers'] = (self._native_employee_audit_timer(scene, 0, 30, employees[0])
                           | self._native_employee_audit_timer(scene, 30, 60, employees[1]))
        self.env.flush_all()
        return scene

    @api.model
    def _native_employee_audit_timer(self, scene, start_minutes, end_minutes, employee):
        return self.env['mrp.workcenter.productivity'].create({
            'workorder_id': scene['workorder'].id, 'workcenter_id': scene['center'].id,
            'company_id': self.env.company.id, 'user_id': False, 'employee_id': employee.id or False,
            'loss_id': scene['loss'].id, 'date_start': scene['anchor'] + timedelta(minutes=start_minutes),
            'date_end': scene['anchor'] + timedelta(minutes=end_minutes)})

    @api.model
    def _native_employee_audit_analytics(self, scene):
        self.env.flush_all()
        wo, production = scene['workorder'], scene['production']
        Line = self.env['account.analytic.line']
        account_fields = [name for name, field in Line._fields.items()
                          if field.type == 'many2one' and field.comodel_name == 'account.analytic.account'
                          and field.store and field.column_type]
        # Discover hidden fixture lines, then enforce ordinary ACL/rules.
        clauses, params = ['ref=%s'], [production.name]
        for name in account_fields:
            clauses.append('"' + name.replace('"', '""') + '" IN %s')
            params.append(tuple(scene['accounts'].ids))
        Line.flush_model()
        self.env.cr.execute('SELECT id FROM account_analytic_line WHERE ' + ' OR '.join(clauses) + ' ORDER BY id', params)
        line_ids = {row[0] for row in self.env.cr.fetchall()}
        relations = {}
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        for name, field in wo._fields.items():
            if field.type not in ('one2many', 'many2many') or field.comodel_name != 'account.analytic.line':
                continue
            if field.type == 'many2many' and field.store and field.relation:
                self.env.cr.execute('SELECT ' + quote(field.column2) + ' FROM ' + quote(field.relation)
                                    + ' WHERE ' + quote(field.column1) + '=%s', [wo.id])
                related_ids = [row[0] for row in self.env.cr.fetchall()]
            elif field.type == 'one2many' and field.inverse_name:
                self.env.cr.execute('SELECT id FROM account_analytic_line WHERE '
                                    + quote(field.inverse_name) + '=%s', [wo.id])
                related_ids = [row[0] for row in self.env.cr.fetchall()]
            else:
                related_ids = wo[name].ids
            relations[name] = sorted(related_ids)
            line_ids.update(related_ids)
        _assert(all(line_id > scene['previous_analytic_id'] for line_id in line_ids),
                'Audit fixture unexpectedly links an existing analytic line')
        lines = _read(Line.browse(sorted(line_ids)))
        columns = ['amount', 'unit_amount', 'ref', 'company_id', 'product_id', 'product_uom_id'] + account_fields
        columns += [name for name in ('employee_id', 'user_id') if name in Line._fields]
        return {'analytic_lines': lines.read(columns), 'analytic_relations': relations}

    @api.model
    def _native_employee_audit_state(self, scene):
        analytics = self._native_employee_audit_analytics(scene)
        wo, production = scene['workorder'], scene['production']
        return {**analytics, 'native_cost': wo._cal_cost(), 'equipment_cost': _CORE_COST(wo),
                'employee_cost': sum(wo.time_ids.mapped('total_cost')),
                'duration': wo.duration, 'basis': wo._native_partial_cost_inputs(),
                'mo': production.read(['state', 'qty_producing', 'qty_produced', 'quality_native_partial_revision'])}

    @api.model
    def _native_employee_audit_case(self, scene, name, callback):
        before = self._native_employee_audit_state(scene)
        result = {'name': name, 'result': 'FAIL'}
        try:
            with self.env.cr.savepoint():
                detail = callback()
                self.env.flush_all()
                raise _AuditRollback(detail)
        except _AuditRollback as rollback:
            result.update(result='PASS', detail=rollback.value)
        except Exception as error:
            result.update(error_type=type(error).__name__, error=str(error))
        self.env.invalidate_all(flush=False)
        _assert(self._native_employee_audit_state(scene) == before, 'Nested cost/analytic case did not roll back: ' + name)
        return result

    @api.model
    def _native_employee_audit_scenario(self, scene):
        wo = scene['workorder']
        baseline = self._native_employee_audit_state(scene)
        _assert(math.isclose(baseline['equipment_cost'], 60) and math.isclose(baseline['employee_cost'], 30)
                and math.isclose(baseline['native_cost'], 90), 'Native two-employee cost is not 60 + 30 = 90')
        _assert(baseline['analytic_lines'], 'Analytic distributions did not create real analytic lines')
        # In-memory captured reference, NOT an immutable booked cost document.
        reference = SimpleNamespace(basis=json.loads(json.dumps(baseline['basis'])), cumulative_cost=90)
        checks = []

        def stable_analytics():
            wo._compute_duration()
            first = self._native_employee_audit_state(scene)
            wo._compute_duration()
            second = self._native_employee_audit_state(scene)
            _assert(baseline == first == second,
                    'Analytic lines or quantities are stale until an extra duration recomputation')
            return {'before': baseline['analytic_lines'], 'after': first['analytic_lines']}
        checks.append(self._native_employee_audit_case(scene, 'repeat_duration_analytics', stable_analytics))

        def new_work():
            self._native_employee_audit_timer(scene, 60, 75, scene['employees'][1])
            first = self._native_employee_audit_state(scene)
            _assert(math.isclose(first['native_cost'], 115), 'New native 15-minute work did not add 15 WC + 10 employee')
            wo._native_partial_check_cost_values(first['basis'], reference, first['native_cost'])
            wo._compute_duration()
            _assert(self._native_employee_audit_state(scene) == first,
                    'Analytic values are stale after new work and change on an extra duration recomputation')
            return {'cumulative': 115, 'delta': 25, 'analytics': first['analytic_lines']}
        checks.append(self._native_employee_audit_case(scene, 'new_closed_work_and_analytics', new_work))

        def overlap():
            self._native_employee_audit_timer(scene, 15, 45, scene['employees'][0])
            state = self._native_employee_audit_state(scene)
            _assert(math.isclose(state['equipment_cost'], 60) and math.isclose(state['employee_cost'], 40)
                    and math.isclose(state['native_cost'], 100), 'Overlapping WC union / employee sum differ')
            try:
                wo._native_partial_check_cost_values(state['basis'], reference, state['native_cost'])
            except ValidationError:
                return {'native_total': 100, 'checkpoint_rejects_retroactive_work': True}
            raise ValidationError('Retroactive overlapping work passed the captured checkpoint comparison')
        checks.append(self._native_employee_audit_case(scene, 'overlap_wc_union_employee_sum', overlap))

        def fallback():
            self._native_employee_audit_timer(scene, 60, 75, self.env['hr.employee'])
            state = self._native_employee_audit_state(scene)
            _assert(math.isclose(state['native_cost'], 117.5), 'WC employee fallback did not add 15 + 12.5')
            wo._native_partial_check_cost_values(state['basis'], reference, state['native_cost'])
            return {'cumulative': 117.5, 'fallback_rate': 50, 'duration': state['duration'],
                    'analytics': state['analytic_lines']}
        checks.append(self._native_employee_audit_case(scene, 'employee_fallback_rate', fallback))

        def native_conversion():
            category = self.env['mrp.workcenter.productivity.loss.type'].create({'loss_type': 'quality'})
            reason = self.env['mrp.workcenter.productivity.loss'].create({
                'name': 'QCOST native conversion ' + uuid.uuid4().hex[:8], 'loss_id': category.id})
            timer = self._native_employee_audit_timer(dict(scene, loss=reason), 60, 90, scene['employees'][1])
            self.env.flush_all()
            expected = reason._convert_to_duration(timer.date_start, timer.date_end, scene['center'])
            _assert(math.isclose(timer.duration, expected)
                    and math.isclose(timer.total_cost, timer.employee_cost * expected / 60),
                    'Employee costing bypassed the native calendar/loss duration conversion')
            return {'native_converted_minutes': expected, 'wall_clock_minutes': 30,
                    'duration_was_not_overridden': True}
        checks.append(self._native_employee_audit_case(scene, 'native_loss_duration_conversion', native_conversion))

        def actor_mapping():
            employee = _read(self.env.user.employee_id)
            if not employee:
                return {'exercised': False, 'reason': 'The actor has no employee; no existing user was modified'}
            timer = self.env['mrp.workcenter.productivity'].create({
                'workorder_id': wo.id, 'workcenter_id': scene['center'].id,
                'company_id': self.env.company.id, 'user_id': self.env.uid,
                'loss_id': scene['loss'].id, 'date_start': scene['anchor'] + timedelta(minutes=90),
                'date_end': scene['anchor'] + timedelta(minutes=105)})
            self.env.flush_all()
            _assert(timer.employee_id == employee, 'Native user-to-employee mapping differs from the supplied source')
            return {'exercised': True, 'native_user_employee_mapping_matches': True}
        checks.append(self._native_employee_audit_case(scene, 'native_user_employee_mapping', actor_mapping))

        changes = {
            'hourly_rate': lambda: scene['employees'][0].write({'hourly_cost': 30}),
            'compensating_rates_same_total': lambda: (scene['employees'][0].write({'hourly_cost': 30}),
                                                      scene['employees'][1].write({'hourly_cost': 30})),
            'employee_assignment': lambda: scene['timers'][0].write({'employee_id': scene['employees'][1].id}),
            'interval_duration': lambda: scene['timers'][0].write({'date_end': scene['anchor'] + timedelta(minutes=40)}),
            'effective_employee_cost': lambda: scene['timers'][0].write({'employee_cost': 25}),
            'loss_category': lambda: scene['loss'].loss_id.write({'loss_type': 'performance'}),
        }
        for name, change in changes.items():
            def changed_inputs(change=change, name=name):
                change()
                state = self._native_employee_audit_state(scene)
                if name == 'compensating_rates_same_total':
                    _assert(math.isclose(state['native_cost'], 90), 'Compensating rates did not preserve the native total')
                try:
                    wo._native_partial_check_cost_values(state['basis'], reference, state['native_cost'])
                except ValidationError:
                    return {'native_cost': state['native_cost'], 'duration': state['duration'],
                            'analytics': state['analytic_lines'], 'captured_basis_rejects_change': True}
                raise ValidationError('Changed previously captured cost inputs were accepted')
            checks.append(self._native_employee_audit_case(scene, name, changed_inputs))

        def rollback_analytics():
            scene['timers'][0].write({'date_end': scene['anchor'] + timedelta(minutes=40)})
            self.env.flush_all()
            _assert(self._native_employee_audit_state(scene) != baseline, 'Fault-injection case did not change real ORM state')
            raise UserError('EXPECTED_EMPLOYEE_ANALYTIC_ROLLBACK')
        fault = self._native_employee_audit_case(scene, 'injected_error_rollback', rollback_analytics)
        if fault.get('error') == 'EXPECTED_EMPLOYEE_ANALYTIC_ROLLBACK':
            fault.update(result='PASS', injected_error=True)
        checks.append(fault)

        def provider_gate():
            try:
                wo._native_partial_cost_basis()
            except ValidationError as error:
                _assert('MrpProductionWorkcenterLine._cal_cost' in str(error), 'Provider gate failed for an unrelated reason')
                return {'production_provider_authorized': False}
            raise ValidationError('Pending Enterprise provider was unexpectedly authorized')
        checks.append(self._native_employee_audit_case(scene, 'production_gate_remains_closed', provider_gate))
        return {'checks': checks, 'baseline': baseline,
                'result': 'AUDIT_PASS' if all(c['result'] == 'PASS' for c in checks) else 'FAIL'}

    @api.model
    def _run_native_employee_cost_audit(self, expected_database):
        if self.env.cr.dbname != expected_database or not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Потрібні системний адміністратор і точна назва staging-бази.'))
        sources = self._native_partial_cost_provider_report(expected_database, include_source=True)
        report = {'result': 'FAIL', 'scope': 'native_employee_cost_and_analytics_only',
                  'production_provider_authorized': False, 'stock_posting_exercised': False,
                  'persisted_checkpoint_exercised': False, 'terminal_loss_flow_proven': False,
                  'sources': sources}
        candidate = sources['enterprise_cost_candidate']
        unapproved = [row for row in sources['mrp.workorder._cal_cost'] if not row['approved']]
        if (len(candidate) != 1 or not candidate[0]['source_matches'] or len(unapproved) != 1
                or unapproved[0]['provider'] != candidate[0]['provider']):
            report.update(error='The exact reported Enterprise cost source/MRO is not present', rollback='NO_FIXTURE_CREATED')
            return report
        names = ('mrp.production', 'mrp.workorder', 'mrp.workcenter', 'mrp.workcenter.productivity',
            'mrp.workcenter.productivity.loss', 'mrp.workcenter.productivity.loss.type', 'mrp.bom',
            'product.product', 'hr.employee', 'account.analytic.account', 'account.analytic.line',
            'stock.move', 'stock.move.line', 'stock.quant', 'stock.valuation.layer', 'account.move',
            'quality.native.partial.release', 'quality.native.partial.cost')
        # Own transaction: finally rolls it back and clears its postcommit
        # callbacks as well. Caller transaction receives only the plain report.
        context = {'allowed_company_ids': self.env.companies.ids,
                   'lang': self.env.lang, 'tz': self.env.user.tz}
        with self.env.registry.cursor() as audit_cr:
            env = api.Environment(audit_cr, self.env.uid, context)
            audit = env[self._name].with_company(self.env.company.id)
            try:
                before = {name: env[name].search_count([]) for name in names}
                try:
                    with audit_cr.savepoint():
                        scene = audit._native_employee_audit_setup()
                        report.update(audit._native_employee_audit_scenario(scene))
                        env.flush_all()
                        raise _AuditRollback()
                except _AuditRollback:
                    pass
                except Exception as error:
                    report.update(result='FAIL', error_type=type(error).__name__, error=str(error))
                env.invalidate_all(flush=False)
                if before != {name: env[name].search_count([]) for name in names}:
                    raise ValidationError(_('Не підтверджено відкат аудиту працівників та аналітики.'))
                report['rollback'] = 'COMPLETE'
            finally:
                audit_cr.rollback()
        return report
