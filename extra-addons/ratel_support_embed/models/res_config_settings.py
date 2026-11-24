import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..lib import assertion

URL_PARAM = "ratel_support_embed.url"
ISSUER_PARAM = "ratel_support_embed.issuer"
# Закритий ключ: параметр системи читають лише адміністратори Odoo й серверний код модуля
KEY_PARAM = "ratel_support_embed.private_key"
# Нові квитки із заявок: команда й мітка, які форма підставляє сама
TEAM_PARAM = "ratel_support_embed.team_id"
TAG_PARAM = "ratel_support_embed.tag_id"
TAG_INIT_PARAM = "ratel_support_embed.tag_init"
TAG_NAME = "Застосунок"

ORIGIN = re.compile(r"https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?")
LOCAL = re.compile(r"http://(localhost|127\.0\.0\.1)(:\d{1,5})?")


def origin(value):
    """Адреса (схема + вузол + порт) без шляху; лише https, для розробки — http://localhost."""
    value = (value or "").strip().lower().rstrip("/")
    return value if ORIGIN.fullmatch(value) or LOCAL.fullmatch(value) else None


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ratel_support_url = fields.Char(
        string="Адреса сервера Ratel Support",
        config_parameter=URL_PARAM,
        help="Наприклад https://support.example.com — без шляху.",
    )
    ratel_support_issuer = fields.Char(
        string="Адреса цієї бази Odoo",
        config_parameter=ISSUER_PARAM,
        help="Як її бачать користувачі в браузері. Порожньо — береться web.base.url. "
        "Ту саму адресу вносять у RATEL_ODOO_EMBED_ORIGINS на сервері Ratel.",
    )
    ratel_support_team_id = fields.Many2one(
        "helpdesk.team",
        string="Команда для квитків із заявок",
        config_parameter=TEAM_PARAM,
        help="Порожньо — форма пам'ятає команду, яку людина обрала минулого разу.",
    )
    ratel_support_tag_id = fields.Many2one(
        "helpdesk.tag",
        string="Мітка для квитків із заявок",
        config_parameter=TAG_PARAM,
        help="Ставиться на кожен квиток, створений із заявки застосунку. Порожньо — без мітки.",
    )
    ratel_support_public_key = fields.Char(
        string="Відкритий ключ",
        readonly=True,
        help="Передайте адміністратору Ratel Support (RATEL_ODOO_ASSERTION_KEYS).",
    )

    @api.model
    def get_values(self):
        values = super().get_values()
        private = self.env["ir.config_parameter"].sudo().get_param(KEY_PARAM)
        values["ratel_support_public_key"] = assertion.public_key(private) if private else False
        return values

    @api.constrains("ratel_support_url", "ratel_support_issuer")
    def _check_ratel_support_urls(self):
        for record in self:
            for value in (record.ratel_support_url, record.ratel_support_issuer):
                if value and not origin(value):
                    raise ValidationError(
                        _("Адреса має бути на кшталт https://support.example.com — без шляху.")
                    )

    def action_ratel_support_new_key(self):
        """Створити (або замінити) ключ підпису. Після заміни новий відкритий ключ треба
        передати адміністратору Ratel — доти екран не відкриватиметься."""
        self.env["ir.config_parameter"].sudo().set_param(KEY_PARAM, assertion.new_private_key())
        return {"type": "ir.actions.client", "tag": "reload"}
