"""Explicit, prospective QUNIT policy. No migration or stock-release authority."""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .native_backorder import native_operation_allows

REQUIRED_ROUTE_MESSAGE = 'Для цього MO обов’язковий поштучний маршрут: потрібні QRUN/QUNIT, чинні допуски інспектора, завершені операції та відсутність відкритих Alert. PASS матриці не дозволяє стандартне завершення або складський випуск. Користувацький поштучний випуск залишається закритим до Enterprise-перевірки.'


def require_quality_manager(env):
    if not env.user.has_group('quality.group_quality_manager'):
        raise AccessError(_('Обов’язковий поштучний режим вмикає керівник якості.'))


class QualityPointUnitRoutePolicy(models.Model):
    _inherit = 'quality.point'

    quality_unit_route_required = fields.Boolean(
        string='Обов’язковий поштучний маршрут', default=False,
        help='Лише для нових міжопераційних QC. Вмикає незворотне обмеження їх MO, '
             'зокрема вже відкритого MO. Наявні QC та завершені MO не змінюються. '
             'Складський випуск користувачам поки недоступний.')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if records.filtered('quality_unit_route_required'):
            require_quality_manager(self.env)
        return records

    def write(self, vals):
        if 'quality_unit_route_required' in vals:
            require_quality_manager(self.env)
        return super().write(vals)

    @api.constrains('quality_unit_route_required', 'x_control_stage')
    def _check_unit_route_stage(self):
        if any(point.quality_unit_route_required and point.x_control_stage != 'in_process' for point in self):
            raise ValidationError(_('Поштучний режим QCP доступний лише для міжопераційного контролю.'))


class ProductionUnitRoutePolicy(models.Model):
    _inherit = 'mrp.production'

    quality_unit_route_required = fields.Boolean(
        string='Обов’язковий поштучний маршрут', readonly=True, copy=False, default=False,
        help='Вмикається явно керівником якості або новим QC з налаштованого QCP. '
             'Не вимикається зміною QCP, видаленням QC або RPC-контекстом.')

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get('quality_unit_route_required') for vals in vals_list):
            raise UserError(_('Режим MO вмикають окремою дією або новою QC.'))
        context = dict(self.env.context)
        context.pop('default_quality_unit_route_required', None)
        return super(ProductionUnitRoutePolicy, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'quality_unit_route_required' in vals:
            raise UserError(_('Увімкнений поштучний режим MO незмінний.'))
        if vals.get('state') == 'done' or vals.get('qty_produced'):
            self._check_required_unit_route_completion()
        return super().write(vals)

    def _enable_required_unit_route(self):
        self.check_access_rights('write')
        self.check_access_rule('write')
        if any(production.state in ('done', 'cancel') or production.qty_produced for production in self):
            raise UserError(_('Поштучний режим вмикають лише для відкритого MO без попереднього випуску.'))
        self._lock_quality_transfer_scope()
        for production in self:
            if production.state in ('done', 'cancel') or production.qty_produced:
                raise UserError(_('Поштучний режим вмикають лише для відкритого MO без попереднього випуску.'))
            if not production.quality_unit_route_required:
                super(ProductionUnitRoutePolicy, production).write({'quality_unit_route_required': True})
                production.message_post(body=_('Увімкнено обов’язковий поштучний маршрут. Стандартний складський випуск заборонено.'), subtype_xmlid='mail.mt_note')

    def action_require_quality_unit_route(self):
        require_quality_manager(self.env)
        self._enable_required_unit_route()
        return True

    def _check_required_unit_route_completion(self):
        # No context bypass. Only the existing validated Python-local native
        # operation may pass; its public release action remains closed.
        for production in self.sorted('id'):
            if native_operation_allows(production):
                continue
            production._lock_quality_transfer_scope()
            if production.quality_unit_route_required:
                raise UserError(_(REQUIRED_ROUTE_MESSAGE))


class QualityCheckUnitRoutePolicy(models.Model):
    _inherit = 'quality.check'

    @api.model_create_multi
    def create(self, vals_list):
        checks = super().create(vals_list)
        # Read the QCP through ordinary ACLs. Activation is a trusted private
        # server step, never inferred from unit_admission_role or PASS counts.
        for check in checks:
            if check.point_id.quality_unit_route_required:
                production = check._identity_production()
                if not production:
                    raise ValidationError(_('Обов’язкова поштучна QC повинна належати MO.'))
                production._enable_required_unit_route()
        return checks
