from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression


# RPC context values cannot authorize registration or mutation of identities.
IDENTIFICATION_TOKEN = object()
IDENTIFICATION_CONTEXT = 'quality_vataga_identification'


class QualityIdentityAccess(models.AbstractModel):
    _name = 'quality.identity.access'
    _description = 'Доступ до ідентифікації виробництва'

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['production_id'])
            # Parent IDs only: avoid recursive reads of production_id here.
            if self._name == 'quality.production.run':
                query = 'SELECT production_id, origin_check_id FROM quality_production_run WHERE id IN %s'
            else:
                query = ('SELECT u.production_id, r.origin_check_id FROM quality_production_unit u '
                         'JOIN quality_production_run r ON r.id = u.registration_run_id WHERE u.id IN %s')
            self.env.cr.execute(query, [tuple(self.ids)])
            rows = self.env.cr.fetchall()
            productions = self.env['mrp.production'].browse({row[0] for row in rows})
            productions.check_access_rights('read')
            productions.check_access_rule('read')
            checks = self.env['quality.check'].browse({row[1] for row in rows})
            checks.check_access_rights('read')
            checks.check_access_rule('read')
        return result

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        productions = self.env['mrp.production']._search([])
        checks = self.env['quality.check']._search([])
        check_field = 'origin_check_id' if self._name == 'quality.production.run' else 'registration_run_id.origin_check_id'
        return super()._search(
            expression.AND([domain, [('production_id', 'in', productions), (check_field, 'in', checks)]]),
            offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid,
        )


