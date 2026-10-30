"""FAIL-QUNIT -> approved Alert -> actual terminal production loss evidence."""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression

from .quality_alert import BOARD_STAGE_XMLIDS
from .native_partial_release import partial_operation, _read


class QualityProductionLossFact(models.Model):
    _name = 'quality.production.loss.fact'
    _inherit = 'quality.native.partial.access'
    _description = 'Незмінний зв’язок FAIL-QUNIT, рішення ТР та виробничої втрати'
    _rec_name = 'unit_id'

    release_id = fields.Many2one('quality.native.partial.release', required=True, readonly=True, ondelete='restrict')
    production_id = fields.Many2one(related='release_id.production_id', string='Виробниче замовлення', store=True, readonly=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    unit_id = fields.Many2one('quality.production.unit', string='Виріб', required=True, readonly=True, ondelete='restrict', index=True)
    alert_id = fields.Many2one('quality.alert', string='Рішення ТР', required=True, readonly=True, ondelete='restrict', index=True)
    loss_id = fields.Many2one('quality.native.production.loss', required=True, readonly=True, ondelete='restrict')
    value = fields.Float(related='loss_id.value', string='Вартість браку', readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)
    accounting_status = fields.Selection(related='loss_id.accounting_status', string='Бухгалтерський облік', readonly=True)
    _sql_constraints = [('unit_unique', 'unique(unit_id)', 'Втрату цього QUNIT вже підтверджено.'),
                        ('loss_unique', 'unique(loss_id)', 'Втрата вже пов’язана з QUNIT.')]

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        scope = [(field, 'in', self.env[model]._search([])) for field, model in (
            ('unit_id', 'quality.production.unit'), ('alert_id', 'quality.alert'),
            ('loss_id', 'quality.native.production.loss'))]
        return super()._search(expression.AND([domain, scope]), offset=offset, limit=limit,
                               order=order, access_rights_uid=access_rights_uid)

    @api.model_create_multi
    def create(self, vals_list):
        operation = partial_operation()
        if not operation or not operation.loss_keys or operation.phase != 'facts':
            raise UserError(_('Втрату QUNIT фіксують лише після реального проведення та завершення MO.'))
        for values in vals_list:
            key = 'qunit:%s' % values.get('unit_id')
            loss = _read(self.env['quality.native.production.loss'].browse(values.get('loss_id')))
            if (set(values) != {'release_id', 'unit_id', 'alert_id', 'loss_id'}
                    or operation.loss_approvals.get(key) != values.get('alert_id')
                    or values['release_id'] != operation.release_id or loss.release_id.id != operation.release_id
                    or loss.unit_key != key or loss.production_id != operation.production
                    or loss.production_id.state != 'done'):
                raise ValidationError(_('Факт втрати не відповідає підтвердженій операції, QUNIT або Alert.'))
        # The base audit model permits creates only in its audit phase. Facts
        # additionally require this narrower exact identity capability.
        operation.phase = 'audit'
        try:
            return super().create(vals_list)
        finally:
            operation.phase = 'facts'

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['unit_id', 'alert_id', 'loss_id'])
            self.env.cr.execute('SELECT unit_id,alert_id,loss_id FROM quality_production_loss_fact WHERE id IN %s', [tuple(self.ids)])
            rows = self.env.cr.fetchall()
            for index, model in enumerate(('quality.production.unit', 'quality.alert', 'quality.native.production.loss')):
                _read(self.env[model].browse({r[index] for r in rows}))
        return result


