"""One explicit confirmation of the whole server-validated batch."""
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class QualityFullReleaseWizard(models.TransientModel):
    _name = 'quality.full.release.wizard'
    _description = 'Підтвердження повного випуску QUNIT'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True)
    unit_ids = fields.Many2many('quality.production.unit', readonly=True)
    product_id = fields.Many2one(related='production_id.product_id')
    company_id = fields.Many2one(related='production_id.company_id')
    tracking = fields.Selection(related='production_id.product_id.tracking')
    lot_id = fields.Many2one('stock.lot', string='Партія готової продукції', check_company=True)
    request_key = fields.Char(required=True, readonly=True)
    confirmed = fields.Boolean(string='Підтверджую фактичний склад всієї партії та реальний складський випуск', default=False)

    @api.model_create_multi
    def create(self, vals_list):
        values = []
        for vals in vals_list:
            if set(vals) != {'production_id'}:
                raise UserError(_('Склад партії та ключ запиту формує сервер; підтвердження встановіть у майстрі.'))
            production = self.env['mrp.production'].browse(vals['production_id']).exists()
            production._check_full_release_test_access()
            units = production._full_release_units()
            production._validate_quality_full_batch(units)
            values.append({'production_id': production.id, 'unit_ids': [(6, 0, units.ids)],
                           'request_key': str(uuid.uuid4()), 'confirmed': False,
                           'lot_id': production.lot_producing_id.id if production.product_id.tracking == 'lot' else False})
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QualityFullReleaseWizard, self.with_context(context)).create(values)

    def write(self, vals):
        if 'lot_id' in vals and any(wizard.confirmed and (wizard.lot_id.id or False) != (vals['lot_id'] or False) for wizard in self):
            raise UserError(_('Спочатку зніміть підтвердження, щоб змінити обрану партію.'))
        if set(vals) - {'confirmed', 'lot_id'}:
            raise UserError(_('Партію, MO та ключ запиту цього підтвердження не змінюють.'))
        return super().write(vals)

    def action_confirm(self):
        self.ensure_one()
        if self.create_uid != self.env.user:
            raise AccessError(_('Підтвердити випуск може лише користувач, який відкрив цей майстер.'))
        if not self.confirmed:
            raise UserError(_('Явно підтвердіть склад всієї партії та фактичний випуск.'))
        # The bridge owns the savepoint, shared MO lock, fresh evidence check,
        # native stock posting and actual facts. No capability enters RPC context.
        self.production_id._quality_native_release(
            self.unit_ids, self.request_key, confirmed=True, full_only=True, finished_lot=self.lot_id)
        return {'type': 'ir.actions.act_window_close'}
