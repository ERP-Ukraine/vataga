from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from ..internal import INTERNAL, RFQ, WORKFLOW, clean_defaults, public_result, refreshable_fields
from ..policy import line_errors
from ..studio_fields import EMPLOYEE_FIELDS, employee_form_arch, validate_employee_fields


REQUEST_AUDIT = frozenset({
    'vataga_number', 'vataga_purchase_managed', 'vataga_approved_once',
    'vataga_rfq_created', 'vataga_rfq_created_at', 'vataga_rfq_created_by_id',
    'vataga_initiator_id',
    'vataga_lock_revision',
    'vataga_legacy_locked',
})
REQUEST_INPUT = frozenset({'vataga_purchase_subject'})


class ApprovalRequest(models.Model):
    _inherit = 'approval.request'

    # Purchase drafts may have no topic; enforce the native requirement for
    # other types below, and the purchase requirement at submission only.
    name = fields.Char(required=False)
    vataga_number = fields.Char(string='Номер заявки', readonly=True, copy=False, index=True)
    vataga_purchase_subject = fields.Char(string='Тема закупівлі')
    vataga_display_subject = fields.Char(string='Тема заявки', compute='_compute_vataga_identity', store=True)
    vataga_display_number = fields.Char(string='Номер заявки', compute='_compute_vataga_identity', store=True)
    vataga_initiator_id = fields.Many2one(
        related='create_uid', string='Автор запису (legacy)', store=True, readonly=True, compute_sudo=False)
    vataga_recipient_id = fields.Many2one('res.partner', string='Отримувач-контакт (legacy)',
                                         check_company=True, copy=False, readonly=True)
    vataga_purchase_managed = fields.Boolean(readonly=True, copy=False)
    vataga_lock_revision = fields.Integer(readonly=True, copy=False, default=0)
    vataga_approved_once = fields.Boolean(readonly=True, copy=False)
    vataga_legacy_locked = fields.Boolean(readonly=True, copy=False)
    vataga_rfq_created = fields.Boolean(string='ЗНКП сформовано', readonly=True, copy=False)
    vataga_rfq_exists = fields.Boolean(string='Наявність ЗНКП для реєстру', compute='_compute_vataga_rfq_exists',
                                      store=True, index=True, compute_sudo=False)
    vataga_rfq_created_at = fields.Datetime(readonly=True, copy=False)
    vataga_rfq_created_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    vataga_can_create_rfq = fields.Boolean(compute='_compute_vataga_rfq')
    vataga_rfq_block_reason = fields.Char(string='Готовність до закупівлі', compute='_compute_vataga_rfq')
    vataga_lines_locked = fields.Boolean(compute='_compute_vataga_lines_locked')

    @api.model
    def _setup_complete(self):
        result = super()._setup_complete()
        validate_employee_fields(self._fields)
        return result

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form':
            arch = employee_form_arch(arch, {name: self._fields[name].domain for name in EMPLOYEE_FIELDS})
            for node in arch.xpath(".//field[@name='name' and not(ancestor::field)]"):
                required = node.get('required', 'True')
                node.set('required', "approval_type != 'purchase' and (%s)" % required)
        return arch, view

    @api.constrains('name', 'category_id')
    def _vataga_native_name_required(self):
        if any(request.approval_type != 'purchase' and not request.name for request in self):
            raise ValidationError(_('Вкажіть назву заявки.'))

    def _vataga_validate_subject(self):
        for request in self.filtered(lambda r: r.approval_type == 'purchase' and r.request_status == 'new'):
            subject = request.vataga_purchase_subject if request.automated_sequence else request.name
            if not (subject or '').strip():
                raise ValidationError(_('Вкажіть тему закупівлі'))

    @api.model
    def _vataga_validate_employee_input(self, vals, company):
        # Only public identity/company/active data; no HR ACL grants or sudo.
        # Do not infer employees from legacy user/partner IDs.
        for name in EMPLOYEE_FIELDS:
            if vals.get(name):
                domain = [('id', '=', vals[name]), ('active', '=', True)]
                if company:
                    domain.append(('company_id', 'in', [False, company.id]))
                employee = self.env['hr.employee.public'].with_context(active_test=False).search(domain, limit=1)
                if not employee:
                    raise ValidationError(_('Виберіть доступного активного співробітника компанії заявки.'))

    @api.depends('name', 'automated_sequence', 'vataga_number', 'vataga_purchase_subject')
    def _compute_vataga_identity(self):
        for request in self:
            request.vataga_display_subject = request.vataga_purchase_subject if request.automated_sequence else request.name
            request.vataga_display_number = request.vataga_number or (request.name if request.automated_sequence else False)

    def _vataga_is_purchase(self):
        self.ensure_one()
        return self.approval_type == 'purchase' or self.vataga_purchase_managed

    @api.model
    def _vataga_check_derived_input(self, vals):
        derived = [name for name in vals if name in self._fields
                   and self._fields[name].readonly
                   and (self._fields[name].related or self._fields[name].compute)]
        if derived:
            raise AccessError(_('Похідні поля закупочної заявки не можна змінювати вручну: %s.',
                                ', '.join(sorted(derived))))

    def _vataga_is_processed(self):
        self.ensure_one()
        return self.vataga_rfq_created or bool(self.product_line_ids.purchase_order_line_id)

    @api.depends('vataga_rfq_created', 'product_line_ids.purchase_order_line_id')
    def _compute_vataga_rfq_exists(self):
        # Display evidence for the WHOLE request. Do not backfill audit flags,
        # dates or authors, and do not substitute this projection for RFQ guards.
        for request in self:
            request.vataga_rfq_exists = bool(
                request.vataga_rfq_created or request.product_line_ids.purchase_order_line_id)

    def _vataga_is_locked(self):
        self.ensure_one()
        return self._vataga_is_purchase() and (
            self.request_status != 'new' or self.vataga_approved_once
            or self.vataga_legacy_locked or self._vataga_is_processed())

    @api.depends('approval_type', 'request_status', 'vataga_approved_once', 'vataga_legacy_locked', 'vataga_rfq_created',
                 'product_line_ids.purchase_order_line_id')
    def _compute_vataga_lines_locked(self):
        for request in self:
            request.vataga_lines_locked = request._vataga_is_locked()

    def _vataga_check_access(self, mode='read'):
        if not self:
            return
        self.check_access_rights(mode)
        self.check_access_rule(mode)
        if any(r.company_id and r.company_id not in self.env.companies for r in self):
            raise AccessError(_('Заявка належить недоступній компанії.'))

    def _vataga_lock(self):
        """All line and request mutations take the same lock in the same order."""
        self._vataga_check_access()
        if self.ids:
            self.flush_recordset(refreshable_fields(self))
            self.env.cr.execute(SQL(
                'SELECT id FROM %s WHERE id IN %s ORDER BY id FOR UPDATE',
                SQL.identifier(self._table), tuple(sorted(self.ids))))
            # A SELECT lock alone is insufficient with REPEATABLE READ when
            # only a child row changes (e.g. two purchasers resolving a line).
            # Touch the parent version so a stale waiter must retry its txn.
            self.env.cr.execute(SQL('''
                UPDATE %s
                   SET vataga_lock_revision = COALESCE(vataga_lock_revision, 0) + 1
                 WHERE id IN %s
            ''', SQL.identifier(self._table), tuple(sorted(self.ids))))
            # Under Odoo's REPEATABLE READ, a conflicting writer can receive
            # SerializationFailure; let the normal RPC retry start a new txn.
            self.invalidate_recordset(refreshable_fields(self))
            relation = self._fields['product_line_ids']
            if not any(self.env.is_protected(relation, request) for request in self):
                lines = self.product_line_ids
                lines.invalidate_recordset(refreshable_fields(lines))

    @api.model_create_multi
    def create(self, vals_list):
        if any(any(k.startswith('vataga_') and k not in REQUEST_INPUT for k in vals) for vals in vals_list):
            raise AccessError(_('Службові ознаки заявки встановлює лише сервер.'))
        clean = self.with_context(clean_defaults(self.env.context, REQUEST_AUDIT))
        values = []
        category_default = clean.default_get(['category_id']).get('category_id')
        for vals in vals_list:
            vals = dict(vals)
            category = self.env['approval.category'].browse(vals.get('category_id', category_default))
            if category and category.approval_type == 'purchase':
                employee_values = {name: clean.env.context.get('default_' + name) for name in EMPLOYEE_FIELDS}
                employee_values.update(vals)
                self._vataga_validate_employee_input(employee_values, category.company_id)
                supplied = {key: value for key, value in vals.items() if key != 'request_status'}
                supplied.update({key[8:]: value for key, value in clean.env.context.items()
                                 if key.startswith('default_') and key != 'default_request_status'})
                self._vataga_check_derived_input(supplied)
                if vals.get('request_status', 'new') != 'new':
                    raise ValidationError(_('Закупочна заявка створюється лише як чернетка.'))
                number = False
                if not category.automated_sequence:
                    number = self.env['ir.sequence'].next_by_code('vataga.purchase.request')
                    if not number:
                        raise UserError(_('Не налаштовано послідовність номерів закупочних заявок Vataga.'))
                vals.update(request_owner_id=self.env.uid, request_status='new', create_uid=self.env.uid,
                            create_date=self.env.cr.now(), company_id=category.company_id.id,
                            vataga_purchase_managed=True,
                            vataga_number=number)
            values.append(vals)
        records = super(ApprovalRequest, clean).create(values)
        for request, vals in zip(records, values):
            if vals.get('vataga_purchase_managed') and request.automated_sequence:
                request.with_context(_vataga_internal=INTERNAL).write({'vataga_number': request.name})
        return records

    def write(self, vals):
        internal = self.env.context.get('_vataga_internal') is INTERNAL
        if any(k.startswith('vataga_') and k not in REQUEST_INPUT for k in vals) and not internal:
            raise AccessError(_('Службові ознаки заявки не можна змінювати вручну.'))
        purchases = self.filtered(lambda r: r._vataga_is_purchase())
        incoming = (self.env['approval.category'].browse(vals['category_id'])
                    if vals.get('category_id') else self.env['approval.category'])
        if incoming and incoming.approval_type == 'purchase':
            purchases = self
        if purchases:
            purchases._vataga_lock()
            if not internal and self.env.context.get('_vataga_workflow') is not WORKFLOW:
                self._vataga_check_derived_input(vals)
            for request in purchases:
                if any(name in vals for name in (*EMPLOYEE_FIELDS, 'category_id', 'company_id')):
                    employee_values = {name: request[name].id for name in EMPLOYEE_FIELDS}
                    employee_values.update(vals)
                    self._vataga_validate_employee_input(employee_values, (incoming or request.category_id).company_id)
                if incoming and (request.approval_type != 'purchase' or incoming.approval_type != 'purchase'):
                    raise UserError(_('Для зміни типу створіть нову заявку. Закупочні дані не можна переносити між типами.'))
                if incoming and incoming.automated_sequence != request.automated_sequence:
                    raise UserError(_('Для категорії з іншим способом нумерації створіть нову закупочну заявку.'))
                if 'company_id' in vals and vals['company_id'] != (incoming or request.category_id).company_id.id:
                    raise AccessError(_('Компанію закупочної заявки визначає її категорія.'))
                if 'request_owner_id' in vals and vals['request_owner_id'] != request.request_owner_id.id:
                    raise AccessError(_('Власника закупочної заявки змінювати заборонено.'))
                if 'request_status' in vals and self.env.context.get('_vataga_workflow') is not WORKFLOW:
                    raise AccessError(_('Використовуйте стандартні дії погодження заявки.'))
                if request._vataga_is_locked() and set(vals).intersection({
                    'category_id', 'company_id', 'vataga_recipient_id', 'vataga_purchase_subject', 'name', 'reference',
                    'partner_id', 'date_start', 'date_end',
                    *EMPLOYEE_FIELDS,
                }):
                    raise UserError(_('Реквізити надісланої або обробленої закупочної заявки захищено.'))
        return super().write(vals)

    def unlink(self):
        for request in self:
            if request._vataga_is_purchase():
                request._vataga_lock()
                if request._vataga_is_locked():
                    raise UserError(_('Не можна видалити надіслану або оброблену закупочну заявку.'))
        return super().unlink()

    def _vataga_validate_lines(self):
        errors = []
        for request in self.filtered(lambda r: r.approval_type == 'purchase'):
            if not request.product_line_ids:
                errors.append(_('%s: додайте хоча б одну товарну позицію.', request.display_name))
            for number, line in enumerate(request.product_line_ids, 1):
                reasons = line_errors(line.product_id, line.description,
                                      line.vataga_product_url, line.quantity)
                if (line.product_id and line.product_uom_id
                        and line.product_uom_id.category_id != line.product_id.uom_id.category_id):
                    reasons.append(_('Одиниця вимірювання несумісна з товаром.'))
                if reasons:
                    errors.append(_('%(request)s, рядок %(number)s: %(reasons)s',
                                    request=request.display_name, number=number, reasons=' '.join(reasons)))
        if errors:
            raise ValidationError('\n'.join(errors))

    def action_confirm(self):
        purchases = self.filtered(lambda r: r._vataga_is_purchase())
        if not purchases:
            return super().action_confirm()
        purchases._vataga_lock()
        with self.env.cr.savepoint():
            purchases._vataga_validate_subject()
            purchases._vataga_preserve_approval_lock()
            self._vataga_validate_lines()
            for line in purchases.product_line_ids:
                if line.product_id and not line.product_uom_id:
                    line.write({'product_uom_id': line.product_id.uom_id.id})
            result = super(ApprovalRequest, self.with_context(_vataga_workflow=WORKFLOW)).action_confirm()
            purchases._vataga_capture_approval()
            return public_result(result)

    def action_approve(self, *args, **kwargs):
        from ..workflow_bridge import approval_scope
        purchases = self.filtered(lambda r: r.approval_type == 'purchase')
        if not purchases:
            return super().action_approve(*args, **kwargs)
        purchases._vataga_lock()
        purchases._vataga_validate_lines()
        before = {r.id: r.request_status for r in purchases}
        with self.env.cr.savepoint(), approval_scope(self):
            purchases._vataga_preserve_approval_lock()
            result = super(ApprovalRequest, self.with_context(_vataga_workflow=WORKFLOW)).action_approve(
                *args, **kwargs)
            purchases.filtered(lambda r: before[r.id] != 'approved')._vataga_capture_approval()
            return public_result(result)

    def _vataga_capture_approval(self):
        # Called only after a successful native workflow action. An approver
        # may have read access to the request and write access to their approval
        # row, without permission to edit the request itself. Elevate ONLY the
        # technical audit; native workflow/status writes remain under native ACL.
        for request in self.sudo():
            if (request.approval_type == 'purchase' and request.request_status == 'approved'
                    and not request.vataga_approved_once and not request.vataga_legacy_locked):
                for line in request.product_line_ids:
                    line._vataga_record_approval()
                request.with_context(_vataga_internal=INTERNAL).write({'vataga_approved_once': True})

    def _vataga_preserve_approval_lock(self):
        for request in self.filtered(lambda r: r._vataga_is_purchase()).sudo():
            if request.request_status == 'approved' and not request.vataga_approved_once:
                # A truthful terminal-state lock, without inventing line history.
                request.with_context(_vataga_internal=INTERNAL).write({'vataga_approved_once': True})

    def action_refuse(self, *args, **kwargs):
        if not any(r._vataga_is_purchase() for r in self):
            return super().action_refuse(*args, **kwargs)
        self.filtered(lambda r: r._vataga_is_purchase())._vataga_lock()
        self._vataga_preserve_approval_lock()
        return public_result(super(ApprovalRequest, self.with_context(_vataga_workflow=WORKFLOW)).action_refuse(*args, **kwargs))

    def action_cancel(self, *args, **kwargs):
        if not any(r._vataga_is_purchase() for r in self):
            return super().action_cancel(*args, **kwargs)
        self.filtered(lambda r: r._vataga_is_purchase())._vataga_lock()
        self._vataga_preserve_approval_lock()
        return public_result(super(ApprovalRequest, self.with_context(_vataga_workflow=WORKFLOW)).action_cancel(*args, **kwargs))

    def action_draft(self, *args, **kwargs):
        if not any(r._vataga_is_purchase() for r in self):
            return super().action_draft(*args, **kwargs)
        self.filtered(lambda r: r._vataga_is_purchase())._vataga_lock()
        self._vataga_preserve_approval_lock()
        for request in self.filtered(lambda r: r._vataga_is_purchase() and not r.vataga_purchase_managed):
            if request.request_status == 'cancel':
                # Pre-install cancellation history cannot prove that a request
                # was never approved. Preserve its data without asserting it was.
                request.with_context(_vataga_internal=INTERNAL).write({'vataga_legacy_locked': True})
        return public_result(super(ApprovalRequest, self.with_context(_vataga_workflow=WORKFLOW)).action_draft(*args, **kwargs))

    def action_withdraw(self, *args, **kwargs):
        if not any(r._vataga_is_purchase() for r in self):
            return super().action_withdraw(*args, **kwargs)
        self.filtered(lambda r: r._vataga_is_purchase())._vataga_lock()
        self._vataga_preserve_approval_lock()
        return public_result(super(ApprovalRequest, self.with_context(_vataga_workflow=WORKFLOW)).action_withdraw(*args, **kwargs))

    def _vataga_rfq_reason(self):
        self.ensure_one()
        if self.approval_type != 'purchase' or self.request_status != 'approved':
            return _('Потрібна затверджена закупочна заявка.')
        if self._vataga_is_processed():
            return _('ЗНКП вже сформовано.')
        if not self.product_line_ids or any(not l.product_id for l in self.product_line_ids):
            return _('Не для всіх позицій вибрано товар.')
        for line in self.product_line_ids:
            if line_errors(line.product_id, line.description, line.vataga_product_url, line.quantity):
                return _('Перевірте кількість та URL товарних позицій.')
            if (not line.product_uom_id
                    or line.product_uom_id.category_id != line.product_id.uom_id.category_id):
                return _('Перевірте сумісність одиниць вимірювання товарів.')
        if not self.env.user.has_group('purchase.group_purchase_user'):
            return _('Потрібні права закупщика.')
        if not self.check_access_rights('write', raise_exception=False):
            return _('Недостатньо прав для обробки заявки.')
        try:
            self._vataga_check_access('write')
            self.env['purchase.order'].check_access_rights('create')
            self.env['purchase.order.line'].check_access_rights('create')
        except AccessError:
            return _('Недостатньо прав для обробки заявки.')
        for line in self.product_line_ids:
            if not line._vataga_seller():
                return _('Не для всіх товарів визначено постачальника.')
        return False

    @api.depends_context('uid', 'company', 'allowed_company_ids')
    @api.depends('request_status', 'approval_type', 'vataga_rfq_created',
                 'product_line_ids.product_id', 'product_line_ids.quantity',
                 'product_line_ids.product_uom_id', 'product_line_ids.purchase_order_line_id')
    def _compute_vataga_rfq(self):
        # Non-stored: fresh form reads also see supplier changes in another tab.
        for request in self:
            try:
                reason = request._vataga_rfq_reason()
            except AccessError:
                reason = _('Недостатньо прав для перевірки постачальників.')
            except UserError as error:
                reason = str(error)
            request.vataga_rfq_block_reason = reason
            request.vataga_can_create_rfq = not reason

    def action_create_purchase_orders(self):
        purchases = self.filtered(lambda r: r.approval_type == 'purchase')
        if not purchases:
            return super().action_create_purchase_orders()
        purchases._vataga_check_access('write')
        purchases._vataga_lock()
        for request in purchases:
            reason = request._vataga_rfq_reason()
            if reason:
                raise UserError(reason)
        with self.env.cr.savepoint():
            from ..rfq_scope import new_rfq_scope
            result = False
            for request in purchases:
                # A separate scope for EACH request, even in a multi-record call.
                # Keep the complete native MRO, grouping and value preparation.
                with new_rfq_scope(self.env.cr, request.ids) as scope:
                    result = super(ApprovalRequest, request.with_context(
                        _vataga_rfq=RFQ)).action_create_purchase_orders()
                    orders = request.product_line_ids.purchase_order_line_id.order_id
                    if not orders or not set(orders.ids) <= scope.order_ids:
                        raise UserError(_('ЗНКП має бути створено окремо для цієї заявки. Операцію скасовано.'))
                if any(not line.purchase_order_line_id for line in request.product_line_ids):
                    raise UserError(_('Стандартне створення ЗНКП не встановило всі зв’язки з позиціями.'))
                request.with_context(_vataga_internal=INTERNAL).write({
                    'vataga_rfq_created': True,
                    'vataga_rfq_created_at': fields.Datetime.now(),
                    'vataga_rfq_created_by_id': self.env.uid,
                })
            others = self - purchases
            if others:
                result = super(ApprovalRequest, others).action_create_purchase_orders()
            return public_result(result)

    def _register_hook(self):
        result = super()._register_hook()
        from ..workflow_bridge import install_bridge
        install_bridge(type(self))
        return result
