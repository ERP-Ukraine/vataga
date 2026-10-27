"""Standard/perpetual native valuation; never create SVLs or GL entries here.

Actual component/labour allocations are managerial cost. They do not replace
standard inventory value and their difference is NOT an invented GL posting.
Enterprise execution remains gated by quality_stock_posting.
"""

import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .stock_posting_core import access, move_sources


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

    @api.model
    def _valuation_profile(self, production):
        product = production.product_id.with_company(production.company_id)
        if product.cost_method == 'standard' and product.valuation == 'real_time':
            if not math.isfinite(product.standard_price) or product.standard_price <= 0:
                raise ValidationError(_('Для цього профілю потрібна додатна нормативна вартість. Зміну вартості товару адаптер не виконує.'))
            return dict(raw_valuation='real_time', standard_price=product.standard_price,
                        profile='standard_real_time_v1')
        return super()._valuation_profile(production)

    def _expected_finished_value(self):
        self.ensure_one()
        if self.valuation_snapshot:
            if self.valuation_snapshot.get('profile') != 'standard_real_time_v1':
                raise ValidationError(_('Невідома політика складської оцінки.'))
            return self.valuation_snapshot['standard_price']
        return super()._expected_finished_value()

    @api.model
    def _native_accounting_facts(self, moves):
        layers = move_sources(moves, 'stock.valuation.layer', 'stock_move_id')
        entries = layers.account_move_id
        access(entries); access(entries.line_ids)
        facts = []
        for move in moves.sorted('id'):
            layer = layers.filtered(lambda row: row.stock_move_id == move)
            entry = layer.account_move_id
            journal, source, destination, asset = move.with_company(move.company_id)._get_accounting_data_for_valuation()
            access(self.env['account.journal'].browse(journal))
            access(self.env['account.account'].browse([source, destination, asset]))
            if (len(layer) != 1 or not entry or entry.state != 'posted'
                    or entry.company_id != move.company_id or entry.journal_id.id != journal
                    or entry.stock_move_id != move or layer.company_id != move.company_id
                    or not move.company_id.currency_id.is_zero(sum(entry.line_ids.mapped('balance')))
                    or not move.company_id.currency_id.is_zero(
                        sum(entry.line_ids.filtered(lambda line: line.account_id.id == asset).mapped('balance')) - layer.value)):
                raise ValidationError(_('Нативні проведені SVL та бухгалтерські записи не узгоджені.'))
            facts.append(dict(move=move.id, layer=layer.id, entry=entry.id, journal=journal,
                              source=source, destination=destination, asset=asset,
                              company=entry.company_id.id, date=str(entry.date),
                              lines=[dict(id=line.id, account=line.account_id.id, balance=line.balance,
                                          currency=line.currency_id.id, amount_currency=line.amount_currency)
                                     for line in entry.line_ids.sorted('id')]))
        return facts

    @api.model
    def _valuation_values(self, production, produced, consumed, profile):
        if profile.get('profile') != 'standard_real_time_v1':
            return super()._valuation_values(production, produced, consumed, profile)
        return {'valuation_snapshot': dict(profile=profile['profile'], standard_price=profile['standard_price'],
                                           entries=self._native_accounting_facts(consumed | produced))}

    @api.constrains('valuation_snapshot', 'line_ids')
    def _check_accounting_snapshot(self):
        for row in self.filtered('valuation_snapshot'):
            if row._native_accounting_facts(row.line_ids.move_id) != row.valuation_snapshot['entries']:
                raise ValidationError(_('Історичні бухгалтерські записи поштучного випуску змінилися.'))

    @api.model
    def _reconcile(self, scope):
        receipts = super()._reconcile(scope)
        receipts._check_accounting_snapshot()
        return receipts

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['valuation_snapshot'])
            self.env.cr.execute('SELECT valuation_snapshot FROM quality_stock_posting WHERE id IN %s', [tuple(self.ids)])
            ids = [entry['entry'] for (snapshot,) in self.env.cr.fetchall() if snapshot for entry in snapshot['entries']]
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

    def _reverse_moves(self, default_values_list=None, cancel=False):
        assert_unregistered_entries(self.env, self.ids)
        return super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)

    def write(self, vals):
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
