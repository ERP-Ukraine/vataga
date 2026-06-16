import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..services import GeminiClient, ResponseParser


_logger = logging.getLogger(__name__)


class AccountGeminiDigitizationJob(models.Model):
    _name = 'account.gemini.digitization.job'
    _description = 'Gemini Digitization Job'
    _order = 'create_date desc, id desc'

    STATE_SELECTION = [
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('review', 'Review'),
        ('done', 'Done'),
        ('error', 'Error'),
        ('cancelled', 'Cancelled'),
    ]

    MODE_SELECTION = [
        ('partial_bill', 'Partial Vendor Bill Recognition'),
        ('full_purchase', 'Full Purchase Order Recognition'),
    ]

    name = fields.Char(
        required=True,
        default=lambda self: _('New Digitization Job'),
    )
    state = fields.Selection(
        selection=STATE_SELECTION,
        required=True,
        default='draft',
        copy=False,
    )
    mode = fields.Selection(
        selection=MODE_SELECTION,
        required=True,
        default='partial_bill',
    )
    res_model = fields.Char(
    )
    res_id = fields.Integer(
    )
    move_id = fields.Many2one(
        comodel_name='account.move',
        string='Vendor Bill',
        ondelete='set null',
    )
    purchase_order_id = fields.Many2one(
        comodel_name='purchase.order',
        string='Purchase Order',
        ondelete='set null',
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string='Source Attachment',
        ondelete='set null',
    )
    raw_request_json = fields.Json(
        string='Raw Request JSON',
        copy=False,
    )
    raw_response_json = fields.Json(
        string='Raw Response JSON',
        copy=False,
    )
    error_message = fields.Text(
        copy=False,
    )
    confidence = fields.Float(
        copy=False,
    )
    recognized_invoice_number = fields.Char(
        copy=False,
    )
    recognized_invoice_date = fields.Date(
        copy=False,
    )
    recognized_amount_untaxed = fields.Monetary(
        currency_field='currency_id',
        copy=False,
    )
    recognized_amount_tax = fields.Monetary(
        currency_field='currency_id',
        copy=False,
    )
    recognized_amount_total = fields.Monetary(
        currency_field='currency_id',
        copy=False,
    )
    line_ids = fields.One2many(
        comodel_name='account.gemini.digitization.line',
        inverse_name='job_id',
        string='Recognized Lines',
        copy=False,
    )

    def action_process(self):
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(_('Cannot process a done or cancelled digitization job.'))

        client = GeminiClient(self.env)
        try:
            self.write({
                'state': 'processing',
                'error_message': False,
            })
            response = client.recognize(self)
            self.write({
                'raw_request_json': client.last_request_payload,
            })
            ResponseParser(self.env).apply_to_job(
                self,
                response,
                raw_response=client.last_raw_response,
            )
        except UserError as error:
            self._save_processing_error(error, client)
            raise
        except Exception as error:
            _logger.exception('Unexpected Gemini digitization processing error.')
            user_error = UserError(_('Помилка обробки Gemini: %s') % error)
            self._save_processing_error(user_error, client)
            raise user_error

        return self._get_job_form_action()

    def action_open_review_wizard(self):
        raise UserError(_('Digitization review wizard is not implemented yet.'))

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_reset_to_draft(self):
        self.write({
            'state': 'draft',
            'error_message': False,
        })
        return True

    def _prepare_request_payload(self):
        raise UserError(_('Gemini request payload preparation is not implemented yet.'))

    def _create_lines_from_response(self):
        raise UserError(_('Creating digitization lines from Gemini response is not implemented yet.'))

    def _get_job_form_action(self):
        self.ensure_one()
        form_view = self.env.ref(
            'account_gemini_digitization.view_account_gemini_digitization_job_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Gemini Digitization Job'),
            'res_model': 'account.gemini.digitization.job',
            'view_mode': 'form',
            'views': [(form_view.id, 'form')],
            'res_id': self.id,
            'target': 'current',
        }

    def _save_processing_error(self, error, client):
        self.ensure_one()
        error_message = self._get_error_message(error)
        values = {
            'state': 'error',
            'error_message': error_message,
        }
        if getattr(client, 'last_request_payload', None):
            values['raw_request_json'] = client.last_request_payload
        if getattr(client, 'last_raw_response', None) is not None:
            values['raw_response_json'] = client.last_raw_response
        elif getattr(client, 'last_raw_text', None):
            values['raw_response_json'] = {'text': client.last_raw_text}
        self.write(values)
        # Keep the diagnostic state visible even though the button raises UserError.
        self.env.cr.commit()

    def _get_error_message(self, error):
        if getattr(error, 'args', None):
            return error.args[0]
        return str(error)
