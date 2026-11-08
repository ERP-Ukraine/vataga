from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare

from .quality_alert import APPROVAL_GROUP, BOARD_STAGE_XMLIDS
from .stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


STOCK_DECISIONS = ('accept_as_is', 'scrap', 'unbuild')


class QualityAlertQuarantine(models.Model):
    _inherit = 'quality.alert'

    quality_failure_snapshot = fields.Html(string='Початкові невідповідності', readonly=True, copy=False)
    decision_reason = fields.Text(string='Підстава рішення', copy=False)
    board_is_quarantine_case = fields.Boolean(compute='_compute_board_batch', compute_sudo=True)
    board_preview_lot_id = fields.Many2one('stock.lot', compute='_compute_board_batch', compute_sudo=True)
    board_preview_quantity = fields.Float(compute='_compute_board_batch', compute_sudo=True)
    board_receipt_id = fields.Many2one('stock.move', readonly=True, copy=False, check_company=True, ondelete='restrict')
    board_lot_id = fields.Many2one('stock.lot', readonly=True, copy=False, check_company=True, ondelete='restrict')
    board_quantity = fields.Float(readonly=True, copy=False, digits='Product Unit of Measure')
    board_executed_at = fields.Datetime(string='Виконано', readonly=True, copy=False)
    board_transfer_id = fields.Many2one('stock.picking', string='Переміщення', readonly=True, copy=False,
                                       check_company=True, ondelete='restrict')
    board_scrap_id = fields.Many2one('stock.scrap', string='Списання', readonly=True, copy=False,
                                    check_company=True, ondelete='restrict')
    board_unbuild_id = fields.Many2one('mrp.unbuild', string='Розбирання', readonly=True, copy=False,
                                      check_company=True, ondelete='restrict')

    def _is_quarantine_board_case(self):
        self.ensure_one()
        check = self.check_id
        return (
            check.x_control_stage == 'in_process'
            and bool(check.production_id.picking_type_id.quality_quarantine_location_id)
        )

    @api.depends('check_id.quality_state', 'check_id.automatic_sample_lot_id', 'check_id.lot_id',
                 'check_id.production_id.state', 'product_id', 'company_id')
    def _compute_board_batch(self):
        for alert in self:
            alert.board_is_quarantine_case = alert._is_quarantine_board_case()
            _receipt, lot, quantity = alert._get_board_batch()
            alert.board_preview_lot_id = lot
            alert.board_preview_quantity = quantity

    def _get_board_batch(self):
        self.ensure_one()
        check = self.check_id.sudo()
        production = check.production_id
        lot = check.automatic_sample_lot_id or check.lot_id
        if (
            not self._is_quarantine_board_case() or check.quality_state != 'fail'
            or production.state != 'done' or check.point_id not in production.quality_quarantine_point_ids
            or len(production.move_finished_ids.quality_release_move_ids) != 1
            or self.company_id != production.company_id or check.company_id != production.company_id
            or self.product_id != production.product_id or (check.product_id and check.product_id != self.product_id)
            or not lot or lot.product_id != self.product_id
        ):
            return False, False, 0.0
        quarantine = production._quality_quarantine_location()
        receipts = production.move_finished_ids.filtered(lambda move: (
            move.state == 'done' and move.location_dest_id == quarantine
            and move.product_id == self.product_id and move.quality_release_move_ids
            and lot in move.move_line_ids.filtered(lambda line: line.quantity > 0).lot_id
        ))
        if len(receipts) != 1:
            return False, False, 0.0
        lines = receipts.move_line_ids.filtered(lambda line: line.quantity > 0)
        if any(line.lot_id != lot or line.location_dest_id != quarantine for line in lines):
            return False, False, 0.0
        return receipts, lot, sum(lines.mapped('quantity_product_uom'))

    def _validate_quarantine_full_control(self):
        for alert in self.filtered(lambda row: row.decision_type == 'full_control' and row._is_quarantine_board_case()):
            checked = sum(alert.full_control_line_ids.mapped('checked_qty'))
            if not checked:
                continue
            _receipt, _lot, quantity = alert._get_board_batch()
            if not quantity or float_compare(checked, quantity, precision_rounding=alert.product_id.uom_id.rounding) > 0:
                raise ValidationError(_('Перевірена кількість не може перевищувати фактичну кількість партії в карантині.'))

    def _prepare_board_quarantine_approval(self):
        self.ensure_one()
        self._validate_quarantine_full_control()
        if not self._is_quarantine_board_case() or self.decision_type not in STOCK_DECISIONS:
            return {}
        if not (self.decision_reason or '').strip():
            raise UserError(_('Вкажіть підставу рішення для всієї партії.'))
        receipt, lot, quantity = self._get_board_batch()
        if not receipt or receipt.quality_release_move_ids.state != 'draft':
            raise UserError(_('Не знайдено єдиної утриманої партії для цього рішення.'))
        return {'board_receipt_id': receipt.id, 'board_lot_id': lot.id, 'board_quantity': quantity}

    def action_execute_board_quarantine(self):
        self.ensure_one()
        if not self.env.user.has_group(APPROVAL_GROUP):
            raise AccessError(_('Виконати складське рішення може лише уповноважений затверджуючий.'))
        self._lock_board_records()
        if (
            not self.is_approved or self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[2])
            or self.approval_user_id != self.env.user or self.approved_by_id != self.env.user
            or self.company_id not in self.env.companies
        ):
            raise AccessError(_('Виконати рішення може лише призначений користувач після затвердження.'))
        if self.board_executed_at:
            return True
        self = self.with_context({'allowed_company_ids': [self.company_id.id]})
        if not self._is_quarantine_board_case():
            raise UserError(_('Дія доступна лише для міжопераційної перевірки карантинного виробництва.'))
        if self.decision_type == 'return_supplier':
            raise UserError(_(
                'Виготовлена партія не має підтвердженого надходження від постачальника. '
                'Повернення не створено, партія залишається в карантині.',
            ))
        if self.decision_type not in STOCK_DECISIONS or not self.board_receipt_id:
            raise UserError(_('Це рішення не дозволяє складський рух.'))
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(_('Для виконання рішення потрібні права складського користувача.'))
        self.check_id.check_access_rights('read')
        self.check_id.check_access_rule('read')
        production = self.check_id.production_id
        production.check_access_rights('read')
        production.check_access_rule('read')
        # Serialize with QC PASS and any other board decision on this MO.
        production.sudo().write({'write_date': fields.Datetime.now()})
        receipt, lot, quantity = self._get_board_batch()
        if (
            not receipt or receipt.id != self.board_receipt_id.id or lot.id != self.board_lot_id.id
            or float_compare(quantity, self.board_quantity, precision_rounding=self.product_id.uom_id.rounding)
            or receipt.quality_release_move_ids.state != 'draft'
        ):
            raise UserError(_('Затверджена партія більше не відповідає утриманому залишку.'))
        self._check_board_batch_stock(receipt, lot, quantity)
        result = self._execute_board_stock_operation(production, receipt, lot, quantity)
        self._record_board_quarantine_execution(result)
        self.message_post(body=_(
            'Виконано затверджене рішення для партії %(lot)s, кількість %(quantity)s.',
            lot=lot.name, quantity=quantity,
        ), subtype_xmlid='mail.mt_note')
        return True

    def _check_board_batch_stock(self, receipt, lot, quantity):
        location = receipt.location_dest_id
        quants = self.env['stock.quant'].sudo().search([
            ('location_id', '=', location.id), ('product_id', '=', self.product_id.id),
            ('lot_id', '=', lot.id), ('company_id', '=', self.company_id.id),
        ])
        if float_compare(
            sum(quants.mapped('quantity')), quantity, precision_rounding=self.product_id.uom_id.rounding,
        ) or any(quants.mapped('reserved_quantity')):
            raise UserError(_('Залишок цієї партії в карантині має точно відповідати затвердженій кількості.'))
        if self.decision_type != 'accept_as_is' and quants.filtered(
            lambda quant: quant.quantity and (quant.package_id or quant.owner_id),
        ):
            raise UserError(_('Списання або розбирання дозволено лише для партії без упаковки та власника.'))

    def _execute_board_stock_operation(self, production, receipt, lot, quantity):
        release = receipt.quality_release_move_ids
        if self.decision_type == 'accept_as_is':
            production.sudo()._quality_complete_release(release, board_accepted=True)
            if release.state != 'done' or not release.picking_id:
                raise UserError(_('Не вдалося провести стандартне переміщення партії.'))
            return {'board_transfer_id': release.picking_id.id}
        if receipt.move_line_ids.filtered(lambda line: line.quantity > 0 and (line.result_package_id or line.owner_id)):
            raise UserError(_('Списання або розбирання дозволено лише для партії без упаковки та власника.'))
        context = {
            'allowed_company_ids': [self.company_id.id],
            QUARANTINE_CONTEXT: QUARANTINE_OPERATION,
        }
        values = {
            'company_id': self.company_id.id, 'product_id': self.product_id.id,
            'product_uom_id': self.product_id.uom_id.id, 'lot_id': lot.id,
            'location_id': receipt.location_dest_id.id,
        }
        if self.decision_type == 'scrap':
            scrap = self.env['stock.scrap'].with_context(context).create(dict(
                values, scrap_qty=quantity, origin=self.display_name, should_replenish=False,
            ))
            scrap.action_validate()
            if scrap.state != 'done':
                raise UserError(_('Не вдалося провести стандартне списання партії.'))
            # Linking before validation would make Odoo count the scrap move as MO output.
            scrap.production_id = production
            result = {'board_scrap_id': scrap.id}
        else:
            unbuild = self._unbuild_board_batch(production, receipt, lot, quantity, values, context)
            result = {'board_unbuild_id': unbuild.id}
        release.with_context(context)._action_cancel()
        return result

    def _unbuild_board_batch(self, production, receipt, lot, quantity, values, context):
        done_moves = production.move_finished_ids.filtered(lambda move: move.state == 'done' and move.quantity > 0)
        raw_moves = production.move_raw_ids.filtered(lambda move: move.state == 'done' and move.quantity > 0)
        bom = production.bom_id
        if not bom or not bom.active or bom.type != 'normal' or done_moves != receipt or not raw_moves:
            raise UserError(_('Для розбирання потрібні активна виробнича специфікація, один випуск і проведені компоненти.'))
        for move in raw_moves.filtered(lambda row: row.has_tracking != 'none'):
            lines = move.move_line_ids.filtered(lambda line: lot in line.produce_line_ids.lot_id and line.lot_id)
            linked_quantity = sum(
                line.product_uom_id._compute_quantity(line.quantity, move.product_uom, round=False)
                for line in lines
            )
            if float_compare(linked_quantity, move.quantity, precision_rounding=move.product_uom.rounding):
                raise UserError(_('Не простежуються всі партії компонентів для стандартного розбирання.'))
        unbuild = self.env['mrp.unbuild'].with_context(context).create(dict(
            values, product_qty=quantity, mo_id=production.id, bom_id=bom.id,
            location_dest_id=production.location_src_id.id,
        ))
        unbuild.action_validate()
        if unbuild.state != 'done':
            raise UserError(_('Не вдалося провести стандартне розбирання партії.'))
        return unbuild
