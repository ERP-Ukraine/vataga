from uuid import uuid4

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..models.quality_unit_stock_release import POSTING_UNAVAILABLE
from ..models.quality_release_access import check_stock_release_roles


class QualityUnitStockReleaseWizard(models.TransientModel):
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
            wizard.posting_note = _(POSTING_UNAVAILABLE)

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if values.get('production_id'):
            production = self.env['mrp.production'].browse(values['production_id'])
            production._check_unit_stock_release_access()
            if 'line_ids' in names:
                values['line_ids'] = [Command.create({'unit_id': unit.id, 'selected': False})
                                      for unit in production.quality_release_unit_ids.sorted('id')]
        return values

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if set(vals) - {'production_id', 'line_ids'}:
                raise UserError(_('Ключ, готовність і кількості майстра встановлює система.'))
            values = dict(self.default_get(['production_id']), **vals)
            self.env['mrp.production'].browse(values.get('production_id'))._check_unit_stock_release_access()
            prepared.append(dict(values, request_key=str(uuid4())))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QualityUnitStockReleaseWizard, self.with_context(context)).create(prepared)

    def write(self, vals):
        if set(vals) - {'line_ids'}:
            raise UserError(_('MO та ключ підготовки незмінні.'))
        return super().write(vals)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT DISTINCT production_id FROM quality_unit_stock_release_wizard WHERE id IN %s', [tuple(self.ids)])
            for production in self.env['mrp.production'].browse([row[0] for row in self.env.cr.fetchall()]):
                production._check_unit_stock_release_access()
        return result

    def action_prepare(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        selected = self.line_ids.filtered('selected')
        if not selected or len(selected.unit_id) != len(selected):
            raise ValidationError(_('Явно оберіть різні QUNIT з чинними дозволами.'))
        # Computed eligibility is presentation only. Re-read permissions now;
        # the private factory locks and validates the full selection again.
        permissions = self.env['quality.unit.release.line']
        for line in selected:
            if line.unit_id.production_id != self.production_id:
                raise ValidationError(_('Не можна змішувати різні MO.'))
            active = self.env['quality.unit.release.line'].search([('unit_id', '=', line.unit_id.id), ('state', '=', 'confirmed')])
            if len(active) != 1:
                raise ValidationError(_('%s: немає єдиного чинного дозволу 13.4А.', line.unit_id.name))
            permissions |= active
        plan = self.env['quality.unit.stock.release.plan']._prepare(self.production_id, permissions, self.request_key)
        return {'type': 'ir.actions.act_window', 'name': _('План випуску — без складського проведення'),
                'res_model': plan._name, 'res_id': plan.id, 'view_mode': 'form', 'target': 'new',
                'views': [(self.env.ref('quality_vataga.quality_unit_stock_release_plan_form').id, 'form')]}


class QualityUnitStockReleaseWizardLine(models.TransientModel):
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

    @api.model_create_multi
    def create(self, vals_list):
        if any(set(vals) - {'wizard_id', 'unit_id', 'selected'} for vals in vals_list):
            raise UserError(_('Дозвіл, допуск і стан рядка визначає система.'))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        for vals in vals_list:
            wizard = self.env['quality.unit.stock.release.wizard'].browse(vals.get('wizard_id'))
            wizard.check_access_rule('write')
            unit = self.env['quality.production.unit'].browse(vals.get('unit_id'))
            unit.check_access_rights('read')
            unit.check_access_rule('read')
            if not unit or unit.production_id != wizard.production_id:
                raise ValidationError(_('Виріб має належати MO цього майстра.'))
        return super(QualityUnitStockReleaseWizardLine, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if set(vals) - {'selected'}:
            raise UserError(_('Зв’язок із виробом і готовність рядка незмінні.'))
        return super().write(vals)

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
            permission = self.env['quality.unit.release.line']
            reason = _('Виріб не належить MO цього майстра.')
            try:
                if line.unit_id and line.unit_id.production_id == line.wizard_id.production_id:
                    permission = permission.search([('unit_id', '=', line.unit_id.id), ('state', '=', 'confirmed')])
                    if len(permission) != 1:
                        permission = self.env['quality.unit.release.line']
                        reason = line.unit_id.quality_release_reason or _('Немає чинного дозволу 13.4А.')
                    else:
                        reason = permission._get_effectiveness_error() or ''
            except AccessError:
                permission = self.env['quality.unit.release.line']
                reason = _('Немає доступу до всіх підстав цього QUNIT.')
            line.permission_id = permission
            line.check_id = permission.check_id
            line.admission_id = permission.admission_id
            line.eligible = bool(permission) and not reason
            line.unavailable_reason = reason
            line.stock_status = _('Випущено на склад') if line.unit_id._stock_posted() else _('Не випущено')
