import math

from markupsafe import Markup

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .incoming_hold import (incoming_context, internal, INCOMING_DEFER_CONTEXT, INCOMING_DEFER_OPERATION)
from .quality_check_sample import AUTOMATIC_SAMPLE_INITIALIZATION


class IncomingQualityPoint(models.Model):
    _inherit = 'quality.point'

    incoming_sample_size = fields.Integer(string='Розмір вхідної вибірки', default=0)
    incoming_acceptance_policy = fields.Selection([
        ('all_pass', 'Прийняти партію, якщо всі обов’язкові зразки PASS'),
    ], string='Правило приймання вхідної партії')

    @api.constrains('incoming_sample_size')
    def _check_incoming_sample_size(self):
        if any(point.incoming_sample_size < 0 for point in self):
            raise ValidationError(_('Розмір вибірки не може бути від’ємним.'))


class IncomingHoldQuality(models.Model):
    _inherit = 'quality.incoming.hold'

    check_ids = fields.One2many('quality.check', 'incoming_hold_id', readonly=True)
    required_point_ids = fields.Many2many('quality.point', readonly=True, copy=False)
    alert_ids = fields.One2many('quality.alert', 'incoming_hold_id', readonly=True)
    sampling_state = fields.Selection([
        ('unconfigured', 'Немає плану контролю'), ('pending', 'Очікує контролю'),
        ('fail', 'Виявлено брак'), ('pass', 'Вибірка PASS'),
    ], compute='_compute_sampling_state')

    @api.depends('check_ids.quality_state', 'check_ids.can_pass_measurement_check', 'required_point_ids', 'failure_reported_at')
    def _compute_sampling_state(self):
        for hold in self:
            if hold._has_failure():
                hold.sampling_state = 'fail'
            elif not hold.check_ids:
                hold.sampling_state = 'unconfigured'
            elif hold._sampling_passed():
                hold.sampling_state = 'pass'
            else:
                hold.sampling_state = 'pending'

    def _applicable_incoming_points(self):
        self.ensure_one()
        return self.env['quality.point'].sudo().search([
            ('company_id', '=', self.company_id.id),
            ('picking_type_ids', 'in', self.receipt_id.picking_type_id.ids),
            ('x_control_stage', '=', 'incoming'), ('operation_id', '=', False),
            '|', ('product_ids', '=', False), ('product_ids', 'in', self.product_id.ids),
            '|', ('product_category_ids', '=', False),
            ('product_category_ids', 'parent_of', self.product_id.categ_id.id),
        ])

    def _start_checks(self):
        self.ensure_one()
        if self.check_ids:
            return True
        points = self._applicable_incoming_points()
        unit = self.env.ref('uom.product_uom_unit')
        if not points or self.uom_id.category_id != unit.category_id:
            return False
        pieces = self.uom_id._compute_quantity(self.received_qty, unit, round=False)
        if not math.isclose(pieces, round(pieces), rel_tol=0, abs_tol=1e-9):
            return False
        if any(point.incoming_sample_size <= 0 or point.incoming_acceptance_policy != 'all_pass'
               or point.test_type_id.technical_name not in ('passfail', 'measure')
               or not (point.visual_sample_control_required or point.control_parameter_line_ids)
               for point in points):
            return False
        context = incoming_context(self.env)
        self.sudo().with_context(context).write({'required_point_ids': [Command.set(points.ids)]})
        for point in points:
            self.env['quality.check'].sudo().with_context(context).create({
                'incoming_hold_id': self.id, 'incoming_sample_size': min(point.incoming_sample_size, int(round(pieces))),
                'incoming_acceptance_policy': point.incoming_acceptance_policy,
                'point_id': point.id, 'product_id': self.product_id.id, 'company_id': self.company_id.id,
                'picking_id': self.receipt_id.id, 'move_line_id': self.receipt_line_id.id,
                'lot_id': self.lot_id.id, 'team_id': point.team_id.id,
                'test_type_id': point.test_type_id.id,
            })
        return True

    def _sampling_batch(self):
        """Keep acceptance common to all source lines of a product in one IN.

        Quantities remain independent (lot, serial, owner and package), but
        splitting a receipt into lines must not split its acceptance gate.
        Separate supplier receipts never share acceptance decisions.
        """
        self.ensure_one()
        ids = self.sudo().search([('receipt_id', '=', self.receipt_id.id),
                                  ('product_id', '=', self.product_id.id)]).ids
        return self.browse(ids)

    def _lock(self):
        batch = self.browse()
        for hold in self:
            batch |= hold._sampling_batch()
        return super(IncomingHoldQuality, batch)._lock()

    def _source_has_failure(self):
        self.ensure_one()
        return super()._has_failure() or any(check.quality_state == 'fail' for check in self.sudo().check_ids)

    def _has_failure(self):
        self.ensure_one()
        return any(source._source_has_failure() for source in self._sampling_batch().sudo())

    def _source_sampling_passed(self):
        self.ensure_one()
        checks = self.sudo().check_ids
        return bool(not self._source_has_failure() and checks and self.required_point_ids and len(checks) == len(self.required_point_ids)
                    and set(checks.point_id.ids) == set(self.required_point_ids.ids)
                    and all(check.quality_state == 'pass' and check.incoming_sample_size > 0
                            and check.incoming_acceptance_policy == 'all_pass'
                            and len(check.sample_ids) == check.incoming_sample_size
                            and check.can_pass_measurement_check for check in checks))

    def _sampling_passed(self):
        self.ensure_one()
        batch = self._sampling_batch().sudo()
        return bool(batch and all(source._source_sampling_passed() for source in batch))

    def _record_manual_failure_alert(self):
        return self._record_failure_alert()

    def _record_failure_alert(self, check=None):
        self.ensure_one()
        # Caller holds the hold lock before the QC lock. The SQL uniqueness is
        # a second barrier, including concurrent requests from different QCPs.
        alerts = self.sudo().alert_ids
        failures = check._get_technical_board_failure_description() if check else Markup('<p>%s</p>') % self.failure_reason
        context = incoming_context(self.env)
        if alerts:
            if not check or check not in alerts.incoming_failure_check_ids:
                alerts.with_context(context).write({
                    'incoming_failure_check_ids': [Command.link(check.id)] if check else [],
                    'incoming_failure_description': Markup(alerts.incoming_failure_description or '') + failures,
                })
            return alerts
        description = Markup('<p>%s</p><p>%s / %s / %s / %s / %s: %s</p>') % (
            _('Вхідний контроль: партія залишається у карантині.'),
            self.product_id.display_name, self.supplier_id.display_name or '', self.lot_id.name or '',
            self.receipt_id.name, _('Фактично отримано'), self.received_qty,
        )
        values = {
            'title': _('Вхідна невідповідність: %s / %s') % (self.receipt_id.name, self.product_id.display_name),
            'incoming_hold_id': self.id, 'incoming_failure_check_ids': [Command.link(check.id)] if check else [],
            'incoming_failure_description': failures,
            'company_id': self.company_id.id, 'product_id': self.product_id.id,
            'picking_id': self.receipt_id.id, 'lot_id': self.lot_id.id,
            'description': description + (failures if not check else Markup('')),
        }
        # Enterprise's alert form displays the template, with the variant in
        # a separate field. Supplying only product_id leaves its Product blank.
        if 'product_tmpl_id' in self.env['quality.alert']._fields:
            values['product_tmpl_id'] = self.product_id.product_tmpl_id.id
        if check:
            values.update(check_id=check.id, team_id=check.team_id.id)
        else:
            teams = self.env['quality.alert.team'].sudo().with_company(self.company_id)
            domain = []
            if 'company_id' in teams._fields:
                domain = ['|', ('company_id', '=', False), ('company_id', '=', self.company_id.id)]
            team = teams.search(domain, limit=1)
            if not team:
                raise UserError(_('Налаштуйте команду якості для реєстрації невідповідності.'))
            values['team_id'] = team.id
        return self.env['quality.alert'].sudo().with_context(context).create(values)


