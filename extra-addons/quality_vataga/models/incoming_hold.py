"""Quantity ledger for new incoming receipts; legacy MRP holds stay unchanged."""
import math
import uuid

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


INCOMING_CONTEXT = 'quality_vataga_incoming_operation'
INCOMING_OPERATION = object()
INCOMING_DEFER_CONTEXT = 'quality_vataga_incoming_defer'
INCOMING_DEFER_OPERATION = object()


def incoming_context(env):
    return dict(env.context, **{
        INCOMING_CONTEXT: INCOMING_OPERATION,
        QUARANTINE_CONTEXT: QUARANTINE_OPERATION,
    })


def internal(env):
    return env.context.get(INCOMING_CONTEXT) is INCOMING_OPERATION


def positive_quantity(quantity, rounding):
    if not math.isfinite(quantity) or float_compare(quantity, 0, precision_rounding=rounding) <= 0:
        raise ValidationError(_('Вкажіть додатну скінченну кількість.'))
    if not math.isclose(quantity / rounding, round(quantity / rounding), rel_tol=0, abs_tol=1e-7):
        raise ValidationError(_('Кількість має відповідати точності одиниці вимірювання.'))


class IncomingHold(models.Model):
    _name = 'quality.incoming.hold'
    _description = 'Вхідна партія у карантині'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _rec_name = 'receipt_line_id'
    _check_company_auto = True

    receipt_line_id = fields.Many2one('stock.move.line', required=True, readonly=True,
                                    ondelete='restrict', index=True, check_company=True)
    receipt_id = fields.Many2one(related='receipt_line_id.picking_id', store=True)
    receipt_move_id = fields.Many2one(related='receipt_line_id.move_id', store=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, index=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True, check_company=True)
    lot_id = fields.Many2one('stock.lot', readonly=True, check_company=True)
    owner_id = fields.Many2one('res.partner', readonly=True)
    package_id = fields.Many2one('stock.quant.package', readonly=True, check_company=True)
    supplier_id = fields.Many2one('res.partner', readonly=True)
    location_id = fields.Many2one('stock.location', required=True, readonly=True, check_company=True)
    destination_id = fields.Many2one('stock.location', required=True, readonly=True, check_company=True)
    uom_id = fields.Many2one(related='product_id.uom_id')
    received_qty = fields.Float(required=True, readonly=True, digits='Product Unit of Measure')
    lock_version = fields.Integer(readonly=True, default=0)
    failure_reason = fields.Text(string='Зафіксована невідповідність', readonly=True)
    failure_reported_by_id = fields.Many2one('res.users', readonly=True)
    failure_reported_at = fields.Datetime(readonly=True)
    inspection_ids = fields.One2many('quality.incoming.inspection', 'hold_id', readonly=True)
    operation_ids = fields.One2many('quality.incoming.operation', 'hold_id', readonly=True)
    checked_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')
    good_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')
    bad_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')
    unchecked_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')
    released_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')
    returned_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')
    remaining_qty = fields.Float(compute='_compute_balances', digits='Product Unit of Measure')

    _sql_constraints = [
        ('source_unique', 'unique(receipt_line_id)', 'Це надходження вже утримується.'),
        ('positive_received', 'check(received_qty > 0)', 'Кількість має бути додатною.'),
    ]

    @api.depends('received_qty', 'inspection_ids.good_qty', 'inspection_ids.bad_qty',
                 'operation_ids.quantity', 'operation_ids.kind', 'operation_ids.bucket')
    def _compute_balances(self):
        for hold in self:
            operations = hold.operation_ids
            hold.checked_qty = sum(row.good_qty + row.bad_qty for row in hold.inspection_ids)
            hold.released_qty = sum(operations.filtered(lambda row: row.kind == 'release').mapped('quantity'))
            hold.returned_qty = sum(operations.filtered(lambda row: row.kind == 'return').mapped('quantity'))
            hold.good_qty = sum(hold.inspection_ids.mapped('good_qty')) - sum(
                operations.filtered(lambda row: row.bucket == 'good').mapped('quantity'))
            hold.bad_qty = sum(hold.inspection_ids.mapped('bad_qty')) - sum(
                operations.filtered(lambda row: row.bucket == 'bad').mapped('quantity'))
            hold.unchecked_qty = hold.received_qty - hold.checked_qty - sum(
                operations.filtered(lambda row: row.bucket == 'unchecked').mapped('quantity'))
            hold.remaining_qty = hold.received_qty - hold.released_qty - hold.returned_qty

    @api.model_create_multi
    def create(self, vals_list):
        if not internal(self.env):
            raise AccessError(_('Утримання створюється лише під час надходження.'))
        return super().create(vals_list)

    def write(self, vals):
        if not internal(self.env) and set(vals) - {'message_follower_ids', 'activity_ids'}:
            raise AccessError(_('Реквізити утриманої партії незмінні.'))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _keep_history(self):
        raise UserError(_('Історія вхідного контролю не видаляється.'))

    def _lock(self):
        self.check_access_rights('read')
        self.check_access_rule('read')
        if any(hold.company_id not in self.env.companies for hold in self):
            raise AccessError(_('Недоступна компанія партії.'))
        self.flush_recordset()
        self.env.cr.execute('SELECT id FROM quality_incoming_hold WHERE id IN %s ORDER BY id FOR UPDATE',
                            [tuple(self.ids)])
        # Odoo uses REPEATABLE READ. A lock alone leaves a waiter with an old
        # snapshot of inspection/operation rows; update the parent so the
        # regular RPC retry starts a fresh transaction before recomputing.
        self.env.cr.execute('UPDATE quality_incoming_hold SET lock_version = lock_version + 1 WHERE id IN %s',
                            [tuple(self.ids)])
        self.invalidate_recordset()
        self.inspection_ids.invalidate_recordset()
        self.operation_ids.invalidate_recordset()

    def _require_group(self, group):
        if not self.env.user.has_group(group):
            raise AccessError(_('Недостатньо прав для цієї дії вхідного контролю.'))

    def _check_quantity(self, quantity):
        positive_quantity(quantity, self.uom_id.rounding)
        unit = self.env.ref('uom.product_uom_unit')
        if self.uom_id.category_id == unit.category_id:
            pieces = self.uom_id._compute_quantity(quantity, unit, round=False)
            if not math.isclose(pieces, round(pieces), rel_tol=0, abs_tol=1e-7):
                raise ValidationError(_('Кількість виробів має бути цілим числом штук.'))

    def _sampling_passed(self):
        # Enterprise bridge supplies the immutable QCP and matrix validation.
        return False

    def _has_failure(self):
        self.ensure_one()
        return bool(self.failure_reported_at)

    def _report_failure(self, reason):
        self.ensure_one()
        self._require_group('quality.group_quality_user')
        self._lock()
        self._check_source()
        if not (reason or '').strip() or not self.remaining_qty:
            raise ValidationError(_('Вкажіть невідповідність для утриманого залишку.'))
        if self.failure_reported_at:
            if self.failure_reason != reason:
                raise UserError(_('Невідповідність вже зафіксовано; додайте уточнення до журналу.'))
            return True
        self.sudo().with_context(incoming_context(self.env)).write({
            'failure_reason': reason, 'failure_reported_by_id': self.env.uid,
            'failure_reported_at': fields.Datetime.now(),
        })
        self._record_manual_failure_alert()
        self.message_post(body=_('Зафіксовано невідповідність: %(reason)s', reason=reason),
                          subtype_xmlid='mail.mt_note')
        return True

    def _record_manual_failure_alert(self):
        return False

    def _start_checks(self):
        return False

    def action_start_checks(self):
        self.ensure_one()
        self._require_group('quality.group_quality_user')
        self._lock()
        if not self.remaining_qty or self.inspection_ids or self.operation_ids:
            raise UserError(_('Партія вже розподілена.'))
        return self._start_checks()

    def _check_source(self):
        self.ensure_one()
        line = self.receipt_line_id
        if (line.state != 'done' or line.picking_id.picking_type_id.code != 'incoming'
                or line.location_id.usage != 'supplier' or line.location_dest_id != self.location_id
                or line.product_id != self.product_id or line.lot_id != self.lot_id
                or line.owner_id != self.owner_id or line.result_package_id != self.package_id
                or line.company_id != self.company_id
                or float_compare(line.quantity_product_uom, self.received_qty,
                                 precision_rounding=self.uom_id.rounding)):
            raise UserError(_('Джерело карантинної партії змінилося.'))

    def _check_stock(self, quantity):
        self._check_source()
        quants = self.env['stock.quant'].sudo().search([
            ('company_id', '=', self.company_id.id), ('location_id', '=', self.location_id.id),
            ('product_id', '=', self.product_id.id), ('lot_id', '=', self.lot_id.id),
            ('owner_id', '=', self.owner_id.id), ('package_id', '=', self.package_id.id),
        ])
        if quants:
            quants.flush_recordset()
            self.env.cr.execute('SELECT id FROM stock_quant WHERE id IN %s ORDER BY id FOR UPDATE',
                                [tuple(quants.ids)])
            quants.invalidate_recordset()
        available = sum(q.quantity - q.reserved_quantity for q in quants)
        if float_compare(quantity, available, precision_rounding=self.uom_id.rounding) > 0:
            raise UserError(_('Недостатній вільний залишок у карантині.'))
        if self.product_id.tracking == 'serial' and quantity != 1:
            raise UserError(_('Серійний виріб переміщується лише цілою одиницею.'))
        if self.package_id:
            package_stock = self.env['stock.quant'].sudo().search([
                ('package_id', '=', self.package_id.id), ('quantity', '!=', 0),
            ])
            if (package_stock - quants or float_compare(quantity, sum(quants.mapped('quantity')),
                                                       precision_rounding=self.uom_id.rounding)):
                raise UserError(_('Частковий рух або змішана упаковка потребує окремого керованого розпакування.'))

    def _inspect(self, good, bad, reason, request_key):
        self.ensure_one()
        self._require_group('quality.group_quality_user')
        self._lock()
        self._check_source()
        if not isinstance(request_key, str) or not request_key or len(request_key) > 100:
            raise ValidationError(_('Некоректний ключ контролю.'))
        existing = self.inspection_ids.filtered(lambda row: row.request_key == request_key)
        if existing:
            if existing.good_qty != good or existing.bad_qty != bad or existing.reason != reason:
                raise ValidationError(_('Ключ вже використано для іншого результату.'))
            return existing
        if not self._has_failure():
            raise UserError(_('Суцільний контроль доступний після підтвердженого браку.'))
        if any(not math.isfinite(qty) or qty < 0 for qty in (good, bad)):
            raise ValidationError(_('Кількості мають бути невід’ємними скінченними числами.'))
        for qty in (good, bad):
            if qty:
                self._check_quantity(qty)
        positive_quantity(good + bad, self.uom_id.rounding)
        if float_compare(good + bad, self.unchecked_qty, precision_rounding=self.uom_id.rounding) > 0:
            raise ValidationError(_('Перевірена кількість перевищує неперевірений залишок партії.'))
        if self.product_id.tracking == 'serial' and (good, bad) not in ((1, 0), (0, 1)):
            raise ValidationError(_('Вкажіть результат одного серійного виробу.'))
        if not (reason or '').strip():
            raise ValidationError(_('Опишіть результат суцільного контролю.'))
        result = self.env['quality.incoming.inspection'].sudo().with_context(incoming_context(self.env)).create({
            'hold_id': self.id, 'good_qty': good, 'bad_qty': bad,
            'reason': reason, 'inspector_id': self.env.uid, 'request_key': request_key,
        })
        self.message_post(body=_('Суцільний контроль: придатних %(good)s, брак %(bad)s. %(reason)s',
                                 good=good, bad=bad, reason=reason))
        return result

    def _execute(self, kind, bucket, quantity, reason, request_key):
        self.ensure_one()
        self._require_group('stock.group_stock_user')
        if kind == 'release':
            self._require_group('quality.group_quality_user')
        self._lock()
        if kind not in ('release', 'return') or bucket not in ('unchecked', 'good', 'bad'):
            raise ValidationError(_('Невідомий вид складського рішення.'))
        if not isinstance(request_key, str) or not request_key or len(request_key) > 100:
            raise ValidationError(_('Некоректний ключ виконання.'))
        existing = self.operation_ids.filtered(lambda row: row.request_key == request_key)
        if existing:
            if (existing.kind != kind or existing.bucket != bucket
                    or existing.quantity != quantity or existing.reason != reason):
                raise ValidationError(_('Ключ вже використано для іншого рішення.'))
            return existing
        self._check_quantity(quantity)
        if not (reason or '').strip():
            raise ValidationError(_('Вкажіть підставу складського рішення.'))
        available = {'unchecked': self.unchecked_qty, 'good': self.good_qty, 'bad': self.bad_qty}[bucket]
        if float_compare(quantity, available, precision_rounding=self.uom_id.rounding) > 0:
            raise ValidationError(_('Кількість перевищує доступний залишок вибраної категорії.'))
        if kind == 'return' and not self._has_failure():
            raise UserError(_('Швидке повернення доступне після підтвердженого браку.'))
        if kind == 'release' and (bucket == 'bad' or (bucket == 'unchecked' and (
                self.inspection_ids or not self._sampling_passed()))):
            raise UserError(_('Немає підтвердженого дозволу на приймання цієї кількості.'))
        self._check_stock(quantity)
        context = incoming_context(self.env)
        if kind == 'return':
            picking, move = self._return_supplier(quantity, context)
        else:
            picking, move = self._release_stock(quantity, context)
        result = self.env['quality.incoming.operation'].sudo().with_context(context).create({
            'hold_id': self.id, 'kind': kind, 'bucket': bucket, 'quantity': quantity,
            'reason': reason, 'request_key': request_key, 'executor_id': self.env.uid,
            'picking_id': picking.id, 'move_id': move.id,
        })
        self.message_post(body=_('Виконано %(kind)s: %(qty)s. Документ %(document)s. %(reason)s',
                                 kind=dict(result._fields['kind'].selection)[kind], qty=quantity,
                                 document=picking.name, reason=reason))
        return result

    def _move_line_values(self, move, quantity):
        return {
            'move_id': move.id, 'picking_id': move.picking_id.id,
            'product_id': self.product_id.id, 'product_uom_id': self.uom_id.id,
            'location_id': self.location_id.id, 'location_dest_id': move.location_dest_id.id,
            'lot_id': self.lot_id.id, 'package_id': self.package_id.id,
            'result_package_id': self.package_id.id if move.location_dest_id.usage == 'internal' else False,
            'owner_id': self.owner_id.id, 'quantity': quantity, 'picked': True,
        }

    def _release_stock(self, quantity, context):
        if (not self.destination_id.active or self.destination_id.usage != 'internal'
                or self.destination_id.quality_quarantine
                or self.destination_id.company_id != self.company_id):
            raise UserError(_('Некоректна локація прийнятої продукції.'))
        warehouse = self.receipt_id.picking_type_id.warehouse_id
        picking = self.env['stock.picking'].with_context(context).create({
            'picking_type_id': warehouse.int_type_id.id, 'company_id': self.company_id.id,
            'location_id': self.location_id.id, 'location_dest_id': self.destination_id.id,
            'origin': self.receipt_id.name,
        })
        move = self.env['stock.move'].with_context(context).create({
            'name': self.product_id.display_name, 'picking_id': picking.id,
            'company_id': self.company_id.id, 'product_id': self.product_id.id,
            'product_uom': self.uom_id.id, 'product_uom_qty': quantity,
            'location_id': self.location_id.id, 'location_dest_id': self.destination_id.id,
            'quality_incoming_hold_id': self.id,
            'move_orig_ids': [Command.link(self.receipt_move_id.id)],
            'move_dest_ids': [Command.set(self.receipt_move_id.quality_incoming_downstream_ids.ids)],
        })
        move._action_confirm(merge=False)
        self.env['stock.move.line'].with_context(context).create(self._move_line_values(move, quantity))
        move.picked = True
        move._action_done(cancel_backorder=True)
        return picking, move

    def _return_supplier(self, quantity, context):
        # Standard return links picking.return_id and move.origin_returned_move_id
        # to the actual supplier receipt, never to an internal release or an MO.
        wizard = self.env['stock.return.picking'].with_context(context).create({'picking_id': self.receipt_id.id})
        wizard.product_return_moves = [Command.clear(), Command.create({
            'move_id': self.receipt_move_id.id, 'product_id': self.product_id.id, 'quantity': quantity,
        })]
        wizard.location_id = self.receipt_line_id.location_id
        picking_id, _type_id = wizard._create_returns()
        picking = self.env['stock.picking'].with_context(context).browse(picking_id)
        # The standard wizard copies the receipt header destination. Controlled
        # receipts keep that planned destination, whereas their posted moves
        # end in quarantine. Keep the return header consistent with its moves.
        picking.write({'location_id': self.location_id.id,
                       'location_dest_id': self.receipt_line_id.location_id.id})
        move = picking.move_ids.filtered(lambda row: row.origin_returned_move_id == self.receipt_move_id)
        if (len(move) != 1 or move.location_id != self.location_id
                or move.location_dest_id != self.receipt_line_id.location_id):
            raise UserError(_('Не вдалося створити повернення вихідного надходження.'))
        move.move_line_ids.unlink()
        move.write({'quality_incoming_hold_id': self.id})
        self.env['stock.move.line'].with_context(context).create(self._move_line_values(move, quantity))
        move.picked = True
        move._action_done(cancel_backorder=True)
        return picking, move

    def action_open_decision(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Рішення вхідного контролю'),
                'res_model': 'quality.incoming.decision', 'view_mode': 'form', 'target': 'new',
                'context': {'default_hold_id': self.id}}


