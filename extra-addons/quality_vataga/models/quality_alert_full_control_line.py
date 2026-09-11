import math

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class QualityAlertFullControlLine(models.Model):
    _name = 'quality.alert.full.control.line'
    _description = 'Результат суцільного контролю'
    _order = 'date, id'

    alert_id = fields.Many2one(
        'quality.alert', required=True, ondelete='cascade', index=True,
        string='Сповіщення про якість',
    )
    date = fields.Date(string='Дата перевірки', required=True, default=fields.Date.context_today)
    checked_qty = fields.Float(string='Перевірено', required=True, digits='Product Unit of Measure')
    good_qty = fields.Float(string='Придатних', required=True, digits='Product Unit of Measure')
    bad_qty = fields.Float(string='Брак', required=True, digits='Product Unit of Measure')
    inspector_id = fields.Many2one(
        'res.users', string='Інспектор', required=True, default=lambda self: self.env.user,
    )

    @api.constrains('checked_qty', 'good_qty', 'bad_qty')
    def _check_quantities(self):
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        for line in self:
            quantities = (line.checked_qty, line.good_qty, line.bad_qty)
            if any(not math.isfinite(qty) or qty < 0 for qty in quantities):
                raise ValidationError(_('Кількість має бути невід’ємним скінченним числом.'))
            if float_compare(line.good_qty + line.bad_qty, line.checked_qty,
                             precision_digits=precision):
                raise ValidationError(_('Кількість придатних та браку має дорівнювати перевіреній кількості.'))

    def _check_alert_access(self, alerts, operation):
        alerts.check_access_rights(operation)
        alerts.check_access_rule(operation)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        # Reading alert_id through the ORM here recursively invokes this check.
        # Fetch only parent IDs; all authorization remains with the parent ORM.
        if self.ids:
            self.flush_recordset(['alert_id'])
            self.env.cr.execute(
                'SELECT DISTINCT alert_id FROM quality_alert_full_control_line WHERE id IN %s',
                [tuple(self.ids)],
            )
            alerts = self.env['quality.alert'].browse([row[0] for row in self.env.cr.fetchall()])
            self._check_alert_access(alerts, 'read' if operation == 'read' else 'write')
        return result

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        # Also protect searches, counts and grouped totals, not only direct reads.
        parents = self.env['quality.alert']._search([])
        return super()._search(
            list(domain) + [('alert_id', 'in', parents)], offset=offset,
            limit=limit, order=order, access_rights_uid=access_rights_uid,
        )

    @api.model_create_multi
    def create(self, vals_list):
        default_alert = self.default_get(['alert_id']).get('alert_id')
        alerts = self.env['quality.alert'].browse(list({
            vals.get('alert_id', default_alert) for vals in vals_list
            if vals.get('alert_id', default_alert)
        }))
        self._check_alert_access(alerts, 'write')
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('alert_id'):
            self._check_alert_access(self.env['quality.alert'].browse(vals['alert_id']), 'write')
        return super().write(vals)
