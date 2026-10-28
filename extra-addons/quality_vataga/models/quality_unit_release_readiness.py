from odoo import _, api, fields, models
from odoo.exceptions import UserError
from .quality_archive import ARCHIVE_MESSAGE


RELEASE_STATES = [
    ('not_ready', 'Не готовий'), ('blocked', 'Заблоковано'),
    ('ready', 'Готовий до випуску'), ('authorized', 'Дозволено до випуску'),
    ('lapsed', 'Дозвіл втратив чинність'),
    ('produced', 'Випущено на склад'),
]


class QualityUnitReleaseReadiness(models.Model):
    _inherit = 'quality.production.unit'

    quality_release_revision = fields.Integer(default=0, readonly=True, copy=False)
    quality_release_line_ids = fields.One2many('quality.unit.release.line', 'unit_id', string='Історія дозволів випуску', readonly=True)
    quality_release_state = fields.Selection(RELEASE_STATES, string='Готовність до випуску', compute='_compute_release_readiness')
    quality_release_reason = fields.Text(string='Причина недоступності', compute='_compute_release_readiness')
    quality_release_last_workorder_id = fields.Many2one('mrp.workorder', string='Остання операція', compute='_compute_release_readiness')
    quality_release_execution_done = fields.Boolean(string='Обробку завершено', compute='_compute_release_readiness')
    quality_release_quality_result = fields.Char(string='Контроль якості', compute='_compute_release_readiness')
    quality_release_admission_id = fields.Many2one('quality.unit.admission', string='Допуск інспектора', compute='_compute_release_readiness')

    @api.model_create_multi
    def create(self, vals_list):
        if any({'quality_release_revision', 'quality_release_line_ids', 'quality_release_state'}.intersection(vals) for vals in vals_list):
            raise UserError(_('Службові поля підготовки випуску встановлює система.'))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_quality_release_')}
        return super(QualityUnitReleaseReadiness, self.with_context(context)).create(vals_list)


    @api.depends('production_id.quality_transfer_revision', 'quality_release_revision', 'quality_release_line_ids.state', 'quality_execution_ids.state')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_release_readiness(self):
        # Compatibility fields only; no release certification is generated.
        for unit in self:
            unit.quality_release_state = 'blocked'
            unit.quality_release_reason = _(ARCHIVE_MESSAGE)
            unit.quality_release_last_workorder_id = False
            unit.quality_release_execution_done = False
            unit.quality_release_quality_result = False
            unit.quality_release_admission_id = False

    def write(self, vals):
        if any(name.startswith('quality_release_') for name in vals):
            raise UserError(_('Архівні реквізити випуску незмінні.'))
        return super().write(vals)
