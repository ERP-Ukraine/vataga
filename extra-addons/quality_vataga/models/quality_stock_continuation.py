"""Named continuation guards; no stock mutation and no costing ACL grants."""

from odoo import _, api, models
from odoo.exceptions import UserError

from .stock_posting_core import access


class QualityStockHistory(models.Model):
    _inherit = 'mrp.production'

    def _check_named_stock_history(self):
        self._check_no_posted_stock_history()
        return self.env['quality.production.unit']


class QualityPostedUnit(models.Model):
    _inherit = 'quality.production.unit'

    def _stock_posted(self):
        access(self)
        self.env['quality.stock.posting'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE unit_id IN %s LIMIT 1', [tuple(self.ids) or (0,)])
        return bool(self.env.cr.fetchone())

    def _assert_not_stock_posted(self):
        if self._stock_posted():
            raise UserError(_('Виріб уже випущено на склад. Повторний маршрут, QC або обробка потребують окремої процедури повернення.'))


class QualityPostedAdmission(models.Model):
    _inherit = 'quality.unit.admission'

    def _get_consumption_error(self):
        if self.controlled_unit_id._stock_posted():
            return _('Допуск використано фактичним складським випуском. Звичайне скасування заборонено.')
        return super()._get_consumption_error()


class QualityPostedPermission(models.Model):
    _inherit = 'quality.unit.release.line'

    def _stock_permission_used(self):
        access(self)
        self.env['quality.stock.posting'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE permission_id IN %s LIMIT 1', [tuple(self.ids) or (0,)])
        return bool(self.env.cr.fetchone())


class QualityPostedTransfer(models.Model):
    _inherit = 'quality.unit.transfer'

    @api.model
    def _check_admission(self, admission, destination):
        admission.controlled_unit_id._assert_not_stock_posted()
        return super()._check_admission(admission, destination)

    def _get_execution_error(self, workorder, current=None):
        if self.controlled_unit_id._stock_posted():
            return _('Виріб уже випущено на склад.')
        return super()._get_execution_error(workorder, current=current)


class QualityPostedExecution(models.Model):
    _inherit = 'quality.unit.execution'

    def _validate_control_selection(self, workorder):
        self.controlled_unit_id._assert_not_stock_posted()
        return super()._validate_control_selection(workorder)


class QualityPostedCheck(models.Model):
    _inherit = 'quality.check'

    def _identify_samples_locked(self, mode, mapping, run, workorder, confirmed):
        units = self.env['quality.production.unit'].browse([row['unit_id'] for row in mapping if row.get('unit_id')])
        units._assert_not_stock_posted()
        return super()._identify_samples_locked(mode, mapping, run, workorder, confirmed)


class QualityPostedSample(models.Model):
    _inherit = 'quality.check.sample'

    def _prepare_rework_tracking(self):
        self.controlled_unit_id._assert_not_stock_posted()
        return super()._prepare_rework_tracking()
