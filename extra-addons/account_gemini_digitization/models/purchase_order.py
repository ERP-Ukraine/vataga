import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


SUPPORTED_DIGITIZATION_MIMETYPES = (
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/jpg',
)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    gemini_has_attachment = fields.Boolean(
        compute='_compute_gemini_has_attachment',
        compute_sudo=True,
    )
    gemini_has_supported_attachment = fields.Boolean(
        compute='_compute_gemini_has_supported_attachment',
        compute_sudo=True,
    )

    @api.depends('message_attachment_count')
    def _compute_gemini_has_attachment(self):
        order_ids_with_attachments = set()
        if self.ids:
            groups = self.env['ir.attachment'].sudo().read_group(
                [
                    ('res_model', '=', 'purchase.order'),
                    ('res_id', 'in', self.ids),
                    ('type', '=', 'binary'),
                ],
                ['res_id'],
                ['res_id'],
            )
            order_ids_with_attachments = {
                group['res_id']
                for group in groups
                if group.get('res_id')
            }
        for order in self:
            order.gemini_has_attachment = order.id in order_ids_with_attachments

    @api.depends('message_attachment_count')
    def _compute_gemini_has_supported_attachment(self):
        supported_order_ids = set()
        if self.ids:
            groups = self.env['ir.attachment'].sudo().read_group(
                [
                    ('res_model', '=', 'purchase.order'),
                    ('res_id', 'in', self.ids),
                    ('type', '=', 'binary'),
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
                ('type', '=', 'binary'),
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
                'Не знайдено придатного файлу для оцифрування. Прикріпіть PDF або зображення PNG/JPEG.'
            ))
        _logger.info(
            'Gemini OCR selected attachment "%s" (%s) for purchase.order %s.',
            attachment.name,
            attachment.mimetype,
            self.id,
        )

        document_name = self.name if self.name and self.name != '/' else self.id
        product_lines = self._get_gemini_digitization_product_lines()
        if product_lines and not self._has_only_gemini_auto_product_lines():
            mode = 'partial_purchase'
            name_template = _('Gemini OCR: Partial Purchase Order %s')
        else:
            mode = 'full_purchase'
            name_template = _('Gemini OCR: Purchase Order %s')
        job = self.env['account.gemini.digitization.job'].create({
            'name': name_template % document_name,
            'mode': mode,
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

    def _get_gemini_digitization_product_lines(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda line: self._is_gemini_digitization_product_line(line)
        )

    def _is_gemini_digitization_product_line(self, line):
        if not line.product_id:
            return False
        display_type = getattr(line, 'display_type', False)
        if display_type:
            return False
        return True

    def _has_only_gemini_auto_product_lines(self):
        self.ensure_one()
        product_lines = self._get_gemini_digitization_product_lines()
        return bool(
            product_lines
            and all(product_lines.mapped('gemini_digitization_auto_created'))
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


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    gemini_digitization_auto_created = fields.Boolean(
        string='Created by Gemini OCR',
        copy=False,
        index=True,
    )
    gemini_digitization_source_article = fields.Char(
        string='Gemini OCR Supplier Article',
        copy=False,
        index=True,
    )
    gemini_digitization_technical_code = fields.Char(
        string='Gemini OCR Technical Code',
        copy=False,
        index=True,
    )
