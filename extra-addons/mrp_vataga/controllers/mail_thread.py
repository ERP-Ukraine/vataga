from odoo import http
from odoo.http import request


class MrpBomThreadController(http.Controller):
    @http.route("/mrp_vataga/mail/thread/messages", methods=["POST"], type="json", auth="user")
    def mrp_bom_thread_messages(
        self,
        thread_model,
        thread_id,
        search_term=None,
        before=None,
        after=None,
        around=None,
        limit=30,
        hide_bom_autologs=False,
    ):
        domain = [
            ("res_id", "=", int(thread_id)),
            ("model", "=", thread_model),
            ("message_type", "!=", "user_notification"),
        ]
        if thread_model == "mrp.bom" and hide_bom_autologs:
            autolog_subtype = request.env.ref('mrp_vataga.mt_bom_autolog', raise_if_not_found=False)
            if autolog_subtype:
                domain.append(("subtype_id", "!=", autolog_subtype.id))
        res = request.env["mail.message"]._message_fetch(
            domain, search_term=search_term, before=before, after=after, around=around, limit=limit
        )
        if not request.env.user._is_public():
            res["messages"].set_message_done()
        return {**res, "messages": res["messages"].message_format()}
