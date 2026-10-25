from uuid import uuid4

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError


class QualityUnitReleaseWizard(models.TransientModel):
    _name = 'quality.unit.release.wizard'
    _description = 'Явний дозвіл випуску конкретних QUNIT'

    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='cascade')
    mode = fields.Selection([('authorize', 'Дозволити випуск'), ('cancel', 'Скасувати дозвіл')], string='Дія', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True)
    reason = fields.Text(string='Причина скасування')
    line_ids = fields.One2many('quality.unit.release.wizard.line', 'wizard_id', string='Конкретні вироби')

    @api.model
    def _open_action(self, production, mode):
        production._check_unit_release_access()
        if mode not in ('authorize', 'cancel'):
            raise ValidationError(_('Невідома дія дозволу випуску.'))
        return {'type': 'ir.actions.act_window', 'name': _('Дозволити випуск виробів') if mode == 'authorize' else _('Скасувати дозвіл випуску'),
                'res_model': self._name, 'target': 'new', 'view_mode': 'form',
                'views': [(self.env.ref('quality_vataga.quality_unit_release_wizard_form').id, 'form')],
                'context': {'default_production_id': production.id, 'default_mode': mode}}

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if values.get('production_id') and values.get('mode'):
            production = self.env['mrp.production'].browse(values['production_id'])
            production._check_unit_release_access()
            if values['mode'] not in ('authorize', 'cancel'):
                raise ValidationError(_('Невідома дія.'))
            if 'line_ids' in names:
                if values['mode'] == 'authorize':
                    rows = [{'unit_id': unit.id} for unit in production.quality_release_unit_ids.sorted('id')]
                else:
                    rows = [{'permission_id': line.id} for line in self.env['quality.unit.release.line'].search([('production_id', '=', production.id), ('state', '=', 'confirmed')])]
                values['line_ids'] = [Command.create(dict(row, selected=False)) for row in rows]
        return values

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if set(vals) - {'production_id', 'mode', 'reason', 'line_ids'}:
                raise UserError(_('Реквізити та ключ майстра встановлює система.'))
            values = dict(self.default_get(['production_id', 'mode']), **vals)
            self.env['mrp.production'].browse(values.get('production_id'))._check_unit_release_access()
            if values.get('mode') not in ('authorize', 'cancel'):
                raise ValidationError(_('Невідома дія.'))
            prepared.append(dict(values, request_key=str(uuid4())))
        context = dict(self.env.context)
        context.pop('default_request_key', None)
        return super(QualityUnitReleaseWizard, self.with_context(context)).create(prepared)

    def write(self, vals):
        if set(vals) - {'reason', 'line_ids'}:
            raise UserError(_('MO, дію та ключ підтвердження змінювати не можна.'))
        return super().write(vals)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT production_id FROM quality_unit_release_wizard WHERE id IN %s', [tuple(self.ids)])
            for production in self.env['mrp.production'].browse([row[0] for row in self.env.cr.fetchall()]):
                production._check_unit_release_access()
        return result

    def action_apply(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.production_id._check_unit_release_access()
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise ValidationError(_('Явно оберіть конкретні QUNIT.'))
        if self.mode == 'authorize':
            if selected.permission_id or len(selected.unit_id) != len(selected):
                raise ValidationError(_('Оберіть різні QUNIT для дозволу.'))
            self.env['quality.unit.release.batch']._authorize(self.production_id, selected.unit_id, self.request_key)
        else:
            if selected.unit_id or len(selected.permission_id) != len(selected):
                raise ValidationError(_('Оберіть різні чинні записи дозволу.'))
            selected.permission_id._cancel(self.production_id, self.reason or '')
        return {'type': 'ir.actions.act_window_close', 'infos': {'quality_vataga_production_id': self.production_id.id}}


class QualityUnitReleaseWizardLine(models.TransientModel):
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

    @api.model_create_multi
    def create(self, vals_list):
        if any(set(vals) - {'wizard_id', 'unit_id', 'permission_id', 'selected'} for vals in vals_list):
            raise UserError(_('Реквізити рядка встановлює система.'))
        for vals in vals_list:
            wizard = self.env['quality.unit.release.wizard'].browse(vals.get('wizard_id'))
            wizard.check_access_rule('write')
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) - {'selected'}:
            raise UserError(_('Виріб та дозвіл у рядку незмінні.'))
        return super().write(vals)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_unit_release_wizard_line WHERE id IN %s', [tuple(self.ids)])
            self.env['quality.unit.release.wizard'].browse([row[0] for row in self.env.cr.fetchall()]).check_access_rule('write')
        return result

    @api.depends('unit_id', 'permission_id', 'wizard_id.mode', 'wizard_id.production_id')
    def _compute_availability(self):
        labels = dict(self.env['quality.production.unit']._fields['quality_release_state'].selection)
        for line in self:
            unit = line.unit_id or line.permission_id.unit_id
            line.controlled_unit_id = unit
            line.last_workorder_id = unit.quality_release_last_workorder_id
            line.execution_done = unit.quality_release_execution_done
            line.quality_result = unit.quality_release_quality_result
            line.admission_id = unit.quality_release_admission_id
            line.readiness = labels.get(unit.quality_release_state, '')
            scope = unit.production_id == line.wizard_id.production_id
            line.eligible = scope and (unit.quality_release_state == 'ready' if line.wizard_id.mode == 'authorize' else line.permission_id.state == 'confirmed')
            line.unavailable_reason = '' if line.eligible else unit.quality_release_reason or _('Виріб або дозвіл не належить цьому MO чи вже змінив стан.')
