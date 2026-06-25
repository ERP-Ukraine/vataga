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

    gemini_has_supported_attachment = fields.Boolean(
        compute='_compute_gemini_has_supported_attachment',
        compute_sudo=True,
    )

    def _compute_gemini_has_supported_attachment(self):
        supported_order_ids = set()
        if self.ids:
            groups = self.env['ir.attachment'].sudo().read_group(
                [
                    ('res_model', '=', 'purchase.order'),
                    ('res_id', 'in', self.ids),
                    ('mimetype', 'in', SUPPORTED_DIGITIZATION_MIMETYPES),
                ],
                ['res_id'],
                ['res_id'],
            )
            supported_order_ids = {
                group['res_id']
                for group in groups
                if group.get('res_id')
            }
        for order in self:
            order.gemini_has_supported_attachment = order.id in supported_order_ids

    def _get_latest_gemini_digitization_attachment(self):
        self.ensure_one()
        return self.env['ir.attachment'].sudo().search(
            [
                ('res_model', '=', 'purchase.order'),
                ('res_id', '=', self.id),
                ('mimetype', 'in', SUPPORTED_DIGITIZATION_MIMETYPES),
            ],
            order='create_date desc, id desc',
            limit=1,
        )

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
                'Спочатку прикріпіть PDF або зображення до замовлення на закупівлю.'
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
