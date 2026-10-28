from odoo import _, api, fields, models

from ..models.quality_archive import ARCHIVE_MESSAGE, RetiredDialog, archive_disabled
from ..models.quality_release_access import check_stock_release_roles


class QualityUnitStockReleaseWizard(RetiredDialog, models.TransientModel):
    _name = 'quality.unit.stock.release.wizard'
    _description = 'Підготовка поіменного складського випуску без проведення'

    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='cascade')
    request_key = fields.Char(required=True, readonly=True)
    line_ids = fields.One2many('quality.unit.stock.release.wizard.line', 'wizard_id', string='Конкретні вироби')
    posting_note = fields.Text(compute='_compute_posting_note')

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        if not check_stock_release_roles(self.env.user, raise_exception=raise_exception):
            return False
        return super().check_access_rights(operation, raise_exception=raise_exception)

    def _compute_posting_note(self):
        for wizard in self:
            wizard.posting_note = _(ARCHIVE_MESSAGE)


    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT production_id FROM quality_unit_stock_release_wizard WHERE id IN %s', [tuple(self.ids)])
            for production in self.env['mrp.production'].browse([row[0] for row in self.env.cr.fetchall()]):
                production._check_unit_stock_release_access()
        return result

    def action_prepare(self):
        archive_disabled()


class QualityUnitStockReleaseWizardLine(RetiredDialog, models.TransientModel):
    _name = 'quality.unit.stock.release.wizard.line'
    _description = 'Вибір QUNIT для підготовки складського випуску'

    wizard_id = fields.Many2one('quality.unit.stock.release.wizard', required=True, ondelete='cascade')
    unit_id = fields.Many2one('quality.production.unit', string='QUNIT', required=True, readonly=True, ondelete='cascade')
    selected = fields.Boolean(string='Обрати')
    permission_id = fields.Many2one('quality.unit.release.line', string='Дозвіл 13.4А', compute='_compute_availability')
    check_id = fields.Many2one('quality.check', string='Останній QC', compute='_compute_availability')
    admission_id = fields.Many2one('quality.unit.admission', string='Чинний допуск', compute='_compute_availability')
    eligible = fields.Boolean(string='Дозвіл для підготовки чинний', compute='_compute_availability')
    unavailable_reason = fields.Text(string='Причина блокування', compute='_compute_availability')
    stock_status = fields.Char(string='Фактичний випуск', compute='_compute_availability')

    _sql_constraints = [('unit_unique', 'unique(wizard_id, unit_id)', 'QUNIT вже є в майстрі.')]

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        if not check_stock_release_roles(self.env.user, raise_exception=raise_exception):
            return False
        return super().check_access_rights(operation, raise_exception=raise_exception)


    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_unit_stock_release_wizard_line WHERE id IN %s', [tuple(self.ids)])
            self.env['quality.unit.stock.release.wizard'].browse([row[0] for row in self.env.cr.fetchall()]).check_access_rule(operation)
        return result

    @api.depends('unit_id', 'unit_id.quality_release_state', 'wizard_id.production_id')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_availability(self):
        for line in self:
            line.permission_id = False
            line.check_id = False
            line.admission_id = False
            line.eligible = False
            line.unavailable_reason = _(ARCHIVE_MESSAGE)
            line.stock_status = False
