"""Isolated production-method tests with ORM/HTTP doubles, no Odoo DB required.

Run this file with Python. Access rules, real transactions and view loading
still require a staging smoke test.
"""
import ast
import base64
import copy
import importlib.util
import json
import logging
from contextlib import nullcontext
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('diagnostics', ROOT / 'services/diagnostics.py')
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class UserError(Exception):
    pass


def production_methods(path, class_name, names=None, **namespace):
    source = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef)
               and node.name == class_name)
    cls.bases = []
    if names is not None:
        cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                    and node.name in names]
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    scope = dict(json=json, re=re, base64=base64, copy=copy, UserError=UserError,
                 _=lambda value: value, _logger=logging.getLogger(__name__),
                 sanitize_diagnostics=diagnostics.sanitize_diagnostics)
    scope.update(namespace)
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(ROOT / path), 'exec'), scope)
    return scope[class_name]


Client = production_methods('services/gemini_client.py', 'GeminiClient')
Job = production_methods('models/digitization_job.py', 'AccountGeminiDigitizationJob', {
    'action_process', 'run_automatic_pipeline', '_save_processing_error', '_get_error_message',
    '_post_diagnostic_error',
}, GeminiClient=Client, Markup=Markup)


class TestStagingDiagnostics(unittest.TestCase):
    def setUp(self):
        self.key = 'test-API-secret+/='
        self.file_data = base64.b64encode(b'%PDF-private-attachment-for-test').decode()
        self.attachment = SimpleNamespace(id=17, name='invoice.pdf', mimetype='application/pdf',
                                          file_size=32, datas=self.file_data)
        config = Mock()
        config.sudo.return_value = config
        config.get_param.side_effect = lambda name, default=None: {
            'account_gemini_digitization.gemini_api_key': self.key,
            'account_gemini_digitization.gemini_model': 'gemini-test',
        }.get(name, default)
        env_type = type('Environment', (), {'__getitem__': lambda env, model: config})
        self.env = env_type()
        self.env.context = {'gemini_automatic_diagnostics': True}
        self.env.cr = Mock()
        self.env.cr.savepoint.side_effect = lambda: nullcontext()

    def job(self):
        job = Job()
        job.env = self.env
        job.id = 42
        job.mode = 'full_bill'
        job.state = 'draft'
        job.attachment_id = self.attachment
        job.ensure_one = Mock()
        job._check_linked_document_access = Mock()
        job.with_context = Mock(return_value=job)
        job._post_diagnostic_error = Mock()
        job.write = lambda values: job.__dict__.update(values)
        return job

    def test_failed_pipeline_reports_real_reason_and_stage_without_secrets(self):
        job = self.job()
        client = Client(self.env)
        client.diagnostic_stage = 'request'
        reason = 'Connection refused ?key=%s file=%s' % (self.key, self.file_data)
        client.last_raw_response = {
            'final_response': {'status_code': None, 'transport_error': {'message': reason}},
            'extracted_text_for_json_parse': self.file_data,
            'json_text_candidate': self.key,
        }

        def fail():
            error = UserError(reason)
            job._save_processing_error(error, client)
            raise error

        job.action_process = fail
        result = job.run_automatic_pipeline()
        self.assertEqual(result['status'], 'error')
        for expected in ('Gemini OCR error', 'Job: 42', 'Stage: request', 'Connection refused'):
            self.assertIn(expected, result['message'])
        self.assertTrue(result['sticky'])
        self.assertEqual(job.state, 'error')
        job._post_diagnostic_error.assert_called_once()
        output = json.dumps([result, job.raw_request_json, job.raw_response_json,
                             job._post_diagnostic_error.call_args.args])
        self.assertNotIn(self.key, output)
        self.assertNotIn(self.file_data, output)
        self.env.cr.commit.assert_not_called()

    def test_buttons_retain_failed_jobs_and_delete_successful_jobs(self):
        for model, cls in (('account_move', 'AccountMove'), ('purchase_order', 'PurchaseOrder')):
            button = production_methods('models/%s.py' % model, cls,
                                        {'action_create_gemini_digitization_job'})
            for status in ('error', 'applied', 'manual_review'):
                with self.subTest(model=model, status=status):
                    doc = Mock()
                    doc.move_type = 'in_invoice'
                    doc.state = 'draft'
                    doc._get_latest_gemini_digitization_attachment.return_value = self.attachment
                    doc._get_gemini_digitization_product_lines.return_value = []
                    job = Mock()
                    job.run_automatic_pipeline.return_value = {'status': status, 'message': status}
                    model_double = Mock()
                    model_double.create.return_value = job
                    doc.env = {'account.gemini.digitization.job': model_double}
                    button.action_create_gemini_digitization_job(doc)
                    self.assertEqual(job._unlink_temporary_job.call_count, int(status != 'error'))

    def test_nested_response_redaction_including_echoed_binary_and_encoded_key(self):
        from urllib.parse import quote
        raw = {'candidates': [{'content': {'parts': [
            {'inlineData': {'mimeType': 'image/png', 'data': 'unexpected-response-binary'}},
            {'text': 'echo ' + self.file_data},
        ]}}], 'transport_error': 'https://api/?key=' + quote(self.key, safe=''),
            'api_key': self.key, 'header': {'x-goog-api-key': self.key}}
        output = json.dumps(diagnostics.sanitize_diagnostics(raw, [self.key, self.file_data]))
        for secret in (self.key, quote(self.key, safe=''), self.file_data, 'unexpected-response-binary'):
            self.assertNotIn(secret, output)

    def test_preflight_failure_persists_attachment_and_model(self):
        job = self.job()
        job._save_processing_error(ValueError('invalid timeout'), Client(self.env))
        diagnostic = job.raw_response_json['diagnostic']
        self.assertEqual(diagnostic['stage'], 'preflight')
        self.assertEqual(diagnostic['attachment']['id'], 17)
        self.assertEqual(diagnostic['model'], 'gemini-test')
        self.assertNotIn('?key=', diagnostic['endpoint'])

    def test_error_chatter_contains_safe_summary_and_job_link(self):
        job = self.job()
        job.move_id = Mock()
        job._save_processing_error(UserError('<bad> ' + self.key + ' ' + self.file_data), Client(self.env))
        Job._post_diagnostic_error(job, job.error_message, job.raw_response_json['diagnostic'])
        body = str(job.move_id.message_post.call_args.kwargs['body'])
        self.assertIn('&lt;bad&gt;', body)
        self.assertIn('id=42', body)
        self.assertNotIn(self.key, body)
        self.assertNotIn(self.file_data, body)

    def test_real_client_tracks_decode_and_text_extraction_failures(self):
        for mode, expected in (('decode', 'attachment_decode'), ('no_candidates', 'text_extraction')):
            with self.subTest(mode=mode):
                job = self.job()
                client = Client(self.env)
                if mode == 'decode':
                    self.attachment.datas = ''
                else:
                    self.attachment.datas = self.file_data
                    client._build_prompt = Mock(return_value='test prompt')
                    client._post_to_gemini = Mock(return_value={'response_json': {'candidates': []}})
                with self.assertRaises(UserError):
                    client.recognize(job)
                self.assertEqual(client.diagnostic_stage, expected)

    def test_transport_exception_is_redacted_and_http_stage_is_distinct(self):
        class RequestException(Exception):
            pass

        class Timeout(RequestException):
            pass

        requests = SimpleNamespace(RequestException=RequestException, Timeout=Timeout, post=Mock())
        client_class = production_methods('services/gemini_client.py', 'GeminiClient', requests=requests)
        client = client_class(self.env)
        client._diagnostic_secrets = [self.key, self.file_data]
        config = {'api_key': self.key, 'timeout': 5}
        requests.post.side_effect = RequestException('Connection refused ?key=' + self.key)
        raw = client._execute_request(config, 'https://example.test', {}, 'test')
        self.assertEqual(client.diagnostic_stage, 'request')
        self.assertNotIn(self.key, json.dumps(raw))
        requests.post.side_effect = None
        requests.post.return_value = SimpleNamespace(status_code=503, headers={}, content=b'')
        raw = client._execute_request(config, 'https://example.test', {}, 'test')
        self.assertEqual(client.diagnostic_stage, 'http_response')
        self.assertEqual(raw['status_code'], 503)

    def test_json_parse_failure_preserves_decoder_details_and_gemini_feedback(self):
        job = self.job()
        client = Client(self.env)
        client.diagnostic_stage = 'json_parse'
        client.last_raw_response = {'final_response': {'status_code': 200, 'response_json': {
            'promptFeedback': {'blockReason': 'OTHER'},
            'candidates': [{'finishReason': 'MAX_TOKENS'}],
        }}, 'extracted_text_for_json_parse': '{broken'}
        try:
            client._extract_json('{broken')
        except UserError as error:
            job._save_processing_error(error, client)
        self.assertIn('Expecting property name', job.error_message)
        raw = job.raw_response_json
        self.assertEqual(raw['json_text_candidate'], '{broken')
        self.assertEqual(raw['diagnostic']['http_status'], 200)
        self.assertEqual(raw['diagnostic']['candidates_count'], 1)
        self.assertEqual(raw['diagnostic']['finishReason'], ['MAX_TOKENS'])
        self.assertEqual(raw['diagnostic']['promptFeedback'], {'blockReason': 'OTHER'})


if __name__ == '__main__':
    unittest.main()
