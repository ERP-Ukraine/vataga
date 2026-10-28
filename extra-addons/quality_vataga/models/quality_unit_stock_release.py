import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from .quality_archive import ARCHIVE_MESSAGE, archive_disabled
from .quality_release_access import check_stock_release_roles, has_stock_release_roles


class QualityUnitStockReleasePlan(models.Model):
    _name = 'quality.unit.stock.release.plan'
    _inherit = 'quality.release.access'
    _description = 'Незмінний план складського випуску QUNIT, без проведення'
    _order = 'id desc'

    name = fields.Char(string='План випуску', required=True, readonly=True, copy=False)
    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True, compute_sudo=False)
    permission_ids = fields.Many2many(
        'quality.unit.release.line', 'quality_stock_plan_permission_rel', 'plan_id', 'permission_id',
        string='Дозволи 13.4А', readonly=True,
    )
    quantity = fields.Integer(string='Запропоновано виробів', required=True, readonly=True)
    prepared_by_id = fields.Many2one('res.users', string='Підготував', required=True, readonly=True, ondelete='restrict')
    prepared_at = fields.Datetime(string='Дата підготовки', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True, copy=False)
    evidence_snapshot = fields.Text(string='Підстави та резерви на момент підготовки', required=True, readonly=True)
    component_summary = fields.Text(string='Пропозиція часткового споживання', required=True, readonly=True)
    posting_note = fields.Text(string='Стан проведення', compute='_compute_posting_note')
    posting_diagnostic = fields.Text(string='Перешкоди складського проведення', compute='_compute_posting_diagnostic')

    _sql_constraints = [
        ('request_unique', 'unique(production_id, request_key)', 'Цей запит підготовки вже оброблено.'),
        ('quantity_positive', 'check(quantity > 0)', 'План потребує конкретних виробів.'),
    ]

    def _compute_posting_note(self):
        for plan in self:
            plan.posting_note = _(ARCHIVE_MESSAGE)

    @api.depends('evidence_snapshot')
    def _compute_posting_diagnostic(self):
        for plan in self:
            plan.posting_diagnostic = _(ARCHIVE_MESSAGE)

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        if not check_stock_release_roles(self.env.user, raise_exception=raise_exception):
            return False
        return super().check_access_rights(operation, raise_exception=raise_exception)

    def check_access_rule(self, operation):
        check_stock_release_roles(self.env.user)
        result = super().check_access_rule(operation)
        if self.ids:
            # Read protective relation IDs directly: normal M2M reads could
            # omit hidden children. This confers no access to those records.
            self.env.cr.execute('SELECT DISTINCT permission_id FROM quality_stock_plan_permission_rel WHERE plan_id IN %s', [tuple(self.ids)])
            permissions = self.env['quality.unit.release.line'].browse([row[0] for row in self.env.cr.fetchall()])
            permissions.check_access_rights('read')
            permissions.check_access_rule('read')
            # Extract only protective identities from the immutable snapshot.
            # The snapshot itself is never returned before the normal rules
            # on all source documents have allowed access.
            sources = {name: set() for name in (
                'stock.move', 'stock.move.line', 'mrp.workorder', 'mrp.workcenter.productivity',
            )}
            self.env.cr.execute('SELECT evidence_snapshot FROM quality_unit_stock_release_plan WHERE id IN %s', [tuple(self.ids)])
            for (snapshot,) in self.env.cr.fetchall():
                try:
                    data = json.loads(snapshot)
                    sources['stock.move'].update(row['move_id'] for row in data['components'])
                    sources['stock.move'].add(data['finished']['move'])
                    sources['stock.move.line'].update(line['id'] for row in data['components'] for line in row['source_lines'])
                    sources['stock.move.line'].update(line['id'] for line in data['finished']['source_lines'])
                    sources['mrp.workorder'].update(row['id'] for row in data['costing']['workorders'])
                    sources['mrp.workcenter.productivity'].update(row['id'] for row in data['costing']['timers'])
                except (KeyError, ValueError, TypeError):
                    raise AccessError(_('Неможливо перевірити доступ до джерел збереженого плану.')) from None
            for model, record_ids in sources.items():
                records = self.env[model].browse(sorted(record_ids))
                records.check_access_rights('read')
                records.check_access_rule('read')
        return result

    @api.model_create_multi
    def create(self, vals_list):
        archive_disabled()

    def write(self, vals):
        archive_disabled()

    def unlink(self):
        raise UserError(_('Історія підготовки випуску не видаляється.'))

    @api.model
    def _prepare(self, production, permissions, request_key):
        archive_disabled()

    def action_check_current(self):
        archive_disabled()

    def action_post(self):
        """Fail closed; neither a context flag nor sudo enables posting."""
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        self.env['quality.stock.posting']._post_plan(self)


