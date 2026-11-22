"""Prevent standard MRP/Stock entry points from bypassing a C decision."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .production_residual_stock import case_internal


def has_production_case(productions):
    return bool(productions.sudo().filtered(lambda production: (
        production.quality_production_case_enabled and production.quality_production_case_ids)))


def move_productions(moves):
    moves = moves.sudo()
    return (moves.production_id | moves.raw_material_production_id | moves.workorder_id.production_id
            | moves.quality_held_move_id.production_id)


class ProductionCaseScrap(models.Model):
    _inherit = 'stock.scrap'

    quality_production_case_id = fields.Many2one('quality.alert', readonly=True, copy=False,
                                                check_company=True, ondelete='restrict')

    def _check_production_case(self, values=None):
        if case_internal(self.env):
            return
        values = values or {}
        production = self.env['mrp.production'].sudo().browse(values.get('production_id'))
        workorder = self.env['mrp.workorder'].sudo().browse(values.get('workorder_id'))
        picking = self.env['stock.picking'].sudo().browse(values.get('picking_id'))
        sources = (self.sudo().production_id | production | self.sudo().workorder_id.production_id
                   | workorder.production_id | move_productions(self.sudo().move_ids)
                   | move_productions((self.sudo().picking_id | picking).move_ids))
        if (values.get('quality_production_case_id') or self.sudo().quality_production_case_id
                or has_production_case(sources)):
            raise UserError(_('Списання виробничого випадку C виконується лише з підтвердженого рішення цього випадку.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_production_case(vals)
        scraps = super().create(vals_list)
        scraps._check_production_case()
        return scraps

    def write(self, vals):
        self._check_production_case(vals)
        result = super().write(vals)
        self._check_production_case()
        return result

    def action_validate(self):
        self._check_production_case()
        return super().action_validate()

    def do_scrap(self):
        self._check_production_case()
        return super().do_scrap()

    @api.ondelete(at_uninstall=False)
    def _keep_production_case_scrap(self):
        self._check_production_case()


class ProductionCaseScrapMove(models.Model):
    _inherit = 'stock.move'

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        if not case_internal(self.env):
            moves.scrap_id._check_production_case()
        return moves

    def write(self, vals):
        if not case_internal(self.env) and (self.scrap_id or vals.get('scrap_id')):
            if has_production_case(move_productions(self)):
                raise UserError(_('Джерело випадку C не можна прив’язати до стороннього списання.'))
            (self.sudo().scrap_id | self.env['stock.scrap'].sudo().browse(vals.get('scrap_id')))._check_production_case()
        result = super().write(vals)
        if not case_internal(self.env):
            self.scrap_id._check_production_case()
        return result

    def _action_done(self, cancel_backorder=False):
        if not case_internal(self.env):
            self.scrap_id._check_production_case()
        return super()._action_done(cancel_backorder=cancel_backorder)


class ProductionCaseUnbuild(models.Model):
    _inherit = 'mrp.unbuild'

    def _check_production_case(self, values=None):
        production = self.env['mrp.production'].browse((values or {}).get('mo_id'))
        if has_production_case(self.sudo().mo_id | production):
            raise UserError(_('Розбирання виробничого випадку C очікує етапу D. Використовувати CAB/Запаси замість карантину заборонено.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_production_case(vals)
        unbuilds = super().create(vals_list)
        unbuilds._check_production_case()
        return unbuilds

    def write(self, vals):
        self._check_production_case(vals)
        return super().write(vals)

    def action_validate(self):
        self._check_production_case()
        return super().action_validate()

    def action_unbuild(self):
        self._check_production_case()
        return super().action_unbuild()
