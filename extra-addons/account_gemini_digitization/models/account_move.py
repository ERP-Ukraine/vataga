from odoo import _, models
from odoo.exceptions import UserError


SUPPORTED_DIGITIZATION_MIMETYPES = (
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/jpg',
)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_latest_gemini_digitization_attachment(self):
        self.ensure_one()
        return self.env['ir.attachment'].search(
            [
                ('res_model', '=', 'account.move'),
                ('res_id', '=', self.id),
                ('mimetype', 'in', SUPPORTED_DIGITIZATION_MIMETYPES),
            ],
            order='create_date desc, id desc',
            limit=1,
        )

    def action_create_gemini_digitization_job(self):
        self.ensure_one()
        if self.move_type != 'in_invoice':
            raise UserError(_('Gemini digitization is available only for vendor bills.'))
        if self.state != 'draft':
            raise UserError(_('Gemini digitization is available only for draft vendor bills.'))

        attachment = self._get_latest_gemini_digitization_attachment()
        if not attachment:
            raise UserError(_(
                'Спочатку завантажте PDF або зображення рахунку постачальника у вкладення документа.'
            ))

        product_lines = self._get_gemini_digitization_product_lines()
        if product_lines:
            mode = 'partial_bill'
            name_template = _('Gemini OCR: Partial Vendor Bill %s')
        else:
            if not self.partner_id:
                raise UserError(_('Спочатку оберіть постачальника в рахунку.'))
            mode = 'full_bill'
            name_template = _('Gemini OCR: Full Vendor Bill %s')

        document_name = self.name if self.name and self.name != '/' else self.id
        job = self.env['account.gemini.digitization.job'].create({
            'name': name_template % document_name,
            'mode': mode,
            'state': 'draft',
            'move_id': self.id,
            'res_model': 'account.move',
            'res_id': self.id,
            'partner_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'attachment_id': attachment.id,
        })
        try:
            result = job.run_automatic_pipeline()
        finally:
            job._unlink_temporary_job()
        return self._get_gemini_digitization_notification_action(
            result['message'],
            notification_type=result.get('notification_type', 'info'),
            sticky=result.get('sticky', False),
        )

    def _get_gemini_digitization_product_lines(self):
        self.ensure_one()
        invoice_lines = self.invoice_line_ids.filtered(
            lambda line: self._is_gemini_digitization_product_line(line)
        )
        if invoice_lines:
            return invoice_lines
        return self.line_ids.filtered(
            lambda line: self._is_gemini_digitization_product_line(line)
        )

    def _is_gemini_digitization_product_line(self, line):
        if not line.product_id:
            return False
        display_type = getattr(line, 'display_type', False)
        if display_type and display_type != 'product':
            return False
        account = getattr(line, 'account_id', False)
        account_type = getattr(account, 'account_type', False) if account else False
        if account_type:
            account_type = str(account_type).lower()
            if 'receivable' in account_type or 'payable' in account_type:
                return False
        return True

    def _get_gemini_digitization_document_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Рахунок постачальника'),
            'res_model': 'account.move',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _get_gemini_digitization_notification_action(
        self,
        message,
        notification_type='info',
        sticky=False,
    ):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Gemini OCR'),
                'message': message,
                'type': notification_type,
                'sticky': sticky,
                'next': self._get_gemini_digitization_document_action(),
            },
        }
