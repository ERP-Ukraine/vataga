from odoo import http
from odoo.http import request


class AccountMoveThreadController(http.Controller):
    _MOVE_AUTOLOG_BODY_PREFIXES = (
        "Invoice updated:",
        "Invoice line added:",
        "Invoice line updated:",
        "Invoice line removed:",
        "Рахунок змінено:",
        "Додано рядок рахунку:",
        "Змінено рядок рахунку:",
        "Видалено рядок рахунку:",
    )

    @http.route("/account_vataga/mail/thread/messages", methods=["POST"], type="json", auth="user")
    def account_move_thread_messages(
        self,
        thread_model,
        thread_id,
        search_term=None,
        before=None,
        after=None,
        around=None,
        limit=30,
        hide_account_move_autologs=False,
    ):
        domain = [
            ("res_id", "=", int(thread_id)),
            ("model", "=", thread_model),
            ("message_type", "!=", "user_notification"),
        ]
        if thread_model == "account.move" and hide_account_move_autologs:
            autolog_subtype = request.env.ref(
                "account_vataga.mt_account_move_autolog",
                raise_if_not_found=False,
            )
            if autolog_subtype:
                domain.append(("subtype_id", "!=", autolog_subtype.id))
            domain.append(("tracking_value_ids", "=", False))
            for prefix in self._MOVE_AUTOLOG_BODY_PREFIXES:
                domain.append(("body", "not ilike", prefix))
        res = request.env["mail.message"]._message_fetch(
            domain,
            search_term=search_term,
            before=before,
            after=after,
            around=around,
            limit=limit,
        )
        if not request.env.user._is_public():
            res["messages"].set_message_done()
        return {**res, "messages": res["messages"].message_format()}
