import inspect

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools import SQL

from ..internal import INTERNAL, RFQ, clean_defaults, registry_period_arch


LINE_AUDIT = frozenset({
    'vataga_approval_recorded', 'vataga_product_missing_at_approval',
    'vataga_filled_after_approval', 'vataga_product_created_from_request',
    'vataga_filled_by_id', 'vataga_filled_at', 'vataga_approved_product_id',
    'vataga_approved_description', 'vataga_approved_url', 'vataga_approved_quantity',
    'vataga_approved_uom_id',
})


class ApprovalProductLine(models.Model):
    _inherit = 'approval.product.line'

    # SQL NOT NULL cannot depend on the parent's approval type. The conditional
    # ORM constraint below restores the original rule for every other type.
    description = fields.Char(required=False)
    vataga_product_url = fields.Char(string='URL')
    vataga_approval_recorded = fields.Boolean(string='Погодження зафіксовано', readonly=True, copy=False)
    vataga_product_missing_at_approval = fields.Boolean(string='Без товару при погодженні', readonly=True, copy=False)
    vataga_filled_after_approval = fields.Boolean(string='Заповнено після погодження', readonly=True, copy=False, index=True)
    vataga_product_created_from_request = fields.Boolean(string='Товар створено із заявки', readonly=True, copy=False, index=True)
    vataga_filled_by_id = fields.Many2one('res.users', string='Заповнив', readonly=True, copy=False)
    vataga_filled_at = fields.Datetime(string='Дата заповнення', readonly=True, copy=False)
    vataga_approved_product_id = fields.Many2one('product.product', string='Погоджений товар', readonly=True, copy=False, ondelete='restrict')
    vataga_approved_description = fields.Char(string='Погоджений опис', readonly=True, copy=False)
    vataga_approved_url = fields.Char(string='Погоджений URL', readonly=True, copy=False)
    vataga_approved_quantity = fields.Float(string='Погоджена кількість', readonly=True, copy=False, digits='Product Unit of Measure')
    vataga_approved_uom_id = fields.Many2one('uom.uom', string='Погоджена одиниця вимірювання', readonly=True, copy=False, ondelete='restrict')
    vataga_can_resolve = fields.Boolean(string='Можна заповнити товар', compute='_compute_vataga_can_resolve')
    vataga_approval_type = fields.Selection(related='approval_request_id.approval_type', string='Тип затвердження', store=True, index=True, compute_sudo=False)
    vataga_request_status = fields.Selection(related='approval_request_id.request_status', string='Статус погодження', store=True, index=True, compute_sudo=False)
    vataga_request_subject = fields.Char(related='approval_request_id.vataga_display_subject', string='Тема заявки', store=True, compute_sudo=False)
    vataga_request_number = fields.Char(related='approval_request_id.vataga_display_number', string='Номер заявки', store=True, compute_sudo=False)
    vataga_request_date = fields.Datetime(related='approval_request_id.create_date', string='Дата створення', store=True, compute_sudo=False)
    # Standard Approvals uses Datetime; ERP-UA may override the source as Date.
    # An explicit projection accepts both without an inconsistent related type
    # or truncating the time component of the standard fields.
    vataga_date_start = fields.Datetime(string='Дата початку', compute='_compute_vataga_date_start', store=True, compute_sudo=False)
    vataga_date_end = fields.Datetime(string='Дата закінчення', compute='_compute_vataga_date_end', store=True, compute_sudo=False)
    vataga_partner_id = fields.Many2one(related='approval_request_id.partner_id', string='Контакт', store=True, compute_sudo=False)
    vataga_owner_id = fields.Many2one(related='approval_request_id.request_owner_id', string='Власник заявки', store=True, index=True, compute_sudo=False)
    vataga_initiator_id = fields.Many2one(related='approval_request_id.vataga_initiator_id', string='Автор запису (legacy)', store=True, index=True, compute_sudo=False)
    vataga_recipient_id = fields.Many2one(related='approval_request_id.vataga_recipient_id', string='Отримувач-контакт (legacy)', store=True, index=True, compute_sudo=False)
    vataga_employee_initiator_id = fields.Many2one(related='approval_request_id.x_studio_iniciation', string='Ініціатор закупівлі', store=True, index=True, compute_sudo=False)
    vataga_employee_recipient_id = fields.Many2one(related='approval_request_id.x_studio_who_recived', string='Отримувач', store=True, index=True, compute_sudo=False)
    vataga_category_id = fields.Many2one(related='approval_request_id.category_id', string='Категорія закупівлі', store=True, index=True, compute_sudo=False)
    vataga_rfq_created = fields.Boolean(related='approval_request_id.vataga_rfq_created', string='ЗНКП сформовано', store=True, compute_sudo=False)

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        registry = (self.env.ref('approvals_purchase_vataga.view_purchase_registry_tree', raise_if_not_found=False)
                    if view.name == 'approval.product.line.vataga.registry.tree' else False)
        if registry and view.id == registry.id:
            source = self.env['approval.request']._fields
            arch = registry_period_arch(arch, {name: source[name].type
                                               for name in ('date_start', 'date_end')})
        return arch, view

    @api.depends('approval_request_id.date_start')
    def _compute_vataga_date_start(self):
        for line in self:
            line.vataga_date_start = fields.Datetime.to_datetime(line.approval_request_id.date_start)

    @api.depends('approval_request_id.date_end')
    def _compute_vataga_date_end(self):
        for line in self:
            line.vataga_date_end = fields.Datetime.to_datetime(line.approval_request_id.date_end)

    @api.model
    def _setup_complete(self):
        result = super()._setup_complete()
        # Preserve source field visibility, just as setup_related() does.
        for name in ('date_start', 'date_end'):
            source = self.env['approval.request']._fields[name]
            if source.type not in ('date', 'datetime'):
                raise TypeError('Unsupported approval.request.%s type: %s' % (name, source.type))
            self._fields['vataga_' + name].groups = source.groups
        return result

    @api.constrains('description', 'approval_request_id')
    def _vataga_check_description(self):
        for line in self:
            if line.approval_request_id.approval_type != 'purchase' and not line.description:
                raise ValidationError(_('Опис є обов’язковим для незакупочних заявок.'))

    def _compute_field_value(self, field):
        """Freeze stored business data even when catalog changes recompute it.

        Uses the verified Odoo 17 ORM compute hook, not guessed Enterprise
        compute/onchange method names. Normal computations retain their native
        dependency graph and run unchanged for editable/nonpurchase records.
        """
        immutable = {'description', 'product_uom_id', 'quantity', 'product_id', 'vataga_product_url'}
        group = self.pool.field_computed.get(field, [field])
        names = {item.name for item in group}
        protected = {item.name for item in group
                     if item.name in immutable and item.store and item.column_type}
        if not protected:
            return super()._compute_field_value(field)
        frozen = self.filtered(lambda l: l.id and l.approval_request_id._vataga_is_locked())
        if not frozen:
            return super()._compute_field_value(field)
        # Do not flush the currently recomputing columns. Their database values
        # are exactly the prior agreed values, including legacy rows whose
        # approval history must remain unasserted.
        ordered = sorted(protected)
        self.env.cr.execute(SQL('SELECT id, %s FROM %s WHERE id IN %s',
                                SQL(', ').join(SQL.identifier(name) for name in ordered),
                                SQL.identifier(self._table), tuple(frozen.ids)))
        saved = {row[0]: dict(zip(ordered, row[1:])) for row in self.env.cr.fetchall()}
        for line in frozen:
            if line.id in saved:
                for name in ordered:
                    if self.env.cache.contains(line, self._fields[name]):
                        saved[line.id][name] = self.env.cache.get(line, self._fields[name])
        if names <= protected:
            result = super(ApprovalProductLine, self - frozen)._compute_field_value(field) if self - frozen else None
        else:
            # Preserve extra computed outputs belonging to the same native
            # callback, while restoring its agreed business outputs below.
            result = super()._compute_field_value(field)
        with self.env.protecting([self._fields[name] for name in ordered], frozen):
            for line in frozen:
                values = saved.get(line.id, {})
                if line.vataga_approval_recorded:
                    if 'description' in values:
                        values['description'] = line.vataga_approved_description
                    if 'quantity' in values:
                        values['quantity'] = line.vataga_approved_quantity
                    if 'vataga_product_url' in values:
                        values['vataga_product_url'] = line.vataga_approved_url
                    if 'product_uom_id' in values and line.vataga_approved_uom_id:
                        values['product_uom_id'] = line.vataga_approved_uom_id.id
                for name, value in values.items():
                    line[name] = value if value is not None else False
        frozen._validate_fields(ordered)
        return result

    @api.model
    def _vataga_registry_owner_only(self):
        # This context only NARROWS the registry path. It grants no rights:
        # removing it restores native document access, including a specifically
        # assigned approver's authorized request outside the general registry.
        return (self.env.context.get('vataga_purchase_registry')
                and not self.env.user.has_group('approvals.group_approval_user'))

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        if not self.env.su:
            requests = self.env['approval.request']
            parent_domain = ['|', ('company_id', '=', False), ('company_id', 'in', self.env.companies.ids)]
            if self._vataga_registry_owner_only():
                parent_domain = expression.AND([parent_domain, [('request_owner_id', '=', self.env.uid)]])
            visible = (requests._search(parent_domain)
                       if requests.check_access_rights('read', raise_exception=False) else [])
            restriction = expression.OR([
                [('vataga_approval_type', '!=', 'purchase')],
                [('approval_request_id', 'in', visible)],
            ])
            domain = expression.AND([domain, restriction])
        return super()._search(domain, offset=offset, limit=limit, order=order,
                               access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if not self.env.su and self.ids:
            # Direct browse/read/export must be protected as well as searches.
            # Read only parent IDs, not protected field values, to avoid a
            # recursive fetch/check_access_rule loop on a cold ORM cache.
            self.env['approval.request'].flush_model(['category_id', 'vataga_purchase_managed'])
            self.env['approval.category'].flush_model(['approval_type'])
            self.flush_recordset(['approval_request_id'])
            self.env.cr.execute(SQL('''
                SELECT DISTINCT l.approval_request_id
                  FROM %s l
                  JOIN %s r ON r.id = l.approval_request_id
                  JOIN %s c ON c.id = r.category_id
                 WHERE l.id IN %s AND (c.approval_type = 'purchase' OR r.vataga_purchase_managed)
            ''', SQL.identifier(self._table), SQL.identifier(self.env['approval.request']._table),
                SQL.identifier(self.env['approval.category']._table), tuple(self.ids)))
            requests = self.env['approval.request'].browse([row[0] for row in self.env.cr.fetchall()])
            if requests:
                requests._vataga_check_access('read' if operation == 'read' else 'write')
                if (self._vataga_registry_owner_only()
                        and any(r.request_owner_id.id != self.env.uid for r in requests)):
                    raise AccessError(_('У загальному реєстрі доступні лише власні заявки.'))
        return result

    @api.model_create_multi
    def create(self, vals_list):
        if any(any(k.startswith('vataga_') and k != 'vataga_product_url' for k in vals) for vals in vals_list):
            raise AccessError(_('Історію товарної позиції встановлює лише сервер.'))
        clean = self.with_context(clean_defaults(self.env.context, LINE_AUDIT))
        default_request = clean.default_get(['approval_request_id']).get('approval_request_id')
        requests = self.env['approval.request'].browse(sorted({
            vals.get('approval_request_id', default_request)
            for vals in vals_list if vals.get('approval_request_id', default_request)
        }))
        purchases = requests.filtered(lambda r: r._vataga_is_purchase())
        purchases._vataga_check_access('write')
        purchases._vataga_lock()
        if any(r._vataga_is_locked() for r in purchases):
            raise UserError(_('Додавати позиції до надісланої або обробленої заявки заборонено.'))
        prepared = []
        purchase_ids = set(purchases.ids)
        for vals in vals_list:
            vals = dict(vals)
            request_id = vals.get('approval_request_id', default_request)
            if request_id in purchase_ids:
                if vals.get('purchase_order_line_id'):
                    raise AccessError(_('Зв’язок із ЗНКП встановлює лише стандартна закупочна операція.'))
                vals['company_id'] = purchases.browse(request_id).company_id.id
                vals['purchase_order_line_id'] = False
            prepared.append(vals)
        return super(ApprovalProductLine, clean).create(prepared)

    def write(self, vals):
        internal = self.env.context.get('_vataga_internal') is INTERNAL
        if any(k.startswith('vataga_') and k != 'vataga_product_url' for k in vals) and not internal:
            raise AccessError(_('Історію товарної позиції змінювати заборонено.'))
        purchases = self.filtered(lambda l: l.approval_request_id._vataga_is_purchase())
        requests = purchases.approval_request_id
        if vals.get('approval_request_id'):
            requests |= self.env['approval.request'].browse(vals['approval_request_id'])
        requests = requests.filtered(lambda r: r._vataga_is_purchase())
        if requests:
            requests._vataga_check_access('write')
            requests._vataga_lock()
            if 'company_id' in vals:
                raise AccessError(_('Компанію позиції визначає заявка.'))
        if 'approval_request_id' in vals and requests:
            # Prevent even a draft move from laundering an existing audit trail.
            if any(l.approval_request_id.id != vals['approval_request_id'] for l in self):
                raise UserError(_('Перенесення закупочних позицій між заявками заборонено.'))
        if purchases and 'purchase_order_line_id' in vals:
            if self.env.context.get('_vataga_rfq') is not RFQ:
                raise AccessError(_('Зв’язок із ЗНКП змінює лише стандартна закупочна операція.'))
        locked = purchases.filtered(lambda l: l.approval_request_id._vataga_is_locked())
        if locked:
            if internal and set(vals) <= LINE_AUDIT:
                return super().write(vals)
            if self.env.context.get('_vataga_rfq') is RFQ and set(vals) == {'purchase_order_line_id'}:
                if any(l.purchase_order_line_id for l in locked):
                    raise UserError(_('Для цієї позиції ЗНКП вже сформовано.'))
                return super().write(vals)
            if len(self) == 1 and set(vals) == {'product_id'} and vals['product_id']:
                return self._vataga_resolve_product(self.env['product.product'].browse(vals['product_id']))
            raise UserError(_('Погоджені дані захищено. Для відсутнього товару використайте «Заповнити товар».'))
        return super().write(vals)

    def unlink(self):
        purchases = self.filtered(lambda l: l.approval_request_id._vataga_is_purchase())
        purchases.approval_request_id._vataga_check_access('write')
        purchases.approval_request_id._vataga_lock()
        if any(l.approval_request_id._vataga_is_locked() for l in purchases):
            raise UserError(_('Видаляти позиції надісланої або обробленої заявки заборонено.'))
        return super().unlink()

    def _vataga_record_approval(self):
        self.ensure_one()
        if self.vataga_approval_recorded:
            return
        self.with_context(_vataga_internal=INTERNAL).write({
            'vataga_approval_recorded': True,
            'vataga_product_missing_at_approval': not self.product_id,
            'vataga_approved_product_id': self.product_id.id,
            'vataga_approved_description': self.description,
            'vataga_approved_url': self.vataga_product_url,
            'vataga_approved_quantity': self.quantity,
            'vataga_approved_uom_id': self.product_uom_id.id,
        })

    def _vataga_assert_can_resolve(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        request = self.approval_request_id
        request._vataga_check_access('write')
        if not self.env.user.has_group('purchase.group_purchase_user'):
            raise AccessError(_('Заповнювати товари може лише закупщик.'))
        if not (request.approval_type == 'purchase' and request.request_status == 'approved'
                and self.vataga_approval_recorded and self.vataga_product_missing_at_approval
                and not self.vataga_filled_after_approval and not self.product_id
                and not request._vataga_is_processed()):
            raise UserError(_('Цю позицію вже заповнено або вона недоступна для заповнення.'))

    @api.depends_context('uid', 'allowed_company_ids')
    @api.depends('product_id', 'vataga_approval_recorded', 'vataga_product_missing_at_approval',
                 'vataga_filled_after_approval', 'approval_request_id.request_status',
                 'approval_request_id.vataga_rfq_created')
    def _compute_vataga_can_resolve(self):
        for line in self:
            try:
                line._vataga_assert_can_resolve()
                line.vataga_can_resolve = True
            except (AccessError, UserError):
                line.vataga_can_resolve = False

    def _vataga_resolve_product(self, product, created=False):
        self.ensure_one()
        self.approval_request_id._vataga_lock()
        self._vataga_assert_can_resolve()
        product.ensure_one()
        product.check_access_rights('read')
        product.check_access_rule('read')
        if not product.exists() or not product.purchase_ok or not product.active:
            raise ValidationError(_('Оберіть активний товар, доступний для закупівлі.'))
        if product.company_id and product.company_id != self.approval_request_id.company_id:
            raise ValidationError(_('Компанія товару не відповідає компанії заявки.'))
        approved_uom = self.vataga_approved_uom_id
        if approved_uom and approved_uom.category_id != product.uom_id.category_id:
            raise ValidationError(_('Категорії одиниць вимірювання товару та погодженої позиції несумісні.'))
        values = {
            'product_id': product.id,
            'description': self.vataga_approved_description,
            'product_uom_id': (approved_uom or product.uom_id).id,
            'vataga_filled_after_approval': True,
            'vataga_product_created_from_request': bool(created),
            'vataga_filled_by_id': self.env.uid,
            'vataga_filled_at': fields.Datetime.now(),
        }
        # Explicit computed-field values are protected by Odoo's normal write
        # implementation; protecting also covers third-party onchanges/computes.
        protected = [self._fields['description'], self._fields['product_uom_id']]
        with self.env.cr.savepoint(), self.env.protecting(protected, self):
            result = super(ApprovalProductLine, self).write(values)
            self.flush_recordset(list(values))
            return result

    def action_vataga_resolve(self):
        self.ensure_one()
        self._vataga_assert_can_resolve()
        return {
            'type': 'ir.actions.act_window', 'name': _('Заповнити товар'),
            'res_model': 'approval.product.resolve.vataga', 'view_mode': 'form',
            'target': 'new', 'context': {'default_line_id': self.id},
        }

    def action_vataga_open_request(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        self.approval_request_id._vataga_check_access()
        return {'type': 'ir.actions.act_window', 'res_model': 'approval.request',
                'res_id': self.approval_request_id.id, 'view_mode': 'form',
                'views': [(False, 'form')], 'target': 'current'}

    def _vataga_seller(self):
        self.ensure_one()
        line = self.with_company(self.approval_request_id.company_id)
        if not line.product_id or not line.product_uom_id:
            return self.env['product.supplierinfo']
        eligible = line.product_id._get_filtered_sellers(
            quantity=line.quantity, date=fields.Date.context_today(line), uom_id=line.product_uom_id)
        helper = getattr(line, '_get_seller_id', None)
        if helper:
            try:
                inspect.signature(helper).bind()
            except (TypeError, ValueError) as error:
                raise UserError(_('Контракт вибору постачальника потребує перевірки в цій версії ERP-UA.')) from error
            seller = helper()
            if not seller:
                return self.env['product.supplierinfo']
            if getattr(seller, '_name', None) != 'product.supplierinfo':
                raise UserError(_('Стандартний метод повернув непідтримуваний тип постачальника.'))
            return seller & eligible
        return line.product_id._select_seller(
            quantity=line.quantity, date=fields.Date.context_today(line), uom_id=line.product_uom_id)
