from uuid import uuid4

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class QualityUnitAdmissionWizard(models.TransientModel):
    _name = 'quality.unit.admission.wizard'
    _inherit = 'quality.admission.access'
    _description = 'Явне рішення інспектора про допуск виробів'

    check_id = fields.Many2one('quality.check', string='Перевірка', required=True, readonly=True, ondelete='cascade')
    mode = fields.Selection([('confirm', 'Підтвердження'), ('revoke', 'Скасування')], required=True, readonly=True, default='confirm')
    operation_run_id = fields.Many2one(related='check_id.operation_run_id', readonly=True)
    workorder_id = fields.Many2one(related='operation_run_id.workorder_id', readonly=True)
    request_key = fields.Char(readonly=True, required=True, copy=False)
    reason = fields.Text(string='Причина скасування')
    line_ids = fields.One2many('quality.unit.admission.wizard.line', 'wizard_id', string='Конкретні вироби')

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if values.get('check_id'):
            check = self.env['quality.check'].browse(values['check_id']).exists()
            if not check:
                raise UserError(_('Перевірку не знайдено.'))
            check._check_unit_admission_access()
            if 'line_ids' in names:
                values['line_ids'] = [Command.create({'sample_id': sample.id, 'selected': False}) for sample in check.sample_ids.sorted('sample_number')]
        return values

    @api.model_create_multi
    def create(self, vals_list):
        default_check = self.default_get(['check_id']).get('check_id') if any('check_id' not in vals for vals in vals_list) else False
        prepared = []
        for vals in vals_list:
            if set(vals) - {'check_id', 'mode', 'reason', 'line_ids'}:
                raise UserError(_('Службові поля майстра встановлює система.'))
            check = self.env['quality.check'].browse(vals.get('check_id', default_check)).exists()
            if not check:
                raise UserError(_('Перевірку не знайдено.'))
            check._check_unit_admission_access()
            prepared.append(dict(vals, request_key=str(uuid4())))
        return super().create(prepared)

    def write(self, vals):
        if set(vals) - {'reason', 'line_ids'}:
            raise UserError(_('Не можна змінити перевірку, дію або ключ рішення майстра.'))
        return super().write(vals)

    def action_apply(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        selected = self.line_ids.filtered('selected')
        self.check_id._execute_unit_admission_action(
            selected.sample_id.ids, self.mode, self.request_key, self.reason or '',
            expected_by_sample={line.sample_id.id: line.expected_admission_id.id for line in selected},
        )
        return {
            'type': 'ir.actions.act_window_close',
            'infos': {'quality_vataga_check_id': self.check_id.id},
        }


class QualityUnitAdmissionWizardLine(models.TransientModel):
    _name = 'quality.unit.admission.wizard.line'
    _inherit = 'quality.admission.access'
    _description = 'Вибір конкретного виробу для рішення про допуск'
    _order = 'sample_number, id'

    wizard_id = fields.Many2one('quality.unit.admission.wizard', required=True, ondelete='cascade')
    check_id = fields.Many2one(related='wizard_id.check_id', store=True, readonly=True)
    sample_id = fields.Many2one('quality.check.sample', required=True, readonly=True, ondelete='cascade')
    sample_number = fields.Integer(related='sample_id.sample_number', readonly=True)
    controlled_unit_id = fields.Many2one(related='sample_id.controlled_unit_id', readonly=True, compute_sudo=False)
    sample_result = fields.Selection(related='sample_id.sample_result', readonly=True, compute_sudo=False)
    expected_admission_id = fields.Many2one('quality.unit.admission', readonly=True, ondelete='set null')
    selected = fields.Boolean(string='Обрати', default=False)
    eligible = fields.Boolean(compute='_compute_availability')
    admission_state = fields.Selection([
        ('held', 'Утримано'), ('confirmed', 'Допущено'), ('revoked', 'Скасовано'),
        ('blocked', 'Допуск призупинено'),
    ], string='Стан допуску', compute='_compute_availability')
    unavailable_reason = fields.Text(string='Причина недоступності', compute='_compute_availability')

    _sql_constraints = [('wizard_sample_unique', 'unique(wizard_id, sample_id)', 'Зразок має бути в майстрі лише один раз.')]

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if set(vals) - {'wizard_id', 'sample_id', 'selected'}:
                raise UserError(_('Службові дані виробу встановлює система.'))
            wizard = self.env['quality.unit.admission.wizard'].browse(vals.get('wizard_id')).exists()
            wizard.check_access_rights('write')
            wizard.check_access_rule('write')
            sample = self.env['quality.check.sample'].browse(vals.get('sample_id')).exists()
            if not wizard or not sample or sample.quality_check_id != wizard.check_id:
                raise ValidationError(_('Зразок має належати перевірці цього майстра.'))
            active = wizard.check_id._active_run_admissions().filtered(
                lambda record: record.controlled_unit_id.id == sample.controlled_unit_id.id,
            ) if wizard.check_id.operation_run_id else self.env['quality.unit.admission']
            if active:
                active.sudo(False).check_access_rights('read')
                active.sudo(False).check_access_rule('read')
            prepared.append(dict(vals, selected=bool(vals.get('selected')), expected_admission_id=active.id or False))
        return super().create(prepared)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['wizard_id'])
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_unit_admission_wizard_line WHERE id IN %s', [tuple(self.ids)])
            wizards = self.env['quality.unit.admission.wizard'].browse([row[0] for row in self.env.cr.fetchall()])
            wizards.check_access_rights('read' if operation == 'read' else 'write')
            wizards.check_access_rule('read' if operation == 'read' else 'write')
        return result

    def write(self, vals):
        if set(vals) - {'selected'}:
            raise UserError(_('У рядку майстра можна змінити лише явний вибір виробу.'))
        self.wizard_id.check_access_rights('write')
        self.wizard_id.check_access_rule('write')
        return super().write(vals)

    @api.depends('sample_id.sample_result', 'selected', 'wizard_id.mode', 'check_id.operation_run_id.unit_admission_ids.state')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_availability(self):
        for line in self:
            check, sample = line.check_id, line.sample_id
            active = check._active_run_admissions().filtered(lambda record: record.controlled_unit_id.id == sample.controlled_unit_id.id) if check.operation_run_id else self.env['quality.unit.admission']
            error = check._get_unit_admission_error(sample)
            state = 'blocked' if active and active._get_effectiveness_error() else ('confirmed' if active else 'held')
            if not active and sample.controlled_unit_id and self.env['quality.unit.admission'].search_count([
                ('operation_run_id', '=', check.operation_run_id.id),
                ('controlled_unit_id', '=', sample.controlled_unit_id.id), ('state', '=', 'revoked'),
            ], limit=1):
                state = 'revoked'
            if line.wizard_id.mode == 'revoke':
                error = active._get_consumption_error() if active else _('Немає чинного допуску для скасування.')
                if active:
                    try:
                        active.sudo(False).check_access_rule('write')
                    except AccessError:
                        error = _('Немає прав на скасування цього допуску.')
                    except (UserError, ValidationError) as access_error:
                        error = str(access_error)
            elif active:
                error = _('Виріб вже має чинний допуск цього запуску.')
            line.eligible = not bool(error)
            line.admission_state = state
            line.unavailable_reason = error or ''
