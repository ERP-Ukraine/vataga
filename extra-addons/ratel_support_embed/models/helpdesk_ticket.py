import re

from markupsafe import Markup
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.mail import plaintext2html

from .res_config_settings import TAG_INIT_PARAM, TAG_NAME, TAG_PARAM, TEAM_PARAM

GROUP = "ratel_support_embed.group_user"
# Ключ запису листування Ratel: літера джерела й номер повідомлення
KEY = re.compile(r"[a-z][0-9]{1,15}")
NUMBER = re.compile(r"[A-Za-z0-9-]{1,40}")
MAX_HEADER, MAX_TEXT, MAX_FILES, MAX_STATUS = 300, 50000, 20, 60


class HelpdeskTicket(models.Model):
    """Квиток, створений фахівцем із заявки Ratel Support у вбудованому екрані.

    Зміст приходить зі сторінки Ratel у рамці через браузер фахівця й записується від імені
    цього користувача Odoo з його правами; сервер Ratel в Odoo нічого не пише. Текст із Ratel —
    завжди лише текст: у розмітку він потрапляє екранованим.
    """

    _inherit = "helpdesk.ticket"

    ratel_ticket_id = fields.Integer("ID заявки Ratel Support", copy=False, readonly=True)
    ratel_number = fields.Char("Заявка Ratel Support", copy=False, readonly=True, index=True)
    ratel_message_ids = fields.One2many("ratel.support.message", "ticket_id")
    # Стан заявки, яким його бачив вбудований екран, коли картку востаннє відкривали
    ratel_status = fields.Char("Стан у Ratel Support", copy=False, readonly=True)
    ratel_status_at = fields.Datetime("Стан у Ratel Support на", copy=False, readonly=True)

    _sql_constraints = [
        (
            "ratel_number_uniq",
            "unique(ratel_number)",
            "Квиток для цієї заявки Ratel Support уже існує.",
        ),
    ]

    @api.model
    def _ratel_check(self):
        if not self.env.user.has_group(GROUP):
            raise AccessError(_("Немає доступу до Ratel Support."))

    @api.model
    def ratel_lookup(self, number):
        """Квиток цієї заявки Ratel (серед доступних користувачеві) і ключі перенесених записів."""
        self._ratel_check()
        if not isinstance(number, str) or not NUMBER.fullmatch(number):
            return False
        ticket = self.with_context(active_test=False).search(
            [("ratel_number", "=", number)], limit=1
        )
        if not ticket:
            return False
        return {
            "id": ticket.id,
            "name": ticket.display_name,
            "keys": ticket.sudo().ratel_message_ids.mapped("key"),
            "status": ticket.ratel_status or "",
        }

    @api.model
    def ratel_defaults(self):
        """Команда й мітка з налаштувань модуля для нового квитка із заявки."""
        self._ratel_check()
        params = self.env["ir.config_parameter"].sudo()

        def record(model, param):
            value = params.get_param(param) or ""
            found = self.env[model].sudo().browse(int(value)).exists() if value.isdigit() else None
            return found.id if found else False

        return {
            "team_id": record("helpdesk.team", TEAM_PARAM),
            "tag_id": record("helpdesk.tag", TAG_PARAM),
        }

    @api.model
    def _ratel_setup_tag(self):
        """Раз, під час встановлення чи оновлення: мітка «Застосунок» для квитків із заявок.

        Далі її міняють або прибирають у Налаштуваннях — оновлення модуля вибору не чіпає.
        """
        params = self.env["ir.config_parameter"].sudo()
        if params.get_param(TAG_INIT_PARAM):
            return
        if not params.get_param(TAG_PARAM):
            tags = self.env["helpdesk.tag"].sudo()
            tag = tags.search([("name", "=ilike", TAG_NAME)], limit=1) or tags.create(
                {"name": TAG_NAME}
            )
            params.set_param(TAG_PARAM, str(tag.id))
        params.set_param(TAG_INIT_PARAM, "1")

    def ratel_set_status(self, status):
        """Записати стан заявки Ratel, яким його щойно показав вбудований екран.

        Стан квитка Odoo (етап) не міняється: це лише довідка для того, хто читає квиток.
        """
        self.ensure_one()
        self._ratel_check()
        self.check_access_rights("write")
        self.check_access_rule("write")
        if not self.ratel_number:
            raise UserError(_("Цей квиток не пов'язано із заявкою Ratel Support."))
        if not isinstance(status, str) or not status.strip() or len(status) > MAX_STATUS:
            raise UserError(_("Некоректний стан із Ratel Support."))
        status = status.strip()
        if self.ratel_status != status:
            self.write({"ratel_status": status, "ratel_status_at": fields.Datetime.now()})
        return {"status": status}

    def ratel_add_message(self, key, header, text, attachment_ids=None):
        """Дописати запис листування Ratel внутрішньою приміткою (листів підписникам немає).

        Повтор із тим самим ключем нічого не додає. Вкладення — лише ті, що цей користувач
        щойно завантажив саме до цього квитка.
        """
        self.ensure_one()
        self._ratel_check()
        self.check_access_rights("write")
        self.check_access_rule("write")
        if not self.ratel_number:
            raise UserError(_("Цей квиток не пов'язано із заявкою Ratel Support."))
        ids = attachment_ids or []
        if (
            not isinstance(key, str)
            or not KEY.fullmatch(key)
            or not isinstance(header, str)
            or not isinstance(text, str)
            or len(header) > MAX_HEADER
            or len(text) > MAX_TEXT
            or not isinstance(ids, list)
            or len(ids) > MAX_FILES
            or not all(type(i) is int for i in ids)
        ):
            raise UserError(_("Некоректний запис із Ratel Support."))
        log = self.env["ratel.support.message"].sudo()
        if log.search_count([("ticket_id", "=", self.id), ("key", "=", key)]):
            return {"added": False}
        attachments = (
            self.env["ir.attachment"]
            .browse(ids)
            .exists()
            .filtered(
                lambda a: (
                    a.res_model == self._name
                    and a.res_id == self.id
                    and a.create_uid == self.env.user
                )
            )
        )
        body = Markup("<p><strong>%s</strong></p>") % header
        if text.strip():
            body += plaintext2html(text)
        message = self.message_post(
            body=body,
            attachment_ids=attachments.ids,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
        log.create({"ticket_id": self.id, "key": key, "message_id": message.id})
        return {"added": True}

    def action_open_ratel(self):
        """Відкрити заявку у вбудованому екрані Ratel Support."""
        self.ensure_one()
        self._ratel_check()
        if not self.ratel_ticket_id:
            raise UserError(_("Цей квиток не пов'язано із заявкою Ratel Support."))
        return {
            "type": "ir.actions.client",
            "tag": "ratel_support_embed.embed",
            "name": "Ratel Support",
            "context": {"ratel_route": f"/t/{self.ratel_ticket_id}"},
        }

    def action_reply_ratel(self):
        """Написати відповідь користувачу застосунку: текст піде у вбудований екран (ODOO-17)."""
        self.ensure_one()
        self._ratel_check()
        if not self.ratel_ticket_id:
            raise UserError(_("Цей квиток не пов'язано із заявкою Ratel Support."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "ratel.support.reply",
            "name": _("Відповідь користувачу в застосунок"),
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id},
        }


class RatelSupportMessage(models.Model):
    """Які записи листування Ratel уже перенесено у квиток — щоб повтор не дублював."""

    _name = "ratel.support.message"
    _description = "Запис листування Ratel Support у квитку"

    ticket_id = fields.Many2one("helpdesk.ticket", required=True, ondelete="cascade", index=True)
    key = fields.Char(required=True)
    message_id = fields.Many2one("mail.message", ondelete="set null")

    _sql_constraints = [
        ("ticket_key_uniq", "unique(ticket_id, key)", "Цей запис уже перенесено."),
    ]
