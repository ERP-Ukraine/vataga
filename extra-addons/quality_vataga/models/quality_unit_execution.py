from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression


EXECUTION_CONTEXT = 'quality_vataga_unit_execution'
EXECUTION_TOKEN = object()
NATIVE_EXECUTION_TOKEN = object()


class QualityExecutionAccess(models.AbstractModel):
    _name = 'quality.execution.access'
    _description = 'Доступ до виконання через робоче завдання'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        scope = [('workorder_id', 'in', self.env['mrp.workorder']._search([]))]
        if self._name == 'quality.unit.execution':
            try:
                transfers = self.env['quality.unit.transfer']._search([])
            except AccessError:
                # Lack of QC access still hides every legacy transfer. Native
                # facts retain the existing WO/QUNIT read ACLs independently.
                transfers = []
            scope += ['|', '&', ('transfer_id', '=', False),
                      ('controlled_unit_id', 'in', self.env['quality.production.unit']._search([])),
                      ('transfer_id', 'in', transfers)]
        return super()._search(expression.AND([domain, scope]), offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['workorder_id'])
            self.env.cr.execute('SELECT DISTINCT workorder_id FROM ' + self._table + ' WHERE id IN %s', [tuple(self.ids)])
            workorders = self.env['mrp.workorder'].browse([row[0] for row in self.env.cr.fetchall()])
            workorders.check_access_rights('read')
            workorders.check_access_rule('read')
            if self._name == 'quality.unit.execution':
                self.flush_recordset(['transfer_id', 'controlled_unit_id'])
                self.env.cr.execute('SELECT DISTINCT transfer_id, controlled_unit_id FROM quality_unit_execution WHERE id IN %s', [tuple(self.ids)])
                rows = self.env.cr.fetchall()
                transfers = self.env['quality.unit.transfer'].browse({row[0] for row in rows if row[0]})
                transfers.check_access_rights('read')
                transfers.check_access_rule('read')
                units = self.env['quality.production.unit'].browse({row[1] for row in rows if not row[0]})
                units.check_access_rights('read')
                units.check_access_rule('read')
        return result


class QualityExecutionBatch(models.Model):
    _name = 'quality.unit.execution.batch'
    _inherit = 'quality.execution.access'
    _description = 'Спільний робочий інтервал вибраних QUNIT'
    _rec_name = 'request_key'
    _order = 'id desc'

    workorder_id = fields.Many2one('mrp.workorder', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='workorder_id.company_id', store=True, compute_sudo=False)
    operator_id = fields.Many2one('res.users', string='Оператор', required=True, readonly=True, ondelete='restrict')
    started_at = fields.Datetime(string='Початок обробки', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True)
    execution_ids = fields.One2many('quality.unit.execution', 'batch_id', readonly=True)
    timer_ids = fields.One2many('mrp.workcenter.productivity', 'quality_execution_batch_id', readonly=True)

    _sql_constraints = [('request_unique', 'unique(workorder_id, request_key)', 'Запуск обробки вже зареєстровано.')]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(EXECUTION_CONTEXT) not in (EXECUTION_TOKEN, NATIVE_EXECUTION_TOKEN):
            raise UserError(_('Виконання створюється лише захищеною дією WO.'))
        prepared = []
        for vals in vals_list:
            if set(vals) != {'workorder_id', 'request_key'}:
                raise UserError(_('Реквізити робочого інтервалу встановлює система.'))
            workorder = self.env['mrp.workorder'].browse(vals['workorder_id'])
            workorder._check_quality_execution_access()
            started_at = fields.Datetime.now()
            if self.env.context.get(EXECUTION_CONTEXT) is NATIVE_EXECUTION_TOKEN:
                started_at = workorder.date_start or started_at
                if vals['request_key'].startswith('partial:'):
                    running = workorder.time_ids.filtered(
                        lambda timer: timer.user_id == self.env.user and not timer.date_end)
                    started_at = running[:1].date_start or started_at
            prepared.append(dict(vals, company_id=workorder.company_id.id, operator_id=self.env.uid,
                                 started_at=started_at, execution_ids=[], timer_ids=[]))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QualityExecutionBatch, self.with_context(context)).create(prepared)

    def write(self, vals):
        raise UserError(_('Історія робочих інтервалів незмінна.'))

    def unlink(self):
        raise UserError(_('Історія робочих інтервалів не видаляється.'))


