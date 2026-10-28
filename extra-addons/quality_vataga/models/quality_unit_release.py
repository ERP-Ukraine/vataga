from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from .quality_archive import ARCHIVE_MESSAGE, archive_disabled


class QualityReleaseAccess(models.AbstractModel):
    _name = 'quality.release.access'
    _description = 'Доступ до дозволів через MO'

    @api.model_create_multi
    def create(self, vals_list):
        archive_disabled()

    def write(self, vals):
        archive_disabled()

    def unlink(self):
        archive_disabled()

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        scope = [('production_id', 'in', self.env['mrp.production']._search([]))]
        if self._name == 'quality.unit.release.line':
            scope.append(('unit_id', 'in', self.env['quality.production.unit']._search([])))
            scope.append(('check_id', 'in', self.env['quality.check']._search([])))
        elif self._name == 'quality.unit.release.event':
            scope.append(('line_id', 'in', self.env['quality.unit.release.line']._search([])))
        return super()._search(expression.AND([domain, scope]), offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['production_id'])
            self.env.cr.execute('SELECT DISTINCT production_id FROM "%s" WHERE id IN %%s' % self._table, [tuple(self.ids)])
            parents = self.env['mrp.production'].browse([row[0] for row in self.env.cr.fetchall()])
            parents.check_access_rights('read')
            parents.check_access_rule('read')
            if self._name == 'quality.unit.release.line':
                self.flush_recordset(['unit_id'])
                self.env.cr.execute('SELECT DISTINCT unit_id FROM quality_unit_release_line WHERE id IN %s', [tuple(self.ids)])
                units = self.env['quality.production.unit'].browse([row[0] for row in self.env.cr.fetchall()])
                units.check_access_rights('read')
                units.check_access_rule('read')
                self.flush_recordset(['check_id'])
                self.env.cr.execute('SELECT DISTINCT check_id FROM quality_unit_release_line WHERE id IN %s', [tuple(self.ids)])
                checks = self.env['quality.check'].browse([row[0] for row in self.env.cr.fetchall()])
                checks.check_access_rights('read')
                checks.check_access_rule('read')
            elif self._name == 'quality.unit.release.event':
                self.env.cr.execute('SELECT DISTINCT line_id FROM quality_unit_release_event WHERE id IN %s', [tuple(self.ids)])
                lines = self.env['quality.unit.release.line'].browse([row[0] for row in self.env.cr.fetchall()])
                lines.check_access_rights('read')
                lines.check_access_rule('read')
        return result


class QualityUnitReleaseBatch(models.Model):
    _name = 'quality.unit.release.batch'
    _inherit = 'quality.release.access'
    _description = 'Пакет дозволу випуску QUNIT (без складського проведення)'
    _order = 'id desc'

    name = fields.Char(string='Пакет дозволу', required=True, readonly=True)
    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True, compute_sudo=False)
    authorized_by_id = fields.Many2one('res.users', string='Дозволив', required=True, readonly=True, ondelete='restrict')
    authorized_at = fields.Datetime(string='Дата дозволу', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True, copy=False)
    line_ids = fields.One2many('quality.unit.release.line', 'batch_id', string='Конкретні вироби', readonly=True)

    _sql_constraints = [('request_unique', 'unique(production_id, request_key)', 'Цей пакет вже підтверджено.')]

    @api.model_create_multi
    def create(self, vals_list):
        archive_disabled()

    def write(self, vals):
        raise UserError(_('Реквізити підтвердженого пакета незмінні.'))

    def unlink(self):
        raise UserError(_('Історія дозволів не видаляється.'))

    @api.model
    def _authorize(self, production, units, request_key):
        archive_disabled()


