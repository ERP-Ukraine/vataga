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
_BANK_EVIDENCE_DEPS = (
    'line_ids.account_id.account_type',
    *(f'{move}.{field}' for move in _FIRST_HOP_MOVES for field in ('payment_id', 'line_ids.account_id')),
    *(f'{move}.{field}' for move in (*_FIRST_HOP_MOVES, *_SECOND_HOP_MOVES)
      for field in ('state', 'company_id', 'journal_id.type', 'statement_line_id.amount')),
)
_STORNO_DEPS = (
    'move_type', 'state', 'payment_state', 'company_id', 'commercial_partner_id',
    'invoice_line_ids.purchase_line_id.order_id', *_BANK_EVIDENCE_DEPS,
)


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

    def _vataga_has_valid_storno_bill(self):
        self.ensure_one()
        bill = self.vataga_storno_bill_id
        return (
            self.move_type == 'in_refund'
            and bill != self
            and bill.move_type == 'in_invoice'
            and bill.state != 'cancel'
            and self.company_id == bill.company_id
            and self.commercial_partner_id
            and self.commercial_partner_id == bill.commercial_partner_id
            and (
                not self.vataga_storno_purchase_ids
                or bool(self.vataga_storno_purchase_ids & bill.invoice_line_ids.purchase_line_id.order_id)
            )
        )

    @api.constrains('vataga_storno_bill_id', 'move_type', 'company_id', 'partner_id', 'invoice_line_ids', 'line_ids')
    def _check_vataga_storno_bill(self):
        for refund in self.filtered('vataga_storno_bill_id'):
            if not refund._vataga_has_valid_storno_bill():
                raise ValidationError(_(
                    "Select a non-cancelled vendor bill from the same company and vendor "
                    "and, when the credit note has purchase orders, from one of those orders."
                ))

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
            move.state == 'posted' and move.payment_state == 'paid' and move._vataga_has_valid_storno_bill()
        ))
        matched = (refunds.vataga_storno_bill_id | refunds)._vataga_bank_matched_moves()
        storno_bills = (refunds & matched).vataga_storno_bill_id & matched
        for move in self:
            move.vataga_storno_payment_display = 'storno' if move in storno_bills else move.payment_state

    def _vataga_bank_matched_moves(self):
        """Follow current partial reconciliations in batches, at most via one payment."""
        def counterparts(lines):
            return lines.matched_debit_ids.debit_move_id | lines.matched_credit_ids.credit_move_id

        def is_bank_match(bank_move, invoice):
            return (
                bank_move.state == 'posted'
                and bank_move.company_id == invoice.company_id
                and bank_move.journal_id.type == 'bank'
                and bank_move.statement_line_id
                and (bank_move.statement_line_id.amount < 0 if invoice.move_type == 'in_invoice'
                     else bank_move.statement_line_id.amount > 0)
            )

        terms = self.line_ids.filtered(lambda line: line.account_id.account_type == 'liability_payable')
        first_hop = counterparts(terms).move_id
        payments = first_hop.filtered(lambda move: move.state == 'posted').payment_id
        # Prime the shared line prefetch before classifying individual payments.
        payment_lines = payments.move_id.line_ids
        payment_lines.account_id.fetch(['account_type'])
        liquidity_by_payment = {payment.id: payment._seek_for_lines()[0] for payment in payments}
        liquidity = payment_lines.browse().union(*liquidity_by_payment.values())
        bank_moves = counterparts(liquidity).move_id | first_hop
        bank_moves.statement_line_id.fetch(['amount'])
        bank_moves_by_payment = {
            payment_id: counterparts(lines).move_id.with_prefetch(bank_moves.ids)
            for payment_id, lines in liquidity_by_payment.items()
        }
        matched_ids = set()
        for term in terms:
            invoice = term.move_id
            for move in counterparts(term).move_id.with_prefetch(bank_moves.ids):
                if is_bank_match(move, invoice):
                    matched_ids.add(invoice.id)
                    break
                if move.state != 'posted' or move.company_id != invoice.company_id:
                    continue
                candidates = bank_moves_by_payment.get(move.payment_id.id)
                if candidates and any(is_bank_match(bank, invoice) for bank in candidates):
                    matched_ids.add(invoice.id)
                    break
        return self.browse(matched_ids)
