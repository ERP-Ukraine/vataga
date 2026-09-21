"""Invoice audit messages, independent of the analytic synchronization hooks.

The outer CRUD operation owns the snapshot. Nested accounting writes are muted,
then final persisted business values are compared, including computed analytics.
"""
from copy import deepcopy

from odoo import api, fields, models
from odoo.tools import html2plaintext
from odoo.tools.misc import formatLang, format_datetime


SKIP = 'account_vataga_skip_invoice_autologs'
EMPTY = 'Порожньо'
COMMON_TECHNICAL_FIELDS = {
    'id', 'display_name', 'create_date', 'create_uid', 'write_date', 'write_uid',
    '__last_update', 'message_ids', 'message_follower_ids', 'message_partner_ids',
    'activity_ids', 'activity_state', 'activity_user_id', 'activity_type_id',
    'activity_date_deadline', 'access_url', 'access_token', 'access_warning',
}
MOVE_TECHNICAL_FIELDS = {
    'line_ids', 'invoice_line_ids', 'state', 'move_type', 'posted_before',
    'payment_id', 'statement_line_id', 'tax_cash_basis_rec_id',
    'always_tax_exigible', 'secure_sequence_number', 'sequence_prefix',
    'sequence_number', 'inalterable_hash', 'payment_state',
    'amount_untaxed', 'amount_tax', 'amount_total', 'amount_residual',
    'amount_untaxed_signed', 'amount_tax_signed', 'amount_total_signed',
    'amount_residual_signed', 'amount_total_in_currency_signed',
}
LINE_TECHNICAL_FIELDS = {
    'move_id', 'sequence', 'display_type', 'parent_state', 'company_id',
    'company_currency_id', 'currency_rate', 'balance', 'debit', 'credit',
    'amount_currency', 'amount_residual', 'amount_residual_currency',
    'price_subtotal', 'price_total', 'tax_tag_ids', 'tax_repartition_line_id',
    'tax_tag_invert', 'tax_base_amount', 'group_tax_id', 'matching_number',
    'date_maturity', 'discount_date', 'discount_amount_currency', 'discount_balance',
}


def business_fields(record, names=None):
    """Stored editable fields, minus accounting/mail internals and payloads.

Computed fields with readonly=False (price, account, taxes, etc.) are editable
business fields too. Never infer editability just from the presence of compute.
One2many records need a dedicated handler; invoice lines have one below.
"""
    excluded = COMMON_TECHNICAL_FIELDS | (
        MOVE_TECHNICAL_FIELDS if record._name == 'account.move' else LINE_TECHNICAL_FIELDS
    )
    field_names = list(record._fields if names is None else names)
    # Odoo 17 filters with None, but validates (and may reject) explicit lists.
    accessible = set(record.check_field_access_rights('read', None))
    field_names = [name for name in field_names if name in record._fields and name in accessible]
    readable = set(record.check_field_access_rights('read', field_names))
    result = []
    for name in field_names:
        field = record._fields.get(name)
        if (not field or name in excluded or name not in readable
                or name.startswith(('message_', 'activity_', 'access_'))
                or not field.store or field.readonly):
            continue
        # Binary/JSON widgets contain service payloads, not scalar user values.
        # Analytic distribution is the structured business exception with a formatter.
        if field.type in ('one2many', 'binary', 'json') and name != 'analytic_distribution':
            continue
        result.append(name)
    return result


def field_label(record, name):
    field = record._fields[name]
    return field._description_string(record.env) or name


def format_value(record, name):
    """Return normalized comparison data and a human-readable value separately."""
    value = record[name]
    field = record._fields[name]
    if field.type == 'many2one':
        return value.id or False, value.display_name or EMPTY
    if field.type in ('many2many', 'reference'):
        raw = tuple(sorted(value.ids)) if field.type == 'many2many' else (
            (value._name, value.id) if value else False
        )
        return raw, ', '.join(value.sorted('id').mapped('display_name')) if value else EMPTY
    if name == 'analytic_distribution':
        raw = {
            tuple(sorted(int(part) for part in key.split(','))): percent
            for key, percent in (value or {}).items()
        }
        entries = []
        for ids, percent in sorted(raw.items()):
            accounts = record.env['account.analytic.account'].browse(ids).exists()
            label = ' / '.join(accounts.mapped('display_name')) or EMPTY
            entries.append('%s — %g%%' % (label, percent))
        return raw, '; '.join(entries) or EMPTY
    if field.type == 'selection':
        return value, dict(field._description_selection(record.env)).get(value, EMPTY)
    if field.type == 'boolean':
        return bool(value), 'Так' if value else 'Ні'
    if field.type == 'date':
        return value, fields.Date.to_string(value) if value else EMPTY
    if field.type == 'datetime':
        return value, format_datetime(record.env, value) if value else EMPTY
    if field.type == 'monetary' or name == 'price_unit':
        move = record if record._name == 'account.move' else record.move_id
        return value, formatLang(record.env, value, currency_obj=move.currency_id)
    if name == 'discount':
        return value, '%g%%' % value
    if field.type == 'float':
        digits = field.get_digits(record.env)
        return value, formatLang(record.env, value, digits=digits[1] if digits else 6)
    if field.type == 'integer':
        return value, str(value)
    if field.type == 'html':
        return value or False, html2plaintext(value) if value else EMPTY
    return deepcopy(value) or False, str(value) if value else EMPTY


