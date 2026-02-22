from odoo import api, fields, models, _
from odoo.tools import float_is_zero
from string import Template


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    percent_of_amount = fields.Float(string='Amount %', digits=(2, 2))
    purpose_pumb = fields.Char()
    purpose_dcu = fields.Char()

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        if 'purpose_dcu' in fields:
            record = self.new({})
            res['purpose_dcu'] = record.template_purpose_dcu()
        if 'purpose_pumb' in fields:
            res['purpose_pumb'] = record._compute_purpose_pumb()
        return res

    def template_purpose_dcu(self):
        batches = self._get_batches()
        move = self._get_batches()[0]['lines'].move_id
        if len(move) > 1 or len(batches) > 1:
            return ''

        return self._prepare_purpose_dcu(move)

    def _compute_purpose_pumb(self):
        batches = self._get_batches()
        move = self._get_batches()[0]['lines'].move_id
        if len(move) > 1 or len(batches) > 1:
            return ''

        return self._prepare_purpose_pumb(move)

    @api.depends('percent_of_amount')
    def _compute_amount(self):
        super()._compute_amount()
        for wizard in self.filtered(lambda w: not float_is_zero(w.percent_of_amount, precision_digits=2)):
            wizard.amount *= wizard.percent_of_amount / 100

    @api.onchange('percent_of_amount')
    def _onchange_percent_of_amount(self):
        self.purpose_dcu = self.template_purpose_dcu()
        self.purpose_pumb = self._compute_purpose_pumb()

    def _update_payment_vals(self, vals):
        """Update the create_vals dictionary for a payment, applying percent and purposes."""
        batch_lines = vals.get('batch', {}).get('lines')
        move = batch_lines.move_id if batch_lines else None

        if move and len(move) == 1 and not self.purpose_dcu and not self.purpose_pumb:
            vals['create_vals']['purpose_pumb'] = self._prepare_purpose_pumb(move)
            vals['create_vals']['purpose_dcu'] = self._prepare_purpose_dcu(move)
            if not float_is_zero(self.percent_of_amount, precision_digits=2) and len(self.line_ids) > 1:
                vals['create_vals']['amount'] *= self.percent_of_amount / 100
        else:
            vals['create_vals']['purpose_pumb'] = self.purpose_pumb
            vals['create_vals']['purpose_dcu'] = self.purpose_dcu

    def _init_payments(self, to_process, edit_mode=False):
        for vals in to_process:
            self._update_payment_vals(vals)
        return super()._init_payments(to_process, edit_mode=edit_mode)

    def _prepare_purpose_pumb(self, move):
        self.ensure_one()
        ref = move.ref or ''
        invoice_date = self._format_date(move.invoice_date)
        tax_info = self._prepare_tax_info(move)
        return _('Payment is reasonable. Ref №%s in %s, %s.') % (ref, invoice_date, tax_info)

    def _prepare_purpose_dcu(self, move):
        self.ensure_one()
        template = self.env['ir.config_parameter'].sudo().get_param(
            'account_vataga.template_purpose_dcu', ''
        )
        allowed_context = {
            "ref": move.ref or '',
            "invoice_date": self._format_date(move.invoice_date),
            "sale_ua_contract_id": move.sale_ua_contract_id.name if move.sale_ua_contract_id else '',
            "sc_date_start": self._format_date(move.sale_ua_contract_id.date_start) if move.sale_ua_contract_id else '',
            "ua_contract_id": move.ua_contract_id.name if move.ua_contract_id else '',
            "uc_date_start": self._format_date(move.ua_contract_id.date_start) if move.ua_contract_id else '',
            "tax_info": self._prepare_tax_info(move)
        }
        return Template(template).safe_substitute(allowed_context)

    def _prepare_tax_info(self, move):
        tax_totals = move.tax_totals or {}
        tax = next(
            (lines[0] for lines in tax_totals.get("groups_by_subtotal", {}).values() if lines),
            None
        )
        if not tax:
            return _('without VAT')

        currency_name = _('uah') if move.currency_id.fiscal_country_codes == 'UA' else ''
        tax_amount = tax.get('tax_group_amount', 0.0)
        if self.percent_of_amount:
            tax_amount *= self.percent_of_amount / 100

        return _('incl. %s = %s') % (tax.get("tax_group_name", ""), f"{tax_amount:.2f} {currency_name}")

    @staticmethod
    def _format_date(date):
        return date.strftime("%d.%m.%Y") if date else ''