class QualityLossProduction(models.Model):
    _inherit = 'mrp.production'

    def _native_loss_before_post(self, operation):
        super()._native_loss_before_post(operation)
        if not self.quality_unit_route_required or self.quality_transfer_controlled:
            raise UserError(_('Втрати потребують сучасного підтвердженого поштучного маршруту.'))
        universe = self._quality_partial_universe()
        good = universe.filtered(lambda u: 'qunit:%s' % u.id in operation.keys)
        lost = universe.filtered(lambda u: 'qunit:%s' % u.id in operation.loss_keys)
        if (len(good) != len(operation.keys) or len(lost) != len(operation.loss_keys)
                or set((good | lost).ids) != set(universe.ids)):
            raise ValidationError(_('Годні та браковані QUNIT повинні точно охоплювати первинну партію.'))
        self._quality_partial_validate_units(good)
        lost._assert_not_stock_posted()
        # No RPC/context-selected alert IDs: discover the actual source scope.
        Alert = self.env['quality.alert']
        Alert.flush_model()
        self.env.cr.execute('SELECT id FROM quality_alert WHERE check_id IN %s ORDER BY id',
                            [tuple(self._full_release_checks().ids) or (0,)])
        alerts = _read(Alert.browse([r[0] for r in self.env.cr.fetchall()]))
        for unit in lost:
            matches = self.env['quality.alert']
            for alert in alerts.filtered(lambda a: a.decision_type == 'scrap' and a.is_approved):
                if unit in alert._quality_loss_approved_scope().controlled_unit_id:
                    matches |= alert
            if len(matches) != 1:
                raise ValidationError(_('Кожен FAIL-QUNIT потребує одного однозначного затвердженого Alert списання.'))
            for other in alerts - matches:
                if other._matrix_hold_is_open() and (not other.vataga_failure_sample_ids
                        or unit in other.vataga_failure_sample_ids.controlled_unit_id):
                    raise ValidationError(_('Інший відкритий Alert утримує цей FAIL-QUNIT. Спочатку узгодьте рішення.'))
            operation.loss_approvals['qunit:%s' % unit.id] = matches.id
        # An approved Alert is indivisible: do not silently settle part of it.
        for alert in alerts.filtered(lambda a: a.id in operation.loss_approvals.values()):
            if alert._quality_loss_approved_scope().controlled_unit_id - lost:
                raise ValidationError(_('Потрібна повна затверджена сфера Alert.'))
        operation.unit_ids = tuple(good.ids)

    def _native_partial_after_post(self, release, operation):
        super()._native_partial_after_post(release, operation)
        if not operation.loss_keys:
            return
        self.env['quality.production.loss.fact'].create([{
            'release_id': release.id, 'unit_id': int(loss.unit_key.split(':')[1]),
            'alert_id': operation.loss_approvals[loss.unit_key], 'loss_id': loss.id}
            for loss in release.loss_ids])


