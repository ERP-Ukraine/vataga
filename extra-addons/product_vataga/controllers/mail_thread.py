from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request

from ..models.product_autolog import PRODUCT_MODELS, SUBTYPE


class ProductThreadController(http.Controller):
    @http.route('/product_vataga/mail/thread/messages', methods=['POST'], type='json', auth='user')
    def product_thread_messages(
        self, thread_model, thread_id, search_term=None, before=None, after=None,
        around=None, limit=30, hide_product_autologs=False,
    ):
        if thread_model not in PRODUCT_MODELS:
            raise NotFound()
        domain = [
            ('res_id', '=', int(thread_id)),
            ('model', '=', thread_model),
            ('message_type', '!=', 'user_notification'),
        ]
        if hide_product_autologs:
            domain += [
                ('subtype_id', '!=', request.env.ref(SUBTYPE).id),
                '|', ('message_type', '!=', 'notification'), ('tracking_value_ids', '=', False),
            ]
        result = request.env['mail.message']._message_fetch(
            domain, search_term=search_term, before=before, after=after, around=around, limit=limit,
        )
        messages = result['messages']
        if not request.env.user._is_public():
            messages.set_message_done()
        return {**result, 'messages': messages.message_format()}
