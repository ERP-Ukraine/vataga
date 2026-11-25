from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


_RECONCILED_LINES = (
    'matched_debit_ids.debit_move_id',
    'matched_credit_ids.credit_move_id',
)
_FIRST_HOP_MOVES = tuple(
    f'line_ids.{edge}.move_id' for edge in _RECONCILED_LINES
)
_SECOND_HOP_MOVES = tuple(
    f'{move}.line_ids.{edge}.move_id'
    for move in _FIRST_HOP_MOVES
    for edge in _RECONCILED_LINES
)
_PARTIAL_AMOUNT_FIELDS = ('amount', 'debit_amount_currency', 'credit_amount_currency')
_PARTIAL_PATHS = tuple(
    f'line_ids.{name}' for name in ('matched_debit_ids', 'matched_credit_ids')
)
_BANK_EVIDENCE_DEPS = (
    'line_ids.account_id.account_type', 'line_ids.currency_id',
    *(f'{path}.{field}' for path in _PARTIAL_PATHS for field in _PARTIAL_AMOUNT_FIELDS),
    *(f'{move}.{field}' for move in _FIRST_HOP_MOVES for field in ('payment_id', 'line_ids.account_id')),
    *(f'{move}.{path}.{field}' for move in _FIRST_HOP_MOVES
      for path in _PARTIAL_PATHS for field in _PARTIAL_AMOUNT_FIELDS),
    *(f'{move}.{field}' for move in (*_FIRST_HOP_MOVES, *_SECOND_HOP_MOVES)
      for field in (
          'state', 'company_id', 'journal_id.type', 'journal_id.default_account_id',
          'statement_line_id.amount', 'line_ids.balance',
          'line_ids.amount_currency', 'line_ids.currency_id',
      )),
)
_STORNO_DEPS = (
    'move_type', 'state', 'payment_state', 'company_id', 'commercial_partner_id',
    'amount_total', 'currency_id', 'date', 'invoice_date',
    'currency_id.rounding', 'currency_id.rate_ids.rate', 'currency_id.rate_ids.name',
    'currency_id.rate_ids.company_id',
    'invoice_line_ids.purchase_line_id.order_id', *_BANK_EVIDENCE_DEPS,
)
_STORNO_LINE_COMMANDS = ('invoice_line_ids', 'line_ids')
_STORNO_LINE_FIELDS = {'purchase_line_id', 'move_id', 'display_type'}
# Defer line checks until the entire document write is complete. An RPC caller
# cannot opt out by passing a context flag with the same name.
_STORNO_DEFER_LINE_CHECK = object()


