"""Real QUNIT references and the deliberately closed Enterprise bridge."""

from odoo import api, fields, models
from .quality_archive import archive_disabled

from .stock_posting_core import access


class QualityStockCostScope(models.Model):
    _inherit = 'quality.stock.cost.scope'

    unit_ids = fields.Many2many('quality.production.unit', 'quality_stock_cost_scope_unit_rel',
                               'scope_id', 'unit_id', string='Зафіксовані QUNIT', readonly=True)


    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT unit_id FROM quality_stock_cost_scope_unit_rel WHERE scope_id IN %s', [tuple(self.ids)])
            access(self.env['quality.production.unit'].browse([row[0] for row in self.env.cr.fetchall()]))
        return result


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


    @api.model
    def _require_enterprise_validation(self):
        archive_disabled()

    @api.model
    def _post_plan(self, plan):
        """Retired API: always fail before inspecting or modifying a plan."""
        self._require_enterprise_validation()


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
