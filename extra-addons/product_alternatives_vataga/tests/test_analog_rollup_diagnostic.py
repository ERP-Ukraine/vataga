from unittest.mock import patch

from odoo.tests import TransactionCase
from odoo.tools.safe_eval import safe_eval

from odoo.addons.product_alternatives_vataga.models import (
    product_analytic_diagnostic,
)
from odoo.addons.product_alternatives_vataga.services import (
    analog_rollup_diagnostic,
)


class _RecordingCursor:
    dbname = 'diagnostic_test'

    def __init__(self):
        self.statements = []
        self.rolled_back = False
        self.closed = False

    def execute(self, statement):
        self.statements.append(statement)

    def fetchone(self):
        return ('on',)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class TestAnalogRollupDiagnostic(TransactionCase):
    def test_diagnostic_uses_isolated_read_only_cursor(self):
        diagnostic_cursor = _RecordingCursor()
        diagnostic_env = object()
        expected_result = {'log_messages': ['diagnostic result']}
        main_cursor = self.env.cr
        marker = self.env['res.partner'].create(
            {'name': 'Main transaction survives diagnostic'}
        )

        with (
            patch.object(
                type(self.env.registry),
                'cursor',
                return_value=diagnostic_cursor,
            ),
            patch.object(
                product_analytic_diagnostic.api,
                'Environment',
                return_value=diagnostic_env,
            ) as environment_mock,
            patch.object(
                analog_rollup_diagnostic,
                'run',
                return_value=expected_result,
            ) as run_mock,
        ):
            result = self.env[
                'product.analytic'
            ]._run_analog_rollup_diagnostic()

        self.assertEqual(result, expected_result)
        self.assertEqual(
            diagnostic_cursor.statements,
            ['SET TRANSACTION READ ONLY', 'SHOW transaction_read_only'],
        )
        self.assertTrue(diagnostic_cursor.rolled_back)
        self.assertTrue(diagnostic_cursor.closed)
        self.assertIs(self.env.cr, main_cursor)
        self.assertTrue(marker.exists())
        main_cursor.execute('SHOW transaction_read_only')
        self.assertEqual(main_cursor.fetchone()[0], 'off')
        environment_mock.assert_called_once_with(
            diagnostic_cursor,
            self.env.uid,
            dict(self.env.context),
        )
        run_mock.assert_called_once()
        self.assertIs(run_mock.call_args.args[0], diagnostic_env)

    def test_large_json_is_split_into_numbered_log_messages(self):
        run_id = 'test-run-id'
        json_report = 'x' * 42000
        messages = analog_rollup_diagnostic._build_log_messages(
            run_id,
            '=== HUMAN REPORT BEGIN ===\nOK\n=== HUMAN REPORT END ===',
            json_report,
            chunk_size=18000,
        )

        self.assertIn('=== HUMAN REPORT BEGIN ===', messages[0])
        json_messages = messages[1:]
        self.assertEqual(len(json_messages), 3)
        payload = ''
        for part_number, message in enumerate(json_messages, start=1):
            header, part = message.split('\n', 1)
            self.assertEqual(
                header,
                f'ANALOG_DIAG {run_id} JSON PART {part_number}/3',
            )
            self.assertLessEqual(len(part), 18000)
            payload += part
        self.assertEqual(
            payload,
            f'=== JSON BEGIN ===\n{json_report}\n=== JSON END ===',
        )

    def test_scheduled_action_code_is_safe_eval_compatible(self):
        cron = self.env.ref(
            'product_alternatives_vataga.ir_cron_analog_rollup_diagnostic'
        )
        calls = []
        logged = []

        class DiagnosticModel:
            def _run_analog_rollup_diagnostic(self, **kwargs):
                calls.append(kwargs)
                return {'log_messages': ['part one', 'part two']}

        safe_eval(
            cron.code.strip(),
            {
                'model': DiagnosticModel(),
                'log': lambda message, level='info': logged.append(
                    (message, level)
                ),
            },
            mode='exec',
            nocopy=True,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]['product_codes'],
            ['RES-BEC-0113', 'RES-BEC-0114'],
        )
        self.assertEqual(calls[0]['contract_references'], ['SE-10417'])
        self.assertFalse(calls[0]['all_contracts'])
        self.assertEqual(
            logged,
            [('part one', 'warning'), ('part two', 'warning')],
        )
        self.assertFalse(cron.active)
