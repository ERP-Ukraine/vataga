"""Read-only historical valuation display and native accounting guards."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .stock_posting_core import access, journal_has


class StockPostingValuation(models.Model):
    _inherit = 'quality.stock.posting'

    valuation_snapshot = fields.Json(readonly=True, copy=False)
    valuation_cost = fields.Float(compute='_compute_valuation_cost', string='Складська оцінка')
    cost_variance = fields.Float(compute='_compute_valuation_cost', string='Фактичні витрати мінус складська оцінка')

    @api.depends('component_cost', 'labour_cost', 'line_ids.valuation_layer_id.value')
    def _compute_valuation_cost(self):
        for row in self:
            row.valuation_cost = sum(row.line_ids.filtered(lambda line: line.kind == 'finished').valuation_layer_id.mapped('value'))
            row.cost_variance = row.component_cost + row.labour_cost - row.valuation_cost


    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['valuation_snapshot'])
            self.env.cr.execute('SELECT valuation_snapshot FROM quality_stock_posting WHERE id IN %s', [tuple(self.ids)])
            ids = [entry['entry'] for (snapshot,) in self.env.cr.fetchall() if snapshot
                   for entry in snapshot['entries'] if entry['entry']]
            if ids:
                entries = self.env['account.move'].browse(ids)
                access(entries); access(entries.line_ids)
        return result


def assert_unregistered_entries(env, ids):
    if not ids:
        return
    env['quality.stock.posting.line'].flush_model(['valuation_layer_id'])
    env['stock.valuation.layer'].flush_model(['account_move_id'])
    env.cr.execute('''SELECT 1 FROM quality_stock_posting_line l
        JOIN stock_valuation_layer v ON v.id=l.valuation_layer_id
        WHERE v.account_move_id IN %s LIMIT 1''', [tuple(ids)])
    if env.cr.fetchone():
        raise UserError(_('Бухгалтерські записи поштучного випуску незмінні. Коригування потребує окремого перевіреного процесу.'))


class StockPostingAccountMove(models.Model):
    _inherit = 'account.move'

    @api.model_create_multi
    def create(self, vals_list):
        moves = [vals.get('stock_move_id', self.env.context.get('default_stock_move_id')) for vals in vals_list]
        if journal_has(self.env, 'move_id', [move for move in moves if move]):
            raise UserError(_('Нові бухгалтерські записи не приєднують до рухів незмінного реєстру.'))
        return super().create(vals_list)

    def _reverse_moves(self, default_values_list=None, cancel=False):
        assert_unregistered_entries(self.env, self.ids)
        return super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)

    def write(self, vals):
        if vals.get('stock_move_id') and journal_has(self.env, 'move_id', [vals['stock_move_id']]):
            raise UserError(_('Бухгалтерську прив’язку рухів незмінного реєстру не змінюють.'))
        if {'state', 'date', 'journal_id', 'company_id', 'line_ids', 'stock_move_id', 'stock_valuation_layer_ids'}.intersection(vals):
            assert_unregistered_entries(self.env, self.ids)
        return super().write(vals)

    def unlink(self):
        assert_unregistered_entries(self.env, self.ids)
        return super().unlink()


class StockPostingAccountLine(models.Model):
    _inherit = 'account.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        assert_unregistered_entries(self.env, [vals.get('move_id', self.env.context.get('default_move_id'))
                                              for vals in vals_list if vals.get('move_id', self.env.context.get('default_move_id'))])
        return super().create(vals_list)

    def write(self, vals):
        if {'move_id', 'account_id', 'debit', 'credit', 'balance', 'amount_currency', 'currency_id',
            'company_id', 'date', 'product_id', 'quantity'}.intersection(vals):
            assert_unregistered_entries(self.env, self.move_id.ids + ([vals['move_id']] if vals.get('move_id') else []))
        return super().write(vals)

    def unlink(self):
        assert_unregistered_entries(self.env, self.move_id.ids)
        return super().unlink()
