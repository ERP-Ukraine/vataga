from collections import defaultdict
from math import isclose

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from .stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION, quarantine_error


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    quality_registered_quantity = fields.Float(
        digits='Product Unit of Measure', readonly=True, copy=False,
    )
    quality_registered_lot_id = fields.Many2one(
        'stock.lot', check_company=True, readonly=True, copy=False,
    )

    quality_quarantine_check_ids = fields.One2many('quality.check', 'production_id')
    quality_quarantine_point_ids = fields.Many2many(
        'quality.point', 'mrp_production_quarantine_point_rel',
        readonly=True, copy=False,
    )
    quality_quarantine_status = fields.Selection([
        ('pending', 'Очікує перевірку якості'),
        ('pass', 'Контроль якості пройдено'),
        ('fail', 'Контроль якості не пройдено'),
        ('accepted', 'Прийнято рішенням Технічної ради'),
    ], compute='_compute_quality_quarantine_status', compute_sudo=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['quality_quarantine_point_ids'] = []
            vals['quality_registered_quantity'] = 0.0
            vals['quality_registered_lot_id'] = False
        return super().create(vals_list)

    def write(self, vals):
        if {'quality_registered_quantity', 'quality_registered_lot_id'} & vals.keys():
            raise UserError(_(
                'Кількість і партію підтверджують через «Зареєструвати виробництво».',
            ))
        if self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION:
            if 'quality_quarantine_point_ids' in vals or (
                {'product_id', 'company_id', 'picking_type_id'} & vals.keys()
                and self.filtered('quality_quarantine_point_ids')
            ):
                raise quarantine_error()
        result = super().write(vals)
        if {'qty_producing', 'lot_producing_id'} & vals.keys():
            stale = self.filtered(
                lambda production: production.quality_registered_quantity and (
                    production.qty_producing != production.quality_registered_quantity
                    or production.lot_producing_id != production.quality_registered_lot_id
                ),
            )
            if stale:
                super(MrpProduction, stale).write({
                    'quality_registered_quantity': 0.0,
                    'quality_registered_lot_id': False,
                })
        return result

    def set_qty_producing(self):
        result = super().set_qty_producing()
        # The Shop Floor confirmation calls this public method after saving
        # the quantity and lot. A work order start only uses the private method.
        for production in self.filtered(
            lambda mo: mo.qty_producing > 0 and mo.state not in ('done', 'cancel')
            and mo.product_tracking != 'serial' and mo.sudo()._quality_matrix_checks(),
        ):
            super(MrpProduction, production).write({
                'quality_registered_quantity': production.qty_producing,
                'quality_registered_lot_id': production.lot_producing_id.id,
            })
        return result

    def _quality_matrix_checks(self):
        self.ensure_one()
        return self.quality_quarantine_check_ids.filtered(
            lambda check: check.x_control_stage == 'in_process'
            and check.measurement_matrix_required and check.point_id
            and check.company_id == self.company_id
            and (not check.product_id or check.product_id == self.product_id),
        )

    def _quality_required_points(self, checks):
        self.ensure_one()
        if self.state == 'done' or self.quality_quarantine_point_ids:
            return self.quality_quarantine_point_ids
        points = self.workorder_ids.quality_point_ids
        points |= self.env['quality.point'].search([
            ('operation_id', '=', False),
            ('company_id', '=', self.company_id.id),
            ('picking_type_ids', 'in', self.picking_type_id.ids),
            ('x_control_stage', '=', 'in_process'),
            '|', ('product_ids', '=', False), ('product_ids', 'in', self.product_id.ids),
            '|', ('product_category_ids', '=', False),
            ('product_category_ids', 'parent_of', self.product_id.categ_id.id),
        ])
        return points.filtered(
            lambda point: point.x_control_stage == 'in_process'
            and ('measure_frequency_type' not in point._fields or point.measure_frequency_type == 'all'),
        ) | checks.point_id

    def _quality_quarantine_location(self):
        self.ensure_one()
        quarantine = self.picking_type_id.quality_quarantine_location_id
        warehouse = self.picking_type_id.warehouse_id
        if (
            quarantine.active and quarantine.quality_quarantine
            and quarantine.usage == 'internal' and quarantine.company_id == self.company_id
            and quarantine.location_id == warehouse.view_location_id
            and quarantine != warehouse.lot_stock_id
        ):
            return quarantine
        return self.env['stock.location']

    def _quality_posted_quantities(self, moves):
        unit = self.env.ref('uom.product_uom_unit')
        quantities = defaultdict(float)
        for line in moves.move_line_ids:
            if line.quantity > 0:
                quantities[line.lot_id.id] += line.product_uom_id._compute_quantity(
                    line.quantity, unit, round=False,
                )
        return quantities

    def _quality_effective_checks(self, checks):
        retries = checks.filtered('quality_recheck_of_id')
        originals = checks - retries
        validated = retries.filtered(lambda retry: (
            retry.quality_state == 'pass' and retry.can_pass_measurement_check
            and retry.quality_recheck_of_id in originals
            and retry.quality_recheck_of_id.quality_state == 'fail'
            and retry.company_id == retry.quality_recheck_of_id.company_id
            and retry.point_id == retry.quality_recheck_of_id.point_id
            and retry.workorder_id == retry.quality_recheck_of_id.workorder_id
            and retry.automatic_sample_count == retry.quality_recheck_of_id.automatic_sample_count
            and retry.automatic_sample_lot_id
                == (retry.quality_recheck_of_id.automatic_sample_lot_id or retry.quality_recheck_of_id.lot_id)
        ))
        return (originals - validated.quality_recheck_of_id) | validated

    def _quality_covered(self, checks, points, quantities):
        checks = self._quality_effective_checks(checks)
        if not quantities or not points or checks.point_id != points:
            return False
        if self.product_tracking != 'none' and False in quantities:
            return False
        coverage = defaultdict(set)
        for check in checks:
            lot = check.automatic_sample_lot_id or check.lot_id
            quantity = quantities.get(lot.id, 0)
            if (
                check.quality_state != 'pass' or not check.can_pass_measurement_check
                or quantity <= 0
                or not isclose(len(check.sample_ids), quantity, rel_tol=0, abs_tol=1e-9)
            ):
                return False
            coverage[lot.id].add(check.point_id.id)
        return all(coverage[lot_id] == set(points.ids) for lot_id in quantities)

    @api.depends(
        'state', 'picking_type_id.quality_quarantine_location_id',
        'quality_quarantine_point_ids', 'qty_producing', 'lot_producing_id',
        'quality_quarantine_check_ids.quality_state',
        'quality_quarantine_check_ids.sample_ids',
        'move_finished_ids.state', 'move_finished_ids.quality_release_move_ids.state',
        'move_finished_ids.quality_release_move_ids.quality_board_accepted',
    )
    def _compute_quality_quarantine_status(self):
        for production in self:
            production.quality_quarantine_status = False
            if not production.picking_type_id.quality_quarantine_location_id:
                continue
            checks = production._quality_matrix_checks()
            points = production._quality_required_points(checks)
            if not points:
                continue
            releases = production.move_finished_ids.quality_release_move_ids
            if (
                production.state == 'done' and releases.filtered('quality_board_accepted')
                and all(move.state == 'done' for move in releases)
            ):
                production.quality_quarantine_status = 'accepted'
            elif 'fail' in production._quality_effective_checks(checks).mapped('quality_state'):
                production.quality_quarantine_status = 'fail'
            elif production.state == 'done':
                production.quality_quarantine_status = (
                    'pass' if releases and all(move.state == 'done' for move in releases)
                    else 'pending'
                )
            else:
                unit = self.env.ref('uom.product_uom_unit')
                uom = production.product_uom_id
                quantities = {}
                if uom.category_id == unit.category_id:
                    quantities[production.lot_producing_id.id] = uom._compute_quantity(
                        production.qty_producing, unit, round=False,
                    )
                production.quality_quarantine_status = (
                    'pass' if production._quality_covered(checks, points, quantities) else 'pending'
                )

    def action_open_quarantine_checks(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if not self.quality_quarantine_status:
            return self.env['ir.actions.actions']._for_xml_id('quality_mrp.quality_check_action_mo')
        # The standard action's default filter excludes operation-bound checks.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Перевірки якості'),
            'res_model': 'quality.check',
            'view_mode': 'tree,form',
            'domain': [('production_id', '=', self.id)],
        }

    def _post_inventory(self, cancel_backorder=False):
        held = []
        for production in self.filtered('picking_type_id.quality_quarantine_location_id'):
            production = production.sudo()
            points = production._quality_required_points(production._quality_matrix_checks())
            if not points:
                continue
            quarantine = production._quality_quarantine_location()
            unit = self.env.ref('uom.product_uom_unit')
            if (
                not quarantine or production.product_id.type != 'product'
                or production.product_uom_id.category_id != unit.category_id
            ):
                raise UserError(_('Для контрольованого випуску потрібен карантин і складський виріб у штуках.'))
            if self.env['stock.rule'].sudo().search_count([
                '|', ('location_src_id', '=', quarantine.id),
                ('location_dest_id', '=', quarantine.id),
            ], limit=1):
                raise UserError(_('Вилучіть карантин із правил складських маршрутів.'))
            production.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION}).write({
                'quality_quarantine_point_ids': [Command.set(points.ids)],
            })
            finished = self.browse(production.id).move_finished_ids.filtered(
                lambda move: move.product_id == production.product_id and not move.byproduct_id,
            )
            if finished.filtered(lambda move: move.state == 'done' and move.quantity > 0
                                 and move.location_dest_id != quarantine):
                raise UserError(_('Частину виробу вже проведено поза карантином. Перевірте рухи випуску.'))
            moves = finished.filtered(lambda move: move.state not in ('done', 'cancel'))
            for move in moves:
                destination = move.location_dest_id
                if (
                    destination.usage != 'internal' or destination.company_id != production.company_id
                    or destination.warehouse_id != production.picking_type_id.warehouse_id
                    or destination.quality_quarantine
                    or move.move_line_ids.location_dest_id - destination
                ):
                    raise UserError(_('Не вдалося визначити єдину складську локацію випуску.'))
                held.append((move, destination, move.move_dest_ids))
                receipt = move.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION})
                receipt.write({
                    'location_dest_id': quarantine.id,
                    'move_dest_ids': [Command.clear()],
                })
                receipt.move_line_ids.write({'location_dest_id': quarantine.id})
        if not held:
            return super()._post_inventory(cancel_backorder=cancel_backorder)
        posting = self.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION})
        result = super(MrpProduction, posting)._post_inventory(cancel_backorder=cancel_backorder)
        for receipt, destination, downstream in held:
            if receipt.state == 'done' and receipt.quantity > 0:
                receipt.production_id._quality_prepare_release(receipt, destination, downstream)
        return result

    def _quality_prepare_release(self, receipt, destination, downstream):
        self.ensure_one()
        move_model = self.env['stock.move'].sudo().with_company(self.company_id).with_context(
            **{QUARANTINE_CONTEXT: QUARANTINE_OPERATION},
        )
        # The draft transfer represents the posted receipt and keeps demand
        # connected. No stock is reserved or moved until the batch passes QC.
        move_model.create({
            'name': self.name,
            'origin': self.name,
            'product_id': receipt.product_id.id,
            'product_uom': receipt.product_uom.id,
            'product_uom_qty': receipt.quantity,
            'company_id': self.company_id.id,
            'warehouse_id': self.picking_type_id.warehouse_id.id,
            'picking_type_id': self.picking_type_id.warehouse_id.int_type_id.id,
            'location_id': receipt.location_dest_id.id,
            'location_dest_id': destination.id,
            'group_id': receipt.group_id.id,
            'quality_held_move_id': receipt.id,
            'move_orig_ids': [Command.link(receipt.id)],
            'move_dest_ids': [Command.set(downstream.ids)],
            'propagate_cancel': False,
        })

    def button_mark_done(self):
        # A stale client can retry after posting. Standard MRP would write
        # consumption links again on the already protected receipt lines.
        posted = self.filtered(lambda mo: mo.state == 'done' and mo.quality_quarantine_point_ids)
        if posted:
            posted.check_access_rights('write')
            posted.check_access_rule('write')
            self -= posted
        if not self:
            return True
        result = super().button_mark_done()
        self.sudo()._quality_release_if_passed()
        return result

    def _quality_release_if_passed(self):
        for production in self.filtered(
            lambda order: order.state == 'done' and order.quality_quarantine_point_ids,
        ):
            releases = production.move_finished_ids.quality_release_move_ids
            pending = releases.filtered(lambda move: move.state == 'draft')
            if not pending or not production._quality_covered(
                production._quality_matrix_checks(), production.quality_quarantine_point_ids,
                production._quality_posted_quantities(releases.quality_held_move_id),
            ):
                continue
            if any(move.state not in ('draft', 'done') for move in releases):
                raise UserError(_('Знайдено незавершений рух випуску з карантину.'))
            production._quality_complete_release(pending)

    def _quality_complete_release(self, pending, *, board_accepted=False):
        self.ensure_one()
        pending = pending.with_company(self.company_id).with_context(
            **{QUARANTINE_CONTEXT: QUARANTINE_OPERATION},
        )
        pending._action_confirm(merge=False)
        for move in pending:
            lines = move.quality_held_move_id.move_line_ids.filtered(lambda line: line.quantity > 0)
            quantities = defaultdict(float)
            for line in lines:
                quantities[line.lot_id, line.result_package_id, line.owner_id] += line.quantity_product_uom
            for (lot, package, owner), quantity in quantities.items():
                quants = self.env['stock.quant'].sudo().search([
                    ('location_id', '=', move.location_id.id),
                    ('product_id', '=', move.product_id.id),
                    ('lot_id', '=', lot.id),
                    ('package_id', '=', package.id),
                    ('owner_id', '=', owner.id),
                ])
                available = sum(quant.quantity - quant.reserved_quantity for quant in quants)
                if float_compare(available, quantity, precision_rounding=move.product_id.uom_id.rounding) < 0:
                    raise UserError(_('У карантині бракує фактично виготовленої партії.'))
            move.write({
                'move_line_ids': [Command.create({
                    'product_id': line.product_id.id,
                    'product_uom_id': line.product_uom_id.id,
                    'location_id': move.location_id.id,
                    'location_dest_id': move.location_dest_id.id,
                    'lot_id': line.lot_id.id,
                    'owner_id': line.owner_id.id,
                    'package_id': line.result_package_id.id,
                    'result_package_id': line.result_package_id.id,
                    'quantity': line.quantity,
                    'picked': True,
                }) for line in lines],
                'picked': True,
                'quality_board_accepted': board_accepted,
            })
        pending._action_done(cancel_backorder=True)


class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    def verify_quality_checks(self):
        for workorder in self:
            pending = workorder.check_ids.filtered(lambda check: check.quality_state == 'none')
            deferred = pending.filtered('quality_quarantine_deferred')
            if not deferred:
                super(MrpWorkorder, workorder).verify_quality_checks()
                continue
            # Same Enterprise verifier, with just the deferred matrix checks removed.
            for check in pending - deferred:
                if check.test_type in ('register_consumed_materials', 'register_byproducts', 'instructions'):
                    check.quality_state = 'pass'
                else:
                    raise UserError(_('You need to complete Quality Checks using the Shop Floor before marking Work Order as Done.'))

    def pre_record_production(self):
        self.ensure_one()
        pending = self.check_ids.filtered(lambda check: check.quality_state == 'none')
        deferred = pending.filtered('quality_quarantine_deferred')
        if not deferred or (pending - deferred).filtered(lambda check: check.test_type != 'instructions'):
            return super().pre_record_production()
        self._check_company()
        if float_compare(self.qty_producing, 0, precision_rounding=self.product_uom_id.rounding) <= 0:
            raise UserError(_('Please set the quantity you are currently producing. It should be different from zero.'))
