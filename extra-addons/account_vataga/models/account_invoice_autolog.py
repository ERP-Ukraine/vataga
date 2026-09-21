"""Invoice audit messages, independent of the analytic synchronization hooks.

The outer CRUD operation owns the snapshot. Nested accounting writes are muted,
then final persisted business values are compared, including computed analytics.
"""
from copy import deepcopy

from odoo import api, fields, models
from odoo.tools.misc import formatLang


SKIP = 'account_vataga_skip_invoice_autologs'
EMPTY = 'Порожньо'
HEADER_FIELDS = {
    'partner_id': 'Постачальника / Покупця',
    'invoice_date': 'Дату рахунку',
    'date': 'Дату обліку',
    'name': 'Номер',
    'ref': 'Референс',
    'currency_id': 'Валюту',
    'journal_id': 'Журнал',
    'invoice_payment_term_id': 'Умови оплати',
}
LINE_FIELDS = {
    'product_id': 'Товар',
    'name': 'Опис',
    'quantity': 'Кількість',
    'product_uom_id': 'Одиницю виміру',
    'price_unit': 'Ціну',
    'discount': 'Знижку',
    'analytic_distribution': 'Аналітику',
    'account_id': 'Рахунок',
    'tax_ids': 'Податки',
}


def snapshot(record, names):
    """Keep raw values for equality and frozen display values for the message."""
    record = record.with_context(**{SKIP: True})
    result = {}
    for name in names:
        value = record[name]
        field = record._fields[name]
        if field.type in ('many2one', 'many2many'):
            raw = tuple(sorted(value.ids))
            display = ', '.join(value.mapped('display_name')) or EMPTY
        elif name == 'analytic_distribution':
            # Combined keys represent sets; their order is not a business edit.
            raw = {
                tuple(sorted(int(part) for part in key.split(','))): percent
                for key, percent in (value or {}).items()
            }
            entries = []
            for ids, percent in sorted(raw.items()):
                accounts = record.env['account.analytic.account'].browse(ids).exists()
                label = ' / '.join(accounts.mapped('display_name')) or EMPTY
                entries.append('%s — %g%%' % (label, percent))
            display = '; '.join(entries) or EMPTY
        elif name == 'price_unit':
            raw = value
            display = formatLang(record.env, value, currency_obj=record.move_id.currency_id)
        elif name == 'discount':
            raw, display = value, '%g%%' % value
        elif field.type == 'float':
            raw, display = value, '%g' % value
        elif field.type == 'date':
            raw, display = value, fields.Date.to_string(value) if value else EMPTY
        else:
            raw, display = deepcopy(value), str(value) if value else EMPTY
        result[name] = (raw, display)
    return result


def changes(before, after, labels):
    return [
        '%s: "%s" → "%s"' % (labels[name], before[name][1], after[name][1])
        for name in before if before[name][0] != after[name][0]
    ]


