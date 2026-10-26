"""Run with Python alone; tests client and safe formatter, not Odoo transactions.

Odoo and HTTP dependencies are isolated mocks. ORM coverage is in test_diagnostics.
"""
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class UserError(Exception):
    pass


def load_client():
    odoo = ModuleType('odoo')
    odoo._ = lambda text: text
    exceptions = ModuleType('odoo.exceptions')
    exceptions.UserError = UserError
    requests = ModuleType('requests')
    requests.RequestException = type('RequestException', (Exception,), {})
    requests.Timeout = type('Timeout', (requests.RequestException,), {})
    requests.post = Mock()
    spec = importlib.util.spec_from_file_location(
        'isolated_gemini_client', ROOT / 'services/gemini_client.py',
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = 'isolated_gemini'
    ds = importlib.util.spec_from_file_location('isolated_gemini.diagnostics', ROOT / 'services/diagnostics.py')
    dm = importlib.util.module_from_spec(ds)
    ds.loader.exec_module(dm)
    with patch.dict(sys.modules, {
        'odoo': odoo, 'odoo.exceptions': exceptions, 'requests': requests,
        'isolated_gemini.diagnostics': dm,
    }):
        spec.loader.exec_module(module)
    return module, requests


CLIENT, REQUESTS = load_client()
# Extract the actual formatter to exercise its allowlist without importing ORM models.
TREE = ast.parse((ROOT / 'models/digitization_job.py').read_text(encoding='utf-8'))
JOB = next(node for node in TREE.body if isinstance(node, ast.ClassDef))
FORMATTER = next(node for node in JOB.body if getattr(node, 'name', '') == '_post_failure_diagnostic')
NAMESPACE = {'_': lambda text: text}
exec(compile(ast.Module(body=[FORMATTER], type_ignores=[]), 'diagnostic_formatter', 'exec'), NAMESPACE)


class TestStandaloneDiagnostics(unittest.TestCase):
    def setUp(self):
        self.state = CLIENT.DiagnosticState(stage='prepare', attempted_at='2026-10-26 12:00:00')
        self.client = CLIENT.GeminiClient(SimpleNamespace(context={'gemini_diagnostic': self.state}))
        self.job = Mock()
        self.config = {'api_key': 'secret-key', 'timeout': 60}

    def test_state_is_hashable_and_shared(self):
        mapping = {self.state: True}
        self.client.diagnostic['stage'] = 'request'
        self.assertTrue(mapping[self.state])
        self.assertEqual(self.state['stage'], 'request')

    def test_http_codes_and_safe_chatter(self):
        for status in (400, 401, 402, 403, 429, 500):
            with self.subTest(status=status):
                response = Mock(status_code=status, headers={}, content=b'present')
                response.json.return_value = {'error': {
                    'code': status, 'message': 'secret-key Bearer private-token document-content',
                }}
                with patch.object(REQUESTS, 'post', return_value=response):
                    with self.assertRaises(UserError):
                        self.client._post_to_gemini(self.job, self.config, 'https://test', {})
                self.assertEqual(self.state['http_status'], status)
                self.assertEqual(self.state['api_code'], status)
                self.assertEqual(self.state['stage'], 'receive')
                body = self._format(self.state)
                self.assertIn('HTTP %s' % status, body)
                self.assertIn('Gemini API code: %s' % status, body)
                for secret in ('secret-key', 'private-token', 'document-content'):
                    self.assertNotIn(secret, body)

    def _format(self, diagnostic):
        job = SimpleNamespace(
            res_id=42, attachment_id=SimpleNamespace(id=7),
            _post_automatic_pipeline_message=lambda message, **kwargs: message,
        )
        return NAMESPACE['_post_failure_diagnostic'](job, diagnostic)

    def test_transport_errors(self):
        for error, reason in ((REQUESTS.Timeout(), 'timeout'), (
            REQUESTS.RequestException('secret-key'), 'connection_error',
        )):
            with patch.object(REQUESTS, 'post', side_effect=error):
                with self.assertRaises(UserError):
                    self.client._post_to_gemini(self.job, self.config, 'https://test', {})
            self.assertEqual(self.state['stage'], 'request')
            self.assertEqual(self.state['reason'], reason)
            self.assertNotIn('secret-key', self._format(self.state))

    def test_invalid_json(self):
        with self.assertRaises(UserError):
            self.client._extract_json('secret-key invalid-json')
        self.assertEqual(self.state['reason'], 'invalid_json')
        self.assertNotIn('secret-key', self._format(self.state))

    def test_missing_candidates(self):
        with self.assertRaises(UserError):
            self.client._extract_text({'response_json': {'secret-key': 'document-content'}})
        self.assertEqual(self.state['reason'], 'no_candidates')
        self.assertNotIn('document-content', self._format(self.state))

    def test_valid_json(self):
        self.assertEqual(self.client._extract_json('{"lines": []}'), {'lines': []})
        self.assertIsNone(self.state.get('reason'))

    def test_untrusted_metadata_is_not_rendered(self):
        self.state['reason'] = 'secret-key'
        self.state['http_status'] = 'secret-key'
        self.state['api_code'] = 'secret-key'
        self.state['stage'] = 'secret-key'
        self.assertNotIn('secret-key', self._format(self.state))


class TestAutomaticPipeline(unittest.TestCase):
    def _run(self, process_error=None, apply_error=None, result=None, mode='full_bill'):
        from contextlib import nullcontext
        pipeline = next(n for n in JOB.body if getattr(n, 'name', '') == 'run_automatic_pipeline')
        service = Mock()
        service.apply.side_effect = apply_error
        service.apply.return_value = result or {'gemini_status': 'applied'}
        namespace = {
            '_': lambda value: value, '_logger': Mock(),
            'DiagnosticState': CLIENT.DiagnosticState,
            'fields': SimpleNamespace(Datetime=SimpleNamespace(now=lambda: '2026-10-26 12:00:00')),
            'DigitizationApplyService': Mock(return_value=service),
        }
        exec(compile(ast.Module(body=[pipeline], type_ignores=[]), 'pipeline', 'exec'), namespace)
        job = Mock(state='review', mode=mode)
        job.env.cr.savepoint.side_effect = lambda: nullcontext()
        job.with_context.return_value = job
        job.action_process.side_effect = process_error
        job._get_automatic_apply_blocker.return_value = False
        job._get_automatic_success_message.return_value = 'success'
        action = namespace['run_automatic_pipeline'](job)
        return job, service, action

    def test_processing_error_posts_once_and_does_not_apply(self):
        job, service, result = self._run(process_error=UserError('private-token'))
        self.assertEqual(result['status'], 'error')
        job._post_failure_diagnostic.assert_called_once()
        service.apply.assert_not_called()
        self.assertNotIn('private-token', str(result))

    def test_apply_errors_use_diagnostics(self):
        for error in (UserError('private-token'), RuntimeError('private-token')):
            with self.subTest(error=type(error).__name__):
                job, service, result = self._run(apply_error=error)
                self.assertEqual(result['status'], 'manual_review')
                diagnostic = job._post_failure_diagnostic.call_args.args[0]
                self.assertEqual(diagnostic['stage'], 'apply')
                self.assertNotIn('private-token', str(result))

    def test_success_has_no_failure_diagnostic(self):
        job, service, result = self._run()
        self.assertEqual(result['status'], 'applied')
        service.apply.assert_called_once()
        job._post_failure_diagnostic.assert_not_called()
        service.validate_for_automatic_apply.assert_not_called()

    def test_partial_result_and_validation_are_preserved(self):
        job, service, result = self._run(mode='partial_bill', result={
            'gemini_status': 'partial_applied', 'gemini_notification_type': 'warning',
        })
        self.assertEqual(result['status'], 'partial_applied')
        self.assertEqual(result['notification_type'], 'warning')
        service.validate_for_automatic_apply.assert_called_once()
        job._post_failure_diagnostic.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
