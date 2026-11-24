from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

GROUP = "ratel_support_embed.group_user"
MAX_TEXT = 4000


class RatelSupportReply(models.TransientModel):
    """Відповідь користувачу застосунку з квитка Odoo.

    Odoo користувачу застосунку нічого не надсилає: текст передається у вбудований екран Ratel
    Support чернеткою, де фахівець його перевіряє й надсилає — шифрує й підписує його браузер.
    Запис майстра після передавання видаляється; у квиток відповідь повертається приміткою
    разом з рештою листування.
    """

    _name = "ratel.support.reply"
    _description = "Відповідь користувачу застосунку Ratel Support"

    ticket_id = fields.Many2one("helpdesk.ticket", required=True, ondelete="cascade")
    ratel_number = fields.Char(related="ticket_id.ratel_number")
    text = fields.Text("Текст відповіді", required=True)

    def action_continue(self):
        self.ensure_one()
        if not self.env.user.has_group(GROUP):
            raise AccessError(_("Немає доступу до Ratel Support."))
        ticket = self.ticket_id
        ticket.check_access_rights("read")
        ticket.check_access_rule("read")
        if not ticket.ratel_ticket_id:
            raise UserError(_("Цей квиток не пов'язано із заявкою Ratel Support."))
        text = (self.text or "").strip()
        if not text:
            raise UserError(_("Напишіть текст відповіді."))
        if len(text) > MAX_TEXT:
            raise UserError(_("Відповідь задовга: не більше %s символів.", MAX_TEXT))
        ratel_id = ticket.ratel_ticket_id
        self.unlink()
        return {
            "type": "ir.actions.client",
            "tag": "ratel_support_embed.embed",
            "name": "Ratel Support",
            "context": {
                "ratel_route": f"/t/{ratel_id}",
                "ratel_draft": {"ratel_id": ratel_id, "text": text},
            },
        }
