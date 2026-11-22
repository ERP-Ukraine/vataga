"""Prevent standard MRP/Stock entry points from bypassing a C decision."""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .production_residual_stock import case_internal


def has_production_case(productions):
    return bool(productions.sudo().filtered(lambda production: (
        production.quality_production_case_enabled and production.quality_production_case_ids)))


class ProductionCaseScrap(models.Model):
    _inherit = 'stock.scrap'

    quality_production_case_id = fields.Many2one('quality.alert', readonly=True, copy=False,
                                                check_company=True, ondelete='restrict')

    def _check_production_case(self, values=None):
        if case_internal(self.env):
            return
        values = values or {}
        production = self.env['mrp.production'].browse(values.get('production_id'))
        if (values.get('quality_production_case_id') or self.sudo().quality_production_case_id
                or has_production_case(self.sudo().production_id | production)):
            raise UserError(_('Списання виробничого випадку C виконується лише з підтвердженого рішення техради.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_production_case(vals)
        scraps = super().create(vals_list)
        scraps._check_production_case()
        return scraps

    def write(self, vals):
        self._check_production_case(vals)
        return super().write(vals)

    def action_validate(self):
        self._check_production_case()
        return super().action_validate()

    def do_scrap(self):
        self._check_production_case()
        return super().do_scrap()

    @api.ondelete(at_uninstall=False)
    def _keep_production_case_scrap(self):
        self._check_production_case()


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