class QualityUnitExecution(models.Model):
    _name = 'quality.unit.execution'
    _inherit = 'quality.execution.access'
    _description = 'Підтверджене поштучне виконання робочої операції'
    _rec_name = 'controlled_unit_id'
    _order = 'id desc'

    batch_id = fields.Many2one('quality.unit.execution.batch', required=True, readonly=True, ondelete='restrict', index=True)
    transfer_id = fields.Many2one('quality.unit.transfer', readonly=True, ondelete='restrict', index=True)
    workorder_id = fields.Many2one(related='batch_id.workorder_id', store=True, readonly=True, compute_sudo=False, index=True)
    production_id = fields.Many2one(related='workorder_id.production_id', store=True, readonly=True, compute_sudo=False, index=True)
    company_id = fields.Many2one(related='workorder_id.company_id', store=True, readonly=True, compute_sudo=False, index=True)
    controlled_unit_id = fields.Many2one('quality.production.unit', required=True, readonly=True, ondelete='restrict', index=True)
    source_run_id = fields.Many2one(related='transfer_id.source_run_id', readonly=True, compute_sudo=False)
    source_workorder_id = fields.Many2one(related='transfer_id.source_workorder_id', readonly=True, compute_sudo=False)
    admission_id = fields.Many2one(related='transfer_id.admission_id', readonly=True, compute_sudo=False)
    operator_id = fields.Many2one(related='batch_id.operator_id', store=True, readonly=True, compute_sudo=False)
    started_at = fields.Datetime(related='batch_id.started_at', store=True, readonly=True, compute_sudo=False)
    state = fields.Selection([('progress', 'В процесі'), ('processed', 'Оброблено'), ('stopped', 'Зупинено')], string='Стан обробки', required=True, readonly=True)
    ended_at = fields.Datetime(string='Завершення / зупинка', readonly=True)
    ended_by_id = fields.Many2one('res.users', string='Підтвердив', readonly=True, ondelete='restrict')
    result_reference = fields.Text(string='Результат операції / посилання на документ', readonly=True)
    stop_reason = fields.Text(string='Причина зупинки', readonly=True)
    check_ids = fields.Many2many('quality.check', 'quality_check_execution_rel', 'execution_id', 'check_id', string='Контроль після обробки', readonly=True)

    _sql_constraints = [
        ('batch_unit_unique', 'unique(batch_id, controlled_unit_id)', 'Виріб вже включено до цього виконання.'),
        ('ended_audit', "check((state = 'progress' AND ended_at IS NULL AND ended_by_id IS NULL) OR (state IN ('processed','stopped') AND ended_at IS NOT NULL AND ended_by_id IS NOT NULL))", 'Потрібен аудит завершення.'),
        ('result_required', "check(state != 'processed' OR length(trim(result_reference)) > 0 AND result_reference IS NOT NULL)", 'Вкажіть результат операції.'),
        ('stop_required', "check(state != 'stopped' OR length(trim(stop_reason)) > 0 AND stop_reason IS NOT NULL)", 'Вкажіть причину зупинки.'),
    ]

    @api.private
    def init(self):
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS quality_execution_operation_unique ON quality_unit_execution(workorder_id, controlled_unit_id) WHERE state IN ('progress', 'processed')")
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS quality_execution_running_unique ON quality_unit_execution(controlled_unit_id) WHERE state = 'progress'")

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(EXECUTION_CONTEXT) is NATIVE_EXECUTION_TOKEN:
            return self._create_native_facts(vals_list)
        if self.env.context.get(EXECUTION_CONTEXT) is not EXECUTION_TOKEN:
            raise UserError(_('Обробку розпочинають лише через захищений майстер.'))
        prepared = []
        for vals in vals_list:
            if set(vals) != {'batch_id', 'transfer_id'}:
                raise UserError(_('Реквізити виконання встановлює система.'))
            batch = self.env['quality.unit.execution.batch'].browse(vals['batch_id'])
            transfer = self.env['quality.unit.transfer'].browse(vals['transfer_id'])
            batch.check_access_rule('read')
            batch.workorder_id._check_quality_execution_access()
            error = transfer._get_execution_error(batch.workorder_id)
            if error:
                raise ValidationError(error)
            prepared.append(dict(vals, controlled_unit_id=transfer.controlled_unit_id.id, state='progress', ended_at=False, ended_by_id=False,
                                 result_reference=False, stop_reason=False, check_ids=[]))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QualityUnitExecution, self.with_context(context)).create(prepared)

    @api.model
    def _create_native_facts(self, vals_list):
        from .quality_native_workorder import partial_execution_allows
        prepared = []
        for vals in vals_list:
            if set(vals) != {'batch_id', 'controlled_unit_id'}:
                raise UserError(_('Реквізити виконання встановлює штатне завершення WO.'))
            batch = self.env['quality.unit.execution.batch'].browse(vals['batch_id'])
            batch.check_access_rights('read')
            batch.check_access_rule('read')
            workorder = batch.workorder_id
            workorder._check_quality_execution_access()
            unit = self.env['quality.production.unit'].browse(vals['controlled_unit_id'])
            unit.check_access_rights('read')
            unit.check_access_rule('read')
            partial = partial_execution_allows(workorder, unit) and workorder.state == 'progress' and batch.request_key.startswith('partial:')
            if ((not partial and (workorder.state != 'done' or batch.request_key != 'native:%s' % workorder.id))
                    or unit._quality_current_production() != workorder.production_id
                    or unit.company_id != workorder.company_id or unit.product_id != workorder.product_id):
                raise ValidationError(_('Підтвердження має відповідати завершеному WO та його QUNIT.'))
            prepared.append(dict(vals, transfer_id=False, state='processed',
                                 ended_at=fields.Datetime.now(), ended_by_id=self.env.uid,
                                 result_reference='Native WO: %s' % workorder.id,
                                 stop_reason=False, check_ids=[] if partial else [(6, 0, workorder._quality_native_checks().ids)]))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QualityUnitExecution, self.with_context(context)).create(prepared)

    @api.model
    def _record_native_completion(self, workorder, units):
        # Called inside the native WO write savepoint, after validation and
        # successful state change. No synthetic handoff or productivity row.
        units = units - workorder._quality_native_history().filtered(lambda row: row.state == 'processed').controlled_unit_id
        if not units:
            return self.browse()
        registrar = self.with_context(**{EXECUTION_CONTEXT: NATIVE_EXECUTION_TOKEN})
        batch = self.env['quality.unit.execution.batch'].with_context(
            **{EXECUTION_CONTEXT: NATIVE_EXECUTION_TOKEN}).create({
                'workorder_id': workorder.id, 'request_key': 'native:%s' % workorder.id})
        return registrar.create([{'batch_id': batch.id, 'controlled_unit_id': unit.id}
                                 for unit in units.sorted('id')])

    @api.model
    def _record_partial_completion(self, workorder, units, request_key):
        from .quality_native_workorder import partial_execution_allows
        if not partial_execution_allows(workorder, units):
            raise UserError(_('Немає захищеного підтвердження обробки QUNIT.'))
        batch = self.env['quality.unit.execution.batch'].with_context(
            **{EXECUTION_CONTEXT: NATIVE_EXECUTION_TOKEN}).create({
                'workorder_id': workorder.id, 'request_key': 'partial:%s:%s' % (workorder.id, request_key)})
        return self.with_context(**{EXECUTION_CONTEXT: NATIVE_EXECUTION_TOKEN}).create([
            {'batch_id': batch.id, 'controlled_unit_id': unit.id} for unit in units.sorted('id')])

    def write(self, vals):
        if self.env.context.get(EXECUTION_CONTEXT) is not EXECUTION_TOKEN or set(vals) != {'state', 'ended_at', 'ended_by_id', 'result_reference', 'stop_reason'}:
            raise UserError(_('Виконання та його ідентичність захищені.'))
        if vals['state'] not in ('processed', 'stopped') or any(record.state != 'progress' for record in self):
            raise UserError(_('Підтверджені результати не переписуються.'))
        return super().write(vals)

    def unlink(self):
        raise UserError(_('Історія виконання не видаляється.'))

    @api.model
    def _start(self, workorder, transfers, request_key):
        workorder._check_quality_execution_access()
        self.check_access_rights('create')
        if not transfers or not request_key:
            raise ValidationError(_('Явно оберіть конкретні прийняті QUNIT.'))
        transfers.check_access_rights('read')
        transfers.check_access_rule('read')
        # Reject foreign immutable identities BEFORE acquiring any MO/run
        # lock. A forged mixed-MO selection must not reverse lock ordering.
        if transfers.destination_workorder_id != workorder or transfers.production_id != workorder.production_id or transfers.company_id != workorder.company_id:
            raise ValidationError(_('Усі приймання мають належати саме цій операції, MO та компанії.'))
        with self.env.cr.savepoint():
            workorder.production_id._lock_quality_transfer_scope()
            transfers.check_id.sorted('id')._lock_execution_evidence()
            workorder.invalidate_recordset()
            transfers.invalidate_recordset()
            previous = self.env['quality.unit.execution.batch'].search([('workorder_id', '=', workorder.id), ('request_key', '=', request_key)])
            if previous:
                if previous.operator_id != self.env.user or set(previous.execution_ids.transfer_id.ids) != set(transfers.ids):
                    raise ValidationError(_('Склад попереднього запиту не відповідає вибору.'))
                return previous.execution_ids
            for transfer in transfers:
                error = transfer._get_execution_error(workorder)
                if error:
                    raise ValidationError(error)
            # One shared timer for the selected group, never one timer per unit.
            if self.env['mrp.workcenter.productivity'].search_count([('workorder_id', '=', workorder.id), ('user_id', '=', self.env.uid), ('date_end', '=', False)]):
                raise UserError(_('Спочатку завершіть або зупиніть власний активний робочий інтервал.'))
            batch = self.env['quality.unit.execution.batch'].with_context(**{EXECUTION_CONTEXT: EXECUTION_TOKEN}).create({'workorder_id': workorder.id, 'request_key': request_key})
            records = self.with_context(**{EXECUTION_CONTEXT: EXECUTION_TOKEN}).create([{'batch_id': batch.id, 'transfer_id': transfer.id} for transfer in transfers.sorted('id')])
            workorder._begin_quality_execution(batch)
            records._log_execution('Розпочато обробку')
            return records

    def _finish(self, workorder, mode, reference):
        workorder._check_quality_execution_access()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self or mode not in ('finish', 'stop') or not reference.strip():
            raise ValidationError(_('Оберіть вироби та вкажіть результат операції або причину зупинки.'))
        if self.workorder_id != workorder:
            raise ValidationError(_('Усі результати мають належати саме цій операції.'))
        target = 'processed' if mode == 'finish' else 'stopped'
        with self.env.cr.savepoint():
            workorder.production_id._lock_quality_transfer_scope()
            self.transfer_id.check_id.sorted('id')._lock_execution_evidence()
            self.invalidate_recordset()
            for record in self.sorted('id'):
                if record.workorder_id != workorder:
                    raise ValidationError(_('Виконання належить іншому робочому завданню.'))
                if record.operator_id != self.env.user:
                    raise AccessError(_('Завершити або зупинити обробку має оператор, який її розпочав.'))
                if record.state == target:
                    if (record.result_reference if mode == 'finish' else record.stop_reason) != reference.strip():
                        raise UserError(_('Результат попереднього підтвердження незмінний.'))
                    continue
                if record.state != 'progress':
                    raise UserError(_('Виконання вже завершено або зупинено.'))
                if mode == 'finish':
                    error = record.transfer_id._get_execution_error(workorder, current=record)
                    if error:
                        raise ValidationError(error)
                record.with_context(**{EXECUTION_CONTEXT: EXECUTION_TOKEN}).write({
                    'state': target, 'ended_at': fields.Datetime.now(), 'ended_by_id': self.env.uid,
                    'result_reference': reference.strip() if mode == 'finish' else False,
                    'stop_reason': reference.strip() if mode == 'stop' else False,
                })
                record._log_execution('Оброблено' if mode == 'finish' else 'Зупинено')
            for batch in self.batch_id:
                if not batch.execution_ids.filtered(lambda row: row.state == 'progress'):
                    batch.timer_ids.filtered(lambda timer: not timer.date_end)._close()
        return True

    def _log_execution(self, action):
        for record in self:
            body = escape(_('%(action)s: %(unit)s; операція %(operation)s; вхідний запуск %(run)s; результат: %(result)s',
                            action=action, unit=record.controlled_unit_id.name, operation=record.workorder_id.display_name,
                            run=record.source_run_id.name, result=record.result_reference or record.stop_reason or '—'))
            record.production_id.message_post(body=body, subtype_xmlid='mail.mt_note')
            # MRP operators need not have write permission on the source QC.
            # Keep mandatory audit in the immutable execution and MO thread;
            # never elevate privileges merely to post a duplicate QC note.

    def _validate_control_selection(self, workorder):
        workorder._check_quality_execution_access()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if not self or self.workorder_id != workorder or any(row.state != 'processed' for row in self) or len(self.controlled_unit_id) != len(self):
            raise ValidationError(_('Оберіть різні оброблені QUNIT лише цієї операції.'))
        self.env.cr.execute('SELECT 1 FROM quality_check_execution_rel WHERE execution_id IN %s LIMIT 1', [tuple(self.ids)])
        if self.env.cr.fetchone():
            raise UserError(_('Для цих результатів вже створено контроль. Відкрийте наявну перевірку.'))

    def _create_control(self, workorder, point):
        with self.env.cr.savepoint():
            return self._create_control_locked(workorder, point)

    def _create_control_locked(self, workorder, point):
        workorder.production_id._lock_quality_transfer_scope()
        self._validate_control_selection(workorder)
        if 'workorder_id' not in self.env['quality.check']._fields:
            raise UserError(_('Потрібна встановлена інтеграція перевірок якості з Work Orders.'))
        point.check_access_rights('read')
        point.check_access_rule('read')
        if not point or point.x_control_stage != 'in_process':
            raise ValidationError(_('Оберіть міжопераційну точку контролю.'))
        if 'company_id' in point._fields and point.company_id and point.company_id != workorder.company_id:
            raise ValidationError(_('Точка контролю належить іншій компанії.'))
        operation_field = point._fields.get('operation_id')
        if operation_field and getattr(operation_field, 'comodel_name', None) == 'mrp.routing.workcenter' and point.operation_id and point.operation_id != workorder.operation_id:
            raise ValidationError(_('Точка контролю призначена іншій операції.'))
        if point.picking_type_ids and workorder.production_id.picking_type_id not in point.picking_type_ids:
            raise ValidationError(_('Точка контролю не застосовується до типу цієї виробничої операції.'))
        product_field = point._fields.get('product_ids')
        if product_field and getattr(product_field, 'comodel_name', None) == 'product.product' and point.product_ids and workorder.product_id not in point.product_ids:
            raise ValidationError(_('Точка контролю не застосовується до цього виробу.'))
        check = self.env['quality.check'].with_context(**{EXECUTION_CONTEXT: EXECUTION_TOKEN}).create({
            'point_id': point.id, 'team_id': point.team_id.id, 'test_type_id': point.test_type_id.id,
            'product_id': workorder.product_id.id, 'production_id': workorder.production_id.id,
            'workorder_id': workorder.id, 'company_id': workorder.company_id.id, 'measure_on': 'product',
            'quality_execution_ids': [(6, 0, self.ids)],
        })
        if len(check.sample_ids) != len(self):
            raise ValidationError(_('Налаштуйте матрицю для цієї точки контролю.'))
        units = self.sorted(lambda row: row.controlled_unit_id.id).controlled_unit_id
        check._identify_samples('reuse', [
            {'sample_id': sample.id, 'unit_id': unit.id}
            for sample, unit in zip(check.sample_ids.sorted('sample_number'), units)
        ], workorder=workorder, confirmed=True)
        return check


