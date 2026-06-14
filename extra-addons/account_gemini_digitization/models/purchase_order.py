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

    gemini_digitization_job_ids = fields.One2many(
        comodel_name='account.gemini.digitization.job',
        inverse_name='purchase_order_id',
        string='Gemini Digitization Jobs',
        readonly=True,
    )
    gemini_digitization_job_count = fields.Integer(
        string='Gemini OCR Jobs',
        compute='_compute_gemini_digitization_job_count',
    )

    def _compute_gemini_digitization_job_count(self):
        for order in self:
            order.gemini_digitization_job_count = len(order.gemini_digitization_job_ids)

    def _get_latest_gemini_digitization_attachment(self):
        self.ensure_one()
        return self.env['ir.attachment'].search(
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

        attachment = self._get_latest_gemini_digitization_attachment()
        if not attachment:
            raise UserError(_(
                'Спочатку завантажте PDF або зображення рахунку постачальника у вкладення замовлення.'
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
        return self._get_gemini_digitization_job_form_action(job)

    def _get_gemini_digitization_job_form_action(self, job):
        form_view = self.env.ref(
            'account_gemini_digitization.view_account_gemini_digitization_job_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Gemini Digitization Job'),
            'res_model': 'account.gemini.digitization.job',
            'view_mode': 'form',
            'views': [(form_view.id, 'form')],
            'res_id': job.id,
            'target': 'current',
        }

    def action_view_gemini_digitization_jobs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'account_gemini_digitization.action_account_gemini_digitization_job'
        )
        action['domain'] = [('purchase_order_id', '=', self.id)]
        action['context'] = {
            'default_purchase_order_id': self.id,
            'default_mode': 'full_purchase',
        }
        if self.gemini_digitization_job_count == 1:
            job = self.gemini_digitization_job_ids
            form_view = self.env.ref(
                'account_gemini_digitization.view_account_gemini_digitization_job_form'
            )
            action['views'] = [(form_view.id, 'form')]
            action['res_id'] = job.id
        return action
