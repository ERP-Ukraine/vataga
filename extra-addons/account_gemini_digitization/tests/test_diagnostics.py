import base64
from unittest.mock import Mock, patch

import requests

from odoo.exceptions import UserError
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..models import digitization_job


class TestGeminiDiagnostics(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'OCR test vendor'})
        cls.env['ir.config_parameter'].sudo().set_param(
            'account_gemini_digitization.gemini_api_key', 'secret-api-key',
        )

    def setUp(self):
        super().setUp()
        network_guard = patch.object(requests, 'post', side_effect=AssertionError('Unexpected HTTP request'))
        network_guard.start()
        self.addCleanup(network_guard.stop)
        self.move = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
        })
        self.attachment = self.env['ir.attachment'].create({
            'name': 'test.pdf', 'mimetype': 'application/pdf',
            'datas': base64.b64encode(b'%PDF-confidential-document'),
            'res_model': 'account.move', 'res_id': self.move.id,
        })

    def _messages(self):
        return self.env['mail.message'].search([
            ('model', '=', 'account.move'), ('res_id', '=', self.move.id),
            ('body', 'ilike', 'Gemini OCR'),
        ])

    def _assert_cleaned(self):
        self.assertFalse(self.env['account.gemini.digitization.job'].search([
            ('move_id', '=', self.move.id),
        ]))

    def _response(self, status=200, body=None):
        response = Mock()
        response.status_code = status
        response.headers = {'Content-Type': 'application/json'}
        response.content = b'present'
        response.json.return_value = body
        return response

    def _recognized_response(self):
        return self._response(body={'candidates': [{
            'content': {'parts': [{'text': '{"lines": []}'}]},
        }]})

    def test_http_errors_survive_cleanup_without_secrets(self):
        for status in (400, 401, 402, 403, 429, 500):
            with self.subTest(status=status):
                response = self._response(status, {'error': {
                    'code': status,
                    'message': 'secret-api-key Bearer private-token confidential-document',
                }})
                before = self._messages()
                with patch.object(requests, 'post', return_value=response) as post:
                    action = self.move.action_create_gemini_digitization_job()
                post.assert_called_once()
                self.assertEqual(action['params']['type'], 'danger')
                messages = self._messages() - before
                self.assertEqual(len(messages), 1)
                body = str(messages.body)
                self.assertIn('HTTP %s' % status, body)
                self.assertIn('UTC', body)
                for secret in ('secret-api-key', 'private-token', 'confidential-document'):
                    self.assertNotIn(secret, body)
                self._assert_cleaned()

    def test_malformed_response(self):
        for payload in ({'candidates': []}, {'candidates': [{
            'content': {'parts': [{'text': 'not JSON secret-api-key'}]},
        }]}, {'candidates': [{
            'content': {'parts': [{'text': '{"lines": "invalid"}'}]},
        }]}):
            with self.subTest(payload=payload):
                before = self._messages()
                with patch.object(requests, 'post', return_value=self._response(body=payload)):
                    self.move.action_create_gemini_digitization_job()
                messages = self._messages() - before
                self.assertEqual(len(messages), 1)
                self.assertNotIn('secret-api-key', str(messages.body))
                self._assert_cleaned()

    def test_transport_error(self):
        with patch.object(requests, 'post', side_effect=requests.ConnectionError(
            'https://example.test?key=secret-api-key Bearer private-token',
        )):
            self.move.action_create_gemini_digitization_job()
        body = str(self._messages().body)
        self.assertIn('час очікування', body)
        self.assertNotIn('secret-api-key', body)
        self.assertNotIn('private-token', body)
        self._assert_cleaned()

    def test_preparation_error(self):
        self.attachment.datas = False
        with patch.object(requests, 'post') as post:
            self.move.action_create_gemini_digitization_job()
        post.assert_not_called()
        self.assertIn('Підготовка', str(self._messages().body))
        self._assert_cleaned()

    def test_missing_attachment_is_reported_without_job(self):
        self.attachment.unlink()
        action = self.move.action_create_gemini_digitization_job()
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('Підготовка', str(self._messages().body))
        self._assert_cleaned()

    def test_matching_failure_rolls_back_and_prevents_apply(self):
        def fail_matching(matcher, job):
            job.move_id.ref = 'must roll back'
            raise RuntimeError('secret-api-key private-token')

        with patch.object(requests, 'post', return_value=self._recognized_response()), \
                patch.object(digitization_job.ProductMatcher, 'match_job', fail_matching), \
                patch.object(digitization_job.DigitizationApplyService, 'apply') as apply:
            self.move.action_create_gemini_digitization_job()
        apply.assert_not_called()
        self.assertFalse(self.move.ref)
        body = str(self._messages().body)
        self.assertIn('зіставлення', body)
        self.assertNotIn('secret-api-key', body)
        self._assert_cleaned()

    def test_apply_failure_rolls_back(self):
        def fail_apply(service):
            service.job.move_id.ref = 'must roll back'
            raise UserError('secret-api-key private-token')

        def processed(job):
            job.state = 'review'

        job_class = type(self.env['account.gemini.digitization.job'])
        with patch.object(job_class, 'action_process', processed), \
                patch.object(job_class, '_get_automatic_apply_blocker', return_value=False), \
                patch.object(digitization_job.DigitizationApplyService, 'apply', fail_apply):
            self.move.action_create_gemini_digitization_job()
        self.assertFalse(self.move.ref)
        self.assertIn('застосувати', str(self._messages().body))
        self.assertNotIn('secret-api-key', str(self._messages().body))
        self._assert_cleaned()

    def test_success_does_not_add_diagnostic_note(self):
        def processed(job):
            job.state = 'review'

        job_class = type(self.env['account.gemini.digitization.job'])
        with patch.object(job_class, 'action_process', processed), \
                patch.object(job_class, '_get_automatic_apply_blocker', return_value=False), \
                patch.object(digitization_job.DigitizationApplyService, 'apply', return_value={
                    'gemini_status': 'applied', 'gemini_notification_type': 'success',
                }):
            action = self.move.action_create_gemini_digitization_job()
        self.assertEqual(action['params']['type'], 'success')
        self.assertFalse(self._messages())
        self._assert_cleaned()
