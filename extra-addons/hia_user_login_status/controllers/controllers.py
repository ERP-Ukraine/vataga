# -*- coding: utf-8 -*-
from odoo import http
from odoo.addons.web.controllers.home import Home as WebHome
from odoo.addons.web.controllers.session import Session
from odoo.http import request
from datetime import datetime


class UserLogin(WebHome):

    @http.route()
    def web_login(self, redirect=None, **kw):
        res = super(UserLogin, self).web_login(redirect=None, **kw)
        if request.params['login_success']:
            user = request.env['res.users'].sudo().search([('login', '=', kw['login'])], limit=1)
            if user:
                user.status = 'done'
                config_perams = request.env['ir.config_parameter'].sudo()
                need_to_store = config_perams.get_param('hia_user_login_status.store_user_time')
                if need_to_store:
                    request.env['res.users.logger'].sudo().create({
                        'username': user.id,
                        'login_time': datetime.now()
                    })
        return res


class UserSession(Session):
    @http.route()
    def logout(self, redirect='/web'):
        user = request.env['res.users'].sudo().search([('id', '=', request.session.uid)], limit=1)
        if user:
            user.status = 'blocked'
            config_perams = request.env['ir.config_parameter'].sudo()
            need_to_store = config_perams.get_param('hia_user_login_status.store_user_time')
            if need_to_store:
                record = request.env['res.users.logger'].sudo().search(
                    [('username', '=', user.id), ('logout_time', '=', False)], limit=1)
                if record:
                    record.logout_time = datetime.now()
        return super(UserSession, self).logout(redirect=redirect)