class IncomingCheck(models.Model):
    _inherit = 'quality.check'

    incoming_hold_id = fields.Many2one('quality.incoming.hold', readonly=True, copy=False,
                                       check_company=True, ondelete='restrict', index=True)
    incoming_sample_size = fields.Integer(readonly=True, copy=False)
    incoming_acceptance_policy = fields.Selection([('all_pass', 'Всі зразки PASS')], readonly=True, copy=False)

    _sql_constraints = [('incoming_point_unique', 'unique(incoming_hold_id, point_id)',
                         'Контроль цієї вхідної партії вже створений.')]

    @api.model_create_multi
    def create(self, vals_list):
        # Standard Enterprise creates point checks while confirming/assigning
        # receipts. For this explicitly opted-in flow, the authoritative QC is
        # created from the posted source line instead. No existing QC is
        # deleted, passed, rebound or included in the permission to release.
        if self.env.context.get(INCOMING_DEFER_CONTEXT) is INCOMING_DEFER_OPERATION:
            vals_list = [vals for vals in vals_list if not self._defer_incoming_point_check(vals)]
        names = {'incoming_hold_id', 'incoming_sample_size', 'incoming_acceptance_policy'}
        for vals in vals_list:
            if any(vals.get(name) for name in names):
                if not internal(self.env) or vals.get('quality_state', 'none') != 'none':
                    raise AccessError(_('Вхідна вибірка створюється лише з карантинної партії.'))
        if not internal(self.env):
            vals_list = [dict(vals, incoming_hold_id=False, incoming_sample_size=0,
                              incoming_acceptance_policy=False) for vals in vals_list]
        checks = super().create(vals_list)
        for check in checks.filtered('incoming_hold_id'):
            if not check.measurement_matrix_required or check.incoming_sample_size < 1:
                raise ValidationError(_('Налаштуйте матрицю та розмір вибірки у QCP.'))
            check._create_measurement_samples(1, check.incoming_sample_size, AUTOMATIC_SAMPLE_INITIALIZATION)
        return checks

    def _defer_incoming_point_check(self, vals):
        if vals.get('incoming_hold_id') or vals.get('quality_state', 'none') != 'none':
            return False
        point = self.env['quality.point'].browse(vals.get('point_id'))
        picking = self.env['stock.picking'].browse(vals.get('picking_id'))
        if (not point or point.x_control_stage != 'incoming' or not picking
                or picking.state in ('done', 'cancel')):
            return False
        moves = picking.move_ids.filtered(lambda move: move.state != 'cancel' and move.product_id.type == 'product')
        if not moves and picking.backorder_id:
            # During standard backorder copying the remaining moves still
            # belong to the source IN until _create_backorder rebinds them.
            moves = picking.backorder_id._get_moves_to_backorder().filtered(
                lambda move: move.product_id.type == 'product')
        if vals.get('product_id'):
            moves = moves.filtered(lambda move: move.product_id.id == vals['product_id'])
        # Operation-wide checks involving ordinary goods remain standard.
        return bool(moves and all(move._incoming_scope() for move in moves))

    def write(self, vals):
        managed = self.filtered('incoming_hold_id')
        if not internal(self.env) and {'incoming_hold_id', 'incoming_sample_size', 'incoming_acceptance_policy'} & vals.keys():
            raise AccessError(_('Обсяг вхідної перевірки незмінний.'))
        protected = {'quality_state', 'picking_id', 'move_line_id', 'lot_id', 'product_id', 'company_id',
                     'point_id', 'test_type_id', 'measure_on', 'production_id', 'workorder_id'}
        if managed and protected & vals.keys():
            managed.incoming_hold_id._lock()
            managed._lock_measurement_checks()
            for check in managed:
                changed_source = any(
                    (check[name].id if check._fields[name].type == 'many2one' else check[name]) != vals[name]
                    for name in protected - {'quality_state'} & vals.keys()
                )
                # Enterprise may repeat the already-bound lot/source while
                # finishing a tracked receipt. Permit identical values only.
                if changed_source:
                    raise UserError(_('Не можна змінювати джерело та критерії вхідного контролю.'))
                if ('quality_state' in vals and check.quality_state != 'none'
                        and vals['quality_state'] != check.quality_state):
                    raise UserError(_('Результат завершеного вхідного контролю незмінний.'))
                if vals.get('quality_state') == 'pass':
                    check._validate_measurement_can_pass()
                elif vals.get('quality_state') == 'fail' and not any(check.sample_ids.mapped('has_failure')):
                    raise ValidationError(_('Спочатку зафіксуйте невідповідність у матриці.'))
            if vals.get('quality_state') in ('pass', 'fail'):
                managed._snapshot_selected_equipment()
        result = super().write(vals)
        if vals.get('quality_state') == 'fail':
            for check in managed:
                check.incoming_hold_id._record_failure_alert(check)
        return result

    def _validate_measurement_can_pass(self):
        for check in self.filtered('incoming_hold_id'):
            if (not check.measurement_matrix_required or check.incoming_acceptance_policy != 'all_pass'
                    or check.incoming_sample_size < 1 or len(check.sample_ids) != check.incoming_sample_size):
                raise ValidationError(_('Вхідна вибірка не відповідає зафіксованому плану QCP.'))
        return super()._validate_measurement_can_pass()

    def _ensure_manual_measurement_samples(self):
        if self.incoming_hold_id:
            raise UserError(_('Кількість зразків визначена планом вхідного контролю.'))
        return super()._ensure_manual_measurement_samples()

    def _ensure_measurement_editable(self, completed_message=None):
        if self.incoming_hold_id:
            self.incoming_hold_id._lock()
            self._lock_measurement_checks()
        return super()._ensure_measurement_editable(completed_message=completed_message)

    @api.ondelete(at_uninstall=False)
    def _keep_incoming_checks(self):
        if self.filtered('incoming_hold_id'):
            raise UserError(_('Історія вхідного контролю не видаляється.'))