def explicit_edit(record, vals, names):
    # ORM computes protect their fields. Such writes are implementation details;
    # a surrounding user operation captures their final effects in its snapshot.
    return any(name in vals and not record.env.is_protected(record._fields[name], record)
               for name in names)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _invoice_autolog_post(self, body):
        self.ensure_one()
        if self.is_invoice(include_receipts=True) and not self.env.context.get(SKIP):
            # Plain text is escaped by mail.thread; the current user owns authorship.
            self.with_context(**{SKIP: True}).message_post(
                body=body, subtype_xmlid='account_vataga.mt_invoice_autolog',
            )

    def _invoice_autolog_lines(self):
        return self.invoice_line_ids.filtered(
            lambda line: line.display_type in ('product', 'line_section', 'line_note')
        )

    def _invoice_autolog_line_snapshot(self):
        return {
            line.id: (line._invoice_autolog_label(), line._invoice_autolog_amount(),
                      snapshot(line, LINE_FIELDS))
            for line in self.with_context(**{SKIP: True})._invoice_autolog_lines()
        }

    def _invoice_autolog_diff_lines(self, before):
        self.ensure_one()
        after = self._invoice_autolog_line_snapshot()
        for line_id, (label, amount, values) in before.items():
            if line_id not in after:
                self._invoice_autolog_post('Видалено рядок: %s, сума: %s' % (label, amount))
            else:
                edits = changes(values, after[line_id][2], LINE_FIELDS)
                if edits:
                    self._invoice_autolog_post(
                        'В рядку %s змінено %s' % (label, '; '.join(edits))
                    )
        for line_id, (label, amount, values) in after.items():
            if line_id not in before:
                self._invoice_autolog_post('Додано рядок: %s, сума: %s' % (label, amount))

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(SKIP):
            return super().create(vals_list)
        moves = super(AccountMove, self.with_context(**{SKIP: True})).create(vals_list)
        moves = moves.with_env(self.env)
        for move in moves.filtered(lambda m: m.is_invoice(include_receipts=True)):
            move._invoice_autolog_diff_lines({})
        return moves

    def write(self, vals):
        business_fields = set(HEADER_FIELDS) | self.ANALYTIC_HEADER_FIELDS | {
            'invoice_line_ids', 'line_ids',
        }
        if self.env.context.get(SKIP) or not business_fields.intersection(vals):
            return super().write(vals)
        invoices = self.filtered(lambda m: m.is_invoice(include_receipts=True)
                                 and explicit_edit(m, vals, business_fields))
        if not invoices:
            return super().write(vals)
        names = [name for name in HEADER_FIELDS if name in vals]
        before = {m.id: (snapshot(m, names), m._invoice_autolog_line_snapshot()) for m in invoices}
        result = super(AccountMove, self.with_context(**{SKIP: True})).write(vals)
        for move in invoices:
            header, lines = before[move.id]
            edits = changes(header, snapshot(move, names), HEADER_FIELDS)
            if edits:
                move._invoice_autolog_post('Змінено ' + '; '.join(edits))
            move._invoice_autolog_diff_lines(lines)
        return result

    def _post(self, soft=True):
        # Posting assigns sequences/dates and recomputes accounting lines internally.
        result = super(AccountMove, self.with_context(**{SKIP: True}))._post(soft=soft)
        return result.with_env(self.env)


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _invoice_autolog_eligible(self):
        return self.filtered(lambda line: line.move_id.is_invoice(include_receipts=True)
                             and line.display_type in ('product', 'line_section', 'line_note'))

    def _invoice_autolog_label(self):
        return self.product_id.display_name or self.name or EMPTY

    def _invoice_autolog_amount(self):
        return formatLang(self.env, self.price_subtotal, currency_obj=self.move_id.currency_id)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(SKIP):
            return super().create(vals_list)
        lines = super(AccountMoveLine, self.with_context(**{SKIP: True})).create(vals_list)
        lines = lines.with_env(self.env)
        for line in lines._invoice_autolog_eligible():
            line.move_id._invoice_autolog_post('Додано рядок: %s, сума: %s' % (
                line._invoice_autolog_label(), line._invoice_autolog_amount(),
            ))
        return lines

    def write(self, vals):
        if self.env.context.get(SKIP) or not set(LINE_FIELDS).intersection(vals):
            return super().write(vals)
        lines = self._invoice_autolog_eligible().filtered(
            lambda line: explicit_edit(line, vals, LINE_FIELDS)
        )
        if not lines:
            return super().write(vals)
        before = {line.id: snapshot(line, LINE_FIELDS) for line in lines}
        result = super(AccountMoveLine, self.with_context(**{SKIP: True})).write(vals)
        for line in lines._invoice_autolog_eligible():
            edits = changes(before[line.id], snapshot(line, LINE_FIELDS), LINE_FIELDS)
            if edits:
                line.move_id._invoice_autolog_post('В рядку %s змінено %s' % (
                    line._invoice_autolog_label(), '; '.join(edits),
                ))
        return result

    def unlink(self):
        if self.env.context.get(SKIP):
            return super().unlink()
        messages = [(line.move_id, 'Видалено рядок: %s, сума: %s' % (
            line._invoice_autolog_label(), line._invoice_autolog_amount(),
        )) for line in self._invoice_autolog_eligible()]
        result = super(AccountMoveLine, self.with_context(**{SKIP: True})).unlink()
        for move, body in messages:
            if move.exists():
                move._invoice_autolog_post(body)
        return result
