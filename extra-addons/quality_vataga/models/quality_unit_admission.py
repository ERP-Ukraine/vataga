from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression


ADMISSION_TOKEN = object()
ADMISSION_CONTEXT = 'quality_vataga_unit_admission'


class QualityAdmissionAccess(models.AbstractModel):
    _name = 'quality.admission.access'
    _description = 'Доступ до допусків через перевірку якості'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        productions = self.env['mrp.production']._search([])
        source_domain = [('production_id', 'in', productions)]
        if 'workorder_id' in self.env['quality.check']._fields:
            source_domain = ['|'] + source_domain + [('workorder_id.production_id', 'in', productions)]
        checks = self.env['quality.check']._search(source_domain)
        identity_domain = []
        if self._name == 'quality.unit.admission':
            identity_domain = [
                ('operation_run_id', 'in', self.env['quality.production.run']._search([])),
                ('controlled_unit_id', 'in', self.env['quality.production.unit']._search([])),
            ]
        return super()._search(
            expression.AND([domain, [('check_id', 'in', checks)], identity_domain]),
            offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid,
        )

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['check_id'])
            # Only model-owned table names; do not recursively read self here.
            self.env.cr.execute(
                'SELECT DISTINCT check_id FROM "%s" WHERE id IN %%s' % self._table,
                [tuple(self.ids)],
            )
            checks = self.env['quality.check'].browse([row[0] for row in self.env.cr.fetchall()])
            parent_operation = 'read' if operation == 'read' else 'write'
            checks.check_access_rights(parent_operation)
            checks.check_access_rule(parent_operation)
            productions = self.env['mrp.production']
            for check in checks:
                productions |= check._identity_production()
            productions.check_access_rights('read')
            productions.check_access_rule('read')
            if self._name == 'quality.unit.admission':
                self.flush_recordset(['operation_run_id', 'controlled_unit_id'])
                self.env.cr.execute('SELECT operation_run_id, controlled_unit_id FROM quality_unit_admission WHERE id IN %s', [tuple(self.ids)])
                rows = self.env.cr.fetchall()
                for model, position in [('quality.production.run', 0), ('quality.production.unit', 1)]:
                    parents = self.env[model].browse({row[position] for row in rows})
                    parents.check_access_rights('read')
                    parents.check_access_rule('read')
        return result