class IncomingInspection(models.Model):
    _name = 'quality.incoming.inspection'
    _description = 'Підтверджений суцільний вхідний контроль'
    _order = 'id'

    hold_id = fields.Many2one('quality.incoming.hold', required=True, readonly=True, ondelete='restrict')
    company_id = fields.Many2one(related='hold_id.company_id', store=True)
    good_qty = fields.Float(required=True, readonly=True, digits='Product Unit of Measure')
    bad_qty = fields.Float(required=True, readonly=True, digits='Product Unit of Measure')
    reason = fields.Text(required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True)
    inspector_id = fields.Many2one('res.users', required=True, readonly=True)
    inspected_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    _sql_constraints = [('request_unique', 'unique(hold_id, request_key)', 'Цей результат вже зафіксований.')]

    @api.model_create_multi
    def create(self, vals_list):
        if not internal(self.env):
            raise AccessError(_('Результат фіксується дією суцільного контролю.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Підтверджені результати незмінні.'))

    @api.ondelete(at_uninstall=False)
    def _keep_history(self):
        raise UserError(_('Результати контролю не видаляються.'))


class IncomingOperation(models.Model):
    _name = 'quality.incoming.operation'
    _description = 'Виконане рішення вхідного контролю'
    _order = 'id'

    hold_id = fields.Many2one('quality.incoming.hold', required=True, readonly=True, ondelete='restrict')
    company_id = fields.Many2one(related='hold_id.company_id', store=True)
    kind = fields.Selection([('release', 'Приймання'), ('return', 'Повернення постачальнику')], required=True, readonly=True)
    bucket = fields.Selection([('unchecked', 'Неперевірені'), ('good', 'Придатні'), ('bad', 'Брак')], required=True, readonly=True)
    quantity = fields.Float(required=True, readonly=True, digits='Product Unit of Measure')
    request_key = fields.Char(required=True, readonly=True)
    reason = fields.Text(required=True, readonly=True)
    executor_id = fields.Many2one('res.users', required=True, readonly=True)
    executed_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    picking_id = fields.Many2one('stock.picking', required=True, readonly=True, ondelete='restrict', check_company=True)
    move_id = fields.Many2one('stock.move', required=True, readonly=True, ondelete='restrict', check_company=True)

    _sql_constraints = [('request_unique', 'unique(hold_id, request_key)', 'Це рішення вже виконане.')]

    @api.model_create_multi
    def create(self, vals_list):
        if not internal(self.env):
            raise AccessError(_('Складське виконання реєструється лише захищеною дією.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Виконане рішення незмінне.'))

    @api.ondelete(at_uninstall=False)
    def _keep_history(self):
        raise UserError(_('Історія складських рішень не видаляється.'))


