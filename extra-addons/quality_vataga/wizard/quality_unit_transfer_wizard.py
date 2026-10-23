from uuid import uuid4

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class QualityUnitTransferWizard(models.TransientModel):
    _name = 'quality.unit.transfer.wizard'
    _description = 'Явна передача або приймання конкретних QUNIT'

    workorder_id = fields.Many2one('mrp.workorder', string='Робоче завдання', required=True, readonly=True, ondelete='cascade')
    check_id = fields.Many2one('quality.check', string='Вихідна перевірка', readonly=True, ondelete='cascade')
    production_id = fields.Many2one(related='workorder_id.production_id', readonly=True, compute_sudo=False)
    mode = fields.Selection([('send', 'Передача'), ('receive', 'Приймання'), ('return', 'Повернення')], required=True, readonly=True)
    destination_workorder_id = fields.Many2one('mrp.workorder', string='Наступна операція', ondelete='cascade')
    request_key = fields.Char(required=True, readonly=True)
    reason = fields.Text(string='Причина повернення')
    line_ids = fields.One2many('quality.unit.transfer.wizard.line', 'wizard_id', string='Конкретні вироби')

    @api.model
    def _check_scope(self, workorder, mode, check):
        workorder.ensure_one()
        workorder.check_access_rights('write')
        workorder.check_access_rule('write')
        if workorder.company_id not in self.env.companies:
            raise ValidationError(_('Операція належить недоступній компанії.'))
        self.env['quality.unit.transfer'].check_access_rights('create' if mode == 'send' else 'write')
        if mode not in ('send', 'receive', 'return'):
            raise ValidationError(_('Оберіть допустиму дію.'))
        if mode == 'send':
            check.ensure_one()
            check._check_unit_admission_access()
            if check.operation_run_id.workorder_id != workorder:
                raise ValidationError(_('Перевірка та запуск мають належати вихідній операції.'))
        elif check:
            raise ValidationError(_('Приймання та повернення обирають конкретні записи передач.'))

    @api.model
    def _open_action(self, workorder, mode, check=None):
        check = check or self.env['quality.check']
        self._check_scope(workorder, mode, check)
        return {
            'type': 'ir.actions.act_window', 'name': _('Передача / приймання QUNIT'),
            'res_model': self._name, 'view_mode': 'form', 'target': 'new',
            'views': [(self.env.ref('quality_vataga.quality_unit_transfer_wizard_form').id, 'form')],
            'context': {'default_workorder_id': workorder.id, 'default_mode': mode, 'default_check_id': check.id or False},
        }

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if values.get('workorder_id') and values.get('mode'):
            workorder = self.env['mrp.workorder'].browse(values['workorder_id']).exists()
            check = self.env['quality.check'].browse(values.get('check_id')).exists()
            mode = values['mode']
            self._check_scope(workorder, mode, check)
            if mode == 'send':
                destinations = self.env['mrp.workorder'].search([('production_id', '=', workorder.production_id.id), ('blocked_by_workorder_ids', 'in', workorder.ids)])
                destinations = destinations.filtered(lambda item: item.blocked_by_workorder_ids == workorder)
                if len(destinations) == 1 and 'destination_workorder_id' in names:
                    values['destination_workorder_id'] = destinations.id
                rows = [{'sample_id': sample.id} for sample in check.sample_ids.sorted('sample_number')]
            else:
                domain = [('state', 'in', ('sent', 'received') if mode == 'return' else ('sent',))]
                domain += ['|', ('source_workorder_id', '=', workorder.id), ('destination_workorder_id', '=', workorder.id)] if mode == 'return' else [('destination_workorder_id', '=', workorder.id)]
                rows = [{'transfer_id': record.id} for record in self.env['quality.unit.transfer'].search(domain)]
            if 'line_ids' in names:
                values['line_ids'] = [Command.create(dict(row, selected=False)) for row in rows]
        return values

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if set(vals) - {'workorder_id', 'check_id', 'mode', 'destination_workorder_id', 'reason', 'line_ids'}:
                raise UserError(_('Службові поля майстра встановлює система.'))
            defaults = self.default_get(['workorder_id', 'check_id', 'mode'])
            values = dict(defaults, **vals)
            self._check_scope(self.env['mrp.workorder'].browse(values.get('workorder_id')), values.get('mode'), self.env['quality.check'].browse(values.get('check_id')))
            prepared.append(dict(values, request_key=str(uuid4())))
        return super().create(prepared)

    def write(self, vals):
        if set(vals) - {'destination_workorder_id', 'reason', 'line_ids'}:
            raise UserError(_('Операцію, перевірку, дію та ключ майстра змінювати не можна.'))
        return super().write(vals)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['workorder_id'])
            self.env.cr.execute('SELECT DISTINCT workorder_id FROM quality_unit_transfer_wizard WHERE id IN %s', [tuple(self.ids)])
            workorders = self.env['mrp.workorder'].browse([row[0] for row in self.env.cr.fetchall()])
            workorders.check_access_rights('read')
            workorders.check_access_rule('read')
            if any(workorder.company_id not in self.env.companies for workorder in workorders):
                raise AccessError(_('Немає доступу до компанії майстра передачі.'))
        return result

    def action_apply(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self._check_scope(self.workorder_id, self.mode, self.check_id)
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise ValidationError(_('Явно оберіть вироби.'))
        transfers = self.env['quality.unit.transfer']
        if self.mode == 'send':
            if not self.destination_workorder_id:
                raise ValidationError(_('Оберіть наступну операцію.'))
            transfers._send_samples(self.check_id, selected.sample_id.ids, self.destination_workorder_id, self.request_key)
        else:
            selected.transfer_id._transition(self.mode, self.request_key, self.reason or '')
        return {
            'type': 'ir.actions.act_window_close',
            'infos': {'quality_vataga_check_id': self.check_id.id or False, 'quality_vataga_workorder_id': self.workorder_id.id},
        }


class QualityUnitTransferWizardLine(models.TransientModel):
    _name = 'quality.unit.transfer.wizard.line'
    _description = 'Явний вибір QUNIT для передачі'

    wizard_id = fields.Many2one('quality.unit.transfer.wizard', required=True, ondelete='cascade')
    sample_id = fields.Many2one('quality.check.sample', readonly=True, ondelete='cascade')
    transfer_id = fields.Many2one('quality.unit.transfer', readonly=True, ondelete='cascade')
    selected = fields.Boolean(string='Обрати')
    controlled_unit_id = fields.Many2one('quality.production.unit', string='QUNIT', compute='_compute_availability')
    sample_number = fields.Integer(string='№ зразка', compute='_compute_availability')
    sample_result = fields.Char(string='Контроль', compute='_compute_availability')
    admission_state = fields.Char(string='Допуск', compute='_compute_availability')
    transfer_state = fields.Char(string='Передача', compute='_compute_availability')
    eligible = fields.Boolean(compute='_compute_availability')
    unavailable_reason = fields.Text(string='Причина недоступності', compute='_compute_availability')

    _sql_constraints = [
        ('one_origin', 'check((sample_id IS NULL) != (transfer_id IS NULL))', 'Оберіть зразок або передачу.'),
        ('sample_unique', 'unique(wizard_id, sample_id)', 'Виріб вже є у майстрі.'),
        ('transfer_unique', 'unique(wizard_id, transfer_id)', 'Передача вже є у майстрі.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if set(vals) - {'wizard_id', 'sample_id', 'transfer_id', 'selected'}:
                raise UserError(_('Службові дані рядка встановлює система.'))
            wizard = self.env['quality.unit.transfer.wizard'].browse(vals.get('wizard_id'))
            wizard.check_access_rights('write')
            wizard.check_access_rule('write')
            sample = self.env['quality.check.sample'].browse(vals.get('sample_id')).exists()
            transfer = self.env['quality.unit.transfer'].browse(vals.get('transfer_id')).exists()
            if wizard.mode == 'send':
                if not sample or transfer or sample.quality_check_id != wizard.check_id:
                    raise ValidationError(_('Зразок має належати вихідній перевірці.'))
                sample.check_access_rights('read')
                sample.check_access_rule('read')
            else:
                if sample or not transfer or wizard.workorder_id not in (transfer.source_workorder_id | transfer.destination_workorder_id if wizard.mode == 'return' else transfer.destination_workorder_id):
                    raise ValidationError(_('Передача не належить цій операції.'))
                transfer.check_access_rights('read')
                transfer.check_access_rule('read')
            prepared.append(dict(vals, sample_id=sample.id or False, transfer_id=transfer.id or False, selected=bool(vals.get('selected'))))
        return super().create(prepared)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['wizard_id'])
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_unit_transfer_wizard_line WHERE id IN %s', [tuple(self.ids)])
            parents = self.env['quality.unit.transfer.wizard'].browse([row[0] for row in self.env.cr.fetchall()])
            parents.check_access_rights('read' if operation == 'read' else 'write')
            parents.check_access_rule('read' if operation == 'read' else 'write')
        return result

    def write(self, vals):
        if set(vals) - {'selected'}:
            raise UserError(_('У рядку майстра змінюється лише явний вибір.'))
        self.wizard_id.check_access_rule('write')
        return super().write(vals)

    @api.depends('sample_id.sample_result', 'transfer_id.state', 'wizard_id.destination_workorder_id')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_availability(self):
        labels = dict(self.env['quality.unit.transfer']._fields['state'].selection)
        for line in self:
            sample = line.sample_id or line.transfer_id.admission_id.sample_id
            admission = line.transfer_id.admission_id or self.env['quality.unit.admission'].search([
                ('operation_run_id', '=', line.wizard_id.check_id.operation_run_id.id),
                ('controlled_unit_id', '=', sample.controlled_unit_id.id), ('state', '=', 'confirmed'),
            ], limit=1)
            line.controlled_unit_id = sample.controlled_unit_id
            line.sample_number = sample.sample_number
            line.sample_result = (sample.sample_result or 'pending').upper()
            line.admission_state = _('Допущено') if admission and not admission._get_effectiveness_error() else _('Утримано')
            current_transfer = line.transfer_id or self.env['quality.unit.transfer'].search([
                ('source_run_id', '=', line.wizard_id.check_id.operation_run_id.id),
                ('controlled_unit_id', '=', sample.controlled_unit_id.id),
            ], limit=1)
            line.transfer_state = labels[current_transfer.state] if current_transfer else _('Очікує передачі')
            error = False
            if line.wizard_id.mode == 'send':
                try:
                    if not admission:
                        raise ValidationError(_('Немає чинного допуску інспектора.'))
                    if not line.wizard_id.destination_workorder_id:
                        raise ValidationError(_('Оберіть наступну операцію.'))
                    self.env['quality.unit.transfer']._check_admission(admission, line.wizard_id.destination_workorder_id)
                except (AccessError, UserError, ValidationError) as failure:
                    error = str(failure)
            elif line.wizard_id.mode == 'receive':
                error = admission._get_effectiveness_error() or (line.transfer_id.state != 'sent' and _('Передача вже оброблена.'))
            else:
                error = line.transfer_id._get_return_error() or (line.transfer_id.state not in ('sent', 'received') and _('Виріб вже повернено.'))
            line.eligible = not bool(error)
            line.unavailable_reason = error or ''
