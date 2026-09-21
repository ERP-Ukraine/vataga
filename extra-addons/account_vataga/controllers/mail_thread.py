from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound


class InvoiceThreadController(http.Controller):
    @http.route('/account_vataga/mail/thread/messages', type='json', auth='user', methods=['POST'])
    def invoice_thread_messages(self, thread_model, thread_id, search_term=None,
                                before=None, after=None, around=None, limit=30,
                                hide_invoice_autologs=False):
        if thread_model != 'account.move':
            raise NotFound()
        move = request.env['account.move'].browse(int(thread_id)).exists()
        if not move:
            raise NotFound()
        move.check_access_rights('read')
        move.check_access_rule('read')
        domain = [
            ('model', '=', 'account.move'), ('res_id', '=', move.id),
            ('message_type', '!=', 'user_notification'),
        ]
        if hide_invoice_autologs:
            subtype = request.env.ref('account_vataga.mt_invoice_autolog')
            domain += [('subtype_id', '!=', subtype.id), ('tracking_value_ids', '=', False)]
        result = request.env['mail.message']._message_fetch(
            domain, search_term=search_term, before=before, after=after,
            around=around, limit=limit,
        )
        result['messages'].set_message_done()
        return {**result, 'messages': result['messages'].message_format()}
