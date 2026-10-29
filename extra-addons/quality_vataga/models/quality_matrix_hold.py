"""Addressed matrix holds; no stock posting, costing or release authority."""

import math

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from .native_backorder import native_operation_allows


class QualityMatrixHoldAlert(models.Model):
    _inherit = 'quality.alert'

    vataga_failure_sample_ids = fields.Many2many(
        'quality.check.sample', 'quality_alert_failure_sample_rel', 'alert_id', 'sample_id',
        string='Зразки, яких стосується Alert', readonly=True, copy=False)
    can_bind_matrix_failure_scope = fields.Boolean(compute='_compute_can_bind_matrix_failure_scope')

    @api.depends('check_id', 'check_id.x_control_stage', 'vataga_failure_sample_ids')
    def _compute_can_bind_matrix_failure_scope(self):
        for alert in self:
            alert.can_bind_matrix_failure_scope = bool(
                alert.check_id.x_control_stage == 'in_process' and not alert.vataga_failure_sample_ids)

    @api.model_create_multi
    def create(self, vals_list):
        if any('vataga_failure_sample_ids' in vals for vals in vals_list):
            raise UserError(_('Сферу невідповідності встановлює система або явне уточнення керівника якості.'))
        defaults = self.default_get(['check_id'])
        checks = self.env['quality.check'].browse(sorted({
            vals.get('check_id', defaults.get('check_id')) for vals in vals_list
            if vals.get('check_id', defaults.get('check_id'))})).exists()
        sources = checks.filtered(lambda check: check.x_control_stage == 'in_process')
        if sources:
            sources.check_access_rights('read')
            sources.check_access_rule('read')
        productions = self.env['mrp.production']
        for check in sources:
            productions |= check._identity_production()
        productions.sorted('id')._lock_quality_transfer_scope()
        for check in sources.sorted('id'):
            check._lock_unit_admission_scope()
        prepared = []
        for vals in vals_list:
            check = self.env['quality.check'].browse(vals.get('check_id', defaults.get('check_id'))).exists()
            values = dict(vals)
            if check and check.x_control_stage == 'in_process':
                check.check_access_rights('read')
                check.check_access_rule('read')
                samples = check.sample_ids.filtered(lambda sample: sample.sample_result == 'fail')
                values['vataga_failure_sample_ids'] = [Command.set(samples.ids)]
            prepared.append(values)
        context = {key: value for key, value in self.env.context.items()
                   if key != 'default_vataga_failure_sample_ids'}
        return super(QualityMatrixHoldAlert, self.with_context(context)).create(prepared)

    def write(self, vals):
        if 'vataga_failure_sample_ids' in vals:
            raise UserError(_('Зафіксовані зразки Alert не змінюють вручну.'))
        if 'check_id' in vals and any(alert.check_id.x_control_stage == 'in_process'
                and alert.check_id.id != vals['check_id'] for alert in self):
            raise UserError(_('Перевірку міжопераційного Alert не переприв’язують.'))
        return super().write(vals)

    def unlink(self):
        if any(alert.check_id.x_control_stage == 'in_process' and alert._matrix_hold_is_open() for alert in self):
            raise UserError(_('Відкритий міжопераційний Alert не видаляють для обходу утримання.'))
        return super().unlink()

    def _matrix_hold_is_open(self):
        self.ensure_one()
        # Our five workflow stages never infer closure from Enterprise
        # `done`, folding or board approval. Keep the previous semantics of
        # historical native stages, without guessing if `done` is absent.
        if self.stage_id in self._board_stages():
            return not self._is_managed_closed()
        return 'done' not in self.stage_id._fields or not self.stage_id.done

    def _bind_matrix_failure_scope(self, samples, reason):
        self.ensure_one()
        if not self.env.user.has_group('quality.group_quality_manager'):
            raise AccessError(_('Уточнення історичної сфери Alert доступне керівнику якості.'))
        self.check_access_rights('write')
        self.check_access_rule('write')
        check = self.check_id
        if not check or check.x_control_stage != 'in_process':
            raise ValidationError(_('Уточнення стосується лише міжопераційного Alert.'))
        check.check_access_rights('read')
        check.check_access_rule('read')
        samples.check_access_rights('read')
        samples.check_access_rule('read')
        check._identity_production()._lock_quality_transfer_scope()
        check._lock_unit_admission_scope()
        self.invalidate_recordset(['vataga_failure_sample_ids'])
        if (self.vataga_failure_sample_ids or check.x_control_stage != 'in_process'
                or not samples or samples.quality_check_id != check or not reason.strip()):
            raise ValidationError(_('Потрібні ще не визначена сфера міжопераційного Alert, явні зразки його перевірки та підстава.'))
        # Current PASS is not evidence of what failed when an old Alert was
        # created. This is an explicit audited identification, not inference.
        super(QualityMatrixHoldAlert, self).write({'vataga_failure_sample_ids': [Command.set(samples.ids)]})
        self.message_post(body=_('Уточнено зразки Alert: %(samples)s. Підстава: %(reason)s',
            samples=', '.join(str(number) for number in samples.mapped('sample_number')), reason=reason.strip()), subtype_xmlid='mail.mt_note')

    def action_open_matrix_failure_scope(self):
        self.ensure_one()
        if not self.env.user.has_group('quality.group_quality_manager'):
            raise AccessError(_('Потрібні права керівника якості.'))
        self.check_access_rights('write')
        self.check_access_rule('write')
        return dict(type='ir.actions.act_window', res_model='quality.matrix.failure.scope.wizard',
            name=_('Уточнити вироби Alert'), view_mode='form', target='new',
            views=[(self.env.ref('quality_vataga.matrix_failure_scope_wizard_form').id, 'form')],
            context={'default_alert_id': self.id})


