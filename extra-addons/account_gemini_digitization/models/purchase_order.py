from odoo import _, fields, models
from odoo.exceptions import UserError


SUPPORTED_DIGITIZATION_MIMETYPES = (
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/jpg',
)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    gemini_attachment_ids = fields.Many2many(
        comodel_name='ir.attachment',
        relation='purchase_order_gemini_attachment_rel',
        column1='purchase_order_id',
        column2='attachment_id',
        string='Файл для Gemini OCR',
        copy=False,
    )

    def _get_latest_gemini_digitization_attachment(self):
        self.ensure_one()
        field_attachment = self._get_latest_gemini_field_attachment()
        if field_attachment:
            return field_attachment
        return self.env['ir.attachment'].search(
            [
                ('res_model', '=', 'purchase.order'),
                ('res_id', '=', self.id),
                ('mimetype', 'in', SUPPORTED_DIGITIZATION_MIMETYPES),
            ],
            order='create_date desc, id desc',
            limit=1,
        )

    def _get_latest_gemini_field_attachment(self):
        self.ensure_one()
        attachments = self.gemini_attachment_ids.filtered(
            lambda attachment: attachment.mimetype in SUPPORTED_DIGITIZATION_MIMETYPES
        )
        if not attachments:
            return self.env['ir.attachment']
        empty_date = fields.Datetime.to_datetime('1970-01-01 00:00:00')
        return attachments.sorted(
            key=lambda attachment: (attachment.create_date or empty_date, attachment.id),
            reverse=True,
        )[:1]

    def action_create_gemini_digitization_job(self):
        self.ensure_one()
        if self.state not in ('draft', 'sent'):
            raise UserError(_(
                'Gemini digitization is available only for draft or sent purchase orders.'
            ))
        if not self.partner_id:
            raise UserError(_(
                'Спочатку оберіть постачальника в замовленні на закупівлю.'
            ))

        attachment = self._get_latest_gemini_digitization_attachment()
        if not attachment:
            raise UserError(_(
                'Спочатку завантажте PDF або зображення рахунку в поле «Файл для Gemini OCR».'
            ))

        document_name = self.name if self.name and self.name != '/' else self.id
        job = self.env['account.gemini.digitization.job'].create({
            'name': _('Gemini OCR: Purchase Order %s') % document_name,
            'mode': 'full_purchase',
            'state': 'draft',
            'purchase_order_id': self.id,
            'res_model': 'purchase.order',
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

    def _get_gemini_digitization_document_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Замовлення на закупівлю'),
            'res_model': 'purchase.order',
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
