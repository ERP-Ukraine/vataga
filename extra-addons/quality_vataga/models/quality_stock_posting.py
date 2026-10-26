"""Real QUNIT references and the deliberately closed Enterprise bridge."""

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError

from .stock_posting_core import access


def unit_key(unit):
    return 'qunit:%s' % unit.id


class QualityStockCostScope(models.Model):
    _inherit = 'quality.stock.cost.scope'

    unit_ids = fields.Many2many('quality.production.unit', 'quality_stock_cost_scope_unit_rel',
                               'scope_id', 'unit_id', string='Зафіксовані QUNIT', readonly=True)

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
    def _freeze_from_executions(self, production, explicit_shared_scopes):
        """Private, audited policy; untagged first-operation time is explicit.

        explicit_shared_scopes maps actual timer IDs to actual unit records.
        It is not accepted by any public RPC endpoint or client context.
        """
        production._check_unit_stock_release_access()
        units = self.env['quality.production.unit']._release_records(
            'quality.production.unit', 'production_id = %s', [production.id],
        )
        members = {}
        for timer in self._timers(production):
            batch = timer.quality_execution_batch_id
            if batch:
                access(batch); access(batch.execution_ids)
                if (timer.id in explicit_shared_scopes or batch.workorder_id != timer.workorder_id
                        or batch.execution_ids.filtered(lambda row: row.state != 'processed' or row.workorder_id != timer.workorder_id)):
                    raise ValidationError(_('Інтервал виконання потребує завершених QUNIT та незмінного складу запуску.'))
                scoped = batch.execution_ids.controlled_unit_id
                if len(scoped) != len(batch.execution_ids):
                    raise ValidationError(_('Неоднозначний склад виконання виробів.'))
            else:
                scoped = explicit_shared_scopes.get(timer.id, self.env['quality.production.unit'])
                access(scoped)
            if not scoped or scoped - units:
                raise ValidationError(_('Спільний інтервал потребує явно підтверджених QUNIT саме цього MO.'))
            members[timer.id] = [unit_key(unit) for unit in scoped]
        if set(explicit_shared_scopes) - set(members):
            raise ValidationError(_('Передано сторонні робочі інтервали.'))
        return self._freeze(production, [unit_key(unit) for unit in units], members)


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
        # No super(), configuration key, user group or context enables this.
        # Pending: serialized 13.3/13.4A continuation after journal reconciliation,
        # per-unit admission revalidation, Shop Floor employee costs, real-time
        # accounting and final-MO closing hooks in the actual Enterprise stack.
        raise UserError(_('Проведення QUNIT заблоковано до інтеграційної перевірки Enterprise: повторна перевірка допусків, продовження 13.3/13.4А, витрати працівників та закриття MO.'))
