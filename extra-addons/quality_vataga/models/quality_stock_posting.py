"""Real QUNIT references and the deliberately closed Enterprise bridge."""

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError

from .stock_posting_core import access, production_sources, CORE_CONTEXT, CORE_TOKEN
from .stock_bridge_authority import permits, posting_authority


def unit_key(unit):
    return 'qunit:%s' % unit.id


class QualityStockCostScope(models.Model):
    _inherit = 'quality.stock.cost.scope'

    unit_ids = fields.Many2many('quality.production.unit', 'quality_stock_cost_scope_unit_rel',
                               'scope_id', 'unit_id', string='Зафіксовані QUNIT', readonly=True)

    @api.model
    def _employee_cost_mode(self, timer):
        return bool(any(name in timer._fields and timer[name] for name in ('employee_id', 'employee_ids'))
                    or 'employee_ids' in timer.workorder_id._fields and timer.workorder_id.employee_ids)

    @api.model
    def _timer_facts(self, timers, members):
        facts = super()._timer_facts(timers, members)
        for row, timer in zip(facts, timers):
            row['employee_cost_mode'] = self._employee_cost_mode(timer)
        return facts

    @api.model
    def _identity_values(self, production, keys):
        units = self.env['quality.production.unit']._release_records(
            'quality.production.unit', 'production_id = %s', [production.id],
        )
        if (set(keys) != {unit_key(unit) for unit in units}
                or any(unit.company_id != production.company_id or unit.product_id != production.product_id for unit in units)):
            raise ValidationError(_('Розподіл потребує повного справжнього поіменного складу MO.'))
        return {'unit_ids': [Command.set(units.ids)]}

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT unit_id FROM quality_stock_cost_scope_unit_rel WHERE scope_id IN %s', [tuple(self.ids)])
            access(self.env['quality.production.unit'].browse([row[0] for row in self.env.cr.fetchall()]))
        return result

    @api.model
    def _freeze_from_executions(self, production, explicit_shared_scopes, selected=None, scope_key=None):
        """Private, audited policy; untagged first-operation time is explicit.

        explicit_shared_scopes maps actual timer IDs to actual unit records.
        It is not accepted by any public RPC endpoint or client context.
        """
        production._check_unit_stock_release_access()
        units = self.env['quality.production.unit']._release_records(
            'quality.production.unit', 'production_id = %s', [production.id],
        )
        members = {}
        bindings = self.env['quality.stock.timer.binding']._for_production(production)
        for timer in self._timers(production):
            batch = timer.quality_execution_batch_id
            if batch:
                access(batch); access(batch.execution_ids)
                if (timer.id in explicit_shared_scopes or batch.workorder_id != timer.workorder_id
                        or batch.execution_ids.filtered(lambda row: row.workorder_id != timer.workorder_id
                            or row.state not in ('progress', 'processed', 'stopped')
                            or selected is None and row.state != 'processed')):
                    raise ValidationError(_('Інтервал виконання потребує завершених QUNIT та незмінного складу запуску.'))
                scoped = batch.execution_ids.controlled_unit_id
                if len(scoped) != len(batch.execution_ids):
                    raise ValidationError(_('Неоднозначний склад виконання виробів.'))
            else:
                binding = bindings.filtered(lambda row: row.timer_id == timer)
                scoped = explicit_shared_scopes.get(timer.id, binding.unit_ids)
                access(scoped)
            if not scoped or scoped - units:
                raise ValidationError(_('Спільний інтервал потребує явно підтверджених QUNIT саме цього MO.'))
            if self._employee_cost_mode(timer) and (selected is None or scoped & selected):
                raise ValidationError(_('Інтервали Shop Floor з працівниками потребують окремо перевіреного розподілу вартості праці. Їх не можна оцінити лише тарифом робочого центру.'))
            members[timer.id] = [unit_key(unit) for unit in scoped]
        if set(explicit_shared_scopes) - set(members):
            raise ValidationError(_('Передано сторонні робочі інтервали.'))
        if selected is None:
            return self._freeze(production, [unit_key(unit) for unit in units], members)
        access(selected)
        if selected - units:
            raise ValidationError(_('Вибрані вироби не належать MO.'))
        return self._freeze_incremental(production, [unit_key(unit) for unit in units], members,
                                        [unit_key(unit) for unit in selected], scope_key)


