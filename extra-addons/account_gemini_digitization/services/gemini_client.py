class GeminiClient:
    DEFAULT_MODEL = 'gemini-1.5-pro'
    DEFAULT_TIMEOUT = 60

    def __init__(self, env):
        self.env = env

    def get_config(self):
        config = self.env['ir.config_parameter'].sudo()
        timeout = config.get_param(
            'account_gemini_digitization.gemini_request_timeout',
            self.DEFAULT_TIMEOUT,
        )
        return {
            'api_key': config.get_param('account_gemini_digitization.gemini_api_key'),
            'model': config.get_param(
                'account_gemini_digitization.gemini_model',
                self.DEFAULT_MODEL,
            ),
            'timeout': int(timeout or self.DEFAULT_TIMEOUT),
        }

    def recognize(self, attachment, payload=None):
        raise NotImplementedError('Gemini API integration is not implemented yet.')