class QualityUnitAdmission(models.Model):
    _name = 'quality.unit.admission'
    _inherit = 'quality.admission.access'
    _description = 'Підтвердження допуску конкретного виробу'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(readonly=True, required=True)
    check_id = fields.Many2one('quality.check', string='Перевірка', required=True, readonly=True, ondelete='restrict', index=True)
    sample_id = fields.Many2one('quality.check.sample', string='Зразок', required=True, readonly=True, ondelete='restrict')
    controlled_unit_id = fields.Many2one('quality.production.unit', string='Виріб', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    operation_run_id = fields.Many2one('quality.production.run', string='Запуск', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    workorder_id = fields.Many2one('mrp.workorder', string='Робоче завдання', required=True, readonly=True, ondelete='restrict', check_company=True)
    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='restrict', check_company=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, ondelete='restrict', index=True)
    inspector_id = fields.Many2one('res.users', string='Інспектор', required=True, readonly=True, ondelete='restrict')
    confirmed_at = fields.Datetime(string='Підтверджено', required=True, readonly=True)
    state = fields.Selection([('confirmed', 'Підтверджено'), ('revoked', 'Скасовано')], required=True, readonly=True, index=True)
    required_check_ids = fields.Many2many('quality.check', string='Перевірки, враховані при допуску', readonly=True, copy=False)
    evidence_snapshot = fields.Text(string='Підстави на момент підтвердження', readonly=True, required=True)
    request_key = fields.Char(required=True, readonly=True, copy=False)
    revoked_by_id = fields.Many2one('res.users', string='Скасував', readonly=True, ondelete='restrict')
    revoked_at = fields.Datetime(string='Скасовано', readonly=True)
    revocation_reason = fields.Text(string='Причина скасування', readonly=True)
    is_effective = fields.Boolean(string='Допуск чинний', compute='_compute_effectiveness')
    ineffective_reason = fields.Text(string='Причина утримання', compute='_compute_effectiveness')

    _sql_constraints = [
        ('request_unit_unique', 'unique(request_key, controlled_unit_id)', 'Це підтвердження вже оброблено.'),
        ('revocation_complete', "check((state = 'confirmed' AND revoked_by_id IS NULL AND revoked_at IS NULL AND revocation_reason IS NULL) OR (state = 'revoked' AND revoked_by_id IS NOT NULL AND revoked_at IS NOT NULL AND revocation_reason IS NOT NULL))", 'Скасування потребує автора, дати та причини.'),
    ]

    @api.private
    def init(self):
        # Keep every revoked confirmation, but at most one active permission
        # for this physical unit in this explicit run, across all its checks.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS quality_unit_admission_active_unique
            ON quality_unit_admission (operation_run_id, controlled_unit_id)
            WHERE state = 'confirmed'
        """)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(ADMISSION_CONTEXT) is not ADMISSION_TOKEN:
            raise UserError(_('Допуски створюються лише явним підтвердженням інспектора.'))
        prepared = []
        for vals in vals_list:
            if set(vals) != {'check_id', 'sample_id', 'request_key'}:
                raise UserError(_('Реквізити допуску встановлює система.'))
            check = self.env['quality.check'].browse(vals['check_id']).exists()
            check._check_unit_admission_access()
            check._lock_unit_admission_scope()
            sample = self.env['quality.check.sample'].browse(vals['sample_id']).exists()
            # Consumption forbids a NEW permission, not reading the validity
            # of the immutable confirmation used for that stock release.
            sample.controlled_unit_id._assert_not_stock_posted()
            error = check._get_unit_admission_error(sample)
            if error:
                raise ValidationError(error)
            checks = check._unit_admission_required_checks()
            run, unit = check.operation_run_id, sample.controlled_unit_id
            prepared.append(dict(
                vals, name='%s / %s' % (unit.name, run.name),
                controlled_unit_id=unit.id, operation_run_id=run.id,
                workorder_id=run.workorder_id.id, production_id=run.production_id.id,
                product_id=run.product_id.id, company_id=run.company_id.id,
                inspector_id=self.env.uid, confirmed_at=fields.Datetime.now(), state='confirmed',
                required_check_ids=[(6, 0, checks.ids)],
                evidence_snapshot='\n'.join(
                    '%s: %s — PASS; %s' % (
                        item.display_name, unit.name,
                        ', '.join(filter(None, item.equipment_selection_ids.mapped('equipment_display_snapshot')))
                        or ', '.join(item.equipment_selection_ids.equipment_ids.mapped('display_name'))
                        or ', '.join(item.equipment_selection_ids.equipment_id.mapped('display_name'))
                        or 'ЗВТ не вимагаються',
                    ) for item in checks
                ),
                revoked_by_id=False, revoked_at=False, revocation_reason=False,
            ))
        context = dict(self.env.context)
        context.pop('default_is_effective', None)
        context.pop('default_ineffective_reason', None)
        return super(QualityUnitAdmission, self.with_context(context)).create(prepared)

    def write(self, vals):
        if (
            self.env.context.get(ADMISSION_CONTEXT) is not ADMISSION_TOKEN
            or set(vals) != {'state', 'revoked_by_id', 'revoked_at', 'revocation_reason'}
            or vals['state'] != 'revoked' or not vals['revocation_reason'].strip()
            or any(record.state != 'confirmed' for record in self)
        ):
            raise UserError(_('Записи допуску незмінні. Використайте дію скасування з причиною.'))
        for record in self:
            error = record._get_consumption_error()
            if error:
                raise UserError(error)
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Історія підтверджень допуску не видаляється.'))

    def _get_consumption_error(self):
        """A later explicit registration is conservatively treated as use.

        No movement is performed here. A future transfer implementation must
        extend this hook with its own immutable consumption records.
        """
        self.ensure_one()
        if self.env['quality.production.run'].sudo().search_count([
            ('unit_ids', 'in', self.controlled_unit_id.ids),
            ('id', '>', self.operation_run_id.id), ('state', '=', 'registered'),
        ], limit=1):
            return _('Виріб вже включено до пізнішого запуску. Скасування потребує окремої процедури повернення; автоматичне повернення не виконується.')
        return False

    def _get_effectiveness_error(self):
        self.ensure_one()
        record = self.sudo(False)
        try:
            record.check_access_rights('read')
            record.check_access_rule('read')
            if record.state != 'confirmed':
                return _('Допуск скасовано.')
            return record.check_id._get_unit_admission_error(record.sample_id)
        except AccessError:
            return _('Немає доступу до підстав цього допуску. Виріб утримується.')
        except (UserError, ValidationError) as error:
            return str(error)

    @api.depends(
        'state', 'check_id.sample_ids.sample_result', 'check_id.equipment_selection_complete',
        'operation_run_id.production_id.qty_producing', 'operation_run_id.workorder_id.production_id',
        'operation_run_id.check_ids.sample_ids.sample_result',
        'operation_run_id.check_ids.equipment_selection_complete',
        'operation_run_id.check_ids.unit_admission_role',
    )
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_effectiveness(self):
        for record in self:
            error = record._get_effectiveness_error()
            record.is_effective = not bool(error)
            record.ineffective_reason = error or ''

    def _revoke(self, reason):
        self.ensure_one()
        self.check_id._check_unit_admission_access()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.check_id._lock_unit_admission_scope()
        self.invalidate_recordset(['state'])
        if self.state != 'confirmed':
            return
        self.with_context(**{ADMISSION_CONTEXT: ADMISSION_TOKEN}).write({
            'state': 'revoked', 'revoked_by_id': self.env.uid,
            'revoked_at': fields.Datetime.now(), 'revocation_reason': reason,
        })
        self.check_id.message_post(body=escape(_(
            'Допуск виробу %(unit)s у запуску %(run)s скасовано. Причина: %(reason)s',
            unit=self.controlled_unit_id.name, run=self.operation_run_id.name, reason=reason,
        )), subtype_xmlid='mail.mt_note')

    @api.constrains('check_id', 'sample_id', 'controlled_unit_id', 'operation_run_id', 'workorder_id', 'production_id', 'product_id', 'company_id')
    def _check_identity(self):
        for record in self:
            check, run, unit = record.check_id, record.operation_run_id, record.controlled_unit_id
            if (
                record.sample_id.quality_check_id != check
                or record.sample_id.controlled_unit_id != unit or check.operation_run_id != run
                or run.state != 'registered' or unit not in run.unit_ids
                or record.workorder_id != run.workorder_id
                or record.workorder_id.production_id != record.production_id
                or record.production_id != run.production_id or not unit._quality_matches_production(record.production_id)
                or record.product_id != run.product_id or record.product_id != unit.product_id
                or record.company_id != run.company_id or record.company_id != unit.company_id
            ):
                raise ValidationError(_('Допуск, зразок, виріб, запуск, MO і робоче завдання повинні відповідати одне одному.'))