class IncomingSample(models.Model):
    _inherit = 'quality.check.sample'

    def _before_measurement_change(self):
        checks = self.quality_check_id.filtered('incoming_hold_id')
        if checks:
            checks.incoming_hold_id._lock()
            checks._lock_measurement_checks()
        return super()._before_measurement_change()


class IncomingAlert(models.Model):
    _inherit = 'quality.alert'

    incoming_hold_id = fields.Many2one('quality.incoming.hold', readonly=True, copy=False,
                                       check_company=True, ondelete='restrict', index=True)
    incoming_failure_check_ids = fields.Many2many('quality.check', readonly=True, copy=False)
    incoming_failure_description = fields.Html(readonly=True, copy=False)
    incoming_supplier_id = fields.Many2one(related='incoming_hold_id.supplier_id')
    incoming_product_id = fields.Many2one(related='incoming_hold_id.product_id', string='Товар вхідної партії')
    incoming_receipt_id = fields.Many2one(related='incoming_hold_id.receipt_id')
    incoming_lot_id = fields.Many2one(related='incoming_hold_id.lot_id')

    _sql_constraints = [('incoming_alert_unique', 'unique(incoming_hold_id)',
                         'Для цієї вхідної партії сповіщення вже створене.')]

    @api.model_create_multi
    def create(self, vals_list):
        if not internal(self.env) and any(any(vals.get(key) for key in (
                'incoming_hold_id', 'incoming_failure_check_ids', 'incoming_failure_description')) for vals in vals_list):
            raise AccessError(_('Джерело автоматичного сповіщення встановлює система.'))
        if not internal(self.env):
            vals_list = [dict(vals, incoming_hold_id=False, incoming_failure_check_ids=[],
                              incoming_failure_description=False) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if not internal(self.env) and {'incoming_hold_id', 'incoming_failure_check_ids',
                                      'incoming_failure_description'} & vals.keys():
            raise AccessError(_('Джерело автоматичного сповіщення незмінне.'))
        return super().write(vals)

    def action_open_incoming_hold(self):
        self.ensure_one()
        self.incoming_hold_id.check_access_rights('read')
        self.incoming_hold_id.check_access_rule('read')
        return {'type': 'ir.actions.act_window', 'res_model': 'quality.incoming.hold',
                'view_mode': 'form', 'res_id': self.incoming_hold_id.id}

    @api.ondelete(at_uninstall=False)
    def _keep_incoming_alerts(self):
        if self.filtered('incoming_hold_id'):
            raise UserError(_('Автоматичне сповіщення вхідної партії не видаляється.'))