class QualityLossAlert(models.Model):
    _inherit = 'quality.alert'

    production_loss_fact_ids = fields.One2many('quality.production.loss.fact', 'alert_id',
                                              string='Підтверджені виробничі втрати', readonly=True, copy=False)
    vataga_scrap_status = fields.Selection(selection_add=[('ready', 'Готово до визнання виробничих втрат'),
                                                        ('done', 'Виробничі втрати підтверджено')])

    @api.depends('production_loss_fact_ids')
    def _compute_scrap_readiness(self):
        return super()._compute_scrap_readiness()

    def write(self, vals):
        if {'check_id', 'production_id', 'workorder_id', 'product_id', 'company_id',
                'vataga_failure_sample_ids', 'production_loss_fact_ids'}.intersection(vals):
            self._lock_board_records()
            self.env['quality.production.loss.fact'].flush_model()
            self.env.cr.execute('SELECT 1 FROM quality_production_loss_fact WHERE alert_id IN %s LIMIT 1',
                                [tuple(self.ids) or (0,)])
            if self.env.cr.fetchone():
                raise UserError(_('Джерело та сфера підтверджених виробничих втрат незмінні.'))
        return super().write(vals)

    def _quality_loss_approved_scope(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if (self.decision_type != 'scrap' or not self.is_approved or not self.approved_by_id or not self.approved_at
                or self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[2])):
            raise UserError(_('Потрібне затверджене рішення ТР «Списання браку» на стадії виконання.'))
        samples = self._matrix_resolution_scope()
        if any(s.sample_result != 'fail' for s in samples):
            raise ValidationError(_('Списання PASS або pending заборонене.'))
        units = samples.controlled_unit_id
        units._assert_not_stock_posted()
        production = self.check_id._identity_production()
        checks = production._full_release_checks().filtered(lambda c: c.point_id == self.check_id.point_id)
        for sample in samples:
            relevant = checks.filtered(lambda c: sample.controlled_unit_id in c.sample_ids.controlled_unit_id)
            if relevant.sorted('id')[-1:] != self.check_id:
                raise ValidationError(_('Для QUNIT існує новіша QC; стара FAIL не є підставою списання.'))
        source = self.check_id.operation_run_id.workorder_id
        processed = source._quality_native_history().filtered(lambda r: r.state == 'processed').controlled_unit_id
        if units - processed and not (source.state == 'done' and source.qty_produced >= production.product_qty):
            raise ValidationError(_('Немає факту фізичної обробки бракованого QUNIT.'))
        return samples

    def _get_scrap_execution_blocker(self):
        if self.production_loss_fact_ids:
            return 'done', _('Виробничі втрати підтверджені. Бухгалтерську проводку за періодичної оцінки ще не створено.')
        status, reason = super()._get_scrap_execution_blocker()
        if status != 'waiting_stock':
            return status, reason
        try:
            self._quality_loss_approved_scope()
        except AccessError:
            raise
        except UserError as error:
            return 'blocked', str(error)
        return 'ready', _('Дія випустить усі готові QUNIT, визнає затверджені втрати та закриє MO без Backorder. '
                          'Потрібне фактично підтверджене повне споживання компонентів. Бухгалтерську проводку не створює.')

    def action_scrap_defective_units(self):
        self.ensure_one()
        if not self.env.user.has_group('quality.group_quality_manager'):
            raise AccessError(_('Виробничі втрати підтверджує керівник якості.'))
        with self.env.cr.savepoint():
            production = self.check_id._identity_production()
            production._native_partial_lock()
            # Same MO -> QC -> Alert lock order as repair/admission.
            for check in production._full_release_checks().sorted('id'):
                check._lock_unit_admission_scope()
            self._lock_board_records()
            if self.production_loss_fact_ids:
                self._validate_production_resolution()
                return True
            samples = self._quality_loss_approved_scope()
            good, _reasons = production._quality_partial_ready_units()
            lost = samples.controlled_unit_id
            production._native_loss_close(['qunit:%s' % u.id for u in good],
                ['qunit:%s' % u.id for u in lost], 'alert-loss:%s' % self.id)
            self.message_post(body=_('Виробничі втрати підтверджено для %(count)s QUNIT. '
                'MO завершено без Backorder; періодична бухгалтерська проводка ще не створена.', count=len(lost)))
            return True

    def action_create_scrap_replacement(self):
        raise UserError(_('Цей сценарій завершує MO з виробничими втратами. Автоматичне нове MO не створюється.'))

    def _validate_production_resolution(self):
        if self.decision_type != 'scrap':
            return super()._validate_production_resolution()
        samples = self._matrix_resolution_scope()
        facts = _read(self.production_loss_fact_ids)
        if (not facts or set(facts.unit_id.ids) != set(samples.controlled_unit_id.ids)
                or len(facts) != len(samples) or any(s.sample_result != 'fail' for s in samples)
                or facts.production_id != self.check_id._identity_production() or facts.production_id.state != 'done'):
            raise UserError(_('Закриття потребує підтверджених виробничих втрат усіх FAIL-QUNIT цього Alert.'))
        return True

    def _production_resolution_complete(self):
        if self.decision_type != 'scrap':
            return super()._production_resolution_complete()
        try:
            return self._validate_production_resolution()
        except AccessError:
            raise
        except UserError:
            return False


class QualityLostUnit(models.Model):
    _inherit = 'quality.production.unit'

    production_loss_fact_ids = fields.One2many('quality.production.loss.fact', 'unit_id', readonly=True)

    def _assert_not_stock_posted(self):
        super()._assert_not_stock_posted()
        self.env['quality.production.loss.fact'].flush_model()
        self.env.cr.execute('SELECT id FROM quality_production_loss_fact WHERE unit_id IN %s', [tuple(self.ids) or (0,)])
        if self.env.cr.fetchone():
            raise UserError(_('Виріб уже визнано виробничою втратою. Повторна обробка, ремонт або випуск заборонені.'))


class QualityLostCheck(models.Model):
    _inherit = 'quality.check'

    def _guard_unit_admission_evidence_edit(self, unit_ids=None):
        self.env['quality.production.loss.fact'].flush_model()
        self.env.cr.execute('''SELECT 1 FROM quality_production_loss_fact f JOIN quality_alert a ON a.id=f.alert_id
            WHERE a.check_id IN %s LIMIT 1''', [tuple(self.ids) or (0,)])
        if self.env.cr.fetchone():
            raise UserError(_('QC є підставою підтверджених виробничих втрат і не редагується.'))
        return super()._guard_unit_admission_evidence_edit(unit_ids)