class QualityTransferExecution(models.Model):
    _inherit = 'quality.unit.transfer'

    execution_ids = fields.One2many('quality.unit.execution', 'transfer_id', readonly=True)
    execution_state = fields.Char(string='Обробка', compute='_compute_execution_state')
    execution_reason = fields.Char(string='Причина утримання', compute='_compute_execution_state')
    previous_result = fields.Selection(string='Попередній контроль', related='admission_id.sample_id.sample_result', readonly=True, compute_sudo=False)

    @api.depends('execution_ids.state', 'state', 'production_id.quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_execution_state(self):
        labels = dict(self.env['quality.unit.execution']._fields['state'].selection)
        for transfer in self:
            latest = transfer.execution_ids.sorted('id', reverse=True)[:1]
            transfer.execution_state = labels[latest.state] if latest else _('Очікує обробки')
            transfer.execution_reason = transfer._get_execution_error(transfer.destination_workorder_id, current=latest if latest.state == 'progress' else None) or ''

    def _get_execution_error(self, workorder, current=None):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.destination_workorder_id != workorder or self.production_id != workorder.production_id or self.company_id != workorder.company_id or self.company_id not in self.env.companies:
            return _('Виріб, передача, операція, MO або компанія не відповідають одне одному.')
        if self.state != 'received':
            return _('Виріб має бути прийнятий цією операцією та не повернений.')
        if not self.production_id.quality_transfer_controlled or self.production_id.state not in ('confirmed', 'progress') or workorder.state in ('done', 'cancel'):
            return _('Поштучне виконання доступне лише для відкритого контрольованого MO / Work Order.')
        if workorder.working_state == 'blocked':
            return _('Робочий центр заблоковано.')
        error = self.admission_id._get_effectiveness_error()
        if error:
            return error
        try:
            self._check_workorders(self.source_workorder_id, workorder)
        except (AccessError, UserError, ValidationError) as error:
            return str(error)
        # Existence checks include hidden records, and grant no access to them.
        self.env['quality.unit.execution'].flush_model(['state', 'workorder_id', 'controlled_unit_id'])
        self.env.cr.execute("SELECT 1 FROM quality_unit_execution WHERE controlled_unit_id = %s AND (state = 'progress' OR (state = 'processed' AND workorder_id = %s)) AND id != %s LIMIT 1", [self.controlled_unit_id.id, workorder.id, current.id if current else 0])
        if self.env.cr.fetchone():
            return _('Виріб вже оброблено цією операцією або він обробляється іншим запуском.')
        return False

    def _get_return_error(self):
        self.ensure_one()
        self.env['quality.unit.execution'].flush_model(['state', 'transfer_id'])
        self.env.cr.execute("SELECT state FROM quality_unit_execution WHERE transfer_id = %s AND state IN ('progress', 'processed') LIMIT 1", [self.id])
        row = self.env.cr.fetchone()
        if row:
            return _('Спочатку зупиніть обробку з причиною.') if row[0] == 'progress' else _('Виріб вже оброблено. Потрібна окрема процедура повторного маршруту; звичайне повернення заборонено.')
        return super()._get_return_error()

    @api.model
    def _advance_processed_receipt(self, admission, request_key):
        """Close the previous edge atomically; retain global active uniqueness."""
        from .quality_unit_transfer import QualityUnitTransfer

        self.env.cr.execute('SELECT 1 FROM quality_unit_transfer WHERE destination_workorder_id = %s LIMIT 1', [admission.workorder_id.id])
        if not self.env.cr.fetchone():
            return  # Original operation, before the first controlled edge.
        self.env.cr.execute("SELECT id FROM quality_unit_transfer WHERE controlled_unit_id = %s AND state IN ('sent','received')", [admission.controlled_unit_id.id])
        previous = self.browse([row[0] for row in self.env.cr.fetchall()])
        previous.check_access_rights('write')
        previous.check_access_rule('write')
        executions = admission.check_id._get_route_execution_evidence().filtered(lambda row: row.controlled_unit_id == admission.controlled_unit_id)
        if len(previous) != 1 or previous.state != 'received' or previous.destination_workorder_id != admission.workorder_id or len(executions) != 1 or executions.state != 'processed' or executions.transfer_id != previous or executions.workorder_id != admission.workorder_id:
            raise ValidationError(_('Передача далі потребує підтвердженої обробки цього QUNIT та нового контролю цієї операції.'))
        # The factory already holds the shared MO revision lock. Keep the
        # existing unique active-transfer index: at most one live edge even
        # during multi-operation execution. Any later failure rolls back both.
        super(QualityUnitTransfer, previous).write({'state': 'advanced'})
        previous.flush_recordset(['state'])
        previous._log_event('advanced', request_key)


class MrpWorkorderUnitExecution(models.Model):
    _inherit = 'mrp.workorder'

    quality_execution_ids = fields.One2many('quality.unit.execution', 'workorder_id', readonly=True)
    quality_execution_waiting = fields.Integer(string='Очікує обробки', compute='_compute_quality_execution_counts')
    quality_execution_progress = fields.Integer(string='В процесі', compute='_compute_quality_execution_counts')
    quality_execution_processed = fields.Integer(string='Оброблено', compute='_compute_quality_execution_counts', help='Підтверджені QUNIT цієї операції. Не є складським випуском або стандартним qty_produced.')
    quality_execution_held = fields.Integer(string='Утримано', compute='_compute_quality_execution_counts')

    @api.depends('quality_execution_ids.state', 'quality_transfer_in_ids.state', 'production_id.quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_quality_execution_counts(self):
        for workorder in self:
            records = self.env['quality.unit.execution'].search([('workorder_id', '=', workorder.id)])
            received = self.env['quality.unit.transfer'].search([('destination_workorder_id', '=', workorder.id), ('state', '=', 'received')])
            active = records.filtered(lambda row: row.state in ('progress', 'processed'))
            waiting = received.filtered(lambda row: row.controlled_unit_id not in active.controlled_unit_id)
            eligible = waiting.filtered(lambda row: not row._get_execution_error(workorder))
            workorder.quality_execution_waiting = len(eligible.controlled_unit_id)
            workorder.quality_execution_progress = len(active.filtered(lambda row: row.state == 'progress').controlled_unit_id)
            workorder.quality_execution_processed = len(active.filtered(lambda row: row.state == 'processed').controlled_unit_id)
            # The source run includes held units which have no transfer at all.
            universe = received.source_run_id.unit_ids
            workorder.quality_execution_held = len(universe - active.controlled_unit_id - eligible.controlled_unit_id)

    def _check_quality_execution_access(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.production_id.check_access_rights('write')
        self.production_id.check_access_rule('write')
        if not self.env.user.has_group('mrp.group_mrp_user') or self.company_id not in self.env.companies:
            raise AccessError(_('Потрібні права оператора виробництва у дозволеній компанії.'))

    def write(self, vals):
        if {'quality_execution_ids', 'quality_execution_waiting', 'quality_execution_progress', 'quality_execution_processed', 'quality_execution_held'}.intersection(vals):
            raise UserError(_('Поштучний прогрес встановлює система.'))
        return super().write(vals)

    def action_open_quality_execution_start(self):
        return self.env['quality.unit.execution.wizard']._open_action(self, 'start')

    def action_open_quality_execution_finish(self):
        return self.env['quality.unit.execution.wizard']._open_action(self, 'finish')

    def action_open_quality_execution_stop(self):
        return self.env['quality.unit.execution.wizard']._open_action(self, 'stop')

    def action_open_quality_execution_control(self):
        return self.env['quality.unit.execution.wizard']._open_action(self, 'control')


class QualityCheckExecutionLock(models.Model):
    _inherit = 'quality.check'

    quality_execution_ids = fields.Many2many('quality.unit.execution', 'quality_check_execution_rel', 'check_id', 'execution_id', readonly=True, copy=False)

    def _get_route_execution_evidence(self):
        self.ensure_one()
        return self.quality_execution_ids

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'quality_execution_ids' in vals:
                if self.env.context.get(EXECUTION_CONTEXT) is not EXECUTION_TOKEN:
                    raise UserError(_('Партію оброблених QUNIT встановлює захищений майстер.'))
                commands = vals['quality_execution_ids']
                if len(commands) != 1 or commands[0][0] != 6:
                    raise ValidationError(_('Потрібен повний явний склад виконаних виробів.'))
                records = self.env['quality.unit.execution'].browse(commands[0][2]).exists()
                records._validate_control_selection(self.env['mrp.workorder'].browse(vals.get('workorder_id')))
        context = dict(self.env.context)
        context.pop('default_quality_execution_ids', None)
        return super(QualityCheckExecutionLock, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'quality_execution_ids' in vals:
            raise UserError(_('Зафіксований склад виконаних виробів незмінний.'))
        return super().write(vals)

    def _get_in_process_sample_quantity(self):
        self.ensure_one()
        if self.quality_execution_ids:
            records = self.quality_execution_ids
            records.check_access_rights('read')
            records.check_access_rule('read')
            if self.x_control_stage != 'in_process' or records.workorder_id != self.workorder_id or records.production_id != self._identity_production() or records.company_id != self._identity_production().company_id or any(row.state != 'processed' for row in records):
                return 0, _('Партія контролю не відповідає підтвердженому поштучному виконанню.')
            return len(records), False
        return super()._get_in_process_sample_quantity()

    def _is_execution_identification(self, mode, mapping, run, workorder):
        self.ensure_one()
        if not self.quality_execution_ids:
            return False
        quantity, error = self._get_in_process_sample_quantity()
        units = self.quality_execution_ids.controlled_unit_id
        if error or mode not in ('reuse', 'existing') or workorder != self.workorder_id or quantity != len(units) or {row.get('unit_id') for row in mapping} != set(units.ids):
            raise ValidationError(_('Можна повторно використати лише явно виконані QUNIT цієї операції.'))
        return True

    def _lock_execution_evidence(self):
        for check in self.sorted('id'):
            check._lock_unit_admission_scope()


class QualityUnitExecutionHistory(models.Model):
    _inherit = 'quality.production.unit'

    quality_execution_ids = fields.One2many('quality.unit.execution', 'controlled_unit_id', string='Історія виконання операцій', readonly=True)