class QualityMatrixHoldCheck(models.Model):
    _inherit = 'quality.check'

    def unlink(self):
        checks = self.filtered(lambda check: check.x_control_stage == 'in_process')
        if checks:
            self.env['quality.alert'].flush_model(['check_id'])
            self.env.cr.execute('SELECT id FROM quality_alert WHERE check_id IN %s', [tuple(checks.ids)])
            alerts = self.env['quality.alert'].browse([row[0] for row in self.env.cr.fetchall()])
            alerts.check_access_rights('read')
            alerts.check_access_rule('read')
            if any(alert._matrix_hold_is_open() for alert in alerts):
                raise UserError(_('Перевірку з відкритим Alert не видаляють для обходу утримання.'))
        return super().unlink()

    def _matrix_alert_hold_error(self, sample):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        unit_id = sample.controlled_unit_id.id or 0
        if 'workorder_id' in self._fields and self.workorder_id:
            ancestors = self.workorder_id
            current = self.workorder_id.quality_native_parent_workorder_id
            while current and current not in ancestors:
                current.check_access_rights('read')
                current.check_access_rule('read')
                ancestors |= current
                current = current.quality_native_parent_workorder_id
            history = 'q.workorder_id IN %s'
            scope = tuple(ancestors.ids)
        else:
            history = 'q.point_id = %s'
            scope = self.point_id.id or 0
        self.env['quality.alert'].flush_model(['check_id', 'vataga_failure_sample_ids'])
        self.env['quality.check.sample'].flush_model(['quality_check_id', 'controlled_unit_id'])
        self.env.cr.execute('''SELECT a.id FROM quality_alert a WHERE
            (a.check_id=%s OR a.check_id IN (
                SELECT s.quality_check_id FROM quality_check_sample s JOIN quality_check q ON q.id=s.quality_check_id
                JOIN quality_point p ON p.id=q.point_id WHERE s.controlled_unit_id=%s
                AND p.x_control_stage='in_process' AND ''' + history + '''))
            AND (NOT EXISTS (SELECT 1 FROM quality_alert_failure_sample_rel r WHERE r.alert_id=a.id)
                OR EXISTS (SELECT 1 FROM quality_alert_failure_sample_rel r JOIN quality_check_sample s ON s.id=r.sample_id
                    WHERE r.alert_id=a.id AND (s.id=%s OR (s.controlled_unit_id=%s AND %s!=0)))) ORDER BY a.id''',
            [self.id, unit_id, scope, sample.id, unit_id, unit_id])
        alerts = self.env['quality.alert'].browse([row[0] for row in self.env.cr.fetchall()])
        alerts.check_access_rights('read')
        alerts.check_access_rule('read')
        alerts.check_id.check_access_rights('read')
        alerts.check_id.check_access_rule('read')
        for alert in alerts:
            if alert._matrix_hold_is_open():
                if not alert.vataga_failure_sample_ids:
                    return _('Сферу відкритого Alert %(alert)s ще не уточнено. Потрібна явна прив’язка проблемних зразків.', alert=alert.display_name)
                return _('Виріб утримується відкритим Alert %(alert)s до завершення його обробки.', alert=alert.display_name)
        return False

    def _get_unit_admission_error(self, sample):
        error = super()._get_unit_admission_error(sample)
        if error:
            return error
        try:
            return self._matrix_alert_hold_error(sample)
        except AccessError:
            return _('Немає доступу до всіх Alert цього виробу; передача заборонена.')


class MatrixNativeCompletion(models.Model):
    _inherit = 'mrp.production'

    def _check_matrix_native_completion(self):
        # The existing controlled-MO barrier remains entirely authoritative.
        # This closes the earlier gap BEFORE first named handoff activation.
        for production in self.sorted('id'):
            if native_operation_allows(production):
                continue
            if production.quality_transfer_controlled:
                continue
            production._lock_quality_transfer_scope()
            Check = self.env['quality.check']
            Check.flush_model()
            scope = 'c.production_id=%s'
            params = [production.id]
            if 'workorder_id' in Check._fields:
                scope += ' OR c.workorder_id IN (SELECT id FROM mrp_workorder WHERE production_id=%s)'
                params.append(production.id)
            self.env.cr.execute("SELECT c.id FROM quality_check c JOIN quality_point p ON p.id=c.point_id WHERE p.x_control_stage='in_process' AND (" + scope + ') ORDER BY c.id', params)
            checks = Check.browse([row[0] for row in self.env.cr.fetchall()])
            if not checks:
                continue
            checks.check_access_rights('read')
            checks.check_access_rule('read')
            unit = self.env.ref('uom.product_uom_unit')
            if production.product_uom_id.category_id != unit.category_id:
                raise UserError(_('Матриця не підтверджує виробничу одиницю вимірювання.'))
            requested = production.product_uom_id._compute_quantity(
                production.qty_producing or production.product_qty, unit, round=False)
            reported = production.product_uom_id._compute_quantity(production.qty_produced, unit, round=False)
            for check in checks:
                if (check.quality_state == 'fail' or check._get_in_process_batch_error() or not check.sample_ids
                        or not math.isfinite(requested) or requested <= 0 or requested != int(requested)
                        or requested > check.x_passed_qty
                        or not math.isfinite(reported) or reported < 0 or reported > check.x_passed_qty
                        or any(sample.sample_result != 'pass' for sample in check.sample_ids)):
                    raise UserError(_('Стандартне завершення всієї партії не дозволене: є FAIL/pending або некоректна матриця. Годні вироби передаються окремо; часткове складське проведення без передчасного Backorder очікує Enterprise-перевірки.'))
                check._validate_measurement_can_pass()
                for sample in check.sample_ids:
                    error = check._matrix_alert_hold_error(sample)
                    if error:
                        raise UserError(error)
