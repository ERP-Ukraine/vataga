import re
import time

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..lib import assertion
from ..models.res_config_settings import ISSUER_PARAM, KEY_PARAM, URL_PARAM, origin

GROUP = "ratel_support_embed.group_user"
# Маршрути екрана Ratel: черга, картка заявки, нове звернення
ROUTE = re.compile(r"/(t/\d{1,12}|new)?")


class RatelSupportEmbed(http.Controller):
    @http.route("/ratel_support_embed/frame", type="json", auth="user")
    def frame(self, route="/"):
        """Адреса рамки з новим одноразовим підтвердженням особи для поточного користувача.

        Підтвердження — у фрагменті адреси (після `#`): до сервера з адресою воно не їде.
        """
        user = request.env.user
        if not user.has_group(GROUP):
            raise AccessError(_("Немає доступу до Ratel Support."))
        if not isinstance(route, str) or not ROUTE.fullmatch(route):
            route = "/"
        params = request.env["ir.config_parameter"].sudo()
        url = origin(params.get_param(URL_PARAM))
        issuer = origin(params.get_param(ISSUER_PARAM) or params.get_param("web.base.url"))
        private = params.get_param(KEY_PARAM)
        if not (url and issuer and private):
            raise UserError(
                _("Ratel Support не налаштовано: адреса сервера й ключ — у Налаштуваннях.")
            )
        token = assertion.make_assertion(private, issuer, url, user.id, time.time())
        return {"src": f"{url}/admin/embed/#{route}?a={token}"}