def snapshot(record, names=None):
    """Keep raw values for equality and frozen display values for the message."""
    record = record.with_context(**{SKIP: True})
    return {name: format_value(record, name) for name in business_fields(record, names)}


def changes(record, before, after):
    return [
        '%s: "%s" → "%s"' % (field_label(record, name), before[name][1], after[name][1])
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
            line.id: line._invoice_autolog_snapshot()
            for line in self.with_context(**{SKIP: True})._invoice_autolog_lines()
        }

    def _invoice_autolog_diff_lines(self, before):
        self.ensure_one()
        after = self._invoice_autolog_line_snapshot()
        for line_id, (label, summary, values) in before.items():
            if line_id not in after:
                self._invoice_autolog_post('Видалено рядок: ' + summary)
            else:
                edits = changes(self.env['account.move.line'], values, after[line_id][2])
                if edits:
                    self._invoice_autolog_post(
                        'В рядку %s змінено: %s' % (label, '; '.join(edits))
                    )
        for line_id, (label, summary, values) in after.items():
            if line_id not in before:
                self._invoice_autolog_post('Додано рядок: ' + summary)

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
        if self.env.context.get(SKIP):
            return super().write(vals)
        names = business_fields(self, vals)
        triggers = set(names) | {
            'invoice_line_ids', 'line_ids',
        }
        if not triggers.intersection(vals):
            return super().write(vals)
        invoices = self.filtered(lambda m: m.is_invoice(include_receipts=True)
                                 and explicit_edit(m, vals, triggers))
        if not invoices:
            return super().write(vals)
        before = {m.id: (snapshot(m, names), m._invoice_autolog_line_snapshot()) for m in invoices}
        result = super(AccountMove, self.with_context(**{SKIP: True})).write(vals)
        for move in invoices:
            header, lines = before[move.id]
            edits = changes(move, header, snapshot(move, names))
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

    def _invoice_autolog_snapshot(self):
        line = self.with_context(**{SKIP: True})
        values = snapshot(line)
        # This only controls presentation order/required zero values, not eligibility.
        primary = ('product_id', 'name', 'quantity', 'product_uom_id', 'price_unit')
        names = [name for name in primary if name in values]
        names += [name for name in values if name not in names]
        required = {'quantity', 'product_uom_id', 'price_unit'} if line.display_type == 'product' else set()
        if not line.product_id:
            required.add('name')
        details = [
            '%s: %s' % (field_label(line, name), values[name][1])
            for name in names if values[name][0] or name in required
        ]
        details.append('%s: %s' % (field_label(line, 'price_subtotal'), line._invoice_autolog_amount()))
        return line._invoice_autolog_label(), '; '.join(details), values

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(SKIP):
            return super().create(vals_list)
        lines = super(AccountMoveLine, self.with_context(**{SKIP: True})).create(vals_list)
        lines = lines.with_env(self.env)
        for line in lines._invoice_autolog_eligible():
            line.move_id._invoice_autolog_post('Додано рядок: ' + line._invoice_autolog_snapshot()[1])
        return lines

    def write(self, vals):
        if self.env.context.get(SKIP):
            return super().write(vals)
        names = business_fields(self, vals)
        if not names:
            return super().write(vals)
        lines = self._invoice_autolog_eligible().filtered(
            lambda line: explicit_edit(line, vals, names)
        )
        if not lines:
            return super().write(vals)
        before = {line.id: snapshot(line) for line in lines}
        result = super(AccountMoveLine, self.with_context(**{SKIP: True})).write(vals)
        for line in lines._invoice_autolog_eligible():
            edits = changes(line, before[line.id], snapshot(line))
            if edits:
                line.move_id._invoice_autolog_post('В рядку %s змінено: %s' % (
                    line._invoice_autolog_label(), '; '.join(edits),
                ))
        return result

    def unlink(self):
        if self.env.context.get(SKIP):
            return super().unlink()
        messages = [(line.move_id, 'Видалено рядок: ' + line._invoice_autolog_snapshot()[1])
                    for line in self._invoice_autolog_eligible()]
        result = super(AccountMoveLine, self.with_context(**{SKIP: True})).unlink()
        for move, body in messages:
            if move.exists():
                move._invoice_autolog_post(body)
        return result
