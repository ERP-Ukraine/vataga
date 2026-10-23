from uuid import uuid4

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class QualityUnitExecutionWizard(models.TransientModel):
    _name = 'quality.unit.execution.wizard'
    _description = 'Явне поштучне виконання Work Order'

    workorder_id = fields.Many2one('mrp.workorder', string='Робоче завдання', required=True, readonly=True, ondelete='cascade')
    mode = fields.Selection([('start', 'Розпочати'), ('finish', 'Завершити'), ('stop', 'Зупинити'), ('control', 'Створити контроль')], string='Дія', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True)
    reference = fields.Text(string='Результат операції / причина зупинки')
    point_id = fields.Many2one('quality.point', string='Точка наступного контролю', domain="[('x_control_stage', '=', 'in_process')]")
    line_ids = fields.One2many('quality.unit.execution.wizard.line', 'wizard_id')

    @api.model
    def _check_scope(self, workorder, mode):
        workorder._check_quality_execution_access()
        if mode not in ('start', 'finish', 'stop', 'control'):
            raise ValidationError(_('Невідома дія виконання.'))
        self.env['quality.unit.execution'].check_access_rights('create' if mode == 'start' else 'write')
        if mode == 'control':
            self.env['quality.check'].check_access_rights('create')
            self.env['quality.production.run'].check_access_rights('create')

    @api.model
    def _open_action(self, workorder, mode):
        self._check_scope(workorder, mode)
        return {'type': 'ir.actions.act_window', 'name': _('Поштучне виконання'),
                'res_model': self._name, 'view_mode': 'form', 'target': 'new',
                'views': [(self.env.ref('quality_vataga.quality_unit_execution_wizard_form').id, 'form')],
                'context': {'default_workorder_id': workorder.id, 'default_mode': mode}}

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if values.get('workorder_id') and values.get('mode'):
            workorder = self.env['mrp.workorder'].browse(values['workorder_id'])
            mode = values['mode']
            self._check_scope(workorder, mode)
            if 'line_ids' in names:
                if mode == 'start':
                    records = self.env['quality.unit.transfer'].search([('destination_workorder_id', '=', workorder.id)])
                    rows = [{'transfer_id': row.id} for row in records]
                else:
                    domain = [('workorder_id', '=', workorder.id), ('state', '=', 'processed' if mode == 'control' else 'progress')]
                    rows = [{'execution_id': row.id} for row in self.env['quality.unit.execution'].search(domain)]
                values['line_ids'] = [Command.create(dict(row, selected=False)) for row in rows]
        return values

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if set(vals) - {'workorder_id', 'mode', 'reference', 'point_id', 'line_ids'}:
                raise UserError(_('Реквізити майстра захищені.'))
            values = dict(self.default_get(['workorder_id', 'mode']), **vals)
            self._check_scope(self.env['mrp.workorder'].browse(values.get('workorder_id')), values.get('mode'))
            prepared.append(dict(values, request_key=str(uuid4())))
        return super().create(prepared)

    def write(self, vals):
        if set(vals) - {'reference', 'point_id', 'line_ids'}:
            raise UserError(_('Операцію, дію та ключ запиту змінювати не можна.'))
        return super().write(vals)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT workorder_id FROM quality_unit_execution_wizard WHERE id IN %s', [tuple(self.ids)])
            workorders = self.env['mrp.workorder'].browse([row[0] for row in self.env.cr.fetchall()])
            workorders.check_access_rights('read')
            workorders.check_access_rule('read')
            if any(row.company_id not in self.env.companies for row in workorders):
                raise AccessError(_('Недоступна компанія майстра.'))
        return result

    def action_apply(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self._check_scope(self.workorder_id, self.mode)
        rows = self.line_ids.filtered('selected')
        if not rows:
            raise ValidationError(_('Явно оберіть конкретні QUNIT.'))
        # Never trust the UI eligibility or a quantity supplied by a client.
        if self.mode == 'start':
            if len(rows.transfer_id) != len(rows) or rows.execution_id:
                raise ValidationError(_('Оберіть різні записи приймання.'))
            self.env['quality.unit.execution']._start(self.workorder_id, rows.transfer_id, self.request_key)
        elif self.mode == 'control':
            if len(rows.execution_id) != len(rows) or rows.transfer_id:
                raise ValidationError(_('Оберіть різні результати обробки.'))
            rows.execution_id._create_control(self.workorder_id, self.point_id)
        else:
            if len(rows.execution_id) != len(rows) or rows.transfer_id:
                raise ValidationError(_('Оберіть різні результати обробки.'))
            rows.execution_id._finish(self.workorder_id, self.mode, self.reference or '')
        return {'type': 'ir.actions.act_window_close', 'infos': {'quality_vataga_workorder_id': self.workorder_id.id}}


class QualityUnitExecutionWizardLine(models.TransientModel):
    _name = 'quality.unit.execution.wizard.line'
    _description = 'Явний вибір QUNIT для виконання'

    wizard_id = fields.Many2one('quality.unit.execution.wizard', required=True, ondelete='cascade')
    transfer_id = fields.Many2one('quality.unit.transfer', readonly=True, ondelete='cascade')
    execution_id = fields.Many2one('quality.unit.execution', readonly=True, ondelete='cascade')
    selected = fields.Boolean(string='Обрати')
    controlled_unit_id = fields.Many2one('quality.production.unit', compute='_compute_availability', string='QUNIT')
    eligible = fields.Boolean(compute='_compute_availability')
    unavailable_reason = fields.Char(compute='_compute_availability', string='Причина недоступності')
    status = fields.Char(compute='_compute_availability', string='Обробка')

    _sql_constraints = [('one_origin', 'check((transfer_id IS NULL) != (execution_id IS NULL))', 'Оберіть приймання або результат обробки.'),
                        ('transfer_unique', 'unique(wizard_id, transfer_id)', 'Приймання вже обрано.'),
                        ('execution_unique', 'unique(wizard_id, execution_id)', 'Виконання вже обрано.')]

    @api.model_create_multi
    def create(self, vals_list):
        if any(set(vals) - {'wizard_id', 'transfer_id', 'execution_id', 'selected'} for vals in vals_list):
            raise UserError(_('Службові поля рядка захищені.'))
        for vals in vals_list:
            wizard = self.env['quality.unit.execution.wizard'].browse(vals.get('wizard_id'))
            wizard.check_access_rights('write')
            wizard.check_access_rule('write')
        return super().create(vals_list)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_unit_execution_wizard_line WHERE id IN %s', [tuple(self.ids)])
            self.env['quality.unit.execution.wizard'].browse([row[0] for row in self.env.cr.fetchall()]).check_access_rule('write')
        return result

    def write(self, vals):
        if set(vals) - {'selected'}:
            raise UserError(_('Реквізити вибраного виробу незмінні.'))
        return super().write(vals)

    @api.depends('transfer_id', 'execution_id', 'wizard_id.mode', 'wizard_id.workorder_id')
    def _compute_availability(self):
        labels = dict(self.env['quality.unit.execution']._fields['state'].selection)
        for line in self:
            record = line.execution_id
            transfer = line.transfer_id or record.transfer_id
            line.controlled_unit_id = transfer.controlled_unit_id
            line.status = labels.get(record.state, _('Очікує обробки'))
            error = ''
            if line.wizard_id.mode == 'start':
                error = transfer._get_execution_error(line.wizard_id.workorder_id) if transfer else _('Немає приймання.')
            elif record.workorder_id != line.wizard_id.workorder_id:
                error = _('Інша операція.')
            elif line.wizard_id.mode == 'control':
                error = _('Потрібен оброблений виріб без наявного контролю.') if record.state != 'processed' or record.check_ids else ''
            elif record.operator_id != self.env.user or record.state != 'progress':
                error = _('Оберіть власне активне виконання.')
            line.eligible = not bool(error)
            line.unavailable_reason = error or ''
