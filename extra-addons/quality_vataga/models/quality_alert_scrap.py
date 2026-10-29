"""Scrap readiness without an unverified defect disposition.

The native release bridge records posted PASS-unit facts. It does not establish
stock provenance or a supported manufacturing disposition for an unproduced
FAIL unit. Native Scrap's product/location/lot selection is insufficient to
identify that physical unit. No replacement may be based on a planned document.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .quality_alert import BOARD_STAGE_XMLIDS


class QualityAlertScrap(models.Model):
    _inherit = 'quality.alert'

    vataga_scrap_status = fields.Selection([
        ('approval', 'Очікує затвердження ТР'),
        ('scope', 'Потрібне уточнення виробів'),
        ('waiting_stock', 'Очікує ідентифікованого складського випуску'),
        ('blocked', 'Виконання заблоковано'),
    ], string='Стан списання', compute='_compute_scrap_readiness', compute_sudo=False)
    vataga_scrap_block_reason = fields.Text(
        string='Причина очікування', compute='_compute_scrap_readiness', compute_sudo=False)

    @api.depends(
        'decision_type', 'stage_id', 'is_approved', 'approved_by_id', 'approved_at',
        'check_id', 'check_id.operation_run_id', 'check_id.operation_run_id.state',
        'vataga_failure_sample_ids', 'vataga_failure_sample_ids.controlled_unit_id',
        'vataga_failure_sample_ids.sample_result',
    )
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_scrap_readiness(self):
        for alert in self:
            if alert.decision_type != 'scrap':
                alert.vataga_scrap_status = False
                alert.vataga_scrap_block_reason = False
                continue
            try:
                status, reason = alert._get_scrap_execution_blocker()
            except AccessError:
                status, reason = 'blocked', _('Немає доступу до всіх вихідних документів та компанії. Списання заборонене.')
            alert.vataga_scrap_status = status
            alert.vataga_scrap_block_reason = reason

    def _get_scrap_execution_blocker(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.decision_type != 'scrap':
            return 'blocked', _('Для цього Alert не обрано рішення ТР «Списання браку».')
        if not self.is_approved or not self.approved_by_id or not self.approved_at:
            return 'approval', _('Спочатку затвердьте рішення ТР «Списання браку». Рішення не є фактом списання.')
        if self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[2]):
            return 'blocked', _('Виконання списання можливе лише на стадії «Виконання рішення». Закриття без фактичного Scrap заборонене.')
        try:
            samples = self._matrix_resolution_scope()
        except AccessError:
            raise
        except UserError as error:
            return 'scope', str(error)
        if any(sample.sample_result != 'fail' for sample in samples):
            return 'blocked', _('Сфера Alert містить PASS або pending. Не можна підмінити підтверджений брак іншими виробами або списати виправлений виріб.')
        return 'waiting_stock', _(
            'Списання закрито: для вибраного FAIL-QUNIT не реалізовано перевірений контракт '
            'походження stock.move.line та доступного внутрішнього залишку. Факти штатного '
            'випуску PASS-QUNIT не створюють залишку незавершеного бракованого виробу. '
            'Віртуальна виробнича локація, QRUN, lot/serial або довільний залишок того самого '
            'товару не є доказом походження. Не реалізовано та не перевірено проведення браку '
            'з фактичним споживанням компонентів і оцінкою запасів без удаваного випуску '
            'годної продукції та передчасного Backorder. Після цього ще потрібні фактичний '
            'Scrap, його точне зіставлення з QUNIT і одноразове дозамовлення фактично списаної '
            'кількості. Списання й нове MO зараз не створюються.')

    def action_scrap_defective_units(self):
        """Fail closed also for direct RPC; never create a draft success proxy."""
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.env['stock.scrap'].check_access_rights('create')
        _status, reason = self._get_scrap_execution_blocker()
        raise UserError(reason)

    def action_create_scrap_replacement(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.env['mrp.production'].check_access_rights('create')
        _status, reason = self._get_scrap_execution_blocker()
        raise UserError(_(
            'Дозамовлення заборонене: немає підтвердженого фактичного списання конкретних '
            'QUNIT та обліку вже компенсованої кількості. FAIL, рішення ТР і довільний '
            'Scrap того самого товару не є підставою нового MO. %(reason)s', reason=reason))

    def _validate_production_resolution(self):
        if self.decision_type == 'scrap':
            _status, reason = self._get_scrap_execution_blocker()
            raise UserError(reason)
        return super()._validate_production_resolution()

    def _production_resolution_complete(self):
        if self.decision_type == 'scrap':
            return False
        return super()._production_resolution_complete()