class AccountMove(models.Model):
    _inherit = 'account.move'

    vataga_storno_bill_id = fields.Many2one(
        'account.move', string='Bill to reverse', copy=False,
        check_company=True, index='btree_not_null', tracking=True,
    )
    vataga_storno_refund_ids = fields.One2many(
        'account.move', 'vataga_storno_bill_id', copy=False,
    )
    vataga_storno_purchase_ids = fields.Many2many(
        'purchase.order', compute='_compute_vataga_storno_purchase_ids',
    )
    vataga_storno_payment_display = fields.Selection(
        selection='_selection_vataga_storno_payment_display', string='Displayed payment status',
        compute='_compute_vataga_storno_payment_display', compute_sudo=True,
    )

    @api.depends('move_type', 'invoice_line_ids.purchase_line_id.order_id')
    def _compute_vataga_storno_purchase_ids(self):
        for move in self:
            move.vataga_storno_purchase_ids = (
                move.invoice_line_ids.purchase_line_id.order_id
                if move.move_type == 'in_refund' else False
            )

    def _vataga_has_valid_storno_bill(self, allow_cancelled=False):
        self.ensure_one()
        bill = self.vataga_storno_bill_id
        return (
            self.move_type == 'in_refund'
            and bill != self
            and bill.move_type == 'in_invoice'
            and (allow_cancelled or bill.state != 'cancel')
            and self.company_id == bill.company_id
            and self.commercial_partner_id
            and self.commercial_partner_id == bill.commercial_partner_id
            and (
                not self.vataga_storno_purchase_ids
                or bool(self.vataga_storno_purchase_ids & bill.invoice_line_ids.purchase_line_id.order_id)
            )
        )

    @api.constrains('vataga_storno_bill_id')
    def _check_vataga_new_storno_bill(self):
        self._vataga_validate_storno_bills()

    def _vataga_validate_storno_bills(self, allow_cancelled=False):
        for refund in self.filtered('vataga_storno_bill_id'):
            if not refund._vataga_has_valid_storno_bill(allow_cancelled=allow_cancelled):
                raise ValidationError(_(
                    "Select a non-cancelled vendor bill from the same company and vendor "
                    "and, when the credit note has purchase orders, from one of those orders."
                ))

    def _vataga_check_storno_links(self):
        moves = self.sudo()
        # An existing link remains useful as history after bill cancellation.
        # Explicit assignments still go through the stricter constraint above.
        (moves | moves.vataga_storno_refund_ids)._vataga_validate_storno_bills(allow_cancelled=True)

    def _vataga_moves_losing_lines(self, vals_list):
        return self.browse({
            move_id
            for vals in vals_list
            for field in _STORNO_LINE_COMMANDS
            for move_id in self._stolen_move({'line_ids': vals.get(field) or ()})
        })

    @api.model_create_multi
    def create(self, vals_list):
        affected = self._vataga_moves_losing_lines(vals_list)
        moves = super(AccountMove, self.with_context(
            vataga_defer_storno_line_check=_STORNO_DEFER_LINE_CHECK,
        )).create(vals_list)
        affected._vataga_check_storno_links()
        return moves.with_env(self.env)

    def write(self, vals):
        if not (
            {'partner_id', 'commercial_partner_id', 'company_id', 'move_type', 'journal_id', *_STORNO_LINE_COMMANDS}
            .intersection(vals)
        ):
            return super().write(vals)

        affected = self | self._vataga_moves_losing_lines([vals])
        result = super(AccountMove, self.with_context(
            vataga_defer_storno_line_check=_STORNO_DEFER_LINE_CHECK,
        )).write(vals)
        affected._vataga_check_storno_links()
        return result

    def unlink(self):
        # The Many2one FK clears links to a deleted bill. Its lines are
        # deleted first by account.move.unlink(), before that FK runs.
        return super(AccountMove, self.with_context(
            vataga_defer_storno_line_check=_STORNO_DEFER_LINE_CHECK,
        )).unlink()

    @api.model
    def _selection_vataga_storno_payment_display(self):
        return [
            *self._fields['payment_state']._description_selection(self.env),
            ('storno', _('Storno completed')),
        ]

    @api.depends(*_STORNO_DEPS, 'vataga_storno_refund_ids', *(
        f'vataga_storno_refund_ids.{name}' for name in _STORNO_DEPS
    ))
    def _compute_vataga_storno_payment_display(self):
        bills = self.filtered(lambda move: (
            move.move_type == 'in_invoice' and move.state == 'posted' and move.payment_state == 'paid'
        ))
        refunds = bills.vataga_storno_refund_ids.filtered(lambda move: (
            move.state == 'posted' and move.payment_state == 'paid'
            and move.amount_total > 0 and move._vataga_has_valid_storno_bill()
        ))
        bank_amounts = (refunds.vataga_storno_bill_id | refunds)._vataga_bank_confirmed_amounts()
        returned = defaultdict(float)
        for refund in refunds:
            bill = refund.vataga_storno_bill_id
            # Preserve nominal amounts when the documents share a currency.
            # Cross-currency credit notes use their accounting date's rate.
            returned[bill.id] += refund.currency_id._convert(
                min(refund.amount_total, bank_amounts.get(refund.id, 0.0)),
                bill.currency_id, bill.company_id, refund.date, round=False,
            )
        storno_ids = {
            bill.id for bill in bills
            if bill.amount_total > 0
            and bill.currency_id.compare_amounts(bank_amounts.get(bill.id, 0.0), bill.amount_total) >= 0
            and bill.currency_id.compare_amounts(returned[bill.id], bill.amount_total) >= 0
        }
        for move in self:
            move.vataga_storno_payment_display = 'storno' if move.id in storno_ids else move.payment_state

    def _vataga_bank_confirmed_amounts(self):
        """Amounts in each document's currency, backed by current bank reconciliations.

        A shared payment's confirmed fraction applies to all its allocations.
        This avoids crediting each invoice with the full amount of one payment.
        Bank fees and other opposite-side lines share the actual cash amount
        proportionally, so a gross reconciliation cannot overstate cash returned.
        """
        terms = self.line_ids.filtered(lambda line: line.account_id.account_type == 'liability_payable')
        partials = terms.matched_debit_ids | terms.matched_credit_ids
        endpoints = partials.debit_move_id | partials.credit_move_id
        first_hop = (endpoints - terms).move_id
        payments = first_hop.filtered(
            lambda move: move.state == 'posted' and move.journal_id.type == 'bank'
        ).payment_id
        payment_lines = payments.move_id.line_ids
        payment_lines.account_id.fetch(['account_type'])
        liquidity_by_payment = {payment.id: payment._seek_for_lines()[0] for payment in payments}
        liquidity = payment_lines.browse().union(*liquidity_by_payment.values())
        funding_partials = liquidity.matched_debit_ids | liquidity.matched_credit_ids
        funding_endpoints = funding_partials.debit_move_id | funding_partials.credit_move_id
        bank_moves = (first_hop | funding_endpoints.move_id).filtered('statement_line_id')
        bank_moves.statement_line_id.fetch(['amount'])
        bank_lines = bank_moves.line_ids
        bank_lines.account_id.fetch(['account_type'])
        bank_fractions = {}
        for move in bank_moves:
            if move.state != 'posted' or move.journal_id.type != 'bank':
                continue
            cash_lines = move.statement_line_id._seek_for_lines()[0]
            cash = sum(cash_lines.mapped('balance'))
            statement_amount = move.statement_line_id.amount
            if not cash or not statement_amount or cash * statement_amount <= 0:
                continue
            # Consider all allocations of the statement, including other invoices.
            opposite = (move.line_ids - cash_lines).filtered(lambda line: line.balance * cash < 0)
            allocated = sum(abs(line.balance) for line in opposite)
            fraction = min(1.0, abs(cash) / allocated) if allocated else 0.0
            for line in opposite:
                bank_fractions[line.id] = fraction

        def bank_fraction(line, document):
            bank = line.move_id
            if bank.company_id != document.company_id:
                return 0.0
            amount = bank.statement_line_id.amount
            right_direction = amount < 0 if document.move_type == 'in_invoice' else amount > 0
            return bank_fractions.get(line.id, 0.0) if right_direction else 0.0

        def line_amount(partial, line):
            # These are the matched amounts in the endpoint's own currency,
            # including FX settlements whose company-currency amounts differ.
            return (partial.debit_amount_currency if partial.debit_move_id == line
                    else partial.credit_amount_currency)

        # Prefetch both hops once before processing per-document allocations.
        all_lines = terms | endpoints | payment_lines | funding_endpoints | bank_lines
        all_lines.fetch(['balance', 'amount_currency', 'currency_id', 'move_id'])
        all_partials = partials | funding_partials
        all_partials.fetch(['debit_move_id', 'credit_move_id', *_PARTIAL_AMOUNT_FIELDS])
        covered_liquidity = defaultdict(float)
        for partial in funding_partials:
            for line, other in (
                (partial.debit_move_id, partial.credit_move_id),
                (partial.credit_move_id, partial.debit_move_id),
            ):
                if line not in liquidity or line.move_id.company_id != other.move_id.company_id:
                    continue
                # The funding direction follows the payment's payable line.
                bank = other.move_id
                amount = bank.statement_line_id.amount
                correct_direction = amount > 0 if line.balance > 0 else amount < 0
                if correct_direction:
                    covered_liquidity[line.id] += line_amount(partial, line) * bank_fractions.get(other.id, 0.0)

        payment_fractions = {}
        for payment in payments:
            lines = liquidity_by_payment[payment.id]
            total = sum(abs(line.balance) for line in lines)
            covered = 0.0
            for line in lines:
                denominator = abs(line.amount_currency)
                fraction = min(1.0, covered_liquidity[line.id] / denominator) if denominator else 0.0
                covered += abs(line.balance) * fraction
            payment_fractions[payment.id] = min(1.0, covered / total) if total else 0.0

        term_ids = set(terms.ids)
        confirmed = defaultdict(float)
        for partial in partials:
            for term, counterpart in (
                (partial.debit_move_id, partial.credit_move_id),
                (partial.credit_move_id, partial.debit_move_id),
            ):
                if term.id not in term_ids or term.move_id.company_id != counterpart.move_id.company_id:
                    continue
                invoice = term.move_id
                if invoice.state != 'posted':
                    continue
                fraction = bank_fraction(counterpart, invoice)
                payment = counterpart.move_id.payment_id
                if payment and counterpart.move_id.state == 'posted':
                    expected_type = 'outbound' if invoice.move_type == 'in_invoice' else 'inbound'
                    if payment.payment_type == expected_type and payment.partner_type == 'supplier':
                        fraction = payment_fractions.get(payment.id, 0.0)
                confirmed[invoice.id] += line_amount(partial, term) * fraction
        return {
            move.id: min(move.amount_total, confirmed[move.id])
            for move in self
        }


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _vataga_storno_line_check_deferred(self):
        return self.env.context.get('vataga_defer_storno_line_check') is _STORNO_DEFER_LINE_CHECK

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self._vataga_storno_line_check_deferred():
            lines.filtered('purchase_line_id').move_id._vataga_check_storno_links()
        return lines

    def write(self, vals):
        if self._vataga_storno_line_check_deferred() or not _STORNO_LINE_FIELDS.intersection(vals):
            return super().write(vals)
        affected = self.filtered('purchase_line_id').move_id
        result = super().write(vals)
        (affected | self.filtered('purchase_line_id').move_id)._vataga_check_storno_links()
        return result

    def unlink(self):
        if self._vataga_storno_line_check_deferred():
            return super().unlink()
        affected = self.filtered('purchase_line_id').move_id
        result = super().unlink()
        affected.exists()._vataga_check_storno_links()
        return result