class QualityStockPosting(models.Model):
    _inherit = 'quality.stock.posting'

    unit_id = fields.Many2one('quality.production.unit', required=True, readonly=True, ondelete='restrict', index=True)
    permission_id = fields.Many2one('quality.unit.release.line', required=True, readonly=True, ondelete='restrict')
    plan_id = fields.Many2one('quality.unit.stock.release.plan', required=True, readonly=True, ondelete='restrict')
    _sql_constraints = [
        ('qunit_unique', 'unique(unit_id)', 'QUNIT вже має фактичний випуск.'),
        ('permission_id_unique', 'unique(permission_id)', 'Дозвіл 13.4А вже використано.'),
    ]

    @api.model
    def _protected_sources(self):
        return dict(super()._protected_sources(), unit_id='quality.production.unit',
                    permission_id='quality.unit.release.line', plan_id='quality.unit.stock.release.plan')

    @api.constrains('unit_id', 'permission_id', 'plan_id', 'production_id', 'unit_key', 'permission_key')
    def _check_qunit_identity(self):
        for row in self:
            access(row.unit_id); access(row.permission_id); access(row.plan_id)
            if (row.unit_id.production_id != row.production_id or row.permission_id.unit_id != row.unit_id
                    or row.plan_id.production_id != row.production_id or row.permission_id not in row.plan_id.permission_ids
                    or row.unit_key != unit_key(row.unit_id) or row.permission_key != 'permission:%s' % row.permission_id.id):
                raise ValidationError(_('Фактичний випуск, QUNIT, дозвіл та план повинні однозначно відповідати одне одному.'))

    @api.model
    def _posting_identity_values(self, scope, key, permission_key, plan):
        """Bind real records BEFORE stock changes; the bridge gate stays shut."""
        if not plan or plan._name != 'quality.unit.stock.release.plan':
            raise ValidationError(_('Потрібен справжній план підготовки 13.4Б.'))
        plan.ensure_one()
        access(plan)
        candidates = plan.permission_ids.filtered(lambda row: 'permission:%s' % row.id == permission_key)
        access(candidates)
        if (len(candidates) != 1 or unit_key(candidates.unit_id) != key
                or candidates.unit_id not in scope.unit_ids or plan.production_id != scope.production_id):
            raise ValidationError(_('Виріб та дозвіл мають належати поточному плану, MO і розподілу.'))
        plan.production_id._check_unit_stock_release_access()
        error = candidates._get_effectiveness_error()
        if error:
            raise ValidationError(error)
        plan.action_check_current()
        return dict(unit_id=candidates.unit_id.id, permission_id=candidates.id, plan_id=plan.id)

    @api.model
    def _check_integration_contract(self, scope):
        if not permits(scope.production_id, 'stock_move'):
            self._require_enterprise_validation()

    @api.model
    def _require_enterprise_validation(self):
        # No configuration/context/test-mode flag enables live posting.
        # Enterprise tests patch ONLY this gate in their disposable DB.
        raise UserError(_('Проведення QUNIT заблоковано до ізольованої Enterprise-перевірки маршруту, Shop Floor, витрат працівників та закриття MO.'))

    @api.model
    def _post_plan(self, plan):
        """Preparation-plan entry; unconditional gate precedes all writes."""
        self._require_enterprise_validation()
        plan.ensure_one()
        access(plan)
        production = plan.production_id
        production._check_unit_stock_release_access()
        if len(plan.permission_ids) != 1:
            raise ValidationError(_('Один запит проведення підтримує рівно один конкретний QUNIT.'))
        permission = plan.permission_ids
        with self.env.cr.savepoint():
            production._lock_quality_transfer_scope()
            for check in permission.unit_id.run_ids.check_ids.sorted('id'):
                check._lock_unit_admission_scope()
            self._lock_production(production)
            production._check_named_stock_history()
            previous = self._receipts(production).filtered(lambda row: row.plan_id == plan)
            if previous:
                if previous.permission_id != permission or previous.posted_by_id != self.env.user:
                    raise ValidationError(_('Попереднє проведення має інший склад або автора.'))
                self._reconcile(previous.scope_id)
                return previous
            production._check_stock_release_permissions(permission)
            plan.action_check_current()
            scope = self.env['quality.stock.cost.scope']._freeze_from_executions(
                production, {}, selected=permission.unit_id, scope_key=plan.request_key)
            with posting_authority(production.id, self.env.uid):
                record = self._post_one(scope, unit_key(permission.unit_id), 'permission:%s' % permission.id,
                                        plan.request_key, plan=plan)
            from .quality_release_evidence import invalidate_release_views
            invalidate_release_views(self.env)
            return record

    @api.model
    def _finish_production(self, scope):
        production = scope.production_id
        units = production._check_named_stock_history()
        if len(units) != production.product_qty or set(units.ids) != set(scope.unit_ids.ids):
            raise ValidationError(_('Не всі QUNIT узгоджено перед закриттям MO.'))
        if production_sources(production, 'quality.unit.execution', "production_id = %s AND state = 'progress'"):
            raise ValidationError(_('Є незавершені поштучні операції.'))
        self._check_closing_costs(scope)
        if self.env['quality.stock.cost.scope']._timers(production).filtered(lambda timer: not timer.date_end):
            raise ValidationError(_('Не можна закривати MO з відкритим часом.'))
        with posting_authority(production.id, self.env.uid, closing=True):
            production.workorder_ids._finish_named_stock_workorders()
            production.write({'date_finished': fields.Datetime.now(), 'is_locked': True})


