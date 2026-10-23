from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression


TRANSFER_CONTEXT = 'quality_vataga_unit_transfer'
TRANSFER_TOKEN = object()
ACTIVE_TRANSFER_STATES = ('sent', 'received')


class QualityUnitTransfer(models.Model):
    _name = 'quality.unit.transfer'
    _inherit = 'quality.admission.access'
    _description = 'Передача конкретного виробу між операціями'
    _order = 'id desc'
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    check_id = fields.Many2one('quality.check', required=True, readonly=True, ondelete='restrict', index=True)
    admission_id = fields.Many2one('quality.unit.admission', string='Допуск інспектора', required=True, readonly=True, ondelete='restrict')
    controlled_unit_id = fields.Many2one('quality.production.unit', string='QUNIT', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    source_run_id = fields.Many2one('quality.production.run', string='Вихідний QRUN', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    source_workorder_id = fields.Many2one('mrp.workorder', string='З операції', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    destination_workorder_id = fields.Many2one('mrp.workorder', string='На операцію', required=True, readonly=True, ondelete='restrict', check_company=True, index=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, ondelete='restrict', index=True)
    state = fields.Selection([
        ('sent', 'Передано'), ('received', 'Прийнято наступною операцією'), ('returned', 'Повернено'),
    ], string='Стан передачі', required=True, readonly=True, index=True)
    request_key = fields.Char(required=True, readonly=True, copy=False)
    sent_by_id = fields.Many2one('res.users', string='Передав', required=True, readonly=True, ondelete='restrict')
    sent_at = fields.Datetime(string='Передано', required=True, readonly=True)
    received_by_id = fields.Many2one('res.users', string='Прийняв', readonly=True, ondelete='restrict')
    received_at = fields.Datetime(string='Прийнято', readonly=True)
    returned_by_id = fields.Many2one('res.users', string='Повернув', readonly=True, ondelete='restrict')
    returned_at = fields.Datetime(string='Повернено', readonly=True)
    return_reason = fields.Text(string='Причина повернення', readonly=True)
    event_ids = fields.One2many('quality.unit.transfer.event', 'transfer_id', string='Історія', readonly=True)

    _sql_constraints = [
        ('request_unit_unique', 'unique(request_key, controlled_unit_id)', 'Цю передачу вже зареєстровано.'),
        ('different_workorders', 'check(source_workorder_id != destination_workorder_id)', 'Операції мають бути різними.'),
        ('receipt_audit', "check((received_by_id IS NULL) = (received_at IS NULL) AND (state != 'received' OR received_at IS NOT NULL))", 'Приймання потребує автора та дати.'),
        ('return_audit', "check((state = 'returned' AND returned_by_id IS NOT NULL AND returned_at IS NOT NULL AND return_reason IS NOT NULL AND length(trim(return_reason)) > 0) OR (state != 'returned' AND returned_by_id IS NULL AND returned_at IS NULL AND return_reason IS NULL))", 'Повернення потребує автора, дати та причини.'),
    ]

    @api.private
    def init(self):
        # This iteration supports one outstanding handoff per physical unit.
        # Receipt is not consumption by a later run, nor permission to run MRP.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS quality_unit_transfer_active_unique
            ON quality_unit_transfer (controlled_unit_id)
            WHERE state IN ('sent', 'received')
        """)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        workorders = self.env['mrp.workorder']._search([])
        parents = [
            ('admission_id', 'in', self.env['quality.unit.admission']._search([])),
            ('controlled_unit_id', 'in', self.env['quality.production.unit']._search([])),
            ('source_run_id', 'in', self.env['quality.production.run']._search([])),
            ('source_workorder_id', 'in', workorders), ('destination_workorder_id', 'in', workorders),
        ]
        return super()._search(expression.AND([domain, parents]), offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            names = ['admission_id', 'controlled_unit_id', 'source_run_id', 'source_workorder_id', 'destination_workorder_id']
            self.flush_recordset(names)
            self.env.cr.execute('SELECT admission_id, controlled_unit_id, source_run_id, source_workorder_id, destination_workorder_id FROM quality_unit_transfer WHERE id IN %s', [tuple(self.ids)])
            rows = self.env.cr.fetchall()
            for index, model in enumerate(('quality.unit.admission', 'quality.production.unit', 'quality.production.run', 'mrp.workorder', 'mrp.workorder')):
                records = self.env[model].browse({row[index] for row in rows})
                records.check_access_rights('read')
                records.check_access_rule('read')
        return result

    @api.model
    def _check_workorders(self, source, destination):
        for workorder in (source, destination):
            workorder.ensure_one()
            workorder.check_access_rights('write')
            workorder.check_access_rule('write')
            if workorder.company_id not in self.env.companies:
                raise AccessError(_('Немає доступу до компанії операції.'))
        production = source.production_id
        production.check_access_rights('read')
        production.check_access_rule('read')
        self.env['mrp.workorder'].flush_model(['blocked_by_workorder_ids'])
        self.env.cr.execute('SELECT blocked_by_id FROM mrp_workorder_dependencies_rel WHERE workorder_id = %s', [destination.id])
        predecessors = {row[0] for row in self.env.cr.fetchall()}
        self.env.cr.execute('SELECT workorder_id FROM mrp_workorder_dependencies_rel WHERE blocked_by_id = %s', [source.id])
        successors = {row[0] for row in self.env.cr.fetchall()}
        if (
            source == destination or destination.production_id != production
            or source.company_id != destination.company_id
            or source.product_id != production.product_id or destination.product_id != production.product_id
            or not production.allow_workorder_dependencies
            or destination.blocked_by_workorder_ids != source
            or source.needed_by_workorder_ids != destination
            or predecessors != {source.id} or successors != {destination.id}
        ):
            raise ValidationError(_('Потрібне наступне робоче завдання цього MO з єдиною прямою залежністю від вихідної операції. Розгалужені та неоднозначні маршрути поки не підтримуються.'))
        # Reject even corrupt dependency cycles; sequence or database IDs do
        # not establish a manufacturing route.
        # Inspect only existence of invalid paths, including dependencies
        # hidden by a record rule. Hidden branches must not authorize a route.
        self.env.cr.execute('''
            WITH RECURSIVE ancestors(id, path, cycle) AS (
                SELECT %s::integer, ARRAY[%s::integer], false
                UNION ALL
                SELECT rel.blocked_by_id, a.path || rel.blocked_by_id, rel.blocked_by_id = ANY(a.path)
                FROM ancestors a JOIN mrp_workorder_dependencies_rel rel ON rel.workorder_id = a.id
                WHERE NOT a.cycle
            )
            SELECT 1 FROM ancestors a JOIN mrp_workorder wo ON wo.id = a.id
            WHERE a.cycle OR a.id = %s OR wo.production_id != %s LIMIT 1
        ''', [source.id, source.id, destination.id, production.id])
        if self.env.cr.fetchone():
            raise ValidationError(_('Циклічна або несумісна залежність операцій.'))
        if production.state not in ('confirmed', 'progress') or source.state != 'progress' or destination.state in ('progress', 'done', 'cancel') or destination.qty_produced or production.qty_produced:
            raise ValidationError(_('Передача доступна з активної операції відкритого MO до ще не розпочатої наступної операції.'))
        return production

    @api.model
    def _check_admission(self, admission, destination):
        admission.ensure_one()
        admission.check_access_rights('read')
        admission.check_access_rule('read')
        admission.check_id._check_unit_admission_access()
        error = admission._get_effectiveness_error()
        if error:
            raise ValidationError(error)
        production = self._check_workorders(admission.workorder_id, destination)
        if admission.production_id != production or admission.controlled_unit_id not in admission.operation_run_id.unit_ids:
            raise ValidationError(_('Виріб, допуск, MO і склад вихідного запуску не відповідають одне одному.'))
        # Historical later runs predate this handoff implementation. Do not
        # silently interpret them as an unrecorded physical return.
        error = admission._get_consumption_error()
        if error:
            raise ValidationError(error)
        return production

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(TRANSFER_CONTEXT) is not TRANSFER_TOKEN:
            raise UserError(_('Передачі створюються лише явною дією передачі виробів.'))
        self.check_access_rights('create')
        prepared = []
        for vals in vals_list:
            if set(vals) != {'admission_id', 'destination_workorder_id', 'request_key'} or not vals['request_key']:
                raise UserError(_('Реквізити передачі встановлює система.'))
            admission = self.env['quality.unit.admission'].browse(vals['admission_id']).exists()
            destination = self.env['mrp.workorder'].browse(vals['destination_workorder_id']).exists()
            admission.check_id._lock_unit_admission_scope()
            admission.production_id._lock_quality_transfer_scope()
            admission.invalidate_recordset()
            admission.production_id.invalidate_recordset(['state', 'qty_producing', 'allow_workorder_dependencies'])
            admission.workorder_id.invalidate_recordset()
            destination.invalidate_recordset()
            production = self._check_admission(admission, destination)
            # A real write on the shared MO serializes activation with native
            # completion/inventory, even under PostgreSQL REPEATABLE READ.
            production._enable_quality_transfer_control()
            prepared.append(dict(
                vals, name='%s → %s' % (admission.controlled_unit_id.name, destination.display_name),
                check_id=admission.check_id.id, controlled_unit_id=admission.controlled_unit_id.id,
                production_id=production.id, source_run_id=admission.operation_run_id.id,
                source_workorder_id=admission.workorder_id.id, company_id=production.company_id.id,
                state='sent', sent_by_id=self.env.uid, sent_at=fields.Datetime.now(),
                received_by_id=False, received_at=False, returned_by_id=False,
                returned_at=False, return_reason=False, event_ids=[],
            ))
        # Explicit values above neutralize client default_* values for all
        # protected fields. No sudo is used to create receipts or audit rows.
        records = super().create(prepared)
        for record in records:
            record._log_event('sent', record.request_key)
        return records

    def write(self, vals):
        if self.env.context.get(TRANSFER_CONTEXT) is not TRANSFER_TOKEN:
            raise UserError(_('Запис передачі захищено. Використайте приймання або повернення.'))
        allowed = {'state', 'received_by_id', 'received_at'} if vals.get('state') == 'received' else {'state', 'returned_by_id', 'returned_at', 'return_reason'}
        if set(vals) != allowed or vals.get('state') not in ('received', 'returned'):
            raise UserError(_('Реквізити передачі незмінні.'))
        if any(record.state not in (('sent',) if vals['state'] == 'received' else ACTIVE_TRANSFER_STATES) for record in self):
            raise UserError(_('Стан передачі вже змінився. Відкрийте майстер знову.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Історія передач не видаляється.'))

    @api.model
    def _send_samples(self, check, sample_ids, destination, request_key):
        check.ensure_one()
        check._check_unit_admission_access()
        self.check_access_rights('create')
        if not sample_ids or len(sample_ids) != len(set(sample_ids)):
            raise ValidationError(_('Явно оберіть різні вироби.'))
        with self.env.cr.savepoint():
            check._lock_unit_admission_scope()
            check._identity_production()._lock_quality_transfer_scope()
            samples = check.sample_ids.filtered(lambda sample: sample.id in sample_ids)
            if set(samples.ids) != set(sample_ids):
                raise ValidationError(_('Вироби мають належати поточній перевірці.'))
            records = self.browse()
            for sample in samples.sorted('id'):
                # Check every unit, never the aggregate x_admitted_qty.
                admission = self.env['quality.unit.admission'].search([
                    ('operation_run_id', '=', check.operation_run_id.id),
                    ('controlled_unit_id', '=', sample.controlled_unit_id.id), ('state', '=', 'confirmed'),
                ])
                if len(admission) != 1:
                    raise ValidationError(_('Виріб %(unit)s не має доступного чинного допуску.', unit=sample.controlled_unit_id.display_name or sample.display_name))
                previous = self.search([('request_key', '=', request_key), ('controlled_unit_id', '=', sample.controlled_unit_id.id)])
                if previous:
                    if previous.state == 'returned' or previous.destination_workorder_id != destination or previous.admission_id != admission:
                        raise UserError(_('Попереднє рішення вже повернено або змінено. Відкрийте новий майстер.'))
                    previous.check_access_rule('read')
                    records |= previous
                    continue
                records |= self.with_context(**{TRANSFER_CONTEXT: TRANSFER_TOKEN}).create({
                    'admission_id': admission.id, 'destination_workorder_id': destination.id, 'request_key': request_key,
                })
            return records

    def _transition(self, mode, request_key, reason=''):
        if mode not in ('receive', 'return') or not self:
            raise ValidationError(_('Оберіть передачі та допустиму дію.'))
        if mode == 'return' and not reason.strip():
            raise ValidationError(_('Вкажіть причину повернення.'))
        self.check_access_rights('write')
        self.check_access_rule('write')
        with self.env.cr.savepoint():
            for record in self.sorted(lambda item: (item.production_id.id, item.id)):
                record.check_id._check_unit_admission_access()
                record.check_id._lock_unit_admission_scope()
                record.production_id._lock_quality_transfer_scope()
                record.invalidate_recordset()
                for workorder in record.source_workorder_id | record.destination_workorder_id:
                    workorder.check_access_rights('write')
                    workorder.check_access_rule('write')
                state = 'received' if mode == 'receive' else 'returned'
                if record.state == state:
                    continue
                if record.state not in (('sent',) if mode == 'receive' else ACTIVE_TRANSFER_STATES):
                    raise UserError(_('Передачу вже повернено.'))
                if mode == 'receive':
                    error = record.admission_id._get_effectiveness_error()
                    if error:
                        raise ValidationError(error)
                    self._check_workorders(record.source_workorder_id, record.destination_workorder_id)
                    values = {'state': state, 'received_by_id': self.env.uid, 'received_at': fields.Datetime.now()}
                else:
                    error = record._get_return_error()
                    if error:
                        raise UserError(error)
                    values = {'state': state, 'returned_by_id': self.env.uid, 'returned_at': fields.Datetime.now(), 'return_reason': reason.strip()}
                record.with_context(**{TRANSFER_CONTEXT: TRANSFER_TOKEN}).write(values)
                record._log_event(state, request_key, reason.strip() if mode == 'return' else '')
        return True

    def _get_return_error(self):
        self.ensure_one()
        self.env['quality.production.run'].flush_model(['unit_ids', 'state'])
        self.env.cr.execute("SELECT 1 FROM quality_production_run r JOIN quality_production_run_unit_rel rel ON rel.run_id = r.id WHERE rel.unit_id = %s AND r.id > %s AND r.state = 'registered' LIMIT 1", [self.controlled_unit_id.id, self.source_run_id.id])
        if self.env.cr.fetchone():
            return _('Виріб включено до пізнішого запуску. Потрібна окрема процедура повернення з виробництва.')
        return False

    def _log_event(self, action, request_key, reason=''):
        self.ensure_one()
        self.env['quality.unit.transfer.event'].with_context(**{TRANSFER_CONTEXT: TRANSFER_TOKEN}).create({
            'transfer_id': self.id, 'action': action, 'request_key': request_key, 'reason': reason,
        })
        labels = dict(self._fields['state'].selection)
        body = escape(_(
            '%(action)s: %(unit)s; %(origin_operation)s → %(destination)s; запуск %(run)s. %(reason)s',
            action=labels[action], unit=self.controlled_unit_id.name,
            origin_operation=self.source_workorder_id.display_name, destination=self.destination_workorder_id.display_name,
            run=self.source_run_id.name, reason=reason,
        ))
        self.check_id.message_post(body=body, subtype_xmlid='mail.mt_note')
        self.production_id.message_post(body=body, subtype_xmlid='mail.mt_note')

    @api.constrains('admission_id', 'check_id', 'controlled_unit_id', 'production_id', 'source_run_id', 'source_workorder_id', 'destination_workorder_id', 'company_id')
    def _check_identity(self):
        for record in self:
            admission = record.admission_id
            if (
                record.check_id != admission.check_id or record.controlled_unit_id != admission.controlled_unit_id
                or record.production_id != admission.production_id or record.source_run_id != admission.operation_run_id
                or record.source_workorder_id != admission.workorder_id or record.company_id != admission.company_id
                or record.destination_workorder_id.production_id != record.production_id
                or record.destination_workorder_id.company_id != record.company_id
            ):
                raise ValidationError(_('Передача, допуск, виріб, запуск, операції, MO та компанія повинні відповідати одне одному.'))


class QualityUnitTransferEvent(models.Model):
    _name = 'quality.unit.transfer.event'
    _description = 'Незмінна подія передачі виробу'
    _order = 'id'

    transfer_id = fields.Many2one('quality.unit.transfer', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='transfer_id.company_id', store=True, readonly=True, compute_sudo=False)
    action = fields.Selection([('sent', 'Передано'), ('received', 'Прийнято'), ('returned', 'Повернено')], required=True, readonly=True)
    actor_id = fields.Many2one('res.users', string='Виконав', required=True, readonly=True, ondelete='restrict')
    occurred_at = fields.Datetime(string='Дата', required=True, readonly=True)
    reason = fields.Text(string='Причина', readonly=True)
    request_key = fields.Char(required=True, readonly=True)

    _sql_constraints = [('request_unique', 'unique(transfer_id, action, request_key)', 'Подію вже зареєстровано.')]

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        return super()._search(expression.AND([domain, [('transfer_id', 'in', self.env['quality.unit.transfer']._search([]))]]), offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['transfer_id'])
            self.env.cr.execute('SELECT DISTINCT transfer_id FROM quality_unit_transfer_event WHERE id IN %s', [tuple(self.ids)])
            parents = self.env['quality.unit.transfer'].browse([row[0] for row in self.env.cr.fetchall()])
            parents.check_access_rights('read')
            parents.check_access_rule('read')
        return result

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(TRANSFER_CONTEXT) is not TRANSFER_TOKEN:
            raise UserError(_('Події передачі встановлює система.'))
        prepared = []
        for vals in vals_list:
            if set(vals) != {'transfer_id', 'action', 'request_key', 'reason'}:
                raise UserError(_('Службові реквізити події встановлює система.'))
            transfer = self.env['quality.unit.transfer'].browse(vals['transfer_id'])
            transfer.check_access_rights('write')
            transfer.check_access_rule('write')
            prepared.append(dict(vals, actor_id=self.env.uid, occurred_at=fields.Datetime.now(), company_id=transfer.company_id.id))
        return super().create(prepared)

    def write(self, vals):
        raise UserError(_('Історія передач незмінна.'))

    def unlink(self):
        raise UserError(_('Історія передач не видаляється.'))


class QualityAdmissionTransfer(models.Model):
    _inherit = 'quality.unit.admission'

    def _get_consumption_error(self):
        self.ensure_one()
        # Protective existence check, including records hidden by a record
        # rule. It conveys no data or authorization and never changes ACLs.
        self.env['quality.unit.transfer'].flush_model(['admission_id', 'state'])
        self.env.cr.execute("SELECT 1 FROM quality_unit_transfer WHERE admission_id = %s AND state IN ('sent', 'received') LIMIT 1", [self.id])
        if self.env.cr.fetchone():
            return _('Виріб передано або прийнято наступною операцією. Спочатку оформіть контрольоване повернення з причиною.')
        return super()._get_consumption_error()