class IncomingDecision(models.TransientModel):
    _name = 'quality.incoming.decision'
    _description = 'Підтвердити рішення вхідного контролю'

    hold_id = fields.Many2one('quality.incoming.hold', required=True, readonly=True)
    company_id = fields.Many2one(related='hold_id.company_id')
    kind = fields.Selection([('release', 'Прийняти'), ('return', 'Повернути постачальнику'),
                             ('inspect', 'Підтвердити суцільний контроль'),
                             ('report', 'Зафіксувати невідповідність')], required=True, default='return')
    bucket = fields.Selection([('unchecked', 'Неперевірені'), ('good', 'Придатні'), ('bad', 'Брак')],
                              required=True, default='unchecked')
    quantity = fields.Float(string='Кількість', digits='Product Unit of Measure')
    good_qty = fields.Float(string='Придатних', digits='Product Unit of Measure')
    bad_qty = fields.Float(string='Брак', digits='Product Unit of Measure')
    reason = fields.Text(string='Підстава / результати', required=True)
    request_key = fields.Char(default=lambda self: str(uuid.uuid4()), required=True, readonly=True)

    def action_confirm(self):
        self.ensure_one()
        if self.kind == 'report':
            self.hold_id._report_failure(self.reason)
            return {'type': 'ir.actions.act_window_close'}
        if self.kind == 'inspect':
            self.hold_id._inspect(self.good_qty, self.bad_qty, self.reason, self.request_key)
            return {'type': 'ir.actions.act_window_close'}
        operation = self.hold_id._execute(self.kind, self.bucket, self.quantity, self.reason, self.request_key)
        return {'type': 'ir.actions.act_window', 'res_model': 'stock.picking', 'view_mode': 'form',
                'res_id': operation.picking_id.id, 'target': 'current'}
