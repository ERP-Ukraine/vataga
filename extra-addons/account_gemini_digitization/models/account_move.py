from odoo import _, fields, models
from odoo.exceptions import UserError


SUPPORTED_DIGITIZATION_MIMETYPES = (
    'application/pdf',
    'image/png',
    'image/jpeg',
    'image/jpg',
)


class AccountMove(models.Model):
    _inherit = 'account.move'

    gemini_digitization_job_ids = fields.One2many(
        comodel_name='account.gemini.digitization.job',
        inverse_name='move_id',
        string='Gemini Digitization Jobs',
        readonly=True,
    )
    gemini_digitization_job_count = fields.Integer(
        string='Gemini OCR Jobs',
        compute='_compute_gemini_digitization_job_count',
    )

    def _compute_gemini_digitization_job_count(self):
        for move in self:
            move.gemini_digitization_job_count = len(move.gemini_digitization_job_ids)

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

        unfinished_job = self._find_unfinished_gemini_digitization_job(attachment)
        if unfinished_job:
            return self._get_gemini_digitization_notification_action(
                _('Для цього документа вже існує незавершене завдання оцифрування Gemini.'),
                notification_type='warning',
            )

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
        result = job.run_automatic_pipeline()
        return self._get_gemini_digitization_notification_action(
            result['message'],
            notification_type=result.get('notification_type', 'info'),
            sticky=result.get('sticky', False),
        )

    def _find_unfinished_gemini_digitization_job(self, attachment):
        self.ensure_one()
        return self.env['account.gemini.digitization.job'].search(
            [
                ('move_id', '=', self.id),
                ('attachment_id', '=', attachment.id),
                ('state', 'in', ('draft', 'processing', 'review')),
            ],
            order='create_date desc, id desc',
            limit=1,
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

    def _get_gemini_digitization_document_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Bill'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.id,
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

    def action_view_gemini_digitization_jobs(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'account_gemini_digitization.action_account_gemini_digitization_job'
        )
        action['domain'] = [('move_id', '=', self.id)]
        default_mode = (
            'partial_bill'
            if self._get_gemini_digitization_product_lines()
            else 'full_bill'
        )
        action['context'] = {
            'default_move_id': self.id,
            'default_mode': default_mode,
        }
        if self.gemini_digitization_job_count == 1:
            job = self.gemini_digitization_job_ids
            form_view = self.env.ref(
                'account_gemini_digitization.view_account_gemini_digitization_job_form'
            )
            action['views'] = [(form_view.id, 'form')]
            action['res_id'] = job.id
        return action
