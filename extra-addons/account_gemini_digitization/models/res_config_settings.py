from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='account_gemini_digitization.gemini_api_key',
        groups='base.group_system',
    )
    gemini_model = fields.Char(
        string='Gemini Model',
        default='gemini-1.5-pro',
        config_parameter='account_gemini_digitization.gemini_model',
        groups='base.group_system',
    )
    gemini_request_timeout = fields.Integer(
        string='Gemini Request Timeout',
        default=60,
        config_parameter='account_gemini_digitization.gemini_request_timeout',
        groups='base.group_system',
    )
    gemini_min_confidence = fields.Float(
        string='Minimum Confidence',
        default=0.90,
        config_parameter='account_gemini_digitization.gemini_min_confidence',
        groups='base.group_system',
    )