class QualitySharedTimerBinding(models.Model):
    _name = 'quality.stock.timer.binding'
    _inherit = 'quality.stock.protected'
    _description = 'Явний незмінний склад спільного робочого інтервалу'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict')
    company_id = fields.Many2one(related='production_id.company_id', store=True, compute_sudo=False)
    timer_id = fields.Many2one('mrp.workcenter.productivity', required=True, readonly=True, ondelete='restrict')
    unit_ids = fields.Many2many('quality.production.unit', 'quality_stock_timer_binding_unit_rel', 'binding_id', 'unit_id', readonly=True)
    reason = fields.Text(required=True, readonly=True)
    actor_id = fields.Many2one('res.users', required=True, readonly=True, ondelete='restrict')
    bound_at = fields.Datetime(required=True, readonly=True)
    _sql_constraints = [('timer_unique', 'unique(timer_id)', 'Склад цього інтервалу вже підтверджено.')]

    @api.model
    def _protected_sources(self):
        return dict(super()._protected_sources(), timer_id='mrp.workcenter.productivity')

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT unit_id FROM quality_stock_timer_binding_unit_rel WHERE binding_id IN %s', [tuple(self.ids)])
            access(self.env['quality.production.unit'].browse([row[0] for row in self.env.cr.fetchall()]))
        return result

    @api.model
    def _for_production(self, production):
        return production_sources(production, self._name, 'production_id = %s')

    @api.model
    def _bind_shared_timer(self, timer, units, reason):
        access(timer); access(units)
        production = timer.workorder_id.production_id
        production._check_unit_stock_release_access()
        with self.env.cr.savepoint():
            production._lock_quality_transfer_scope()
            if (not units or units.production_id != production or units.company_id != production.company_id
                    or units.product_id != production.product_id or not reason.strip() or timer.quality_execution_batch_id):
                raise ValidationError(_('Потрібні справжній спільний інтервал, явні QUNIT цього MO та причина.'))
            units._assert_not_stock_posted()
            return self.with_context(**{CORE_CONTEXT: CORE_TOKEN}).create(dict(
                production_id=production.id, timer_id=timer.id, unit_ids=[Command.set(units.ids)],
                reason=reason.strip(), actor_id=self.env.uid, bound_at=fields.Datetime.now())).with_env(self.env)