class MrpProductionStockReleasePlan(models.Model):
    _inherit = 'mrp.production'

    quality_stock_release_plan_ids = fields.One2many(
        'quality.unit.stock.release.plan', 'production_id', string='Плани складського випуску',
        compute='_compute_stock_release_plans', compute_sudo=False, readonly=True, groups='mrp.group_mrp_user',
    )
    can_prepare_unit_stock_release = fields.Boolean(compute='_compute_stock_release_role')

    @api.depends_context('uid')
    def _compute_stock_release_role(self):
        for production in self:
            production.can_prepare_unit_stock_release = False

    @api.depends('quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_stock_release_plans(self):
        allowed = has_stock_release_roles(self.env.user)
        plans = self.env['quality.unit.stock.release.plan']
        for production in self:
            production.quality_stock_release_plan_ids = plans.search([('production_id', '=', production.id)]) if allowed else plans

    @api.model_create_multi
    def create(self, vals_list):
        if any({'quality_stock_release_plan_ids', 'can_prepare_unit_stock_release'}.intersection(vals) for vals in vals_list):
            raise UserError(_('Історію планів встановлює система.'))
        context = dict(self.env.context)
        context.pop('default_quality_stock_release_plan_ids', None)
        context.pop('default_can_prepare_unit_stock_release', None)
        return super(MrpProductionStockReleasePlan, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if {'quality_stock_release_plan_ids', 'can_prepare_unit_stock_release'}.intersection(vals):
            raise UserError(_('Історію планів встановлює система.'))
        return super().write(vals)

    def _check_unit_stock_release_access(self):
        self.ensure_one()
        check_stock_release_roles(self.env.user)
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.company_id not in self.env.companies:
            raise AccessError(_('Немає доступу до компанії архівного MO.'))
        for name in ('stock.move', 'stock.move.line'):
            self.env[name].check_access_rights('read')


    def action_open_unit_stock_release_wizard(self):
        archive_disabled()

    def action_open_stock_timer_binding(self):
        archive_disabled()


class QualityReleasePermissionStockPlans(models.Model):
    _inherit = 'quality.unit.release.line'

    stock_release_plan_ids = fields.Many2many(
        'quality.unit.stock.release.plan', 'quality_stock_plan_permission_rel', 'permission_id', 'plan_id',
        string='Підготовлені плани', readonly=True, groups='mrp.group_mrp_user',
    )


class QualityUnitStockReleaseHistory(models.Model):
    _inherit = 'quality.production.unit'

    stock_release_plan_ids = fields.Many2many(
        'quality.unit.stock.release.plan', string='Підготовка складського випуску',
        compute='_compute_stock_release_plans', compute_sudo=False, groups='mrp.group_mrp_user',
    )
    can_prepare_unit_stock_release = fields.Boolean(
        related='production_id.can_prepare_unit_stock_release', compute_sudo=False,
    )

    @api.depends('quality_release_line_ids.stock_release_plan_ids')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_stock_release_plans(self):
        allowed = has_stock_release_roles(self.env.user)
        plans = self.env['quality.unit.stock.release.plan']
        for unit in self:
            unit.stock_release_plan_ids = plans.search([('permission_ids.unit_id', '=', unit.id)]) if allowed else plans