class QualityProductionRun(models.Model):
    _name = 'quality.production.run'
    _inherit = 'quality.identity.access'
    _description = 'Запуск виробничої операції для контролю'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(string='Ідентифікатор запуску', required=True, readonly=True, copy=False)
    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    workorder_id = fields.Many2one('mrp.workorder', string='Робоче завдання', readonly=True, ondelete='restrict', check_company=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, ondelete='restrict', index=True)
    quantity = fields.Integer(string='Зафіксована кількість виробів', required=True, readonly=True)
    origin_check_id = fields.Many2one('quality.check', required=True, readonly=True, ondelete='restrict', index=True)
    registration_mode = fields.Selection([('new', 'Нові вироби'), ('reuse', 'Зареєстровані вироби')], required=True, readonly=True)
    responsible_id = fields.Many2one('res.users', string='Відповідальний', required=True, readonly=True, ondelete='restrict')
    state = fields.Selection([('draft', 'Реєстрація'), ('registered', 'Склад зафіксовано')], required=True, readonly=True)
    unit_ids = fields.Many2many('quality.production.unit', 'quality_production_run_unit_rel', 'run_id', 'unit_id', string='Вироби', readonly=True, copy=False)
    check_ids = fields.One2many('quality.check', 'operation_run_id', string='Перевірки', readonly=True, copy=False)

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Ідентифікатор запуску повинен бути унікальним.'),
        ('origin_check_unique', 'unique(origin_check_id)', 'Для цієї перевірки запуск вже зареєстровано.'),
        ('quantity_positive', 'check(quantity > 0)', 'Кількість виробів має бути додатною.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(IDENTIFICATION_CONTEXT) is not IDENTIFICATION_TOKEN:
            raise UserError(_('Запуск створюється лише майстром ідентифікації перевірки.'))
        allowed = {'production_id', 'workorder_id', 'quantity', 'origin_check_id', 'registration_mode'}
        prepared = []
        for vals in vals_list:
            if set(vals) - allowed:
                raise UserError(_('Службові дані запуску встановлює система.'))
            production = self.env['mrp.production'].browse(vals.get('production_id')).exists()
            production.check_access_rights('read')
            production.check_access_rule('read')
            if not production or production.company_id not in self.env.companies:
                raise ValidationError(_('Оберіть доступне виробниче замовлення.'))
            prepared.append(dict(
                vals, name=self.env.ref('quality_vataga.sequence_quality_production_run').next_by_id(),
                product_id=production.product_id.id, company_id=production.company_id.id,
                workorder_id=vals.get('workorder_id') or False,
                responsible_id=self.env.uid, state='draft', unit_ids=[], check_ids=[],
            ))
        return super().create(prepared)

    def write(self, vals):
        if (
            self.env.context.get(IDENTIFICATION_CONTEXT) is not IDENTIFICATION_TOKEN
            or set(vals) - {'unit_ids', 'state'}
            or any(run.state != 'draft' for run in self)
        ):
            raise UserError(_('Склад та реквізити запуску незмінні. Створіть інший запуск.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Зареєстровані запуски є історією контролю та не видаляються.'))

    @api.constrains('production_id', 'workorder_id', 'product_id', 'company_id', 'origin_check_id', 'quantity', 'unit_ids', 'state')
    def _check_identity(self):
        for run in self:
            production = run.production_id
            check = run.origin_check_id
            if (
                run.product_id != production.product_id or run.company_id != production.company_id
                or run.workorder_id and run.workorder_id.production_id != production
                or check._identity_production() != production or check.product_id != run.product_id
                or 'company_id' in check._fields and check.company_id and check.company_id != run.company_id
                or 'workorder_id' in check._fields and check.workorder_id and check.workorder_id != run.workorder_id
                or check.in_process_sample_quantity != run.quantity
            ):
                raise ValidationError(_('Запуск, перевірка та робоче завдання повинні належати одній партії MO, виробу і компанії.'))
            if run.state == 'registered' and len(run.unit_ids) != run.quantity:
                raise ValidationError(_('Склад запуску не відповідає його зафіксованій кількості.'))
            if any(
                not unit._quality_matches_production(production) or unit.product_id != run.product_id
                or unit.company_id != run.company_id
                for unit in run.unit_ids
            ):
                raise ValidationError(_('До запуску можна включати лише сумісні вироби цього MO.'))


class QualityProductionUnit(models.Model):
    _name = 'quality.production.unit'
    _inherit = 'quality.identity.access'
    _description = 'Контрольований виріб'
    _order = 'id'
    _check_company_auto = True

    name = fields.Char(string='Ідентифікатор виробу', required=True, readonly=True, copy=False)
    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, ondelete='restrict', index=True)
    registration_run_id = fields.Many2one('quality.production.run', string='Перша реєстрація', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    unit_number = fields.Integer(required=True, readonly=True)
    lot_id = fields.Many2one('stock.lot', string='Партія / серійний номер', readonly=True, ondelete='restrict', check_company=True)
    serial_lot_id = fields.Many2one('stock.lot', readonly=True, ondelete='restrict', copy=False)
    run_ids = fields.Many2many('quality.production.run', 'quality_production_run_unit_rel', 'unit_id', 'run_id', string='Запуски', readonly=True, copy=False)
    sample_ids = fields.One2many(
        'quality.check.sample', 'controlled_unit_id', string='Зразки перевірок',
        readonly=True, copy=False,
        domain=lambda self: [('quality_check_id', 'in', self.env['quality.check'].search([]).ids)],
        depends_context=('uid', 'allowed_company_ids'),
    )
    check_ids = fields.Many2many('quality.check', compute='_compute_checks', string='Перевірки', compute_sudo=False)

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Ідентифікатор виробу повинен бути унікальним.'),
        ('run_number_unique', 'unique(registration_run_id, unit_number)', 'Виріб цього запуску вже зареєстровано.'),
        ('serial_unique', 'unique(serial_lot_id)', 'Цей реальний серійний номер вже має внутрішній ідентифікатор.'),
        ('number_positive', 'check(unit_number > 0)', 'Номер виробу має бути додатним.'),
    ]

    @api.depends('sample_ids.quality_check_id')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_checks(self):
        for unit in self:
            unit.check_ids = self.env['quality.check'].search([
                ('id', 'in', unit.sample_ids.quality_check_id.ids),
            ])

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(IDENTIFICATION_CONTEXT) is not IDENTIFICATION_TOKEN:
            raise UserError(_('Вироби реєструються лише майстром ідентифікації перевірки.'))
        allowed = {'production_id', 'registration_run_id', 'unit_number', 'lot_id'}
        prepared = []
        for vals in vals_list:
            if set(vals) - allowed:
                raise UserError(_('Службові дані виробу встановлює система.'))
            run = self.env['quality.production.run'].browse(vals.get('registration_run_id')).exists()
            run.check_access_rights('write')
            run.check_access_rule('write')
            if not run or run.state != 'draft' or vals.get('production_id') != run.production_id.id:
                raise ValidationError(_('Нові вироби реєструються лише у своєму незавершеному запуску.'))
            lot = self.env['stock.lot'].browse(vals.get('lot_id')).exists()
            if lot:
                lot.check_access_rights('read')
                lot.check_access_rule('read')
            prepared.append(dict(
                vals, name=self.env.ref('quality_vataga.sequence_quality_production_unit').next_by_id(),
                product_id=run.product_id.id, company_id=run.company_id.id,
                lot_id=lot.id or False,
                serial_lot_id=lot.id if run.product_id.tracking == 'serial' else False,
                run_ids=[], sample_ids=[],
            ))
        return super().create(prepared)

    def write(self, vals):
        raise UserError(_('Ідентифікатор та реквізити зареєстрованого виробу незмінні.'))

    def unlink(self):
        raise UserError(_('Зареєстровані вироби є історією контролю та не видаляються.'))

    @api.constrains('registration_run_id', 'production_id', 'product_id', 'company_id', 'unit_number', 'lot_id')
    def _check_identity(self):
        for unit in self:
            run = unit.registration_run_id
            if (
                unit.production_id != run.production_id or unit.product_id != run.product_id
                or unit.company_id != run.company_id or unit.unit_number > run.quantity
            ):
                raise ValidationError(_('Виріб повинен відповідати своєму першому запуску.'))
            if unit.lot_id and (
                unit.lot_id.product_id != unit.product_id
                or unit.lot_id.company_id and unit.lot_id.company_id != unit.company_id
                or unit.product_id.tracking not in ('lot', 'serial')
            ):
                raise ValidationError(_('Реальна партія / серійний номер не відповідає виробу та компанії.'))


class MrpProductionIdentity(models.Model):
    _inherit = 'mrp.production'

    def write(self, vals):
        # Identity metadata only; quantities, states and stock flows are untouched.
        if {'product_id', 'company_id'}.intersection(vals):
            self.check_access_rights('write')
            self.check_access_rule('write')
            changed = self.filtered(lambda production: any(
                name in vals and production[name].id != vals[name]
                for name in ('product_id', 'company_id')
            ))
            if changed:
                self.env.cr.execute('SELECT id FROM mrp_production WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(changed.ids)])
                self.env.cr.execute('SELECT 1 FROM quality_production_run WHERE production_id IN %s LIMIT 1', [tuple(changed.ids)])
                if self.env.cr.fetchone():
                    raise UserError(_('Не можна змінити виріб або компанію MO після реєстрації ідентифікаторів.'))
        return super().write(vals)
