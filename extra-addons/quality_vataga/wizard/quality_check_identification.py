from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression


class QualityCheckIdentificationWizard(models.TransientModel):
    _name = 'quality.check.identification.wizard'
    _description = 'Явна ідентифікація зразків перевірки'

    check_id = fields.Many2one('quality.check', required=True, readonly=True, ondelete='cascade')
    mode = fields.Selection([
        ('new', 'Новий запуск — нові фізичні вироби'),
        ('existing', 'Додаткова перевірка наявного запуску'),
        ('reuse', 'Новий запуск — зареєстровані вироби'),
    ], string='Спосіб ідентифікації', required=True)
    production_id = fields.Many2one('mrp.production', compute='_compute_source')
    product_id = fields.Many2one(related='check_id.product_id', readonly=True)
    company_id = fields.Many2one(related='production_id.company_id', readonly=True)
    quantity = fields.Integer(related='check_id.in_process_sample_quantity', readonly=True)
    run_id = fields.Many2one('quality.production.run', string='Наявний запуск')
    workorder_id = fields.Many2one('mrp.workorder', string='Робоче завдання нового запуску')
    available_unit_ids = fields.Many2many('quality.production.unit', compute='_compute_available_units')
    line_ids = fields.One2many('quality.check.identification.line', 'wizard_id', string='Відповідність зразків виробам')
    confirm_mapping = fields.Boolean(string='Підтверджую відповідність виробів зразкам і наявним результатам')

    @api.model_create_multi
    def create(self, vals_list):
        default_check = self.default_get(['check_id']).get('check_id') if any('check_id' not in vals for vals in vals_list) else False
        for vals in vals_list:
            check = self.env['quality.check'].browse(vals.get('check_id', default_check)).exists()
            if not check:
                raise UserError(_('Перевірку не знайдено.'))
            check._check_identification_access()
        return super().create(vals_list)

    def write(self, vals):
        if 'check_id' in vals and any(wizard.check_id.id != vals['check_id'] for wizard in self):
            raise UserError(_('Не можна змінити перевірку майстра. Відкрийте інший майстер.'))
        return super().write(vals)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        checks = self.env['quality.check']._search([])
        return super()._search(expression.AND([domain, [('check_id', 'in', checks)]]),
                               offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['check_id'])
            self.env.cr.execute('SELECT DISTINCT check_id FROM quality_check_identification_wizard WHERE id IN %s', [tuple(self.ids)])
            checks = self.env['quality.check'].browse([row[0] for row in self.env.cr.fetchall()])
            parent_operation = 'read' if operation == 'read' else 'write'
            checks.check_access_rights(parent_operation)
            checks.check_access_rule(parent_operation)
        return result

    @api.onchange('mode', 'run_id')
    def _onchange_selection(self):
        self.confirm_mapping = False
        if self.mode != 'existing':
            self.run_id = False
        for line in self.line_ids:
            line.controlled_unit_id = False
            line.lot_id = False

    @api.depends('check_id')
    def _compute_source(self):
        for wizard in self:
            wizard.production_id = wizard.check_id._identity_production() if wizard.check_id else False

    @api.depends('mode', 'run_id', 'check_id')
    def _compute_available_units(self):
        for wizard in self:
            units = self.env['quality.production.unit']
            if wizard.mode == 'existing' and wizard.run_id:
                units = wizard.run_id.unit_ids
            elif wizard.mode == 'reuse' and wizard.production_id:
                units = units.search([('production_id', '=', wizard.production_id.id)])
            wizard.available_unit_ids = units

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if 'mode' in names:
            values['mode'] = False
        if 'confirm_mapping' in names:
            values['confirm_mapping'] = False
        if values.get('check_id'):
            check = self.env['quality.check'].browse(values['check_id']).exists()
            if not check:
                raise UserError(_('Перевірку не знайдено.'))
            check._check_identification_access()
            if 'line_ids' in names:
                # Propose no existing unit automatically. The inspector selects
                # every sample-to-unit correspondence explicitly.
                values['line_ids'] = [Command.create({'sample_id': sample.id}) for sample in check.sample_ids.sorted('sample_number')]
        return values

    def action_apply(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.check_id._identify_samples(
            self.mode,
            [{'sample_id': line.sample_id.id, 'unit_id': line.controlled_unit_id.id or False,
              'lot_id': line.lot_id.id or False} for line in self.line_ids],
            run=self.run_id, workorder=self.workorder_id, confirmed=self.confirm_mapping,
        )
        return {'type': 'ir.actions.act_window_close'}


class QualityCheckIdentificationLine(models.TransientModel):
    _name = 'quality.check.identification.line'
    _description = 'Відповідність зразка контрольованому виробу'
    _order = 'sample_number, id'

    wizard_id = fields.Many2one('quality.check.identification.wizard', required=True, ondelete='cascade')
    sample_id = fields.Many2one('quality.check.sample', required=True, readonly=True, ondelete='cascade')
    sample_number = fields.Integer(related='sample_id.sample_number', readonly=True, store=True)
    sample_result = fields.Selection(related='sample_id.sample_result', readonly=True, compute_sudo=False)
    controlled_unit_id = fields.Many2one('quality.production.unit', string='Зареєстрований виріб')
    lot_id = fields.Many2one('stock.lot', string='Реальна партія / серійний номер')

    _sql_constraints = [
        ('sample_unique', 'unique(wizard_id, sample_id)', 'Зразок може бути лише в одному рядку майстра.'),
        ('unit_unique', 'unique(wizard_id, controlled_unit_id)', 'Оберіть різні вироби для зразків.'),
    ]

    @api.constrains('sample_id', 'wizard_id')
    def _check_sample_parent(self):
        for line in self:
            if line.sample_id.quality_check_id != line.wizard_id.check_id:
                raise ValidationError(_('Зразок не належить перевірці цього майстра.'))

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        wizards = self.env['quality.check.identification.wizard']._search([])
        return super()._search(expression.AND([domain, [('wizard_id', 'in', wizards)]]),
                               offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['wizard_id'])
            self.env.cr.execute('SELECT DISTINCT wizard_id FROM quality_check_identification_line WHERE id IN %s', [tuple(self.ids)])
            wizards = self.env['quality.check.identification.wizard'].browse([row[0] for row in self.env.cr.fetchall()])
            parent_operation = 'read' if operation == 'read' else 'write'
            wizards.check_access_rights(parent_operation)
            wizards.check_access_rule(parent_operation)
        return result

    @api.model_create_multi
    def create(self, vals_list):
        default_wizard = self.default_get(['wizard_id']).get('wizard_id')
        wizards = self.env['quality.check.identification.wizard'].browse({
            vals.get('wizard_id', default_wizard) for vals in vals_list
            if vals.get('wizard_id', default_wizard)
        })
        wizards.check_access_rights('write')
        wizards.check_access_rule('write')
        return super().create(vals_list)

    def write(self, vals):
        self.wizard_id.check_access_rights('write')
        self.wizard_id.check_access_rule('write')
        if {'wizard_id', 'sample_id'}.intersection(vals):
            raise UserError(_('Не можна змінювати зразок або власника рядка майстра.'))
        return super().write(vals)

    def unlink(self):
        self.wizard_id.check_access_rights('write')
        self.wizard_id.check_access_rule('write')
        return super().unlink()
