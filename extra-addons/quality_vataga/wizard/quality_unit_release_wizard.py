from odoo import _, api, fields, models
from ..models.quality_archive import ARCHIVE_MESSAGE, RetiredDialog, archive_disabled


class QualityUnitReleaseWizard(RetiredDialog, models.TransientModel):
    _name = 'quality.unit.release.wizard'
    _description = 'Явний дозвіл випуску конкретних QUNIT'

    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='cascade')
    mode = fields.Selection([('authorize', 'Дозволити випуск'), ('cancel', 'Скасувати дозвіл')], string='Дія', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True)
    reason = fields.Text(string='Причина скасування')
    line_ids = fields.One2many('quality.unit.release.wizard.line', 'wizard_id', string='Конкретні вироби')

    @api.model
    def _open_action(self, production, mode):
        archive_disabled()


    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT production_id FROM quality_unit_release_wizard WHERE id IN %s', [tuple(self.ids)])
            for production in self.env['mrp.production'].browse([row[0] for row in self.env.cr.fetchall()]):
                production._check_unit_release_access()
        return result


class QualityUnitReleaseWizardLine(RetiredDialog, models.TransientModel):
    _name = 'quality.unit.release.wizard.line'
    _description = 'Вибір QUNIT для дозволу випуску'

    wizard_id = fields.Many2one('quality.unit.release.wizard', required=True, ondelete='cascade')
    unit_id = fields.Many2one('quality.production.unit', readonly=True, ondelete='cascade')
    permission_id = fields.Many2one('quality.unit.release.line', readonly=True, ondelete='cascade')
    selected = fields.Boolean(string='Обрати')
    controlled_unit_id = fields.Many2one('quality.production.unit', string='QUNIT', compute='_compute_availability')
    last_workorder_id = fields.Many2one('mrp.workorder', string='Остання операція', compute='_compute_availability')
    execution_done = fields.Boolean(string='Обробку завершено', compute='_compute_availability')
    quality_result = fields.Char(string='Контроль якості', compute='_compute_availability')
    admission_id = fields.Many2one('quality.unit.admission', string='Допуск інспектора', compute='_compute_availability')
    readiness = fields.Char(string='Готовність', compute='_compute_availability')
    eligible = fields.Boolean(compute='_compute_availability')
    unavailable_reason = fields.Text(string='Причина блокування', compute='_compute_availability')

    _sql_constraints = [('one_origin', 'check((unit_id IS NULL) != (permission_id IS NULL))', 'Оберіть виріб або дозвіл.'),
                        ('unit_unique', 'unique(wizard_id, unit_id)', 'QUNIT вже є у майстрі.'),
                        ('permission_unique', 'unique(wizard_id, permission_id)', 'Дозвіл вже є у майстрі.')]


    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_unit_release_wizard_line WHERE id IN %s', [tuple(self.ids)])
            self.env['quality.unit.release.wizard'].browse([row[0] for row in self.env.cr.fetchall()]).check_access_rule('read')
        return result

    @api.depends('unit_id', 'permission_id', 'wizard_id.mode', 'wizard_id.production_id')
    def _compute_availability(self):
        for line in self:
            line.controlled_unit_id = line.unit_id or line.permission_id.unit_id
            line.last_workorder_id = False
            line.execution_done = False
            line.quality_result = False
            line.admission_id = False
            line.readiness = _(ARCHIVE_MESSAGE)
            line.eligible = False
            line.unavailable_reason = _(ARCHIVE_MESSAGE)