class QualityUnitReleaseLine(models.Model):
    _name = 'quality.unit.release.line'
    _inherit = 'quality.release.access'
    _description = 'Захищений дозвіл випуску конкретного QUNIT'
    _rec_name = 'unit_id'
    _order = 'id desc'

    batch_id = fields.Many2one('quality.unit.release.batch', string='Пакет', required=True, readonly=True, ondelete='restrict', index=True)
    unit_id = fields.Many2one('quality.production.unit', string='QUNIT', required=True, readonly=True, ondelete='restrict', index=True)
    production_id = fields.Many2one(related='batch_id.production_id', store=True, readonly=True, compute_sudo=False, index=True)
    company_id = fields.Many2one(related='batch_id.company_id', store=True, readonly=True, compute_sudo=False, index=True)
    product_id = fields.Many2one('product.product', string='Товар', required=True, readonly=True, ondelete='restrict')
    last_workorder_id = fields.Many2one('mrp.workorder', string='Остання операція', required=True, readonly=True, ondelete='restrict')
    last_run_id = fields.Many2one('quality.production.run', string='Останній QRUN', required=True, readonly=True, ondelete='restrict')
    check_id = fields.Many2one('quality.check', string='Контроль після останньої операції', required=True, readonly=True, ondelete='restrict')
    admission_id = fields.Many2one('quality.unit.admission', string='Допуск інспектора', required=True, readonly=True, ondelete='restrict')
    authorized_by_id = fields.Many2one(related='batch_id.authorized_by_id', store=True, readonly=True, compute_sudo=False)
    authorized_at = fields.Datetime(related='batch_id.authorized_at', store=True, readonly=True, compute_sudo=False)
    state = fields.Selection([('confirmed', 'Підтверджено'), ('cancelled', 'Скасовано')], required=True, readonly=True, index=True)
    current_state = fields.Selection([('authorized', 'Дозволено до випуску'), ('lapsed', 'Дозвіл втратив чинність'), ('cancelled', 'Скасовано'), ('used', 'Використано')], string='Чинність дозволу', compute='_compute_effectiveness')
    is_effective = fields.Boolean(string='Дозвіл чинний', compute='_compute_effectiveness')
    ineffective_reason = fields.Text(string='Причина втрати чинності', compute='_compute_effectiveness')
    evidence_snapshot = fields.Text(string='Підстави на момент підтвердження', required=True, readonly=True)
    execution_ids = fields.Many2many('quality.unit.execution', string='Виконання операцій', readonly=True)
    transfer_ids = fields.Many2many('quality.unit.transfer', string='Передачі та приймання', readonly=True)
    admission_ids = fields.Many2many('quality.unit.admission', string='Допуски маршруту', readonly=True)
    check_ids = fields.Many2many('quality.check', string='Обов’язкові перевірки', readonly=True)
    cancelled_by_id = fields.Many2one('res.users', string='Скасував', readonly=True, ondelete='restrict')
    cancelled_at = fields.Datetime(string='Дата скасування', readonly=True)
    cancellation_reason = fields.Text(string='Причина скасування', readonly=True)
    event_ids = fields.One2many('quality.unit.release.event', 'line_id', string='Аудит', readonly=True)

    _sql_constraints = [
        ('batch_unit_unique', 'unique(batch_id, unit_id)', 'QUNIT вже є у цьому пакеті.'),
        ('cancellation_audit', "check((state = 'confirmed' AND cancelled_by_id IS NULL AND cancelled_at IS NULL AND cancellation_reason IS NULL) OR (state = 'cancelled' AND cancelled_by_id IS NOT NULL AND cancelled_at IS NOT NULL AND length(trim(cancellation_reason)) > 0 AND cancellation_reason IS NOT NULL))", 'Скасування потребує автора, дати та причини.'),
    ]

    @api.private
    def init(self):
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS quality_unit_release_active_unique ON quality_unit_release_line(unit_id) WHERE state = 'confirmed'")

    @api.model_create_multi
    def create(self, vals_list):
        archive_disabled()

    def write(self, vals):
        archive_disabled()

    def unlink(self):
        raise UserError(_('Історія дозволів випуску не видаляється.'))

    def _get_effectiveness_error(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.state != 'confirmed':
            return _('Дозвіл скасовано.')
        if self._stock_permission_used():
            return _('Дозвіл використано складським випуском.')
        return _(ARCHIVE_MESSAGE)

    @api.depends('state', 'unit_id.quality_release_revision', 'production_id.quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_effectiveness(self):
        for line in self:
            error = line._get_effectiveness_error()
            line.is_effective = not bool(error)
            line.ineffective_reason = error or ''
            line.current_state = 'used' if line._stock_permission_used() else 'cancelled' if line.state == 'cancelled' else 'lapsed' if error else 'authorized'

    def _cancel(self, production, reason):
        archive_disabled()

    def _log_event(self, action, reason=''):
        archive_disabled()


class QualityUnitReleaseEvent(models.Model):
    _name = 'quality.unit.release.event'
    _inherit = 'quality.release.access'
    _description = 'Незмінна подія дозволу випуску QUNIT'
    _order = 'id'

    line_id = fields.Many2one('quality.unit.release.line', required=True, readonly=True, ondelete='restrict', index=True)
    production_id = fields.Many2one(related='line_id.production_id', store=True, readonly=True, compute_sudo=False)
    company_id = fields.Many2one(related='line_id.company_id', store=True, readonly=True, compute_sudo=False)
    action = fields.Selection([('confirmed', 'Дозволено'), ('cancelled', 'Скасовано')], string='Подія', required=True, readonly=True)
    actor_id = fields.Many2one('res.users', string='Виконав', required=True, readonly=True, ondelete='restrict')
    occurred_at = fields.Datetime(string='Дата', required=True, readonly=True)
    reason = fields.Text(string='Причина', readonly=True)

    _sql_constraints = [('event_unique', 'unique(line_id, action)', 'Цю подію вже зафіксовано.')]

    @api.model_create_multi
    def create(self, vals_list):
        archive_disabled()

    def write(self, vals):
        raise UserError(_('Події дозволу незмінні.'))

    def unlink(self):
        raise UserError(_('Події дозволу не видаляються.'))


class MrpProductionUnitRelease(models.Model):
    _inherit = 'mrp.production'

    quality_release_unit_ids = fields.One2many('quality.production.unit', 'production_id', string='Конкретні вироби', readonly=True)
    quality_release_batch_ids = fields.One2many('quality.unit.release.batch', 'production_id', string='Пакети дозволу випуску', readonly=True)
    quality_release_total = fields.Integer(string='Усього виробів', compute='_compute_unit_release_counts')
    quality_release_ready = fields.Integer(string='Готово до випуску', compute='_compute_unit_release_counts')
    quality_release_authorized = fields.Integer(string='Дозволено до випуску', compute='_compute_unit_release_counts')
    quality_release_not_ready = fields.Integer(string='Не готово', compute='_compute_unit_release_counts')
    quality_release_blocked = fields.Integer(string='Заблоковано', compute='_compute_unit_release_counts')
    quality_release_produced = fields.Integer(string='Фактично випущено', compute='_compute_unit_release_counts', help='Фактичні QUNIT за незмінним складським реєстром. Це не кількість PASS або дозволів.')

    @api.depends('quality_release_unit_ids.quality_release_state', 'quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_unit_release_counts(self):
        for production in self:
            units = production.quality_release_unit_ids
            states = units.mapped('quality_release_state')
            production.quality_release_total = len(units)
            production.quality_release_ready = states.count('ready')
            production.quality_release_authorized = states.count('authorized')
            production.quality_release_not_ready = states.count('not_ready')
            production.quality_release_blocked = states.count('blocked') + states.count('lapsed')
            production.quality_release_produced = states.count('produced')

    def _check_unit_release_access(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.company_id not in self.env.companies:
            raise AccessError(_('Немає доступу до компанії архівного MO.'))

    @api.model_create_multi
    def create(self, vals_list):
        if any(any(name.startswith('quality_release_') for name in vals) for vals in vals_list):
            raise UserError(_('Показники та історію дозволів обчислює система.'))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_quality_release_')}
        return super(MrpProductionUnitRelease, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if any(name.startswith('quality_release_') for name in vals):
            raise UserError(_('Показники та історію дозволів обчислює система.'))
        return super().write(vals)

    def action_open_unit_release_wizard(self):
        archive_disabled()

    def action_open_unit_release_cancel_wizard(self):
        archive_disabled()
